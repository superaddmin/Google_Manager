import time

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.runtime_job import RuntimeJob, RuntimeState
from app.services.gmail_service import GmailService

TERMINAL = {'completed', 'failed', 'cancelled'}


class QueueConflict(ValueError):
    pass


class RuntimeQueue:
    @staticmethod
    def enqueue(kind, payload, active_key=None, request_key=None):
        if request_key:
            existing = RuntimeJob.query.filter_by(request_key=request_key).first()
            if existing:
                return existing
        job = RuntimeJob(kind=kind, payload=GmailService._encrypt(payload), active_key=active_key,
                         account_ids=payload.get('accountIds', []),
                         request_key=request_key, snapshot={'totalCount': len(payload.get('accountIds', [])),
                                                          'completedCount': 0, 'failedCount': 0, 'logs': []})
        db.session.add(job)
        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if request_key:
                existing = RuntimeJob.query.filter_by(request_key=request_key).first()
                if existing:
                    return existing
            raise QueueConflict('已有同类任务等待或正在执行') from error
        return job

    @staticmethod
    def latest(kind, active=False):
        query = RuntimeJob.query.filter_by(kind=kind)
        if active:
            query = query.filter(or_(RuntimeJob.status.notin_(TERMINAL), RuntimeJob.active_key.isnot(None)))
        return query.order_by(RuntimeJob.created_at.desc()).first()

    @staticmethod
    def cancel(job):
        values = {'cancel_requested': True, 'updated_at': time.time()}
        if job.status == 'pending':
            changed = RuntimeJob.query.filter_by(id=job.id, status='pending').update(
                {**values, 'status': 'cancelled', 'active_key': None}, synchronize_session=False)
            if not changed:
                RuntimeJob.query.filter_by(id=job.id).update(values, synchronize_session=False)
        elif job.status not in TERMINAL:
            RuntimeJob.query.filter_by(id=job.id).update(values, synchronize_session=False)
        db.session.commit()
        db.session.refresh(job)
        return job

    @classmethod
    def cancel_for_account(cls, account_id):
        for job in RuntimeJob.query.filter(RuntimeJob.status.notin_(TERMINAL), RuntimeJob.kind.in_(('googlemail', 'oauth'))).all():
            if account_id in job.account_ids:
                cls.cancel(job)

    @staticmethod
    def claim(kinds=None):
        query = RuntimeJob.query.filter_by(status='pending').filter(
            RuntimeJob.available_at <= time.time(), RuntimeJob.cancel_requested.is_(False),
        )
        if kinds:
            query = query.filter(RuntimeJob.kind.in_(kinds))
        job = query.order_by(RuntimeJob.created_at).first()
        if job is None:
            return None
        identifier = job.id
        claimed = RuntimeJob.query.filter_by(id=identifier, status='pending', cancel_requested=False).update(
            {'status': 'running', 'updated_at': time.time(), 'attempts': RuntimeJob.attempts + 1},
            synchronize_session=False,
        )
        db.session.commit()
        return db.session.get(RuntimeJob, identifier) if claimed else None

    @staticmethod
    def state(key, default=None):
        record = db.session.get(RuntimeState, key)
        return dict(record.value) if record else dict(default or {})

    @staticmethod
    def set_state(key, value):
        record = db.session.get(RuntimeState, key)
        if record is None:
            try:
                with db.session.begin_nested():
                    db.session.add(RuntimeState(key=key, value=value))
                    db.session.flush()
            except IntegrityError:
                pass
        RuntimeState.query.filter_by(key=key).update({'value': value}, synchronize_session=False)
        db.session.commit()

    @classmethod
    def daemon_status(cls):
        desired = cls.state('gmail_daemon', {'enabled': False, 'intervalSeconds': 180})
        snapshot = cls.state('gmail_daemon_status')
        heartbeat = cls.state('worker').get('heartbeat', 0)
        return {**snapshot, **desired, 'isRunning': bool(desired.get('enabled') and heartbeat > time.time() - 30),
                'workerOnline': heartbeat > time.time() - 30}
