import time
import uuid
from datetime import datetime, timezone

from app import db


class RuntimeJob(db.Model):
    __tablename__ = 'runtime_jobs'

    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    kind = db.Column(db.String(40), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default='pending', index=True)
    active_key = db.Column(db.String(80), unique=True, nullable=True)
    request_key = db.Column(db.String(64), unique=True, nullable=True)
    payload = db.Column(db.Text, nullable=False)
    account_ids = db.Column(db.JSON, nullable=False, default=list)
    snapshot = db.Column(db.JSON, nullable=False, default=dict)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    available_at = db.Column(db.Float, nullable=False, default=time.time)
    created_at = db.Column(db.Float, nullable=False, default=time.time)
    updated_at = db.Column(db.Float, nullable=False, default=time.time)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {**self.snapshot, 'taskId': self.id, 'status': self.status,
                'createdAt': datetime.fromtimestamp(self.created_at, timezone.utc).isoformat(),
                'cancelRequested': self.cancel_requested}


class RuntimeState(db.Model):
    __tablename__ = 'runtime_states'

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.JSON, nullable=False, default=dict)
