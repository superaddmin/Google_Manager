"""Gmail OAuth connection model."""
from datetime import datetime, timezone

from app import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GmailConnection(db.Model):
    """授权后的 Gmail 账号连接。"""
    __tablename__ = 'gmail_connections'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    token_data = db.Column(db.Text, nullable=False)
    scopes = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'scopes': self.scopes.split(' ') if self.scopes else [],
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
        }