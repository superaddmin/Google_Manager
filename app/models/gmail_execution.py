import hashlib
import json
import time
import uuid

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app import db


class GmailExecution(db.Model):
    __tablename__ = 'gmail_executions'

    key = db.Column(db.String(64), primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey('gmail_task_logs.id', ondelete='CASCADE'), nullable=False, unique=True)
    lease_until = db.Column(db.Float, nullable=False)
    lease_token = db.Column(db.String(32), nullable=True)

    @classmethod
    def acquire(cls, connection, rule, message_id, actions, status):
        from app.models.gmail_task_log import GmailTaskLog, GmailActionConfirmation
        key = hashlib.sha256(json.dumps([connection.id, rule.id, rule.search_query, rule.requires_confirmation, actions, message_id], sort_keys=True).encode()).hexdigest()
        now = time.time()
        record = db.session.get(cls, key)
        if record is None:
            try:
                with db.session.begin_nested():
                    log = GmailTaskLog(connection_id=connection.id, rule_id=rule.id, message_id=message_id,
                                       action='modify', status=status,
                                       request_data=json.dumps({'addLabelIds': actions[0], 'removeLabelIds': actions[1]}))
                    db.session.add(log)
                    db.session.flush()
                    db.session.add(cls(key=key, log_id=log.id, lease_until=now + 300,
                                       lease_token=uuid.uuid4().hex))
                    if status == 'pending_confirmation':
                        db.session.add(GmailActionConfirmation(task_log_id=log.id))
                    db.session.flush()
                db.session.commit()
                return log
            except IntegrityError:
                record = db.session.get(cls, key)
        log = db.session.get(GmailTaskLog, record.log_id)
        if log.status in {'running', 'confirming'} and record.lease_until > now:
            raise RuntimeError('同一邮件动作正在执行，稍后重试')
        retryable = GmailTaskLog.query.filter_by(id=log.id).filter(or_(
            GmailTaskLog.status == 'failed',
            GmailTaskLog.status.in_(('running', 'confirming')) & (cls.query.filter(cls.key == key, cls.lease_until <= now).exists()),
        )).update({'status': status, 'error_message': None, 'completed_at': None}, synchronize_session=False)
        if not retryable:
            db.session.rollback()
            return None
        record.lease_until = now + 300
        record.lease_token = uuid.uuid4().hex
        if status == 'pending_confirmation':
            if log.confirmation:
                log.confirmation.status = 'pending'
                log.confirmation.reviewer = None
                log.confirmation.reviewed_at = None
            else:
                db.session.add(GmailActionConfirmation(task_log_id=log.id))
        db.session.commit()
        db.session.refresh(log)
        return log

    @classmethod
    def recover_expired_confirmations(cls):
        from app.models.gmail_task_log import GmailTaskLog
        expired = cls.query.filter(cls.lease_until <= time.time()).subquery()
        logs = GmailTaskLog.query.filter(GmailTaskLog.status == 'confirming', GmailTaskLog.id.in_(db.select(expired.c.log_id))).all()
        for log in logs:
            changed = GmailTaskLog.query.filter(GmailTaskLog.id == log.id, GmailTaskLog.status == 'confirming',
                                               GmailTaskLog.id.in_(db.select(expired.c.log_id))).update(
                {'status': 'pending_confirmation'}, synchronize_session=False)
            if changed:
                execution = cls.query.filter_by(log_id=log.id).one()
                execution.lease_token = uuid.uuid4().hex
                if log.confirmation:
                    log.confirmation.status = 'pending'
                    log.confirmation.reviewer = None
                    log.confirmation.reviewed_at = None
        db.session.commit()
