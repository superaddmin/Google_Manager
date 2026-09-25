import secrets
import time

from flask import Blueprint, Response, current_app, jsonify, request, session
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException

from app import db
from app.models.cdk import (CdkApproval, CdkBatch, CdkCard, CdkChannel, CdkCustomer, CdkCustomerSession,
                            CdkRedemption, CdkRequest, CdkStaff, CdkStaffSession)
from app.services import cdk_catalog as catalog
from app.services import cdk_identity as identity
from app.services import cdk_redemption as redemption
from app.services.cdk_crypto import CdkError, decrypt, normalize_code
from app.services.cdk_service import (audit, begin_write, card_for_code, card_public, check_redeemable,
                                     ensure_enabled, get_batch, get_card, run_command, text_value, version_check)
from app.services.recharge_service import RechargeModeDisabledError, RechargeService, RechargeUpstreamError
from app.services.request_security import protect_write_request


cdk_bp = Blueprint('cdk', __name__)


def success(data, status=200):
    return jsonify(success=True, data=data, message='操作成功', request_id=secrets.token_hex(8)), status


@cdk_bp.errorhandler(Exception)
def failure(error):
    db.session.rollback()
    if isinstance(error, CdkError):
        code, message, status = error.code, str(error), error.status
    elif isinstance(error, RechargeModeDisabledError):
        code, message, status = 'FULFILLMENT_DISABLED', str(error), 503
    elif isinstance(error, RechargeUpstreamError):
        code, message, status = 'UPSTREAM_UNAVAILABLE', '上游结果尚未确认，请保留任务编号查询', 502
    elif isinstance(error, OperationalError):
        code, message, status = 'DATABASE_BUSY', '数据库暂不可用，请使用原请求键重试', 503
    elif isinstance(error, HTTPException):
        code, message, status = 'HTTP_ERROR', error.description, error.code
    else:
        code, message, status = 'INTERNAL_ERROR', '操作未完成，请保留请求键并联系管理员', 500
        current_app.logger.error('CDK error type=%s path=%s', type(error).__name__, request.path)
    if status in {401, 403, 409, 422}:
        try:
            token = session.get('cdk_staff_token')
            record = db.session.get(CdkStaffSession, identity.token_hash(token)) if token else None
            actor = 'staff:' + record.staff_id if record else 'request:' + identity.private_digest('audit-ip', request.remote_addr or '')
            audit(actor, 'request.denied', 'request', endpoint=request.endpoint, error_code=code)
            db.session.commit()
        except Exception:
            db.session.rollback()
    response = jsonify(success=False, data=None, message=message, error_code=code,
                       retryable=status in {429, 502, 503}, request_id=secrets.token_hex(8))
    if status == 429:
        response.headers['Retry-After'] = '60'
    return response, status


@cdk_bp.before_request
def safety():
    rejected = protect_write_request()
    if rejected:
        return rejected
    if request.method == 'POST' and (not request.is_json or not isinstance(request.get_json(silent=True), dict)):
        raise CdkError('INVALID_JSON', '请求体必须为 JSON 对象', 415)
    if request.endpoint == 'cdk.config':
        return
    ensure_enabled()
    identity.rate_limit('ip', request.remote_addr or '', 300)
    values = request.get_json(silent=True) or {}
    if isinstance(values.get('code'), str):
        identity.rate_limit('code-probe', request.remote_addr or '', 30)
        try:
            code = normalize_code(values['code'])
        except CdkError:
            code = values['code'][:128]
        identity.rate_limit('card', code, 20)
    if isinstance(values.get('token_input'), str):
        identity.rate_limit('credential', values['token_input'], 20)
    if request.path.startswith('/api/cdk/admin/') and request.endpoint != 'cdk.staff_login':
        identity.require_staff()


@cdk_bp.get('/config')
def config():
    return success({'enabled': bool(current_app.config.get('CDK_ENABLED')), 'claim_enabled': identity.otp_enabled(),
                    'fulfillment_enabled': RechargeService.get_mode() != 'disabled',
                    'format': 'GM1', 'max_batch': catalog.MAX_BATCH})


@cdk_bp.post('/admin/session/login')
def staff_login():
    return success(identity.login(request.get_json()))


@cdk_bp.get('/admin/session')
def staff_me():
    staff, record = identity.staff_session()
    return success(identity.staff_public(staff, record))


@cdk_bp.post('/admin/session/logout')
def staff_logout():
    _, record = identity.staff_session()
    db.session.delete(record)
    db.session.commit()
    session.pop('cdk_staff_token', None)
    return success({})


def admin_command(operation, payload, function, authorize):
    actor = identity.require_staff()
    result = run_command('staff:' + actor.id, operation, request.headers.get('Idempotency-Key'), payload,
                          function, authorize=authorize)
    return success(result)


@cdk_bp.get('/admin/<kind>')
def admin_list(kind):
    if kind not in {'channels', 'benefits', 'batches', 'cards', 'stocks', 'approvals', 'distributions', 'audits', 'jobs'}:
        raise CdkError('NOT_FOUND', '接口不存在', 404)
    return success(catalog.list_resources(kind, request.args))


@cdk_bp.get('/admin/reports')
def reports():
    return success(catalog.reports())


@cdk_bp.post('/admin/<kind>')
def admin_create(kind):
    payload = request.get_json()
    functions = {'channels': catalog.create_channel, 'benefits': catalog.create_benefit, 'batches': catalog.create_batch,
                 'approvals': catalog.request_approval, 'distributions': catalog.distribute}
    if kind not in functions:
        raise CdkError('NOT_FOUND', '接口不存在', 404)

    def authorize():
        if kind in {'channels', 'benefits'}:
            return identity.require_staff('catalog', '*')
        if kind == 'batches':
            return identity.require_staff('issue', payload.get('channel_id'))
        if kind == 'distributions':
            return get_card(payload.get('card_id'), 'distribute')
        action = payload.get('action')
        if action not in catalog.APPROVAL_ACTIONS:
            raise CdkError('INVALID_ACTION', '审批操作无效')
        return catalog.approval_target(action, payload.get('target_id'),
            {'export': 'export', 'release': 'reconcile', 'resolve_mutation': 'reconcile', 'activate': 'issue'}.get(action, 'control'))

    return admin_command(kind + '.create', payload, lambda: functions[kind](payload), authorize)


@cdk_bp.post('/admin/imports/preview')
def preview_import():
    payload = request.get_json()
    return admin_command('import.preview', payload, lambda: catalog.preview_import(payload), lambda: identity.require_staff('stock', '*'))


@cdk_bp.post('/admin/imports/<identifier>/commit')
def commit_import(identifier):
    payload = request.get_json()
    return admin_command('import.commit:' + identifier, payload, lambda: catalog.commit_import(identifier, payload),
                          lambda: identity.require_staff('stock', '*'))


@cdk_bp.post('/admin/stocks/<identifier>/verify')
def verify_stock(identifier):
    return success(catalog.verify_stock(identifier, request.get_json()))


@cdk_bp.post('/admin/batches/<identifier>/generate')
def generate(identifier):
    payload = request.get_json()
    return admin_command('generate:' + identifier, payload, lambda: catalog.generate_batch(identifier, payload),
                          lambda: get_batch(identifier, 'issue'))


@cdk_bp.post('/admin/<kind>/<identifier>/freeze')
def freeze(kind, identifier):
    if kind not in {'batches', 'cards'}:
        raise CdkError('NOT_FOUND', '接口不存在', 404)
    payload = request.get_json()
    return admin_command('freeze:' + identifier, payload, lambda: catalog.freeze('batch' if kind == 'batches' else 'card', identifier, payload),
                          lambda: get_batch(identifier, 'control') if kind == 'batches' else get_card(identifier, 'control'))


@cdk_bp.post('/admin/approvals/<identifier>/decide')
def decide(identifier):
    payload = request.get_json()

    def authorize():
        approval = db.session.get(CdkApproval, identifier)
        if not approval:
            raise CdkError('NOT_FOUND', '审批不存在', 404)
        identity.require_staff('approve', approval.channel_id, recent=True)

    return admin_command('decide:' + identifier, payload, lambda: catalog.decide_approval(identifier, payload), authorize)


@cdk_bp.post('/admin/distributions/<identifier>/ticket')
def ticket(identifier):
    return success(catalog.reveal_ticket(identifier))


@cdk_bp.post('/admin/exports/<identifier>/download')
def download(identifier):
    content = catalog.download_export(identifier)
    return Response(content, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="cdk-{identifier}.csv"'})


@cdk_bp.post('/admin/cards/search')
def search_card():
    from app.services.cdk_service import find_resource
    code = normalize_code(request.get_json().get('code'))
    identifier = find_resource('card', code)
    if not identifier:
        raise CdkError('NOT_FOUND', '未找到可访问卡密', 404)
    return success(card_public(get_card(identifier)))


@cdk_bp.get('/admin/cards/<identifier>')
def card_detail(identifier):
    card = get_card(identifier)
    records = CdkRedemption.query.filter_by(card_id=card.id).order_by(CdkRedemption.created_at.desc()).limit(50).all()
    return success({'card': card_public(card), 'redemptions': [redemption.redemption_public(record) for record in records]})


@cdk_bp.get('/admin/redemptions')
def admin_redemptions():
    actor = identity.require_staff()
    query = CdkRedemption.query.join(CdkCard, CdkCard.id == CdkRedemption.card_id).join(CdkBatch, CdkBatch.id == CdkCard.batch_id)
    if '*' not in actor.scopes:
        query = query.filter(CdkBatch.channel_id.in_(actor.scopes))
    if request.args.get('state'):
        query = query.filter(CdkRedemption.state == request.args['state'])
    if request.args.get('cursor'):
        query = query.filter(CdkRedemption.id > request.args['cursor'])
    records = query.order_by(CdkRedemption.id).limit(51).all()
    return success({'items': [redemption.redemption_public(record) for record in records[:50]],
                    'next_cursor': records[49].id if len(records) > 50 else None})


@cdk_bp.post('/admin/redemptions/<identifier>/<action>')
def admin_redemption_action(identifier, action):
    redemption.owned_redemption(identifier, True, 'control')
    if action == 'refresh':
        return success(redemption.refresh(identifier))
    if action in {'recall', 'close'}:
        return success(redemption.mutate(identifier, action, request.get_json(), request.headers.get('Idempotency-Key'), True))
    raise CdkError('NOT_FOUND', '接口不存在', 404)


@cdk_bp.route('/admin/users', methods=['GET', 'POST'])
def users():
    identity.require_staff('iam', '*', recent=True)
    if request.method == 'GET':
        return success({'items': [identity.staff_public(staff) for staff in CdkStaff.query.order_by(CdkStaff.username).limit(100)]})
    payload = request.get_json()

    def create():
        actor = identity.require_staff('iam', '*', recent=True)
        staff = identity.create_staff(payload.get('username'), payload.get('password'), payload.get('role'), payload.get('scopes'))
        audit(actor.id, 'staff.create', staff.id, role=staff.role, scopes=staff.scopes)
        return identity.staff_public(staff)

    return admin_command('staff.create', payload, create, lambda: identity.require_staff('iam', '*', recent=True))


@cdk_bp.post('/admin/users/<identifier>/update')
def update_user(identifier):
    payload = request.get_json()

    def update():
        actor = identity.require_staff('iam', '*', recent=True)
        target = db.session.get(CdkStaff, identifier)
        if not target:
            raise CdkError('NOT_FOUND', '员工不存在', 404)
        if actor.id == identifier:
            raise CdkError('SELF_CHANGE_FORBIDDEN', '禁止修改自己的权限或停用自己', 403)
        version_check(target, payload.get('version'))
        role, scopes = payload.get('role', target.role), payload.get('scopes', target.scopes)
        if role not in identity.CAPABILITIES or not isinstance(scopes, list) or not scopes or len(scopes) > 100:
            raise CdkError('INVALID_SCOPE', '角色或范围无效')
        if any(not isinstance(scope, str) or (scope != '*' and not db.session.get(CdkChannel, scope)) for scope in scopes):
            raise CdkError('INVALID_SCOPE', '渠道范围不存在')
        from app.services.cdk_service import boolean
        target.role, target.scopes, target.enabled = role, sorted(set(scopes)), boolean(payload, 'enabled', target.enabled)
        if payload.get('password'):
            from werkzeug.security import generate_password_hash
            password = payload['password']
            if not isinstance(password, str) or not 12 <= len(password) <= 256:
                raise CdkError('INVALID_PASSWORD', '密码长度须为 12-256 字符')
            target.password_hash = generate_password_hash(password)
        target.version += 1
        audit(actor.id, 'staff.update', target.id, role=target.role, scopes=target.scopes, enabled=target.enabled)
        return identity.staff_public(target)

    return admin_command('staff.update:' + identifier, payload, update, lambda: identity.require_staff('iam', '*', recent=True))


@cdk_bp.post('/admin/channels/<identifier>/update')
def update_channel(identifier):
    payload = request.get_json()

    def update():
        actor = identity.require_staff('catalog', '*')
        channel = db.session.get(CdkChannel, identifier)
        if not channel:
            raise CdkError('NOT_FOUND', '渠道不存在', 404)
        from app.services.cdk_service import boolean
        version_check(channel, payload.get('version'))
        channel.enabled = boolean(payload, 'enabled')
        channel.version += 1
        audit(actor.id, 'channel.update', channel.id, channel.id, enabled=channel.enabled)
        return {'id': channel.id, 'version': channel.version}

    return admin_command('channel.update:' + identifier, payload, update, lambda: identity.require_staff('catalog', '*'))


@cdk_bp.get('/admin/customers')
def customers():
    identity.require_staff('iam', '*')
    rows = CdkCustomer.query.order_by(CdkCustomer.id)
    if request.args.get('cursor'):
        rows = rows.filter(CdkCustomer.id > request.args['cursor'])
    records = rows.limit(51).all()
    return success({'items': [{'id': customer.id, 'enabled': customer.enabled, 'version': customer.version,
                              'email': decrypt(customer.email_ciphertext)[:2] + '***@' + decrypt(customer.email_ciphertext).split('@')[-1]}
                             for customer in records[:50]], 'next_cursor': records[49].id if len(records) > 50 else None})


@cdk_bp.post('/admin/customers/<identifier>/update')
def update_customer(identifier):
    payload = request.get_json()

    def update():
        actor = identity.require_staff('iam', '*', recent=True)
        customer = db.session.get(CdkCustomer, identifier)
        if not customer:
            raise CdkError('NOT_FOUND', '客户不存在', 404)
        from app.services.cdk_service import boolean
        version_check(customer, payload.get('version'))
        customer.enabled = boolean(payload, 'enabled')
        customer.version += 1
        if not customer.enabled:
            CdkCustomerSession.query.filter_by(customer_id=identifier).delete()
        audit(actor.id, 'customer.update', customer.id, enabled=customer.enabled)
        return {'id': customer.id, 'enabled': customer.enabled, 'version': customer.version}

    return admin_command('customer.update:' + identifier, payload, update, lambda: identity.require_staff('iam', '*', recent=True))


@cdk_bp.post('/validate')
def validate():
    card = card_for_code(request.get_json().get('code'))
    check_redeemable(card)
    return success(card_public(card))


@cdk_bp.post('/challenges')
def challenge():
    return success(redemption.challenge(request.get_json()))


@cdk_bp.post('/redemptions')
def redeem():
    result = redemption.prepare(request.get_json(), request.headers.get('Idempotency-Key'))
    return success(result, 200 if result['state'] == 'succeeded' else 202)


@cdk_bp.post('/redemptions/recover')
def recover():
    key = text_value(request.get_json(), 'idempotency_key', 80)
    record = db.session.get(CdkRequest, (identity.principal(), 'redeem', key))
    if not record:
        raise CdkError('NOT_FOUND', '尚未找到此请求的核销记录，请使用原请求键重试', 404)
    return success(redemption.redemption_public(redemption.owned_redemption(record.result['id'])))


@cdk_bp.get('/redemptions/mine')
def my_redemptions():
    records = CdkRedemption.query.filter_by(principal=identity.principal()).order_by(CdkRedemption.created_at.desc()).limit(100).all()
    return success({'items': [redemption.redemption_public(record) for record in records]})


@cdk_bp.get('/redemptions/<identifier>')
def detail(identifier):
    return success(redemption.redemption_public(redemption.owned_redemption(identifier)))


@cdk_bp.post('/redemptions/<identifier>/<action>')
def redemption_action(identifier, action):
    redemption.owned_redemption(identifier)
    if action == 'refresh':
        return success(redemption.refresh(identifier))
    if action in {'recall', 'close'}:
        return success(redemption.mutate(identifier, action, request.get_json(), request.headers.get('Idempotency-Key')))
    raise CdkError('NOT_FOUND', '接口不存在', 404)


@cdk_bp.post('/customer/otp/send')
def send_otp():
    return success(identity.send_otp(request.get_json()))


@cdk_bp.post('/customer/otp/verify')
def verify_otp():
    return success(identity.verify_otp(request.get_json()))


@cdk_bp.get('/customer/me')
def customer_me():
    customer, _ = identity.customer_session(required=True)
    return success({'id': customer.id, 'email': decrypt(customer.email_ciphertext)})


@cdk_bp.post('/customer/logout')
def customer_logout():
    _, record = identity.customer_session(required=True)
    db.session.delete(record)
    db.session.commit()
    session.pop('cdk_customer_token', None)
    return success({})


@cdk_bp.post('/claims')
def claim():
    identity.customer_session(required=True)
    payload = request.get_json()
    result = run_command(identity.principal(), 'claim', request.headers.get('Idempotency-Key'), payload,
                          lambda: redemption.claim(payload), authorize=lambda: identity.customer_session(required=True))
    return success(result)


@cdk_bp.get('/cards/mine')
def my_cards():
    customer, _ = identity.customer_session(required=True)
    records = CdkCard.query.filter_by(owner_id=customer.id).order_by(CdkCard.created_at.desc()).limit(100).all()
    return success({'items': [card_public(card) for card in records]})


@cdk_bp.post('/cards/<identifier>/reveal')
def reveal_card(identifier):
    begin_write()
    customer, record = identity.customer_session(required=True)
    card = db.session.get(CdkCard, identifier)
    if not card or card.owner_id != customer.id:
        raise CdkError('NOT_FOUND', '卡密不存在', 404)
    if record.authenticated_at < time.time() - 900:
        raise CdkError('REAUTH_REQUIRED', '请重新验证邮箱后查看完整卡密', 401)
    batch = db.session.get(CdkBatch, card.batch_id)
    if card.control == 'void' or batch.state == 'closed':
        raise CdkError('CODE_VOID', '卡密已作废', 409)
    result = {'code': decrypt(card.ciphertext)}
    audit('customer:' + customer.id, 'card.reveal', card.id, batch.channel_id)
    db.session.commit()
    return success(result)
