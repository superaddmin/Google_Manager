import csv
import io
import json
import re
import secrets
import time

from sqlalchemy import func

from app import db
from app.models.cdk import (CdkApproval, CdkAudit, CdkBatch, CdkBenefit, CdkCard, CdkChannel,
                            CdkDistribution, CdkJob, CdkRedemption, CdkStaff, CdkStock)
from app.models.recharge_task import RechargeTask
from app.services.cdk_crypto import CdkError, decrypt, encrypt
from app.services.cdk_identity import (email_value, otp_enabled, private_digest, require_staff, token_hash)
from app.services.cdk_service import (audit, batch_public, benefit_public, boolean, card_public, find_resource,
                                     get_batch, get_card, index_resource, integer, issue_card, text_value,
                                     timestamp, version_check)
from app.services.recharge_service import RechargeService


MAX_BATCH = 100
APPROVAL_ACTIONS = {'activate', 'unfreeze_batch', 'close_batch', 'unfreeze_card', 'void', 'extend', 'reissue', 'export', 'release', 'resolve_mutation'}


def create_channel(payload):
    actor = require_staff('catalog', '*')
    code = text_value(payload, 'code', 64)
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', code):
        raise CdkError('INVALID_INPUT', '渠道编号只支持字母、数字、下划线和连字符')
    if CdkChannel.query.filter_by(code=code).first():
        raise CdkError('DUPLICATE_CHANNEL', '渠道编号已存在', 409)
    channel = CdkChannel(code=code, name=text_value(payload, 'name'))
    db.session.add(channel)
    db.session.flush()
    audit(actor.id, 'channel.create', channel.id, channel.id)
    return {'id': channel.id}


def create_benefit(payload):
    actor = require_staff('catalog', '*')
    product_code = text_value(payload, 'product_code', 64)
    revision = integer(payload, 'revision', 1, 100000)
    plan = text_value(payload, 'plan_type', 64)
    if plan not in {'PLUS', 'PRO'}:
        raise CdkError('UNSUPPORTED_BENEFIT', '首期平台券支持可验证 Session 邮箱的 PLUS / PRO 权益')
    if CdkBenefit.query.filter_by(product_code=product_code, revision=revision).first():
        raise CdkError('DUPLICATE_BENEFIT', '权益版本已存在，请使用新的修订号', 409)
    currency = text_value(payload, 'currency', 3)
    if not re.fullmatch('[A-Z]{3}', currency):
        raise CdkError('INVALID_CURRENCY', '币种须为三位大写字母')
    benefit = CdkBenefit(product_code=product_code, revision=revision, name=text_value(payload, 'name'), plan_type=plan,
                         renewal_allowed=boolean(payload, 'renewal_allowed'), currency=currency,
                         reference_amount_minor=integer(payload, 'reference_amount_minor', 0, 100000000))
    db.session.add(benefit)
    db.session.flush()
    audit(actor.id, 'benefit.create', benefit.id)
    return {'id': benefit.id}


def preview_import(payload):
    actor = require_staff('stock', '*')
    benefit_id = text_value(payload, 'benefit_id', 32)
    if not db.session.get(CdkBenefit, benefit_id):
        raise CdkError('NOT_FOUND', '权益版本不存在', 404)
    content = payload.get('content')
    if not isinstance(content, str) or len(content.encode()) > 128 * 1024:
        raise CdkError('IMPORT_TOO_LARGE', '导入文件须小于 128 KiB')
    valid_until = timestamp(payload, 'valid_until')
    if valid_until <= time.time():
        raise CdkError('INVALID_TIME', '库存有效期必须晚于当前时间')
    purchase_ref = text_value(payload, 'purchase_ref')
    lines = content.splitlines()
    if not 1 <= len(lines) <= MAX_BATCH:
        raise CdkError('IMPORT_TOO_LARGE', f'单次须导入 1-{MAX_BATCH} 行，每行一份供应商原码')
    rows, accepted, seen = [], [], set()
    for number, line in enumerate(lines, 1):
        code = line.strip()
        reason = None
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,120}', code) or code.upper().startswith('GM1'):
            reason = 'INVALID_PROVIDER_CODE'
        elif code in seen or find_resource('stock', code):
            reason = 'DUPLICATE_CODE'
        elif RechargeTask.query.filter_by(redeem_code=code).first():
            reason = 'HISTORICAL_TASK'
        seen.add(code)
        rows.append({'row': number, 'last4': code[-4:], 'error_code': reason})
        if reason is None:
            accepted.append(code)
    job = CdkJob(kind='import', actor_id=actor.id, ciphertext=encrypt(json.dumps({
        'codes': accepted, 'benefit_id': benefit_id, 'valid_until': valid_until, 'purchase_ref': purchase_ref,
        'is_mock': RechargeService.get_mode() == 'mock',
    })), result={'rows': rows, 'valid_count': len(accepted), 'error_count': len(rows) - len(accepted)}, expires_at=time.time() + 1800)
    db.session.add(job)
    db.session.flush()
    audit(actor.id, 'import.preview', job.id, valid_count=len(accepted), error_count=len(rows) - len(accepted))
    return {'id': job.id, **job.result}


def commit_import(identifier, payload):
    actor = require_staff('stock', '*')
    job = db.session.get(CdkJob, identifier)
    if not job or job.kind != 'import' or job.actor_id != actor.id:
        raise CdkError('NOT_FOUND', '导入预览不存在', 404)
    version_check(job, payload.get('version'))
    if job.state != 'prepared' or job.expires_at <= time.time():
        raise CdkError('IMPORT_EXPIRED', '导入预览已过期或已提交', 409)
    if payload.get('accept_valid_rows') is not True:
        raise CdkError('CONFIRMATION_REQUIRED', '请明确确认仅导入预览中的有效行')
    raw = json.loads(decrypt(job.ciphertext))
    if raw['valid_until'] <= time.time() or raw['is_mock'] != (RechargeService.get_mode() == 'mock'):
        raise CdkError('IMPORT_EXPIRED', '库存有效期或运行环境已变化，请重新预览', 409)
    identifiers = []
    for code in raw['codes']:
        if find_resource('stock', code) or RechargeTask.query.filter_by(redeem_code=code).first():
            raise CdkError('IMPORT_CONFLICT', '预览后部分卡密已被登记或使用，请重新预览', 409)
        stock = CdkStock(benefit_id=raw['benefit_id'], ciphertext=encrypt(code), last4=code[-4:],
                         valid_until=raw['valid_until'], purchase_ref=raw['purchase_ref'], is_mock=raw['is_mock'])
        db.session.add(stock)
        db.session.flush()
        index_resource('stock', code, stock.id)
        identifiers.append(stock.id)
        audit(actor.id, 'stock.import', stock.id)
    job.state, job.ciphertext, job.version = 'completed', None, job.version + 1
    job.result = {**job.result, 'stock_ids': identifiers}
    audit(actor.id, 'import.commit', job.id, count=len(identifiers))
    return {'id': job.id, 'stock_ids': identifiers}


def verify_stock(identifier, payload):
    from app.services.cdk_service import begin_write
    actor = require_staff('stock', '*')
    stock = db.session.get(CdkStock, identifier)
    if not stock:
        raise CdkError('NOT_FOUND', '库存不存在', 404)
    version_check(stock, payload.get('version'))
    if stock.state not in {'quarantine', 'available'}:
        raise CdkError('STOCK_LOCKED', '已分配库存不能重新验证', 409)
    if stock.is_mock != (RechargeService.ensure_enabled() == 'mock'):
        raise CdkError('MODE_MISMATCH', '库存环境与履约模式不匹配', 409)
    expected_plan = db.session.get(CdkBenefit, stock.benefit_id).plan_type
    code = decrypt(stock.ciphertext)
    snapshot = stock.version
    db.session.rollback()
    result = RechargeService._validate_provider_code(code)
    begin_write()
    actor = require_staff('stock', '*')
    stock = db.session.get(CdkStock, identifier)
    version_check(stock, snapshot)
    if stock.state not in {'quarantine', 'available'} or stock.valid_until <= time.time():
        raise CdkError('STOCK_LOCKED', '库存状态或有效期已变化', 409)
    valid = result.get('plan_type') == expected_plan and result.get('status') in {'valid', 'unused'}
    historical = RechargeTask.query.filter_by(redeem_code=code).first()
    if historical:
        valid = False
    stock.state = 'available' if valid else 'unusable'
    stock.verification = 'valid' if valid else 'invalid_or_historical'
    stock.verified_at, stock.version = time.time(), stock.version + 1
    audit(actor.id, 'stock.verify', stock.id, state=stock.state)
    db.session.commit()
    return stock_public(stock)


def stock_public(stock):
    return {name: getattr(stock, name) for name in ('id', 'benefit_id', 'last4', 'state', 'card_id', 'valid_until',
            'verified_at', 'verification', 'purchase_ref', 'is_mock', 'version')}


def create_batch(payload):
    channel_id = text_value(payload, 'channel_id', 32)
    actor = require_staff('issue', channel_id)
    channel = db.session.get(CdkChannel, channel_id)
    if not channel or not channel.enabled:
        raise CdkError('CHANNEL_RESTRICTED', '渠道不存在或已停用')
    benefit_id = text_value(payload, 'benefit_id', 32)
    if not db.session.get(CdkBenefit, benefit_id):
        raise CdkError('NOT_FOUND', '权益版本不存在', 404)
    start, end = timestamp(payload, 'not_before'), timestamp(payload, 'expires_at')
    if end <= max(start, time.time()) or end - start > 366 * 86400:
        raise CdkError('INVALID_TIME', '有效期须晚于当前时间且不超过 366 天')
    customer_required = boolean(payload, 'customer_required')
    if customer_required and not otp_enabled():
        raise CdkError('OTP_UNAVAILABLE', '配置邮箱登录后才能发行限定客户的卡密', 503)
    batch = CdkBatch(name=text_value(payload, 'name'), benefit_id=benefit_id, channel_id=channel_id,
                     maker_id=actor.id, quantity=integer(payload, 'quantity', 1, MAX_BATCH), not_before=start,
                     expires_at=end, customer_required=customer_required, is_mock=RechargeService.get_mode() == 'mock')
    db.session.add(batch)
    db.session.flush()
    audit(actor.id, 'batch.create', batch.id, channel_id)
    return batch_public(batch)


def generate_batch(identifier, payload):
    batch = get_batch(identifier, 'issue')
    actor = require_staff('issue', batch.channel_id)
    version_check(batch, payload.get('version'))
    if batch.state != 'draft' or CdkCard.query.filter_by(batch_id=batch.id).first():
        raise CdkError('BATCH_LOCKED', '仅允许对未生成卡密的草稿批次执行生成', 409)
    stocks = CdkStock.query.filter(CdkStock.benefit_id == batch.benefit_id, CdkStock.state == 'available',
                                  CdkStock.is_mock == batch.is_mock, CdkStock.valid_until >= batch.expires_at,
                                  CdkStock.verified_at >= time.time() - 86400).order_by(CdkStock.id).limit(batch.quantity).all()
    if len(stocks) != batch.quantity:
        raise CdkError('INSUFFICIENT_STOCK', '有效且最近 24 小时已验证的库存不足', 409)
    for stock in stocks:
        card = issue_card(batch)
        stock.card_id, stock.state, stock.version = card.id, 'allocated', stock.version + 1
        audit(actor.id, 'card.generate', card.id, batch.channel_id, stock_id=stock.id)
    batch.version += 1
    audit(actor.id, 'batch.generate', batch.id, batch.channel_id, count=batch.quantity)
    return batch_public(batch)


def freeze(kind, identifier, payload):
    target = get_batch(identifier, 'control') if kind == 'batch' else get_card(identifier, 'control')
    batch = target if kind == 'batch' else get_batch(target.batch_id, 'control')
    actor = require_staff('control', batch.channel_id)
    version_check(target, payload.get('version'))
    reason = text_value(payload, 'reason', 256)
    state = target.state if kind == 'batch' else target.control
    if state not in {'active', 'frozen'}:
        raise CdkError('STATE_CONFLICT', '只能冻结已激活资源', 409)
    if kind == 'batch':
        target.state = 'frozen'
    else:
        target.control = 'frozen'
    target.version += 1
    audit(actor.id, kind + '.freeze', identifier, batch.channel_id, reason=reason)
    return {'id': identifier, 'version': target.version}


def approval_target(action, identifier, capability):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise CdkError('NOT_FOUND', '审批目标不存在', 404)
    if action in {'activate', 'unfreeze_batch', 'close_batch', 'export'}:
        target = get_batch(identifier, capability)
        return target, target
    if action in {'release', 'resolve_mutation'}:
        target = db.session.get(CdkRedemption, identifier)
        if not target:
            raise CdkError('NOT_FOUND', '核销意图不存在', 404)
        card = get_card(target.card_id, capability)
    else:
        target = card = get_card(identifier, capability)
    return target, get_batch(card.batch_id, capability)


def request_approval(payload):
    action = text_value(payload, 'action', 32)
    if action not in APPROVAL_ACTIONS:
        raise CdkError('INVALID_ACTION', '不支持的审批操作')
    capability = {'export': 'export', 'release': 'reconcile', 'resolve_mutation': 'reconcile', 'activate': 'issue'}.get(action, 'control')
    target, batch = approval_target(action, text_value(payload, 'target_id', 32), capability)
    actor = require_staff(capability, batch.channel_id, recent=True)
    version_check(target, payload.get('version'))
    parameters = {}
    if action == 'extend':
        parameters['expires_at'] = timestamp(payload, 'expires_at')
    if action == 'export':
        card_ids = payload.get('card_ids')
        if (not isinstance(card_ids, list) or not 1 <= len(card_ids) <= MAX_BATCH
                or any(not isinstance(value, str) or len(value) != 32 for value in card_ids)
                or len(set(card_ids)) != len(card_ids)):
            raise CdkError('INVALID_SELECTION', '请选择 1-100 张不重复卡密')
        parameters['cards'] = []
        for identifier in sorted(card_ids):
            card = get_card(identifier, 'export')
            if card.batch_id != batch.id:
                raise CdkError('INVALID_SELECTION', '导出必须来自同一批次')
            parameters['cards'].append({'id': card.id, 'version': card.version})
    if action in {'release', 'resolve_mutation'}:
        evidence_hash = text_value(payload, 'evidence_sha256', 64)
        if not re.fullmatch('[0-9a-fA-F]{64}', evidence_hash):
            raise CdkError('INVALID_EVIDENCE', '证据摘要无效')
        parameters = {'evidence_sha256': evidence_hash.lower(), 'reference': text_value(payload, 'reference', 128),
                      'observed_at': timestamp(payload, 'observed_at'), 'decision': text_value(payload, 'decision', 32)}
        expected_decision = 'not_consumed' if action == 'release' else 'not_applied'
        if parameters['decision'] != expected_decision:
            raise CdkError('INVALID_EVIDENCE', '释放必须具有明确未消费证据')
        if action == 'resolve_mutation':
            parameters['mutation_id'] = text_value(payload, 'mutation_id', 64)
    approval = CdkApproval(maker_id=actor.id, channel_id=batch.channel_id, action=action, target_id=target.id,
                           target_version=target.version, parameters=parameters, reason=text_value(payload, 'reason', 256),
                           expires_at=time.time() + 1800)
    db.session.add(approval)
    db.session.flush()
    audit(actor.id, 'approval.request', approval.id, batch.channel_id, operation=action, resource_id=target.id)
    return {'id': approval.id, 'state': approval.state}


def decide_approval(identifier, payload):
    approval = db.session.get(CdkApproval, identifier)
    if not approval:
        raise CdkError('NOT_FOUND', '审批不存在', 404)
    checker = require_staff('approve', approval.channel_id, recent=True)
    version_check(approval, payload.get('version'))
    if approval.maker_id == checker.id:
        raise CdkError('SELF_APPROVAL', '申请人与复核人必须是不同员工', 403)
    if approval.state != 'pending' or approval.expires_at <= time.time():
        raise CdkError('APPROVAL_EXPIRED', '审批已处理或已过期', 409)
    maker = db.session.get(CdkStaff, approval.maker_id)
    capability = {'export': 'export', 'release': 'reconcile', 'resolve_mutation': 'reconcile', 'activate': 'issue'}.get(approval.action, 'control')
    from app.services.cdk_identity import CAPABILITIES
    if (not maker.enabled or capability not in CAPABILITIES.get(maker.role, set())
            or ('*' not in maker.scopes and approval.channel_id not in maker.scopes)):
        raise CdkError('APPROVAL_INVALID', '申请人员权限已发生变化', 409)
    approval.checker_id = checker.id
    if payload.get('approved') is not True:
        approval.state = 'rejected'
        approval.result = {'id': approval.target_id}
    else:
        target, batch = approval_target(approval.action, approval.target_id, 'approve')
        version_check(target, approval.target_version)
        approval.result = apply_approval(approval, target, batch, checker)
        approval.state = 'applied'
    approval.version += 1
    audit(checker.id, 'approval.' + approval.state, approval.id, approval.channel_id,
          operation=approval.action, resource_id=approval.target_id, maker=approval.maker_id)
    return {'id': approval.id, 'state': approval.state, 'result': approval.result}


def apply_approval(approval, target, batch, checker):
    action, now = approval.action, time.time()
    if action in {'activate', 'unfreeze_batch'}:
        expected = 'draft' if action == 'activate' else 'frozen'
        effective_end = batch.expires_at if action == 'activate' else db.session.query(func.max(CdkCard.expires_at)).filter_by(batch_id=batch.id).scalar()
        if batch.state != expected or not effective_end or effective_end <= now:
            raise CdkError('STATE_CONFLICT', '批次状态或有效期不允许激活', 409)
        if not db.session.get(CdkChannel, batch.channel_id).enabled:
            raise CdkError('CHANNEL_RESTRICTED', '渠道已停用', 409)
        cards = CdkCard.query.filter_by(batch_id=batch.id, replacement_of=None).all()
        if len(cards) != batch.quantity:
            raise CdkError('BATCH_INCOMPLETE', '批次尚未完成生成', 409)
        if action == 'activate':
            for card in cards:
                stock = CdkStock.query.filter_by(card_id=card.id, state='allocated').first()
                if not stock or stock.valid_until < card.expires_at or stock.verified_at < now - 86400:
                    raise CdkError('STOCK_UNAVAILABLE', '库存不足、已过期或校验已过时', 409)
                card.control, card.version = 'active', card.version + 1
        batch.state = 'active'
    elif action == 'close_batch':
        if batch.state not in {'draft', 'frozen'}:
            raise CdkError('STATE_CONFLICT', '仅可关闭草稿或已冻结批次', 409)
        if batch.state == 'draft':
            for card in CdkCard.query.filter_by(batch_id=batch.id):
                if card.control != 'draft' or card.usage != 'unused':
                    raise CdkError('STATE_CONFLICT', '草稿批次出现已发行卡密，禁止回收', 409)
                stock = CdkStock.query.filter_by(card_id=card.id).first()
                if stock:
                    stock.card_id, stock.state, stock.version = None, 'quarantine', stock.version + 1
                    audit(checker.id, 'stock.draft_release', stock.id, batch.channel_id, previous_card=card.id)
                card.control, card.version = 'void', card.version + 1
        batch.state = 'closed'
    elif action == 'export':
        if batch.state != 'active':
            raise CdkError('STATE_CONFLICT', '仅激活批次可以导出', 409)
        codes = []
        for selected in approval.parameters['cards']:
            card = db.session.get(CdkCard, selected['id'])
            version_check(card, selected['version'])
            if card.control != 'active' or card.usage != 'unused' or card.distribution != 'distributed' or card.owner_id or card.expires_at <= now:
                raise CdkError('EXPORT_RESTRICTED', '仅可导出已分发且尚未领取、未使用的有效卡密', 409)
            distribution = CdkDistribution.query.filter_by(card_id=card.id).one()
            if distribution.audience_digest:
                raise CdkError('EXPORT_RESTRICTED', '指定客户的卡密只能通过领取票据交付', 409)
            codes.append({'id': card.id, 'version': card.version, 'code': decrypt(card.ciphertext)})
        job = CdkJob(kind='export', actor_id=approval.maker_id, channel_id=batch.channel_id, state='completed',
                     ciphertext=encrypt(json.dumps(codes)), result={'count': len(codes), 'downloads': 0}, expires_at=now + 900)
        db.session.add(job)
        db.session.flush()
        return {'export_id': job.id}
    elif action in {'release', 'resolve_mutation'}:
        from app.services.cdk_redemption import release_with_evidence, resolve_mutation_with_evidence
        return (release_with_evidence if action == 'release' else resolve_mutation_with_evidence)(target, approval, checker.id)
    else:
        card = target
        stock = CdkStock.query.filter_by(card_id=card.id).first()
        if card.usage != 'unused' or card.control == 'void':
            raise CdkError('STATE_CONFLICT', '已占用、已使用或已作废卡密不能执行此操作', 409)
        if action == 'unfreeze_card':
            if card.control != 'frozen':
                raise CdkError('STATE_CONFLICT', '仅可解冻被冻结卡密', 409)
            card.control = 'active'
        elif action == 'void':
            card.control = 'void'
            if stock:
                stock.state, stock.version = 'unusable', stock.version + 1
        elif action == 'extend':
            until = approval.parameters['expires_at']
            if until <= max(card.expires_at, now) or until > batch.not_before + 366 * 86400 or not stock or until > stock.valid_until:
                raise CdkError('INVALID_TIME', '延期必须向后且不得超出供应商有效期或 366 天上限')
            card.expires_at = until
        elif action == 'reissue':
            if card.control != 'frozen' or not stock or stock.state != 'allocated' or card.expires_at <= now:
                raise CdkError('STATE_CONFLICT', '补发需要先冻结未使用且库存有效的卡密', 409)
            if CdkCard.query.filter_by(replacement_of=card.id).first():
                raise CdkError('REISSUE_CONFLICT', '此卡密已有补发记录', 409)
            replacement = issue_card(batch, card.id)
            replacement.control, replacement.expires_at = 'active', card.expires_at
            replacement.owner_id = card.owner_id
            replacement.distribution = 'claimed' if card.owner_id else 'unassigned'
            card.control = 'void'
            card.version += 1
            stock.card_id, stock.version = replacement.id, stock.version + 1
            audit(checker.id, 'stock.reassigned', stock.id, batch.channel_id, previous_card=card.id, next_card=replacement.id)
            return {'replacement_id': replacement.id}
    target.version += 1
    return {'id': target.id, 'version': target.version}


def distribute(payload):
    card = get_card(text_value(payload, 'card_id', 32), 'distribute')
    batch = get_batch(card.batch_id, 'distribute')
    actor = require_staff('distribute', batch.channel_id, recent=True)
    version_check(card, payload.get('version'))
    if card.control != 'active' or batch.state != 'active' or card.usage != 'unused' or card.distribution != 'unassigned' or card.expires_at <= time.time():
        raise CdkError('STATE_CONFLICT', '仅能分发已激活、未分发且未使用的卡密', 409)
    audience = payload.get('audience_email', '')
    if audience and not otp_enabled():
        raise CdkError('OTP_UNAVAILABLE', '需先配置邮箱登录才能指定客户', 503)
    token = secrets.token_urlsafe(32)
    distribution = CdkDistribution(card_id=card.id, recipient_ref=text_value(payload, 'recipient_ref'),
                                    ticket_hash=token_hash(token), ticket_ciphertext=encrypt(token),
                                    audience_digest=private_digest('customer-email', email_value(audience)) if audience else None,
                                    expires_at=min(card.expires_at, time.time() + 7 * 86400), actor_id=actor.id)
    db.session.add(distribution)
    db.session.flush()
    card.distribution, card.version = 'distributed', card.version + 1
    audit(actor.id, 'card.distribute', card.id, batch.channel_id, distribution_id=distribution.id)
    return {'id': distribution.id, 'card_id': card.id}


def reveal_ticket(identifier):
    from app.services.cdk_service import begin_write
    begin_write()
    distribution = db.session.get(CdkDistribution, identifier)
    if not distribution:
        raise CdkError('NOT_FOUND', '分发记录不存在', 404)
    card = get_card(distribution.card_id, 'distribute')
    batch = get_batch(card.batch_id, 'distribute')
    actor = require_staff('distribute', batch.channel_id, recent=True)
    if distribution.claimed_at or distribution.expires_at <= time.time() or card.control != 'active' or batch.state != 'active':
        raise CdkError('TICKET_UNAVAILABLE', '领取票据已失效', 409)
    result = {'ticket': decrypt(distribution.ticket_ciphertext), 'expires_at': distribution.expires_at}
    audit(actor.id, 'distribution.reveal', distribution.id, batch.channel_id)
    db.session.commit()
    return result


def download_export(identifier):
    from app.services.cdk_service import begin_write
    begin_write()
    job = db.session.get(CdkJob, identifier)
    if not job or job.kind != 'export':
        raise CdkError('NOT_FOUND', '导出记录不存在', 404)
    actor = require_staff('export', job.channel_id, recent=True)
    if actor.id != job.actor_id or job.expires_at <= time.time() or job.result['downloads'] >= 3:
        raise CdkError('EXPORT_EXPIRED', '导出已过期、超出下载次数或不属于当前员工', 403)
    rows = json.loads(decrypt(job.ciphertext))
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['card_id', 'platform_code'])
    for row in rows:
        card = get_card(row['id'], 'export')
        version_check(card, row['version'])
        batch = get_batch(card.batch_id, 'export')
        if card.control != 'active' or card.usage != 'unused' or card.expires_at <= time.time() or batch.state != 'active':
            raise CdkError('EXPORT_STALE', '卡密状态已变化，请重新审批导出', 409)
        writer.writerow([card.id, row['code']])
    job.result = {**job.result, 'downloads': job.result['downloads'] + 1}
    audit(actor.id, 'export.download', identifier, job.channel_id, count=len(rows), attempt=job.result['downloads'])
    db.session.commit()
    return stream.getvalue()


def list_resources(kind, parameters):
    actor = require_staff('audit' if kind == 'audits' else 'read')
    try:
        limit = int(parameters.get('limit', 50))
    except ValueError as error:
        raise CdkError('INVALID_INPUT', 'limit 必须为整数') from error
    if not 1 <= limit <= 100:
        raise CdkError('INVALID_INPUT', 'limit 必须为 1-100')
    models = {'channels': CdkChannel, 'benefits': CdkBenefit, 'batches': CdkBatch, 'cards': CdkCard,
              'stocks': CdkStock, 'approvals': CdkApproval, 'distributions': CdkDistribution, 'audits': CdkAudit, 'jobs': CdkJob}
    model = models[kind]
    query = model.query
    if kind == 'stocks':
        require_staff('stock', '*')
    if '*' not in actor.scopes:
        if kind in {'batches', 'approvals', 'audits', 'jobs'}:
            query = query.filter(model.channel_id.in_(actor.scopes))
        elif kind == 'channels':
            query = query.filter(model.id.in_(actor.scopes))
        elif kind == 'cards':
            query = query.join(CdkBatch, CdkBatch.id == CdkCard.batch_id).filter(CdkBatch.channel_id.in_(actor.scopes))
        elif kind == 'distributions':
            query = query.join(CdkCard, CdkCard.id == CdkDistribution.card_id).join(CdkBatch, CdkBatch.id == CdkCard.batch_id).filter(CdkBatch.channel_id.in_(actor.scopes))
    for name in ('batch_id', 'state', 'control', 'usage', 'benefit_id'):
        if parameters.get(name) and hasattr(model, name):
            query = query.filter(getattr(model, name) == parameters[name])
    if parameters.get('cursor'):
        query = query.filter(model.id > parameters['cursor'])
    records = query.order_by(model.id).limit(limit + 1).all()
    serializers = {'cards': card_public, 'batches': batch_public, 'benefits': benefit_public, 'stocks': stock_public}
    fields = {
        'channels': ('id', 'code', 'name', 'enabled', 'version'),
        'approvals': ('id', 'maker_id', 'checker_id', 'channel_id', 'action', 'target_id', 'target_version', 'parameters', 'reason', 'state', 'expires_at', 'version', 'result'),
        'distributions': ('id', 'card_id', 'recipient_ref', 'expires_at', 'claimed_at'),
        'audits': ('id', 'actor', 'action', 'target_id', 'channel_id', 'details', 'created_at'),
        'jobs': ('id', 'kind', 'state', 'result', 'expires_at', 'version'),
    }
    items = [serializers[kind](record) if kind in serializers else {name: getattr(record, name) for name in fields[kind]} for record in records[:limit]]
    return {'items': items, 'next_cursor': records[limit - 1].id if len(records) > limit else None}


def reports():
    actor = require_staff('read')
    cards = db.session.query(CdkCard.control, CdkCard.usage, func.count(CdkCard.id)).join(CdkBatch, CdkBatch.id == CdkCard.batch_id)
    redemptions = db.session.query(CdkRedemption.state, func.count(CdkRedemption.id)).join(CdkCard, CdkCard.id == CdkRedemption.card_id).join(CdkBatch, CdkBatch.id == CdkCard.batch_id)
    if '*' not in actor.scopes:
        cards = cards.filter(CdkBatch.channel_id.in_(actor.scopes))
        redemptions = redemptions.filter(CdkBatch.channel_id.in_(actor.scopes))
    return {'cards': [{'control': control, 'usage': usage, 'count': count} for control, usage, count in cards.group_by(CdkCard.control, CdkCard.usage)],
            'redemptions': dict(redemptions.group_by(CdkRedemption.state).all()), 'financial_data': False}
