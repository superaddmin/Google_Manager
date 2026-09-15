"""Googlemail 子进程任务适配器。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}
EMAIL_PATTERN = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
PASSTHROUGH_ENVIRONMENT_KEYS = {
    'APPDATA',
    'CHROME_CHANNEL',
    'COMSPEC',
    'HOME',
    'HTTPS_PROXY',
    'HTTP_PROXY',
    'LOCALAPPDATA',
    'NO_PROXY',
    'PATH',
    'PATHEXT',
    'PLAYWRIGHT_BROWSERS_PATH',
    'PROGRAMFILES',
    'PROGRAMFILES(X86)',
    'PROGRAMW6432',
    'PROXY',
    'SYSTEMROOT',
    'TEMP',
    'TMP',
    'USERPROFILE',
    'WINDIR',
}


class GooglemailTaskError(RuntimeError):
    """Googlemail 任务错误。"""


class GooglemailValidationError(ValueError):
    """账号或运行选项校验失败。"""

    def __init__(self, message, invalid_account_ids=None):
        super().__init__(message)
        self.invalid_account_ids = invalid_account_ids or []


@dataclass
class GooglemailTaskRecord:
    task_id: str
    task_dir: Path
    input_file: Path
    output_dir: Path
    account_ids_by_email: dict[str, int]
    total_count: int
    options: dict
    status: str = 'pending'
    completed_count: int = 0
    failed_count: int = 0
    synced_count: int = 0
    manual_review_count: int = 0
    exit_code: int | None = None
    error_code: str | None = None
    created_at: str = field(default_factory=lambda: _iso_now())
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    timed_out: bool = False
    process: subprocess.Popen | None = field(default=None, repr=False)


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _bounded_integer(value, name, default, minimum, maximum):
    if value is None or value == '':
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GooglemailValidationError(f'{name} 必须是整数')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise GooglemailValidationError(f'{name} 必须是整数') from error
    if parsed < minimum or parsed > maximum:
        raise GooglemailValidationError(f'{name} 必须在 {minimum} 到 {maximum} 之间')
    return parsed


def normalize_googlemail_options(options=None):
    """规范化主页提交的非敏感运行选项。"""
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise GooglemailValidationError('运行选项格式错误')

    headless = options.get('headless', True)
    if not isinstance(headless, bool):
        raise GooglemailValidationError('headless 必须是布尔值')

    recovery_emails = options.get('recoveryEmails', [])
    if isinstance(recovery_emails, str):
        recovery_emails = re.split(r'[,\r\n]+', recovery_emails)
    if not isinstance(recovery_emails, list):
        raise GooglemailValidationError('恢复邮箱池格式错误')
    recovery_emails = [str(item).strip() for item in recovery_emails if str(item).strip()]
    if len(recovery_emails) > 50 or any(
        not EMAIL_PATTERN.fullmatch(item) for item in recovery_emails
    ):
        raise GooglemailValidationError('恢复邮箱池包含无效地址或数量超过 50')

    return {
        'headless': headless,
        'slowMo': _bounded_integer(options.get('slowMo'), '操作延迟', 200, 0, 5000),
        'accountDelay': _bounded_integer(
            options.get('accountDelay'), '账号间隔', 5000, 0, 600000
        ),
        'accountsPerRecovery': _bounded_integer(
            options.get('accountsPerRecovery'), '恢复邮箱复用数', 5, 1, 100
        ),
        'maxRuntimeMinutes': _bounded_integer(
            options.get('maxRuntimeMinutes'), '最长运行时间', 120, 1, 480
        ),
        'recoveryEmails': recovery_emails,
    }


class GooglemailTaskManager:
    """管理单个 Googlemail 子进程及脱敏任务状态。"""

    def __init__(self, project_root=None, node_path=None, popen_factory=None):
        repository_root = Path(__file__).resolve().parents[2]
        self.project_root = Path(project_root or repository_root / 'googlemail').resolve()
        self.runtime_root = self.project_root / 'runtime' / 'tasks'
        self.node_path = node_path or shutil.which('node')
        self.popen_factory = popen_factory or subprocess.Popen
        self._tasks = {}
        self._lock = threading.RLock()

    def availability(self):
        reasons = []
        if not self.node_path:
            reasons.append('NODE_NOT_FOUND')
        if not (self.project_root / 'src' / 'main.mjs').is_file():
            reasons.append('ENTRY_NOT_FOUND')
        if not (self.project_root / 'node_modules' / 'playwright').is_dir():
            reasons.append('DEPENDENCIES_NOT_INSTALLED')
        return {
            'available': not reasons,
            'reasons': reasons,
        }

    def active_task(self):
        with self._lock:
            records = [
                record for record in self._tasks.values()
                if record.status not in TERMINAL_STATUSES
            ]
            if not records:
                return None
            return self._public_task(max(records, key=lambda item: item.created_at))

    def latest_task(self):
        with self._lock:
            if not self._tasks:
                return None
            return self._public_task(
                max(self._tasks.values(), key=lambda item: item.created_at)
            )

    def get_task(self, task_id):
        with self._lock:
            record = self._tasks.get(task_id)
            return self._public_task(record) if record else None

    def start_task(self, app, accounts, options=None):
        availability = self.availability()
        if not availability['available']:
            raise GooglemailTaskError(availability['reasons'][0])

        normalized_options = normalize_googlemail_options(options)
        account_lines, account_ids_by_email = self._serialize_accounts(accounts)

        with self._lock:
            if any(record.status not in TERMINAL_STATUSES for record in self._tasks.values()):
                raise GooglemailTaskError('TASK_ALREADY_RUNNING')

            self._prune_tasks()
            task_id = uuid.uuid4().hex
            task_dir = (self.runtime_root / task_id).resolve()
            if self.runtime_root.resolve() not in task_dir.parents:
                raise GooglemailTaskError('INVALID_TASK_PATH')
            output_dir = task_dir / 'output'
            input_file = task_dir / 'accounts.txt'
            task_dir.mkdir(parents=True, exist_ok=False)
            with input_file.open('w', encoding='utf-8', newline='\n') as input_stream:
                input_stream.write('\n'.join(account_lines) + '\n')
            try:
                input_file.chmod(0o600)
            except OSError:
                pass

            record = GooglemailTaskRecord(
                task_id=task_id,
                task_dir=task_dir,
                input_file=input_file,
                output_dir=output_dir,
                account_ids_by_email=account_ids_by_email,
                total_count=len(account_lines),
                options=normalized_options,
            )
            self._tasks[task_id] = record

            worker = threading.Thread(
                target=self._run_task,
                args=(app, record),
                name=f'googlemail-{task_id[:8]}',
                daemon=True,
            )
            worker.start()
            return self._public_task(record)

    def cancel_task(self, task_id):
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            if record.status in TERMINAL_STATUSES:
                return self._public_task(record)
            record.cancel_requested = True
            process = record.process

        if process and process.poll() is None:
            threading.Thread(
                target=self._terminate_process,
                args=(process,),
                name=f'googlemail-cancel-{task_id[:8]}',
                daemon=True,
            ).start()
        return self.get_task(task_id)

    def _serialize_accounts(self, accounts):
        if not accounts:
            raise GooglemailValidationError('至少选择一个账号')
        if len(accounts) > 500:
            raise GooglemailValidationError('单次任务最多处理 500 个账号')

        lines = []
        account_ids_by_email = {}
        invalid_ids = []
        for account in accounts:
            fields = [
                str(account.email or '').strip(),
                str(account.password or ''),
                str(account.recovery or '').strip(),
                str(account.secret or '').replace(' ', '').strip(),
            ]
            if (
                not fields[0]
                or not fields[1].strip()
                or any('\r' in field or '\n' in field or '----' in field for field in fields)
            ):
                invalid_ids.append(account.id)
                continue
            lines.append('----'.join(fields))
            account_ids_by_email[fields[0]] = account.id

        if invalid_ids:
            raise GooglemailValidationError('所选账号包含缺失或不兼容字段', invalid_ids)
        return lines, account_ids_by_email

    def _run_task(self, app, record):
        timer = None
        process = None
        exit_code = None
        process_started = False
        runtime_error_code = None
        synced_count = 0
        result_sync_succeeded = False
        try:
            with self._lock:
                if record.cancel_requested:
                    return
                record.status = 'running'
                record.started_at = _iso_now()

            env = {
                key: value
                for key, value in os.environ.items()
                if key.upper() in PASSTHROUGH_ENVIRONMENT_KEYS
            }
            env.update({
                'ACCOUNTS_FILE': str(record.input_file),
                'OUTPUT_DIR': str(record.output_dir),
                'HEADLESS': str(record.options['headless']).lower(),
                'SLOW_MO': str(record.options['slowMo']),
                'ACCOUNT_DELAY': str(record.options['accountDelay']),
                'ACCOUNTS_PER_RECOVERY': str(record.options['accountsPerRecovery']),
                'RECOVERY_EMAIL_POOL': ','.join(record.options['recoveryEmails']),
            })
            creation_flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            process = self.popen_factory(
                [self.node_path, 'src/main.mjs'],
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creation_flags,
            )
            process_started = True
            with self._lock:
                record.process = process
                cancel_requested = record.cancel_requested

            if cancel_requested and process.poll() is None:
                self._terminate_process(process)

            timer = threading.Timer(
                record.options['maxRuntimeMinutes'] * 60,
                self._timeout_task,
                args=(record.task_id,),
            )
            timer.daemon = True
            timer.start()

            if process.stdout:
                for _line in process.stdout:
                    pass
            exit_code = process.wait()
        except Exception:
            runtime_error_code = (
                'PROCESS_RUNTIME_FAILED' if process_started else 'PROCESS_START_FAILED'
            )
            if process and process.poll() is None:
                self._terminate_process(process)
            if process:
                try:
                    exit_code = process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        finally:
            if timer:
                timer.cancel()

            with self._lock:
                record.status = 'finalizing'
                record.process = None
                record.exit_code = exit_code
                self._refresh_counts(record)

            if process_started:
                try:
                    synced_count = self._sync_results(app, record)
                    result_sync_succeeded = True
                except Exception:
                    runtime_error_code = 'RESULT_SYNC_FAILED'

            cleanup_succeeded = self._remove_file(record.input_file)
            if result_sync_succeeded:
                cleanup_succeeded = (
                    self._remove_file(record.output_dir / 'result.txt')
                    and cleanup_succeeded
                )

            with self._lock:
                record.synced_count = synced_count
                if runtime_error_code == 'RESULT_SYNC_FAILED':
                    record.status = 'failed'
                    record.error_code = runtime_error_code
                elif not cleanup_succeeded:
                    record.status = 'failed'
                    record.error_code = 'SENSITIVE_CLEANUP_FAILED'
                elif record.timed_out:
                    record.status = 'failed'
                    record.error_code = 'TASK_TIMEOUT'
                elif record.cancel_requested:
                    record.status = 'cancelled'
                    record.error_code = 'TASK_CANCELLED'
                elif runtime_error_code:
                    record.status = 'failed'
                    record.error_code = runtime_error_code
                elif exit_code != 0:
                    record.status = 'failed'
                    record.error_code = 'PROCESS_EXIT_NONZERO'
                else:
                    record.status = 'completed'
                    record.error_code = None
                record.finished_at = _iso_now()
                self._refresh_counts(record)

            self._persist_task(app, record)

    def _persist_task(self, app, record):
        """把已结束的任务写入历史表，失败不影响任务状态。"""
        from app import db
        from app.models.googlemail_task import GooglemailTask

        try:
            public = self._public_task(record)
            with app.app_context():
                row = db.session.get(GooglemailTask, record.task_id)
                if row is None:
                    row = GooglemailTask(task_id=record.task_id)
                    db.session.add(row)
                row.status = public['status']
                row.total_count = public['totalCount']
                row.completed_count = public['completedCount']
                row.failed_count = public['failedCount']
                row.synced_count = public['syncedCount']
                row.manual_review_count = public['manualReviewCount']
                row.exit_code = public['exitCode']
                row.error_code = public['errorCode']
                row.headless = public['headless']
                row.created_at = public['createdAt']
                row.started_at = public['startedAt']
                row.finished_at = public['finishedAt']
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger = getattr(app, 'logger', None)
            if logger:
                logger.warning('Googlemail 任务历史写入失败')

    def _sync_results(self, app, record):
        result_file = record.output_dir / 'result.txt'
        if not result_file.is_file():
            return 0

        from app import db
        from app.services.account_service import AccountService

        updates = []
        for raw_line in result_file.read_text(encoding='utf-8').splitlines():
            if not raw_line.strip():
                continue
            fields = raw_line.split('----')
            if len(fields) != 4:
                raise ValueError('Googlemail 结果文件格式无效')
            email, _password, recovery, secret = fields
            account_id = record.account_ids_by_email.get(email)
            if not account_id or not secret.strip():
                raise ValueError('Googlemail 结果文件包含无效账号或密钥')
            updates.append((account_id, recovery, secret))

        if result_file.stat().st_size > 0 and not updates:
            raise ValueError('Googlemail 结果文件格式无效')

        synced_count = 0
        with app.app_context():
            try:
                for account_id, recovery, secret in updates:
                    update_data = {'secret': secret}
                    if recovery:
                        update_data['recovery'] = recovery
                    if not AccountService.update_account(account_id, update_data, commit=False):
                        raise ValueError('Googlemail 结果对应账号不存在')
                    synced_count += 1
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
        return synced_count

    @staticmethod
    def _remove_file(path, attempts=3):
        for attempt in range(attempts):
            try:
                path.unlink(missing_ok=True)
                return True
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.05)
        return False

    def _refresh_counts(self, record):
        progress_file = record.output_dir / 'progress.json'
        if progress_file.is_file():
            try:
                progress = json.loads(progress_file.read_text(encoding='utf-8'))
                if not isinstance(progress, dict):
                    raise ValueError('Googlemail 进度文件格式无效')
                completed = progress.get('completed', [])
                failed = progress.get('failed', [])
                if isinstance(completed, list) and isinstance(failed, list):
                    known_emails = set(record.account_ids_by_email)
                    completed_emails = set(completed) & known_emails
                    failed_emails = (set(failed) & known_emails) - completed_emails
                    record.completed_count = len(completed_emails)
                    record.failed_count = len(failed_emails)
            except (OSError, ValueError, TypeError):
                pass

        manual_review_file = record.output_dir / 'manual-review.jsonl'
        if manual_review_file.is_file():
            try:
                record.manual_review_count = sum(
                    1 for line in manual_review_file.read_text(encoding='utf-8').splitlines()
                    if line.strip()
                )
            except (OSError, UnicodeError):
                pass

    def _public_task(self, record):
        self._refresh_counts(record)
        processed = min(record.completed_count + record.failed_count, record.total_count)
        return {
            'taskId': record.task_id,
            'status': record.status,
            'totalCount': record.total_count,
            'completedCount': record.completed_count,
            'failedCount': record.failed_count,
            'pendingCount': max(record.total_count - processed, 0),
            'syncedCount': record.synced_count,
            'manualReviewCount': record.manual_review_count,
            'exitCode': record.exit_code,
            'errorCode': record.error_code,
            'headless': record.options['headless'],
            'createdAt': record.created_at,
            'startedAt': record.started_at,
            'finishedAt': record.finished_at,
        }

    def _timeout_task(self, task_id):
        with self._lock:
            record = self._tasks.get(task_id)
            if not record or record.status in TERMINAL_STATUSES:
                return
            record.timed_out = True
            process = record.process
        if process and process.poll() is None:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process):
        tree_terminated = False
        if os.name == 'nt' and getattr(process, 'pid', None):
            try:
                result = subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                tree_terminated = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass

        try:
            if not tree_terminated:
                process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        except OSError:
            pass

    def _prune_tasks(self):
        terminal_records = sorted(
            (record for record in self._tasks.values() if record.status in TERMINAL_STATUSES),
            key=lambda item: item.created_at,
            reverse=True,
        )
        for record in terminal_records[20:]:
            self._tasks.pop(record.task_id, None)


googlemail_tasks = GooglemailTaskManager()
