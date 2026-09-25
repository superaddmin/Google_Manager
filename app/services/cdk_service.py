import json
import re
import time

from flask import current_app
from sqlalchemy import text

from app import db
from app.models.cdk import CdkAudit, CdkBatch, CdkBenefit, CdkCard, CdkChannel, CdkFingerprint, CdkRequest, new_id
from app.services.cdk_crypto import CdkError, decrypt, digest, encrypt, generate_code, keyring, normalize_code
from app.services.cdk_identity import customer_session, private_digest, require_staff


def ensure_enabled():
    if not current_app.config.get('CDK_ENABLED'):
        raise CdkError('CDK_DISABLED', '平台卡密功能尚未启用', 503)
    keyring('ENCRYPTION')
    keyring('LOOKUP')


def begin_write():
    db.session.rollback()
    if db.engine.dialect.name != 'sqlite':
        raise CdkError('DATABASE_UNSUPPORTED', '当前 CDK 版本仅支持已验收的 SQLite 部署', 503)
    db.session.execute(text('BEGIN IMMEDIATE'))


def fingerprint(payload):
    return private_digest('cdk-request', json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':')))


def run_command(principal, operation, request_key, payload, function, authorize=None, return_created=False):
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_key):
        raise CdkError('IDEMPOTENCY_REQUIRED', '请提供 16-80 位 Idempotency-Key', 428)
    request_fingerprint = fingerprint(payload)
    begin_write()
    try:
        if authorize:
            authorize()
        previous = db.session.get(CdkRequest, (principal, operation, request_key))
        if previous:
            if previous.fingerprint != request_fingerprint:
                raise CdkError('IDEMPOTENCY_CONFLICT', '相同请求键不能用于不同内容', 409)
            result = previous.result
        else:
            result = function()
            db.session.add(CdkRequest(principal=principal, operation=operation, request_key=request_key,
                                     fingerprint=request_fingerprint, result=result))
        db.session.commit()
        return (result, previous is None) if return_created else result
    except Exception:
        db.session.rollback()
        raise


def audit(actor, action, target, channel_id=None, **details):
    db.session.add(CdkAudit(actor=actor, action=action, target_id=target, channel_id=channel_id, details=details))


def find_resource(kind, value):
    for key_id in keyring('LOOKUP'):
        record = db.session.get(CdkFingerprint, (kind, key_id, digest(kind, value, key_id)))
        if record:
            return record.resource_id
    return None


def index_resource(kind, value, resource_id):
    if find_resource(kind, value):
        raise CdkError('DUPLICATE_CODE', '此卡密已经登记', 409)
    for key_id in keyring('LOOKUP'):
        db.session.add(CdkFingerprint(kind=kind, key_id=key_id, digest=digest(kind, value, key_id), resource_id=resource_id))


def text_value(payload, field, maximum=128):
    value = payload.get(field)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or any(ord(character) < 32 for character in value):
        raise CdkError('INVALID_INPUT', f'{field} 格式无效')
    return value.strip()


def integer(payload, field, minimum, maximum):
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CdkError('INVALID_INPUT', f'{field} 必须是 {minimum}-{maximum} 的整数')
    return value


def timestamp(payload, field):
    from datetime import datetime
    value = payload.get(field)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError) as error:
        raise CdkError('INVALID_TIME', f'{field} 必须为带时区的 ISO 时间') from error


def boolean(payload, field, default=False):
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise CdkError('INVALID_INPUT', f'{field} 必须为布尔值')
    return value


def version_check(entity, value):
    if isinstance(value, bool) or not isinstance(value, int) or entity.version != value:
        raise CdkError('VERSION_CONFLICT', '对象已变化，请刷新后重试', 409)


def get_batch(identifier, capability='read'):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise CdkError('NOT_FOUND', '批次不存在', 404)
    batch = db.session.get(CdkBatch, identifier)
    if not batch:
        raise CdkError('NOT_FOUND', '批次不存在', 404)
    require_staff(capability, batch.channel_id)
    return batch


def get_card(identifier, capability='read'):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise CdkError('NOT_FOUND', '卡密不存在', 404)
    card = db.session.get(CdkCard, identifier)
    if not card:
        raise CdkError('NOT_FOUND', '卡密不存在', 404)
    get_batch(card.batch_id, capability)
    return card


def benefit_public(benefit):
    return {name: getattr(benefit, name) for name in ('id', 'product_code', 'revision', 'name', 'plan_type',
            'renewal_allowed', 'reference_amount_minor', 'currency')}


def batch_public(batch):
    return {name: getattr(batch, name) for name in ('id', 'name', 'benefit_id', 'channel_id', 'maker_id', 'quantity',
            'state', 'not_before', 'expires_at', 'customer_required', 'is_mock', 'version', 'created_at')}


def card_public(card):
    batch = db.session.get(CdkBatch, card.batch_id)
    now = time.time()
    return {**{name: getattr(card, name) for name in ('id', 'batch_id', 'control', 'usage', 'distribution', 'expires_at',
            'replacement_of', 'version')}, 'masked_code': 'GM1-••••-' + card.last4,
            'not_before': batch.not_before, 'expired': card.expires_at <= now, 'not_started': batch.not_before > now,
            'benefit': benefit_public(db.session.get(CdkBenefit, batch.benefit_id)), 'is_mock': batch.is_mock}


def card_for_code(value):
    code = normalize_code(value)
    identifier = find_resource('card', code)
    card = db.session.get(CdkCard, identifier) if identifier else None
    if not card:
        raise CdkError('INVALID_CODE', '卡密不存在或无法使用', 404)
    customer, _ = customer_session()
    batch = db.session.get(CdkBatch, card.batch_id)
    if (card.owner_id and (not customer or customer.id != card.owner_id)):
        raise CdkError('USER_RESTRICTED', '此卡密已绑定其他客户', 403)
    if batch.customer_required and not customer:
        raise CdkError('CUSTOMER_REQUIRED', '此批次需先完成邮箱登录', 401)
    from app.models.cdk import CdkDistribution
    distribution = CdkDistribution.query.filter_by(card_id=card.id).first()
    if distribution and distribution.audience_digest and not card.owner_id:
        raise CdkError('CLAIM_REQUIRED', '此卡密必须先由指定客户凭票据领取', 403)
    return card


def check_redeemable(card):
    from app.models.cdk import CdkStock
    from app.services.recharge_service import RechargeService
    batch = db.session.get(CdkBatch, card.batch_id)
    channel = db.session.get(CdkChannel, batch.channel_id)
    now = time.time()
    if card.control == 'void' or batch.state == 'closed':
        raise CdkError('CODE_VOID', '此卡密已作废', 409)
    if card.control != 'active' or batch.state != 'active':
        raise CdkError('CODE_FROZEN', '此卡密尚未激活或已冻结', 409)
    if not channel.enabled:
        raise CdkError('CHANNEL_RESTRICTED', '卡密所属渠道已停用', 403)
    if batch.not_before > now:
        raise CdkError('NOT_YET_VALID', '此卡密尚未生效', 409)
    if card.expires_at <= now:
        raise CdkError('CODE_EXPIRED', '此卡密已过期', 409)
    if card.usage != 'unused':
        raise CdkError('ALREADY_USED' if card.usage == 'redeemed' else 'REDEMPTION_IN_PROGRESS', '此卡密已使用或正在处理中', 409)
    if batch.is_mock != (RechargeService.get_mode() == 'mock'):
        raise CdkError('MODE_MISMATCH', '卡密所属环境与当前履约模式不一致', 409)
    stock = CdkStock.query.filter_by(card_id=card.id).first()
    if not stock or stock.state != 'allocated' or stock.valid_until <= now:
        raise CdkError('STOCK_UNAVAILABLE', '履约库存不可用，请联系管理员', 409)
    return batch, stock


def issue_card(batch, replacement_of=None):
    code = generate_code()
    card = CdkCard(id=new_id(), batch_id=batch.id, ciphertext=encrypt(code), last4=code[-4:],
                   expires_at=batch.expires_at, replacement_of=replacement_of)
    index_resource('card', code, card.id)
    db.session.add(card)
    db.session.flush()
    return card


def guard_legacy_code(value):
    from app.services.recharge_service import RechargeContractError
    if not isinstance(value, str):
        return
    code = value.strip()
    if code.upper().startswith('GM1'):
        raise RechargeContractError('平台卡密请使用平台卡密兑换入口')
    from app.models.cdk import CdkRedemption
    if code.startswith('TK-') and CdkRedemption.query.filter_by(task_no=code).first():
        raise RechargeContractError('平台卡密任务请使用本人核销记录入口')
    if CdkFingerprint.query.filter_by(kind='stock').first() and find_resource('stock', code):
        raise RechargeContractError('该卡密已由平台托管，请使用平台兑换入口')
