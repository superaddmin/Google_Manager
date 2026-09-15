"""Gmail 规则执行日志与人工确认模型。"""
import json
from datetime import datetime, timezone

from app import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GmailTaskLog(db.Model):
    """记录每次规则动作的状态、输入和结果。"""

    __tablename__ = 'gmail_task_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('gmail_connections.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    rule_id = db.Column(
        db.Integer,
        db.ForeignKey('gmail_rules.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    message_id = db.Column(db.String(255), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(30), nullable=False, index=True)
    request_data = db.Column(db.Text, nullable=False, default='{}')
    result_data = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    connection = db.relationship('GmailConnection', backref='task_logs')
    rule = db.relationship('GmailRule', backref='task_logs')
    confirmation = db.relationship(
        'GmailActionConfirmation',
        back_populates='task_log',
        uselist=False,
        cascade='all, delete-orphan',
    )

    @staticmethod
    def _decode(value):
        if value is None:
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value

    def to_dict(self):
        return {
            'id': self.id,
            'connectionId': self.connection_id,
            'ruleId': self.rule_id,
            'messageId': self.message_id,
            'action': self.action,
            'status': self.status,
            'requestData': self._decode(self.request_data),
            'resultData': self._decode(self.result_data),
            'errorMessage': self.error_message,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'completedAt': self.completed_at.isoformat() if self.completed_at else None,
            'confirmation': self.confirmation.to_dict() if self.confirmation else None,
        }


class GmailActionConfirmation(db.Model):
    """需要管理员确认的 Gmail 动作。"""

    __tablename__ = 'gmail_action_confirmations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_log_id = db.Column(
        db.Integer,
        db.ForeignKey('gmail_task_logs.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
        index=True,
    )
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    reviewer = db.Column(db.String(100), nullable=True)
    note = db.Column(db.String(500), nullable=True)
    requested_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    task_log = db.relationship('GmailTaskLog', back_populates='confirmation')

    def to_dict(self):
        return {
            'id': self.id,
            'taskLogId': self.task_log_id,
            'status': self.status,
            'reviewer': self.reviewer,
            'note': self.note,
            'requestedAt': self.requested_at.isoformat() if self.requested_at else None,
            'reviewedAt': self.reviewed_at.isoformat() if self.reviewed_at else None,
        }
