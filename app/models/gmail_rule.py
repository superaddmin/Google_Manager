"""Gmail 自动化规则模型。"""
import json
from datetime import datetime, timezone

from app import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GmailRule(db.Model):
    """匹配 Gmail 查询并执行标签/状态操作的规则。"""

    __tablename__ = 'gmail_rules'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('gmail_connections.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False)
    search_query = db.Column('query', db.String(500), nullable=False, default='')
    actions = db.Column(db.Text, nullable=False, default='{}')
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    requires_confirmation = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    connection = db.relationship(
        'GmailConnection',
        backref=db.backref('rules', lazy='dynamic', cascade='all, delete-orphan'),
    )

    def action_config(self):
        try:
            value = json.loads(self.actions or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def to_dict(self):
        return {
            'id': self.id,
            'connectionId': self.connection_id,
            'name': self.name,
            'query': self.search_query,
            'actions': self.action_config(),
            'enabled': self.enabled,
            'priority': self.priority,
            'requiresConfirmation': self.requires_confirmation,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
        }
