import time

from flask import current_app
from sqlalchemy import text

from app import db
from app.models.cdk import (CdkAudit, CdkBatch, CdkCard, CdkCustomer, CdkDistribution, CdkFingerprint,
                            CdkJob, CdkOutbox, CdkRedemption, CdkStock)
from app.services.cdk_crypto import CdkError, decrypt, digest, encrypt, keyring, normalize_code
from app.services.cdk_service import audit, begin_write


def validate_cdk(limit=None):
    if db.session.execute(text('PRAGMA foreign_key_check')).first():
        raise CdkError('DATA_INCONSISTENT', '数据库存在外键不一致', 503)
    counts = {'cards': 0, 'stocks': 0, 'redemptions': 0}
    for kind, model in (('card', CdkCard), ('stock', CdkStock)):
        query = model.query.order_by(model.id)
        if limit:
            query = query.limit(limit)
        for record in query.yield_per(100):
            raw = decrypt(record.ciphertext)
            if kind == 'card':
                normalize_code(raw)
            for key_id in keyring('LOOKUP'):
                index = db.session.get(CdkFingerprint, (kind, key_id, digest(kind, raw, key_id)))
                if not index or index.resource_id != record.id:
                    raise CdkError('INDEX_INCOMPLETE', '卡密查询索引不完整，禁止切换密钥版本', 503)
            counts['cards' if kind == 'card' else 'stocks'] += 1
    query = CdkRedemption.query.order_by(CdkRedemption.id)
    if limit:
        query = query.limit(limit)
    for record in query.yield_per(100):
        card = db.session.get(CdkCard, record.card_id)
        stock = db.session.get(CdkStock, record.stock_id)
        if record.state in {'released', 'cancelled'}:
            if record.active_card_key or record.active_stock_key:
                raise CdkError('DATA_INCONSISTENT', '终结意图仍占用资产', 503)
        elif record.state == 'succeeded':
            if card.successful_redemption_id != record.id or card.usage != 'redeemed' or stock.state != 'consumed' or stock.card_id != card.id:
                raise CdkError('DATA_INCONSISTENT', '成功意图与权益消费状态不一致', 503)
        elif card.active_redemption_id != record.id or card.usage != 'reserved' or stock.state != 'reserved' or stock.card_id != card.id:
            raise CdkError('DATA_INCONSISTENT', '在途意图与库存占用不一致', 503)
        counts['redemptions'] += 1
    for model, field in ((CdkCustomer, 'email_ciphertext'), (CdkDistribution, 'ticket_ciphertext'), (CdkJob, 'ciphertext')):
        query = model.query.filter(getattr(model, field).isnot(None)).order_by(model.id)
        if limit:
            query = query.limit(limit)
        for record in query.yield_per(100):
            decrypt(getattr(record, field))
    return counts


def rotate_keys(apply=False):
    keyring('ENCRYPTION')
    versions = list(keyring('LOOKUP'))
    counts = {'values': 0, 'indexes': 0}
    if apply:
        begin_write()
    try:
        for kind, model in (('card', CdkCard), ('stock', CdkStock)):
            for record in model.query.order_by(model.id).yield_per(100):
                raw = decrypt(record.ciphertext)
                counts['values'] += 1
                for key_id in versions:
                    value = digest(kind, raw, key_id)
                    existing = db.session.get(CdkFingerprint, (kind, key_id, value))
                    if existing and existing.resource_id != record.id:
                        raise CdkError('DUPLICATE_CODE', '密钥轮换发现重复资产，已回滚', 409)
                    if not existing:
                        counts['indexes'] += 1
                        if apply:
                            db.session.add(CdkFingerprint(kind=kind, key_id=key_id, digest=value, resource_id=record.id))
                if apply:
                    record.ciphertext = encrypt(raw)
        for model, field in ((CdkCustomer, 'email_ciphertext'), (CdkDistribution, 'ticket_ciphertext'), (CdkJob, 'ciphertext')):
            for record in model.query.filter(getattr(model, field).isnot(None)).yield_per(100):
                value = decrypt(getattr(record, field))
                counts['values'] += 1
                if apply:
                    setattr(record, field, encrypt(value))
        if apply:
            audit('host-operator', 'keys.rotate', 'cdk', values=counts['values'], indexes=counts['indexes'])
            db.session.commit()
        else:
            db.session.rollback()
        return counts
    except Exception:
        db.session.rollback()
        raise


def observe_outbox():
    begin_write()
    events = CdkOutbox.query.filter_by(processed_at=None).order_by(CdkOutbox.created_at).limit(100).all()
    for event in events:
        event.processed_at = time.time()
    db.session.commit()
    return len(events)


def validate_cdk_ciphertexts(engine, max_rows=None):
    import json
    import os
    from cryptography.fernet import Fernet
    from flask import has_app_context
    from sqlalchemy import inspect
    keys = current_app.config.get('CDK_ENCRYPTION_KEYS', {}) if has_app_context() else os.environ.get('CDK_ENCRYPTION_KEYS', '{}')
    if isinstance(keys, str):
        keys = json.loads(keys or '{}')
    tables = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        for table, field in (('cdk_cards', 'ciphertext'), ('cdk_stocks', 'ciphertext'), ('cdk_customers', 'email_ciphertext'),
                             ('cdk_distributions', 'ticket_ciphertext'), ('cdk_jobs', 'ciphertext')):
            if table not in tables:
                continue
            statement = f'SELECT {field} FROM {table} WHERE {field} IS NOT NULL ORDER BY id'
            if max_rows is not None:
                statement += ' LIMIT :maximum'
            for row in connection.execute(text(statement), {'maximum': max_rows}):
                try:
                    key_id, payload = row[0].split(':', 1)
                    Fernet(keys[key_id].encode()).decrypt(payload.encode())
                except Exception as error:
                    raise RuntimeError('CDK 业务密文无法使用配置的独立密钥读取') from error
