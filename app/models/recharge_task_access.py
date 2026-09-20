import hashlib
import hmac

from flask import current_app, has_request_context, session

from app import db


class RechargeTaskAccess(db.Model):
    """Persist the creating session's authority without storing its bearer token."""

    __tablename__ = 'recharge_task_access'

    task_no = db.Column(db.String(64), db.ForeignKey('recharge_tasks.task_no'), primary_key=True)
    owner_digest = db.Column(db.String(64), nullable=False)

    @staticmethod
    def _owner_digest(task_no):
        if not has_request_context():
            return None
        context = session.get('recharge_context')
        if not isinstance(context, str) or not context:
            return None
        secret = current_app.secret_key
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        return hmac.new(secret, f'task-owner:{task_no}:{context}'.encode('utf-8'), hashlib.sha256).hexdigest()

    @classmethod
    def bind(cls, task_no):
        """Add an ownership grant to the caller's task-creation transaction."""
        digest = cls._owner_digest(task_no)
        if digest is not None:
            db.session.add(cls(task_no=task_no, owner_digest=digest))

    @classmethod
    def permits_current_session(cls, task_no):
        if not has_request_context():
            return False
        from app.services.auth_service import is_admin_authenticated
        if is_admin_authenticated():
            return True
        digest = cls._owner_digest(task_no)
        record = db.session.get(cls, task_no) if digest is not None else None
        return record is not None and hmac.compare_digest(record.owner_digest, digest)
