"""Gmail Pub/Sub watch 状态模型。"""
from datetime import datetime, timezone

from app import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GmailWatch(db.Model):
    """记录 Gmail watch 返回的 historyId 和过期时间。"""

    __tablename__ = 'gmail_watches'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    connection_id = db.Column(
        db.Integer,
        db.ForeignKey('gmail_connections.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
        index=True,
    )
    topic_name = db.Column(db.String(500), nullable=False)
    history_id = db.Column(db.String(100), nullable=True)
    expiration_at = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    connection = db.relationship(
        'GmailConnection',
        backref=db.backref('watch', uselist=False, cascade='all, delete-orphan'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'connectionId': self.connection_id,
            'topicName': self.topic_name,
            'historyId': self.history_id,
            'expirationAt': self.expiration_at.isoformat() if self.expiration_at else None,
            'active': self.active,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
        }
