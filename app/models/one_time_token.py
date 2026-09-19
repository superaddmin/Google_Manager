import hashlib
import time

from app import db


class OneTimeToken(db.Model):
    __tablename__ = 'one_time_tokens'

    namespace = db.Column(db.String(40), primary_key=True)
    token_hash = db.Column(db.String(64), primary_key=True)
    binding = db.Column(db.String(64), nullable=True)
    payload = db.Column(db.JSON, nullable=False)
    expires_at = db.Column(db.Float, nullable=False, index=True)
    consumed_at = db.Column(db.Float, nullable=True)

    @staticmethod
    def digest(token):
        return hashlib.sha256(str(token).encode('utf-8')).hexdigest()

    @classmethod
    def register(cls, namespace, token, payload, ttl, binding=None):
        now = time.time()
        cls.query.filter(cls.namespace == namespace, cls.expires_at <= now).delete(synchronize_session=False)
        db.session.add(cls(
            namespace=namespace,
            token_hash=cls.digest(token),
            payload=payload,
            binding=binding,
            expires_at=now + ttl,
        ))
        db.session.commit()

    @classmethod
    def lookup(cls, namespace, token):
        if not token:
            return None
        return cls.query.filter_by(namespace=namespace, token_hash=cls.digest(token)).first()

    @classmethod
    def consume(cls, namespace, token, binding=None):
        if not token:
            return None
        now = time.time()
        query = cls.query.filter_by(namespace=namespace, token_hash=cls.digest(token), consumed_at=None)
        query = query.filter(cls.expires_at > now)
        if binding is not None:
            query = query.filter_by(binding=binding)
        record = query.first()
        if record is None:
            return None
        payload = dict(record.payload)
        updated = query.update({'consumed_at': now}, synchronize_session=False)
        db.session.commit()
        return payload if updated == 1 else None

    @classmethod
    def clear(cls, namespace):
        cls.query.filter_by(namespace=namespace).delete(synchronize_session=False)
        db.session.commit()
