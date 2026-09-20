"""管理员认证与数据库共享登录封禁。"""
import hashlib
import hmac
import secrets
import time

from flask import current_app, session
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.admin_session import AdminSession
from app.models.request_limit import LoginAttempt

MAX_FAILED_ATTEMPTS = 3
BAN_DURATION = 24 * 60 * 60
SALT_VALID_RANGE = 10
MAX_PASSWORD_BYTES = 4096
ADMIN_SESSION_TOKEN_KEY = 'admin_session_token'


def _utf8_bytes(value):
    if not isinstance(value, str):
        return None
    try:
        return value.encode('utf-8')
    except UnicodeEncodeError:
        return None


class AuthService:
    @staticmethod
    def is_ip_banned(ip):
        record = db.session.get(LoginAttempt, ip)
        remaining = max(0, int(record.banned_until - time.time())) if record else 0
        return remaining > 0, remaining

    @staticmethod
    def record_failed_attempt(ip):
        now = time.time()
        if db.session.get(LoginAttempt, ip) is None:
            try:
                with db.session.begin_nested():
                    db.session.add(LoginAttempt(ip=ip))
                    db.session.flush()
            except IntegrityError:
                pass
        attempts = case((LoginAttempt.last_attempt < now - 3600, 1), else_=LoginAttempt.attempts + 1)
        LoginAttempt.query.filter_by(ip=ip).update({
            'attempts': attempts,
            'last_attempt': now,
            'banned_until': case((attempts >= MAX_FAILED_ATTEMPTS, now + BAN_DURATION), else_=LoginAttempt.banned_until),
        }, synchronize_session=False)
        db.session.commit()
        record = db.session.get(LoginAttempt, ip, populate_existing=True)
        return record.banned_until > now, max(0, MAX_FAILED_ATTEMPTS - record.attempts)

    @staticmethod
    def clear_failed_attempts(ip):
        LoginAttempt.query.filter_by(ip=ip).update({'attempts': 0, 'last_attempt': 0, 'banned_until': 0})
        db.session.commit()

    @staticmethod
    def verify_password(password, expected_password):
        password_bytes = _utf8_bytes(password)
        expected_bytes = _utf8_bytes(expected_password)
        return (password_bytes is not None and expected_bytes is not None
                and bool(expected_bytes) and hmac.compare_digest(password_bytes, expected_bytes))

    @staticmethod
    def is_valid_password_input(password):
        password_bytes = _utf8_bytes(password)
        return (password_bytes is not None and bool(password_bytes)
                and len(password_bytes) <= MAX_PASSWORD_BYTES)

    @staticmethod
    def _token_hash(token):
        token_bytes = _utf8_bytes(token)
        if token_bytes is None or len(token_bytes) > 128:
            return None
        return hashlib.sha256(token_bytes).hexdigest()

    @staticmethod
    def _credential_version(expected_password, secret_key):
        password_bytes = _utf8_bytes(expected_password)
        if isinstance(secret_key, str):
            secret_bytes = _utf8_bytes(secret_key)
        elif isinstance(secret_key, bytes):
            secret_bytes = secret_key
        else:
            secret_bytes = None
        if not password_bytes or not secret_bytes:
            return None
        return hmac.new(
            secret_bytes,
            b'google-manager-admin-password-v1\x00' + password_bytes,
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def create_admin_session(expected_password, secret_key, lifetime_seconds):
        credential_version = AuthService._credential_version(expected_password, secret_key)
        if credential_version is None or lifetime_seconds <= 0:
            raise ValueError('管理员会话配置无效')
        now = time.time()
        token = secrets.token_urlsafe(32)
        db.session.add(AdminSession(
            token_hash=AuthService._token_hash(token),
            credential_version=credential_version,
            created_at=now,
            expires_at=now + lifetime_seconds,
        ))
        db.session.commit()
        return token

    @staticmethod
    def validate_admin_session(token, expected_password, secret_key):
        token_hash = AuthService._token_hash(token)
        credential_version = AuthService._credential_version(expected_password, secret_key)
        if token_hash is None or credential_version is None:
            return False
        record = db.session.get(AdminSession, token_hash)
        now = time.time()
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            return False
        if not hmac.compare_digest(record.credential_version, credential_version):
            record.revoked_at = now
            db.session.commit()
            return False
        return True

    @staticmethod
    def revoke_admin_session(token):
        token_hash = AuthService._token_hash(token)
        if token_hash is None:
            return False
        now = time.time()
        updated = AdminSession.query.filter(
            AdminSession.token_hash == token_hash,
            AdminSession.revoked_at.is_(None),
        ).update({'revoked_at': now}, synchronize_session=False)
        db.session.commit()
        return updated == 1

    @staticmethod
    def generate_salt(timestamp):
        return hashlib.md5(str(timestamp - 2003).encode()).hexdigest()

    @staticmethod
    def verify_salt(salt):
        now = int(time.time())
        return any(AuthService.generate_salt(now + offset) == salt
                   for offset in range(-SALT_VALID_RANGE, SALT_VALID_RANGE + 1))

    @staticmethod
    def get_ban_info():
        now = time.time()
        return [{'ip': record.ip, 'banned_until': record.banned_until,
                 'remaining': int(record.banned_until - now)}
                for record in LoginAttempt.query.filter(LoginAttempt.banned_until > now).all()]


def is_admin_authenticated():
    """校验当前 Cookie 对应的服务端管理员会话。"""
    if session.get('authenticated') is not True:
        return False
    token = session.get(ADMIN_SESSION_TOKEN_KEY)
    valid = AuthService.validate_admin_session(
        token,
        current_app.config.get('ADMIN_PASSWORD'),
        current_app.secret_key,
    )
    if not valid:
        session.pop('authenticated', None)
        session.pop(ADMIN_SESSION_TOKEN_KEY, None)
    return valid


def get_admin_session_actor_id():
    """返回可审计但不可用于重放登录的管理员会话标识。"""
    if not is_admin_authenticated():
        return None
    token_bytes = _utf8_bytes(session.get(ADMIN_SESSION_TOKEN_KEY))
    secret_key = current_app.secret_key
    secret_bytes = _utf8_bytes(secret_key) if isinstance(secret_key, str) else secret_key
    if token_bytes is None or not isinstance(secret_bytes, bytes) or not secret_bytes:
        return None
    digest = hmac.new(
        secret_bytes,
        b'google-manager-admin-actor-v1\x00' + token_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f'admin:{digest[:24]}'
