import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import threading
import time
from sqlalchemy import or_

from app import create_app, db
from app.models.account import Account
from app.models.gmail_watch import GmailWatch
from app.models.gmail_execution import GmailExecution
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_task import RechargeTask
from app.models.runtime_job import RuntimeJob
from app.services.batch_oauth_service import batch_oauth_manager
from app.services.email_poller import gmail_sync_daemon
from app.services.gmail_service import GmailService
from app.services.gmail_rule_service import GmailRuleService
from app.services.googlemail_service import googlemail_tasks
from app.services.recharge_service import RechargeService
from app.services.runtime_queue import RuntimeQueue, TERMINAL


def acquire_worker_lock(directory):
    Path(directory).mkdir(parents=True, exist_ok=True)
    stream = open(Path(directory) / 'worker.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            stream.write(b'0')
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('后台任务进程已在运行')
    return stream


def recover_interrupted_jobs():
    for job in RuntimeJob.query.filter_by(status='running').all():
        if job.kind in {'gmail_notification', 'gmail_sync'}:
            job.status = 'pending'
        else:
            job.status = 'failed'
            job.snapshot = {**job.snapshot, 'errorMessage': '进程中断，请核对账号实际状态后手动重试'}
            if job.kind not in {'googlemail', 'oauth'}:
                job.active_key = None
        job.updated_at = time.time()
    db.session.commit()


def run_job(application, identifier, stopping):
    with application.app_context():
        job = db.session.get(RuntimeJob, identifier)
        manager = None
        internal_id = None
        try:
            if job.cancel_requested or stopping.is_set():
                job.status = 'cancelled'
                job.active_key = None
                db.session.commit()
                return
            payload = GmailService._decrypt(job.payload)
            if job.kind in {'googlemail', 'oauth'}:
                accounts = Account.query.filter(Account.id.in_(payload['accountIds'])).all()
                if len(accounts) != len(payload['accountIds']) or any(account.status == 'locked' for account in accounts):
                    raise ValueError('账号不存在或已锁定')
                manager = googlemail_tasks if job.kind == 'googlemail' else batch_oauth_manager
                if job.kind == 'googlemail':
                    started = manager.start_task(application, accounts, payload.get('options'))
                    internal_id = started['taskId']
                else:
                    started = manager.start_batch(application, payload['accountIds'], payload.get('options'))
                    internal_id = started.task_id
                while True:
                    db.session.expire_all()
                    job = db.session.get(RuntimeJob, identifier)
                    locked = Account.query.filter(Account.id.in_(payload['accountIds']), Account.status == 'locked').first()
                    if job.cancel_requested or stopping.is_set() or locked:
                        manager.cancel_task(internal_id)
                    snapshot = manager.get_task(internal_id)
                    if not isinstance(snapshot, dict):
                        snapshot = snapshot.to_dict()
                    job.snapshot = snapshot
                    job.updated_at = time.time()
                    if snapshot['status'] in TERMINAL:
                        job.status = snapshot['status']
                        job.active_key = None
                        db.session.commit()
                        return
                    db.session.commit()
                    time.sleep(0.5)
            elif job.kind == 'gmail_notification':
                job.snapshot = GmailService.process_notification(payload['email'], payload['historyId']) or {}
            elif job.kind == 'gmail_sync':
                job.snapshot = gmail_sync_daemon.sync_once(application)
                if job.snapshot.get('failed'):
                    raise RuntimeError('邮箱同步存在失败项')
            job.status = 'completed'
            job.active_key = None
            job.updated_at = time.time()
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            automation_stopped = internal_id is None
            if internal_id is not None:
                try:
                    manager.cancel_task(internal_id)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        snapshot = manager.get_task(internal_id)
                        if not isinstance(snapshot, dict):
                            snapshot = snapshot.to_dict()
                        if snapshot['status'] in TERMINAL:
                            automation_stopped = True
                            break
                        time.sleep(0.5)
                except Exception:
                    automation_stopped = False
            job = db.session.get(RuntimeJob, identifier)
            job.snapshot = {**job.snapshot, 'errorMessage': '任务执行失败：' + type(error).__name__}
            if job.kind in {'gmail_notification', 'gmail_sync'}:
                job.status = 'pending'
                job.available_at = time.time() + min(3600, 15 * 2 ** min(job.attempts, 8))
            else:
                job.status = 'failed'
                if automation_stopped:
                    job.active_key = None
                else:
                    job.cancel_requested = True
                    job.snapshot['errorMessage'] = '无法确认自动化进程已停止，保留任务锁；请停止服务并核对账号状态'
            job.updated_at = time.time()
            db.session.commit()
        finally:
            db.session.remove()


def maintenance(application):
    with application.app_context():
        failures = []
        recharge_cursor = RuntimeQueue.state('maintenance').get('rechargeCursor', 0)
        GmailExecution.recover_expired_confirmations()
        retried = GmailRuleService.retry_due_actions()
        if retried['failed']:
            failures.append('gmail_actions:retry_failed')
        RechargeMutation.recover_expired()
        desired = RuntimeQueue.state('gmail_daemon')
        previous = RuntimeQueue.state('gmail_daemon_status')
        if desired.get('enabled') and previous.get('lastRunTimestamp', 0) + desired.get('intervalSeconds', 180) <= time.time():
            try:
                result = gmail_sync_daemon.sync_once(application)
                if result.get('failed'):
                    raise RuntimeError('邮箱同步存在失败项')
                RuntimeQueue.set_state('gmail_daemon_status', {**gmail_sync_daemon.status(),
                    'lastRunTimestamp': time.time(), 'consecutiveFailures': 0})
            except Exception as error:
                RuntimeQueue.set_state('gmail_daemon_status', {'lastError': type(error).__name__, 'lastRunTimestamp': time.time(),
                    'consecutiveFailures': previous.get('consecutiveFailures', 0) + 1})
                failures.append('gmail_sync:' + type(error).__name__)
        if application.config['RECHARGE_MODE'] == 'live':
            task_query = RechargeTask.query.filter(RechargeTask.is_mock.is_(False), or_(
                RechargeTask.status.in_(('pending', 'unknown', 'processing')),
                RechargeMutation.query.filter(RechargeMutation.task_no == RechargeTask.task_no,
                                              RechargeMutation.state == 'unknown').exists(),
            ))
            # Rotate independently of status timestamps so failed polls cannot
            # monopolize the bounded reconciliation batch.
            tasks = task_query.filter(RechargeTask.id > recharge_cursor).order_by(RechargeTask.id).limit(20).all()
            if len(tasks) < 20 and recharge_cursor:
                tasks += task_query.filter(RechargeTask.id <= recharge_cursor).order_by(RechargeTask.id).limit(20 - len(tasks)).all()
            targets = [(task.id, task.task_no) for task in tasks]
            for task_id, task_no in targets:
                recharge_cursor = task_id
                try:
                    RechargeService.get_task_by_no(task_no)
                except Exception as error:
                    db.session.rollback()
                    failures.append('recharge:' + type(error).__name__)
        from datetime import datetime, timedelta, timezone
        renew_before = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        for watch in GmailWatch.query.filter(GmailWatch.active.is_(True), GmailWatch.expiration_at <= renew_before).all():
            try:
                GmailService.watch(watch.connection)
            except Exception as error:
                db.session.rollback()
                failures.append('gmail_watch:' + type(error).__name__)
        previous = RuntimeQueue.state('maintenance')
        RuntimeQueue.set_state('maintenance', {
            'lastAttempt': time.time(), 'lastSuccess': time.time() if not failures else previous.get('lastSuccess', 0),
            'consecutiveFailures': previous.get('consecutiveFailures', 0) + 1 if failures else 0,
            'errors': sorted(set(failures)),
            'rechargeCursor': recharge_cursor,
        })
        db.session.remove()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    application = create_app()
    if options.check:
        with application.app_context():
            heartbeat = RuntimeQueue.state('worker').get('heartbeat', 0)
            raise SystemExit(0 if heartbeat > time.time() - 30 else 1)
    lock_stream = acquire_worker_lock(application.instance_path)
    stopping = threading.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda unused_signal, unused_frame: stopping.set())
    with application.app_context():
        recover_interrupted_jobs()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            active = {'automation': None, 'gmail': None}
            upkeep = None
            last_upkeep = 0
            while not stopping.is_set():
                with application.app_context():
                    RuntimeQueue.set_state('worker', {'heartbeat': time.time()})
                    for lane, kinds in {'automation': ('googlemail', 'oauth'), 'gmail': ('gmail_notification', 'gmail_sync')}.items():
                        if active[lane] is None or active[lane].done():
                            if active[lane]:
                                active[lane].result()
                            job = RuntimeQueue.claim(kinds)
                            active[lane] = executor.submit(run_job, application, job.id, stopping) if job else None
                    if (upkeep is None or upkeep.done()) and time.time() - last_upkeep >= 30:
                        if upkeep:
                            try:
                                upkeep.result()
                            except Exception as error:
                                previous = RuntimeQueue.state('maintenance')
                                RuntimeQueue.set_state('maintenance', {**previous, 'lastAttempt': time.time(),
                                    'consecutiveFailures': previous.get('consecutiveFailures', 0) + 1,
                                    'errors': [type(error).__name__]})
                        upkeep = executor.submit(maintenance, application)
                        last_upkeep = time.time()
                stopping.wait(1)
    finally:
        with application.app_context():
            RuntimeQueue.set_state('worker', {'heartbeat': 0})
        lock_stream.close()


if __name__ == '__main__':
    main()
