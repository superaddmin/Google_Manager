import time
import uuid

from sqlalchemy.exc import IntegrityError

from app import db


class RechargeBillingMutation(db.Model):
    __tablename__ = 'recharge_billing_mutations'

    credential_hash = db.Column(db.String(64), primary_key=True)
    action = db.Column(db.String(16), nullable=False)
    operation_id = db.Column(db.String(32), nullable=False)
    state = db.Column(db.String(16), nullable=False)
    started_at = db.Column(db.Float, nullable=False)
    lease_until = db.Column(db.Float, nullable=True)
    lease_token = db.Column(db.String(32), nullable=True)
    updated_at = db.Column(db.Float, nullable=False)
    confirmed_auto_renew = db.Column(db.Boolean, nullable=True)
    result_status = db.Column(db.String(128), nullable=True)

    LEASE_SECONDS = 30
    ACTION_TARGETS = {'cancel': False, 'resume': True}

    @classmethod
    def claim(cls, credential_hash, action):
        if action not in cls.ACTION_TARGETS:
            raise ValueError('unsupported billing mutation action')

        for _ in range(4):
            now = time.time()
            cls.query.filter(
                cls.credential_hash == credential_hash,
                cls.state == 'pending',
                cls.lease_until <= now,
            ).update({
                'state': 'unknown',
                'lease_until': None,
                'lease_token': None,
                'updated_at': now,
            }, synchronize_session=False)
            db.session.commit()
            db.session.expire_all()

            record = db.session.get(cls, credential_hash)
            if record is None:
                operation_id = uuid.uuid4().hex
                lease_token = uuid.uuid4().hex
                try:
                    db.session.add(cls(
                        credential_hash=credential_hash,
                        action=action,
                        operation_id=operation_id,
                        state='pending',
                        started_at=now,
                        lease_until=now + cls.LEASE_SECONDS,
                        lease_token=lease_token,
                        updated_at=now,
                    ))
                    db.session.commit()
                    return {
                        'status': 'acquired',
                        'operation_id': operation_id,
                        'lease_token': lease_token,
                    }
                except IntegrityError:
                    db.session.rollback()
                    continue

            if record.state == 'pending':
                return {'status': 'busy', 'operation_id': record.operation_id}

            if record.state == 'unknown':
                if record.action != action:
                    return {'status': 'blocked', 'operation_id': record.operation_id}
                lease_token = uuid.uuid4().hex
                updated = cls.query.filter_by(
                    credential_hash=credential_hash,
                    action=action,
                    operation_id=record.operation_id,
                    state='unknown',
                ).update({
                    'state': 'pending',
                    'started_at': now,
                    'lease_until': now + cls.LEASE_SECONDS,
                    'lease_token': lease_token,
                    'updated_at': now,
                }, synchronize_session=False)
                db.session.commit()
                if updated == 1:
                    return {
                        'status': 'acquired',
                        'operation_id': record.operation_id,
                        'lease_token': lease_token,
                    }
                continue

            if record.state == 'done':
                target = cls.ACTION_TARGETS[action]
                same_confirmed_intent = (
                    record.action == action
                    and record.confirmed_auto_renew is target
                )
                operation_id = record.operation_id if same_confirmed_intent else uuid.uuid4().hex
                lease_token = uuid.uuid4().hex
                confirmed_filter = (
                    cls.confirmed_auto_renew.is_(None)
                    if record.confirmed_auto_renew is None
                    else cls.confirmed_auto_renew == record.confirmed_auto_renew
                )
                updated = cls.query.filter(
                    cls.credential_hash == credential_hash,
                    cls.operation_id == record.operation_id,
                    cls.state == 'done',
                    confirmed_filter,
                ).update({
                    'action': action,
                    'operation_id': operation_id,
                    'state': 'pending',
                    'started_at': now,
                    'lease_until': now + cls.LEASE_SECONDS,
                    'lease_token': lease_token,
                    'updated_at': now,
                    'confirmed_auto_renew': None,
                    'result_status': None,
                }, synchronize_session=False)
                db.session.commit()
                if updated == 1:
                    return {
                        'status': 'acquired',
                        'operation_id': operation_id,
                        'lease_token': lease_token,
                    }
                continue

            return {'status': 'blocked', 'operation_id': record.operation_id}

        return {'status': 'busy', 'operation_id': None}

    @classmethod
    def mark_unknown(cls, credential_hash, operation_id, lease_token):
        now = time.time()
        updated = cls.query.filter_by(
            credential_hash=credential_hash,
            operation_id=operation_id,
            state='pending',
            lease_token=lease_token,
        ).update({
            'state': 'unknown',
            'lease_until': None,
            'lease_token': None,
            'updated_at': now,
        }, synchronize_session=False)
        db.session.commit()
        return updated == 1

    @classmethod
    def finish(cls, credential_hash, operation_id, action, auto_renew, status):
        now = time.time()
        updated = cls.query.filter(
            cls.credential_hash == credential_hash,
            cls.operation_id == operation_id,
            cls.action == action,
            cls.state.in_(('pending', 'unknown')),
        ).update({
            'state': 'done',
            'lease_until': None,
            'lease_token': None,
            'updated_at': now,
            'confirmed_auto_renew': auto_renew,
            'result_status': status,
        }, synchronize_session=False)
        db.session.commit()
        if updated == 1:
            return True
        db.session.expire_all()
        record = db.session.get(cls, credential_hash)
        return bool(
            record is not None
            and record.operation_id == operation_id
            and record.action == action
            and record.state == 'done'
            and record.confirmed_auto_renew is auto_renew
        )

    @classmethod
    def snapshot(cls, credential_hash):
        db.session.expire_all()
        record = db.session.get(cls, credential_hash)
        if record is None:
            return None
        return {
            'operation_id': record.operation_id,
            'action': record.action,
            'state': record.state,
            'lease_until': record.lease_until,
            'lease_token': record.lease_token,
            'updated_at': record.updated_at,
        }

    @classmethod
    def reconcile_query(cls, credential_hash, snapshot, auto_renew, status):
        if snapshot is None:
            return False

        now = time.time()
        state = snapshot['state']
        action = snapshot['action']
        operation_id = snapshot['operation_id']
        updated_at = snapshot['updated_at']

        if state == 'pending':
            lease_until = snapshot['lease_until']
            if lease_until is None or lease_until > now:
                return False
            lease_token = snapshot['lease_token']
            lease_token_filter = (
                cls.lease_token.is_(None)
                if lease_token is None
                else cls.lease_token == lease_token
            )
            target = cls.ACTION_TARGETS[action]
            target_confirmed = auto_renew is target
            values = {
                'state': 'done' if target_confirmed else 'unknown',
                'lease_until': None,
                'lease_token': None,
                'updated_at': now,
            }
            if target_confirmed:
                values.update({
                    'confirmed_auto_renew': auto_renew,
                    'result_status': status,
                })
            updated = cls.query.filter(
                cls.credential_hash == credential_hash,
                cls.operation_id == operation_id,
                cls.action == action,
                cls.state == 'pending',
                cls.updated_at == updated_at,
                cls.lease_until == lease_until,
                cls.lease_until <= now,
                lease_token_filter,
            ).update(values, synchronize_session=False)
            db.session.commit()
            return updated == 1

        if state == 'unknown':
            target = cls.ACTION_TARGETS[action]
            if auto_renew is not target:
                return False
            updated = cls.query.filter_by(
                credential_hash=credential_hash,
                operation_id=operation_id,
                action=action,
                state='unknown',
                updated_at=updated_at,
            ).update({
                'state': 'done',
                'lease_until': None,
                'lease_token': None,
                'updated_at': now,
                'confirmed_auto_renew': auto_renew,
                'result_status': status,
            }, synchronize_session=False)
            db.session.commit()
            return updated == 1

        if state == 'done':
            updated = cls.query.filter_by(
                credential_hash=credential_hash,
                operation_id=operation_id,
                state='done',
                updated_at=updated_at,
            ).update({
                'confirmed_auto_renew': auto_renew,
                'result_status': status,
                'updated_at': now,
            }, synchronize_session=False)
            db.session.commit()
            return updated == 1

        return False
