"""持久化管理员登录会话。"""
import time

from app import db


class AdminSession(db.Model):
    __tablename__ = 'admin_sessions'

    token_hash = db.Column(db.String(64), primary_key=True)
    credential_version = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.Float, nullable=False, default=time.time)
    expires_at = db.Column(db.Float, nullable=False, index=True)
    revoked_at = db.Column(db.Float, nullable=True, index=True)
