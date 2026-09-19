"""批量 Google OAuth 2.0 自动授权任务管理器。

协调 Playwright 无头浏览器与 Flask 后端，实现全自动批量挂机授权 Gmail API。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.gmail_connection import GmailConnection
from app.services.gmail_service import GmailService, OAuthStateManager


TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}


class BatchOAuthError(RuntimeError):
    """批量授权任务错误。"""


@dataclass
class BatchOAuthTaskRecord:
    task_id: str
    task_dir: Path
    task_file: Path
    output_dir: Path
    total_count: int
    account_ids: list[int]
    options: dict
    status: str = 'pending'
    completed_count: int = 0
    failed_count: int = 0
    current_index: int = 0
    current_email: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec='seconds'))
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    error_message: str | None = None
    logs: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    process: subprocess.Popen | None = field(default=None, repr=False)

    def to_dict(self):
        return {
            'taskId': self.task_id,
            'status': self.status,
            'totalCount': self.total_count,
            'completedCount': self.completed_count,
            'failedCount': self.failed_count,
            'currentIndex': self.current_index,
            'currentEmail': self.current_email,
            'createdAt': self.created_at,
            'startedAt': self.started_at,
            'finishedAt': self.finished_at,
            'errorMessage': self.error_message,
            'logs': self.logs[-100:],  # 最近 100 条脱敏日志
            'results': self.results,
        }


class BatchOAuthManager:
    """管理批量 OAuth 2.0 自动授权任务。"""

    def __init__(self, project_root=None, node_path=None, popen_factory=None):
        repository_root = Path(__file__).resolve().parents[2]
        self.project_root = Path(project_root or repository_root / 'googlemail').resolve()
        self.runtime_root = self.project_root / 'runtime' / 'oauth_tasks'
        self.node_path = node_path or shutil.which('node')
        self.popen_factory = popen_factory or subprocess.Popen
        self._tasks: dict[str, BatchOAuthTaskRecord] = {}
        self._lock = threading.RLock()

    def availability(self):
        reasons = []
        if not self.node_path:
            reasons.append('NODE_NOT_FOUND')
        if not (self.project_root / 'src' / 'batch-oauth-worker.mjs').is_file():
            reasons.append('OAUTH_WORKER_NOT_FOUND')
        if not (self.project_root / 'node_modules' / 'playwright').is_dir():
            reasons.append('PLAYWRIGHT_NOT_INSTALLED')
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
            return max(records, key=lambda item: item.created_at)

    def latest_task(self):
        with self._lock:
            if not self._tasks:
                return None
            return max(self._tasks.values(), key=lambda item: item.created_at)

    def get_task(self, task_id):
        with self._lock:
            return self._tasks.get(task_id)

    def start_batch(self, app, account_ids, options=None):
        """启动指定账号列表的批量 OAuth 2.0 自动授权。"""
        avail = self.availability()
        if not avail['available']:
            raise BatchOAuthError(f'运行环境不可用: {avail["reasons"][0]}')

        if not account_ids or not isinstance(account_ids, list):
            raise BatchOAuthError('请至少选择一个账号进行授权')

        options = options or {}
        headless = options.get('headless', True)
        slow_mo = int(options.get('slowMo', 150))
        account_delay = int(options.get('accountDelay', 3000))
        proxy = str(options.get('proxy', '')).strip() or os.environ.get('PROXY', '')

        with self._lock:
            if any(record.status not in TERMINAL_STATUSES for record in self._tasks.values()):
                raise BatchOAuthError('已有批量授权任务正在执行中，请等待完成或取消后再试')

            # 检索账号信息并生成每个账号专属的 OAuth 授权链接与 State
            task_items = []
            with app.app_context():
                accounts = Account.query.filter(Account.id.in_(account_ids)).all()
                if not accounts:
                    raise BatchOAuthError('未找到有效的账号记录')

                for acc in accounts:
                    if not acc.password:
                        continue
                    # 生成专属 authorization_url 并自动把 State 注册到 OAuthStateManager
                    auth_url, state = GmailService.authorization_url(account_id=acc.id)
                    task_items.append({
                        'accountId': acc.id,
                        'email': acc.email,
                        'password': acc.password,
                        'secret': acc.secret or '',
                        'recovery': acc.recovery or '',
                        'authUrl': auth_url,
                        'state': state,
                    })

            if not task_items:
                raise BatchOAuthError('所选账号缺少登录凭据（密码为空），无法发起自动授权')

            task_id = uuid.uuid4().hex
            task_dir = (self.runtime_root / task_id).resolve()
            output_dir = task_dir / 'output'
            task_file = task_dir / 'tasks.json'

            task_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            with task_file.open('w', encoding='utf-8') as f:
                json.dump(task_items, f, ensure_ascii=False, indent=2)

            record = BatchOAuthTaskRecord(
                task_id=task_id,
                task_dir=task_dir,
                task_file=task_file,
                output_dir=output_dir,
                total_count=len(task_items),
                account_ids=[item['accountId'] for item in task_items],
                options={
                    'headless': headless,
                    'slowMo': slow_mo,
                    'accountDelay': account_delay,
                    'proxy': proxy,
                },
            )
            self._tasks[task_id] = record

            worker = threading.Thread(
                target=self._run_worker,
                args=(app, record),
                name=f'batch-oauth-{task_id[:8]}',
                daemon=True,
            )
            worker.start()
            return record

    def cancel_task(self, task_id):
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            if record.status in TERMINAL_STATUSES:
                return record
            record.cancel_requested = True
            record.status = 'cancelled'
            record.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
            proc = record.process

        if proc and proc.poll() is None:
            threading.Thread(
                target=self._terminate_process,
                args=(proc,),
                name=f'cancel-proc-{task_id[:8]}',
                daemon=True,
            ).start()
        return record

    def _terminate_process(self, process):
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _run_worker(self, app, record: BatchOAuthTaskRecord):
        process = None
        try:
            with self._lock:
                if record.cancel_requested:
                    return
                record.status = 'running'
                record.started_at = datetime.now(timezone.utc).isoformat(timespec='seconds')

            env = os.environ.copy()
            env.update({
                'OAUTH_TASKS_FILE': str(record.task_file),
                'OUTPUT_DIR': str(record.output_dir),
                'HEADLESS': 'true' if record.options['headless'] else 'false',
                'SLOW_MO': str(record.options['slowMo']),
                'ACCOUNT_DELAY': str(record.options['accountDelay']),
            })
            if record.options.get('proxy'):
                env['PROXY'] = record.options['proxy']

            creation_flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            process = self.popen_factory(
                [self.node_path, 'src/batch-oauth-worker.mjs'],
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creation_flags,
            )

            with self._lock:
                record.process = process

            # 读取子进程标准输出
            if process.stdout:
                for line in process.stdout:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith('__OAUTH_EVENT__'):
                        event_json = stripped[len('__OAUTH_EVENT__'):]
                        try:
                            event_data = json.loads(event_json)
                            self._handle_event(app, record, event_data)
                        except Exception:
                            pass
                    else:
                        with self._lock:
                            record.logs.append({
                                'time': datetime.now(timezone.utc).strftime('%H:%M:%S'),
                                'message': stripped,
                            })

            exit_code = process.wait()
            with self._lock:
                if record.status not in TERMINAL_STATUSES:
                    record.status = 'completed' if exit_code == 0 else 'failed'
                record.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')

        except Exception as err:
            with self._lock:
                record.status = 'failed'
                record.error_message = str(err)
                record.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        finally:
            with self._lock:
                record.process = None

            # 清理含敏感明文的临时任务文件
            try:
                if record.task_file.exists():
                    record.task_file.unlink()
            except Exception:
                pass

            # 同步最终结果到数据库与记录
            self._sync_final_results(app, record)

    def _handle_event(self, app, record: BatchOAuthTaskRecord, event: dict):
        ev_type = event.get('event')
        with self._lock:
            if ev_type == 'account_started':
                record.current_index = event.get('index', 0) + 1
                record.current_email = event.get('email')
            elif ev_type == 'account_completed':
                if event.get('success'):
                    record.completed_count += 1
                else:
                    record.failed_count += 1
                record.results.append(event)
            elif ev_type == 'log':
                record.logs.append({
                    'time': datetime.now(timezone.utc).strftime('%H:%M:%S'),
                    'message': event.get('message', ''),
                })
            elif ev_type == 'batch_finished':
                record.completed_count = event.get('completed', record.completed_count)
                record.failed_count = event.get('failed', record.failed_count)

    def _sync_final_results(self, app, record: BatchOAuthTaskRecord):
        """解析结果文件并将授权成功信息同步到数据库。"""
        results_file = record.output_dir / 'oauth-results.jsonl'
        if not results_file.exists():
            return

        with app.app_context():
            try:
                with results_file.open('r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        item = json.loads(line)
                        account_id = item.get('accountId')
                        email = item.get('email')
                        success = item.get('success')

                        if success and account_id:
                            acc = db.session.get(Account, account_id)
                            conn = GmailConnection.query.filter_by(email=email).first()
                            if acc and conn:
                                history = AccountHistory(
                                    account_id=acc.id,
                                    field_name='gmail_oauth',
                                    old_value=acc.remark or '',
                                    new_value='[自动授权成功] Gmail API 凭证已生成并保存',
                                )
                                db.session.add(history)
                db.session.commit()
            except Exception:
                db.session.rollback()


# 全局单例管理器
batch_oauth_manager = BatchOAuthManager()
