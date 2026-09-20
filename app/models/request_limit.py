import time

from sqlalchemy.exc import IntegrityError

from app import db


class RequestLimit(db.Model):
    __tablename__ = 'request_limits'

    key = db.Column(db.String(128), primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.Float, nullable=False, index=True)

    @classmethod
    def consume(cls, key, limit, window):
        now = time.time()
        bucket = f'{key}:{int(now // window)}'
        cls.query.filter(cls.expires_at <= now).delete(synchronize_session=False)
        try:
            with db.session.begin_nested():
                db.session.add(cls(key=bucket, count=0, expires_at=(int(now // window) + 1) * window))
                db.session.flush()
        except IntegrityError:
            pass
        updated = cls.query.filter(cls.key == bucket, cls.count < limit).update(
            {'count': cls.count + 1}, synchronize_session=False,
        )
        db.session.commit()
        return updated == 1


class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'

    ip = db.Column(db.String(64), primary_key=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_attempt = db.Column(db.Float, nullable=False, default=0)
    banned_until = db.Column(db.Float, nullable=False, default=0)
