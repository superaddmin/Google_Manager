import hmac
import json
import secrets
import time
import urllib.parse
from datetime import datetime, timezone

from flask import current_app

from app import db
from app.models.cdk import (CdkBatch, CdkBenefit, CdkCard, CdkDistribution, CdkEvidence, CdkOutbox, CdkRedemption,
                            CdkRequest, CdkStock, new_id)
from app.models.one_time_token import OneTimeToken
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_task import RechargeTask
from app.models.recharge_task_access import RechargeTaskAccess
from app.services.cdk_crypto import CdkError, decrypt
from app.services.cdk_identity import customer_session, email_value, principal, require_staff, token_hash
from app.services.cdk_service import (audit, begin_write, boolean, card_for_code, card_public, check_redeemable,
                                     fingerprint, get_card, run_command, text_value, version_check)
from app.services.recharge_service import RechargeService, RechargeUpstreamError


def normalized_intent(payload):
    card = card_for_code(payload.get('code'))
    batch = db.session.get(CdkBatch, card.batch_id)
    benefit = db.session.get(CdkBenefit, batch.benefit_id)
    plan = text_value(payload, 'plan_type', 64)
    if plan != benefit.plan_type:
        raise CdkError('PLAN_MISMATCH', '所选套餐与卡密权益不符')
    token = payload.get('token_input')
    if not isinstance(token, str) or not 1 <= len(token.strip()) <= 65535:
        raise CdkError('INVALID_CREDENTIAL', 'Session JSON 长度无效')
    token = token.strip()
    try:
        parsed = json.loads(token)
        if not isinstance(parsed, dict) or not isinstance(parsed.get('accessToken'), str) or not parsed['accessToken'].strip():
            raise ValueError()
        target_email = email_value(parsed.get('user', {}).get('email'))
    except (ValueError, TypeError, AttributeError) as error:
        raise CdkError('INVALID_CREDENTIAL', '需提供含 accessToken 和 user.email 的完整 Session JSON') from error
    email = email_value(payload.get('account_email'))
    if email != target_email:
        raise CdkError('BENEFICIARY_MISMATCH', '目标邮箱必须与 Session JSON 中的 user.email 一致')
    renewal = boolean(payload, 'is_renewal')
    if renewal and not benefit.renewal_allowed:
        raise CdkError('RENEWAL_UNSUPPORTED', '此权益不支持续费')
    if payload.get('agreement_accepted') is not True or payload.get('acknowledge_non_free') is not True:
        raise CdkError('CONFIRMATION_REQUIRED', '请确认协议、目标账号和套餐覆盖规则')
    values = {'card_id': card.id, 'benefit_id': benefit.id, 'channel_id': batch.channel_id, 'principal': principal(),
              'plan_type': plan, 'account_email': email, 'token_digest': fingerprint(token), 'is_renewal': renewal,
              'agreement_accepted': True, 'acknowledge_non_free': True}
    return card, values, token


def challenge(payload):
    RechargeService.ensure_enabled()
    card, values, _ = normalized_intent(payload)
    check_redeemable(card)
    token = secrets.token_urlsafe(32)
    OneTimeToken.register('cdk_redeem', token, {'card_id': card.id}, 300, binding=fingerprint(values))
    return {'challenge_token': token, 'expires_in': 300, 'card': card_public(card)}


def redemption_public(redemption):
    task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
    return {'id': redemption.id, 'card_id': redemption.card_id, 'task_no': redemption.task_no, 'state': redemption.state,
            'release_decision': redemption.release_decision, 'version': redemption.version, 'created_at': redemption.created_at,
            'updated_at': redemption.updated_at, 'mutation_action': redemption.mutation_action,
            'mutation_state': redemption.mutation_state, 'mutation_id': redemption.mutation_id,
            'task': task.to_public_dict(), 'data_source': 'local',
            'status_url': '/api/cdk/redemptions/' + redemption.id}


def owned_redemption(identifier, admin=False, capability='read'):
    record = db.session.get(CdkRedemption, identifier)
    if not record:
        raise CdkError('NOT_FOUND', '未找到核销记录', 404)
    if admin:
        get_card(record.card_id, capability)
    elif record.principal != principal():
        raise CdkError('NOT_FOUND', '未找到核销记录', 404)
    return record


def changed(redemption, state, actor='system'):
    redemption.state = state
    redemption.version += 1
    redemption.updated_at = time.time()
    card = db.session.get(CdkCard, redemption.card_id)
    batch = db.session.get(CdkBatch, card.batch_id)
    audit(actor, 'redemption.' + state, redemption.id, batch.channel_id, task_no=redemption.task_no)
    db.session.add(CdkOutbox(redemption_id=redemption.id, aggregate_version=redemption.version, state=state))


def prepare(payload, request_key):
    RechargeService.ensure_enabled()
    card, values, token = normalized_intent(payload)
    owner = values['principal']

    def reserve():
        card, current, _ = normalized_intent(payload)
        if current != values:
            raise CdkError('INTENT_CHANGED', '核销上下文已变化', 409)
        batch, stock = check_redeemable(card)
        challenge_token = text_value(payload, 'challenge_token', 128)
        record = OneTimeToken.lookup('cdk_redeem', challenge_token)
        if (not record or record.expires_at <= time.time() or record.consumed_at is not None
                or not hmac.compare_digest(record.binding or '', fingerprint(values))):
            raise CdkError('CHALLENGE_INVALID', '校验令牌已失效或与完整兑换信息不符', 409)
        record.consumed_at = time.time()
        task_no = 'TK-CDK-' + new_id().upper()
        task = RechargeTask(task_no=task_no, redeem_code=decrypt(stock.ciphertext), account_email=values['account_email'],
                            plan_type=values['plan_type'], is_renewal=values['is_renewal'], status='pending',
                            status_text='已占用库存，等待提交', is_mock=batch.is_mock, card_last4='', notify_email=None)
        db.session.add(task)
        db.session.flush()
        RechargeTaskAccess.bind(task_no)
        db.session.add(RechargeOperation(task_no=task_no, active_key=RechargeService._fingerprint(
            f'{RechargeService.get_mode()}:{task.redeem_code}')))
        redemption = CdkRedemption(id=new_id(), card_id=card.id, stock_id=stock.id, task_no=task_no, principal=owner,
                                    fingerprint=fingerprint(values), active_card_key=card.id, active_stock_key=stock.id)
        db.session.add(redemption)
        db.session.flush()
        card.usage, card.active_redemption_id, card.version = 'reserved', redemption.id, card.version + 1
        stock.state, stock.version = 'reserved', stock.version + 1
        changed(redemption, 'prepared', owner)
        return {'id': redemption.id}

    result = run_command(owner, 'redeem', request_key, values, reserve)
    redemption = owned_redemption(result['id'])
    if redemption.state == 'prepared':
        dispatch(redemption.id, values, token)
    db.session.expire_all()
    return redemption_public(owned_redemption(result['id']))


def dispatch(identifier, values, token):
    begin_write()
    redemption = db.session.get(CdkRedemption, identifier)
    if redemption.state != 'prepared' or redemption.fingerprint != fingerprint(values):
        db.session.rollback()
        return
    if redemption.created_at < time.time() - 300:
        db.session.rollback()
        recover_interrupted()
        return
    task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
    mode = RechargeService.ensure_enabled()
    if task.is_mock != (mode == 'mock'):
        raise CdkError('MODE_MISMATCH', '任务模式不匹配', 409)
    redemption.dispatch_started_at, redemption.lease_token = time.time(), secrets.token_hex(16)
    changed(redemption, 'dispatching')
    snapshot = redemption.version
    payload = {name: values[name] for name in ('plan_type', 'account_email', 'is_renewal', 'agreement_accepted', 'acknowledge_non_free')}
    payload.update(redeem_code=task.redeem_code, token_input=token, email_verified=True, notify_channel='site',
                   notify_email=values['account_email'], idempotency_key=task.task_no, client_task_no=task.task_no)
    db.session.commit()
    if mode == 'mock':
        apply_observation(identifier, {'task_no': 'MOCK-' + identifier, 'client_task_no': task.task_no,
                                       'plan_type': task.plan_type, 'status': 'processing'}, snapshot, True)
        return
    try:
        response = RechargeService._upstream_post('/user/tasks', payload)
        if not isinstance(response, dict) or response.get('ok') is not True:
            raise RechargeUpstreamError('上游尚未确认')
        apply_observation(identifier, response.get('task'), snapshot, True)
    except Exception as error:
        mark_unknown(identifier, snapshot)
        current_app.logger.warning('CDK dispatch pending verification id=%s type=%s', identifier, type(error).__name__)


def mark_unknown(identifier, expected_version):
    begin_write()
    redemption = db.session.get(CdkRedemption, identifier)
    if redemption.version == expected_version and redemption.state not in {'succeeded', 'released', 'cancelled'}:
        task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
        task.status, task.status_text = 'unknown', '上游结果待核对，请勿重复提交'
        changed(redemption, 'unknown')
    db.session.commit()


def apply_observation(identifier, remote, expected_version, require_client=False):
    begin_write()
    redemption = db.session.get(CdkRedemption, identifier)
    if redemption.version != expected_version or redemption.state in {'released', 'cancelled'}:
        db.session.rollback()
        return
    task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
    operation = db.session.get(RechargeOperation, task.task_no)
    if not isinstance(remote, dict):
        raise RechargeUpstreamError('上游缺少有效任务')
    upstream_no = RechargeService._validate_upstream_task_identity(task, remote, operation, require_client)
    status = remote.get('status')
    status = 'completed' if status == 'success' else status
    if status not in RechargeService.TASK_STATUS_TEXT:
        raise RechargeUpstreamError('上游任务状态无效')
    if task.status == 'closed' and status != 'closed':
        raise RechargeUpstreamError('终态冲突')
    if task.status == 'completed' and status not in {'completed', 'closed'}:
        raise RechargeUpstreamError('成功任务不得回退')
    if redemption.mutation_state in {'dispatching', 'unknown'}:
        expected = 'recalled' if redemption.mutation_action == 'recall' else 'closed'
        if status != expected:
            raise RechargeUpstreamError('操作结果尚未确认，需继续查询或人工核对')
        redemption.mutation_state = 'done'
    card, stock = db.session.get(CdkCard, redemption.card_id), db.session.get(CdkStock, redemption.stock_id)
    if upstream_no:
        other = RechargeOperation.query.filter(RechargeOperation.upstream_task_no == upstream_no,
                                                RechargeOperation.task_no != task.task_no).first()
        if other:
            raise RechargeUpstreamError('上游任务已绑定其他本地任务')
        operation.upstream_task_no = upstream_no
    task.status, task.status_text = status, RechargeService.TASK_STATUS_TEXT[status]
    task.notice = '状态已通过平台卡密协调器核对。'
    task.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if status == 'completed' or (status == 'closed' and card.usage == 'redeemed'):
        card.usage, card.successful_redemption_id = 'redeemed', redemption.id
        stock.state, redemption.release_decision = 'consumed', 'consumed'
        new_state = 'succeeded'
    elif status in {'failed', 'recalled', 'closed', 'unknown'}:
        new_state = 'unknown'
    else:
        new_state = 'processing'
    card.version, stock.version = card.version + 1, stock.version + 1
    changed(redemption, new_state)
    db.session.commit()


def refresh(identifier):
    record = db.session.get(CdkRedemption, identifier)
    task = RechargeTask.query.filter_by(task_no=record.task_no).one()
    mode = RechargeService.ensure_enabled()
    if task.is_mock != (mode == 'mock'):
        raise CdkError('MODE_MISMATCH', '任务与当前环境不匹配', 409)
    if record.state in {'prepared', 'released', 'cancelled'} or (record.state == 'succeeded' and record.mutation_state != 'unknown'):
        return redemption_public(record)
    if record.state == 'dispatching' and record.dispatch_started_at > time.time() - 120:
        return redemption_public(record)
    snapshot = record.version
    task_no = task.task_no
    operation = db.session.get(RechargeOperation, task_no)
    upstream_no, code = operation.upstream_task_no, task.redeem_code
    db.session.rollback()
    if mode == 'mock':
        remote = {'task_no': upstream_no, 'client_task_no': task_no, 'plan_type': task.plan_type, 'status': 'completed'}
    else:
        if upstream_no:
            result = RechargeService._upstream_get('/user/tasks/' + urllib.parse.quote(upstream_no, safe=''))
        else:
            result = RechargeService._upstream_post('/user/tasks/lookup', {'redeem_code': code})
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise RechargeUpstreamError('上游查询尚未确认结果')
        remote = result.get('task')
    apply_observation(identifier, remote, snapshot)
    return redemption_public(db.session.get(CdkRedemption, identifier))


def mutate(identifier, action, payload, request_key, admin=False):
    record = owned_redemption(identifier, admin, 'control')
    owner = 'staff:' + require_staff('control').id if admin else principal()

    def reserve_mutation():
        record = owned_redemption(identifier, admin, 'control')
        version_check(record, payload.get('version'))
        if payload.get('confirmed') is not True:
            raise CdkError('CONFIRMATION_REQUIRED', '请明确确认订单操作')
        if record.mutation_state in {'dispatching', 'unknown'}:
            raise CdkError('OPERATION_PENDING', '上一操作结果尚未确认', 409)
        task = RechargeTask.query.filter_by(task_no=record.task_no).one()
        mode = RechargeService.ensure_enabled()
        if task.is_mock != (mode == 'mock'):
            raise CdkError('MODE_MISMATCH', '任务与当前环境不匹配', 409)
        if (action == 'recall' and record.state != 'processing') or (action == 'close' and task.status != 'completed'):
            raise CdkError('STATE_CONFLICT', '当前状态不支持此操作', 409)
        operation = db.session.get(RechargeOperation, task.task_no)
        if not operation.upstream_task_no:
            raise CdkError('OPERATION_PENDING', '上游任务身份尚未确认', 409)
        record.mutation_action, record.mutation_id, record.mutation_state = action, new_id(), 'dispatching'
        record.version += 1
        record.updated_at = time.time()
        return {'id': record.id, 'operation_id': record.mutation_id, 'dispatch_version': record.version}

    result, created = run_command(owner, 'mutation:' + action + ':' + identifier, request_key, payload, reserve_mutation,
                                  authorize=lambda: owned_redemption(identifier, admin, 'control'), return_created=True)
    record = owned_redemption(identifier, admin, 'control')
    if not created:
        return redemption_public(record)
    task = RechargeTask.query.filter_by(task_no=record.task_no).one()
    operation = db.session.get(RechargeOperation, task.task_no)
    values = {'task_no': operation.upstream_task_no, 'redeem_code': task.redeem_code, 'email': task.account_email,
              'confirmed': True, 'idempotency_key': result['operation_id'], 'client_task_no': task.task_no,
              'expected_status': task.status}
    mode = RechargeService.ensure_enabled()
    if task.is_mock != (mode == 'mock'):
        raise CdkError('MODE_MISMATCH', '任务与当前环境不匹配', 409)
    db.session.rollback()
    try:
        if mode == 'mock':
            remote = {'task_no': operation.upstream_task_no, 'client_task_no': task.task_no,
                      'plan_type': task.plan_type, 'status': 'recalled' if action == 'recall' else 'closed'}
        else:
            response = RechargeService._upstream_post('/user/tasks/' + action, values)
            if not isinstance(response, dict) or response.get('ok') is not True:
                raise RechargeUpstreamError('上游尚未确认操作')
            remote = response.get('task')
        apply_observation(identifier, remote, result['dispatch_version'])
    except Exception as error:
        begin_write()
        record = db.session.get(CdkRedemption, identifier)
        if record.mutation_id == result['operation_id'] and record.mutation_state == 'dispatching':
            record.mutation_state = 'unknown'
            changed(record, record.state if record.state == 'succeeded' else 'unknown')
        db.session.commit()
        current_app.logger.warning('CDK mutation pending verification id=%s type=%s', identifier, type(error).__name__)
    return redemption_public(db.session.get(CdkRedemption, identifier))


def release_with_evidence(redemption, approval, actor):
    evidence = approval.parameters
    if redemption.state != 'unknown' or redemption.mutation_state in {'dispatching', 'unknown'}:
        raise CdkError('RELEASE_FORBIDDEN', '仅可释放不存在待核对操作的未知履约', 409)
    if not redemption.dispatch_started_at or redemption.dispatch_started_at > time.time() - 120:
        raise CdkError('RELEASE_FORBIDDEN', '请求仍可能在途，禁止释放', 409)
    if not redemption.created_at <= evidence['observed_at'] <= time.time() + 60:
        raise CdkError('INVALID_EVIDENCE', '证据时间必须晚于意图创建且不能在未来')
    if db.session.get(CdkEvidence, evidence['evidence_sha256']):
        raise CdkError('EVIDENCE_REUSED', '证据已经用于其他释放操作', 409)
    card, stock = db.session.get(CdkCard, redemption.card_id), db.session.get(CdkStock, redemption.stock_id)
    task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
    if card.usage != 'reserved' or task.status in {'completed', 'closed'}:
        raise CdkError('RELEASE_FORBIDDEN', '已确认成功或关闭的权益不能释放', 409)
    release_local(redemption, 'released', actor)
    redemption.evidence_hash = evidence['evidence_sha256']
    db.session.add(CdkEvidence(digest=evidence['evidence_sha256'], redemption_id=redemption.id, approval_id=approval.id))
    audit(actor, 'stock.release_evidence', stock.id, approval.channel_id, reference=evidence['reference'],
          evidence_sha256=evidence['evidence_sha256'], approval_id=approval.id)
    return {'id': redemption.id, 'state': redemption.state}


def resolve_mutation_with_evidence(redemption, approval, actor):
    evidence = approval.parameters
    if redemption.mutation_state != 'unknown' or redemption.mutation_id != evidence['mutation_id']:
        raise CdkError('STATE_CONFLICT', '待核对操作已变化', 409)
    if not redemption.updated_at <= evidence['observed_at'] <= time.time() + 60:
        raise CdkError('INVALID_EVIDENCE', '证据时间必须晚于待核对操作')
    if db.session.get(CdkEvidence, evidence['evidence_sha256']):
        raise CdkError('EVIDENCE_REUSED', '证据已经用于其他操作', 409)
    db.session.add(CdkEvidence(digest=evidence['evidence_sha256'], redemption_id=redemption.id, approval_id=approval.id))
    redemption.mutation_state = 'not_applied'
    changed(redemption, redemption.state, actor)
    audit(actor, 'mutation.not_applied', redemption.id, approval.channel_id, reference=evidence['reference'],
          evidence_sha256=evidence['evidence_sha256'], mutation_id=redemption.mutation_id)
    return {'id': redemption.id, 'mutation_state': redemption.mutation_state}


def release_local(redemption, state, actor='system'):
    card, stock = db.session.get(CdkCard, redemption.card_id), db.session.get(CdkStock, redemption.stock_id)
    task = RechargeTask.query.filter_by(task_no=redemption.task_no).one()
    card.usage, card.active_redemption_id, card.version = 'unused', None, card.version + 1
    stock.state, stock.version = 'allocated', stock.version + 1
    redemption.active_card_key = redemption.active_stock_key = None
    redemption.release_decision = 'not_consumed'
    redemption.state = state
    task.status, task.status_text = 'failed', '已确认未提交或未消费'
    db.session.get(RechargeOperation, task.task_no).active_key = None
    changed(redemption, state, actor)


def recover_interrupted():
    begin_write()
    now = time.time()
    records = CdkRedemption.query.filter(CdkRedemption.state.in_(['prepared', 'dispatching'])).order_by(CdkRedemption.created_at).limit(100).all()
    for record in records:
        if record.state == 'prepared' and record.created_at < now - 300 and record.dispatch_started_at is None:
            release_local(record, 'cancelled')
        elif record.state == 'dispatching' and record.dispatch_started_at < now - 120:
            task = RechargeTask.query.filter_by(task_no=record.task_no).one()
            task.status, task.status_text = 'unknown', '提交中断，等待核对'
            changed(record, 'unknown')
    for record in CdkRedemption.query.filter_by(mutation_state='dispatching').filter(CdkRedemption.updated_at < now - 120).limit(100):
        record.mutation_state = 'unknown'
        changed(record, record.state if record.state == 'succeeded' else 'unknown')
    from app.models.cdk import CdkJob
    CdkJob.query.filter(CdkJob.expires_at < now, CdkJob.ciphertext.isnot(None)).update({'ciphertext': None}, synchronize_session=False)
    db.session.commit()


def claim(payload):
    customer, _ = customer_session(required=True)
    ticket = text_value(payload, 'ticket', 128)
    distribution = CdkDistribution.query.filter_by(ticket_hash=token_hash(ticket)).first()
    if not distribution or distribution.claimed_at or distribution.expires_at <= time.time():
        raise CdkError('TICKET_INVALID', '领取票据无效或已使用', 409)
    if distribution.audience_digest and distribution.audience_digest != customer.email_digest:
        raise CdkError('USER_RESTRICTED', '此票据指定了其他领取客户', 403)
    card = db.session.get(CdkCard, distribution.card_id)
    check_redeemable(card)
    if card.owner_id or card.distribution != 'distributed':
        raise CdkError('CLAIM_CONFLICT', '卡密已被领取', 409)
    card.owner_id, card.distribution, card.version = customer.id, 'claimed', card.version + 1
    distribution.claimed_at = time.time()
    batch = db.session.get(CdkBatch, card.batch_id)
    audit('customer:' + customer.id, 'card.claim', card.id, batch.channel_id)
    return {'card_id': card.id}
