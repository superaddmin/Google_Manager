import hashlib
import hmac
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage

from flask import current_app, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models.cdk import CdkCustomer, CdkCustomerSession, CdkStaff, CdkStaffSession
from app.models.one_time_token import OneTimeToken
from app.models.request_limit import RequestLimit
from app.services.cdk_crypto import CdkError, decrypt, digest, encrypt
from app.utils.email import canonicalize_email


CAPABILITIES = {
    'admin': {'read', 'catalog', 'stock', 'issue', 'control', 'distribute', 'export', 'approve', 'reconcile', 'audit', 'iam'},
    'operator': {'read', 'catalog', 'stock', 'issue', 'control', 'distribute', 'export'},
    'reviewer': {'read', 'approve', 'reconcile', 'audit'},
    'support': {'read'},
    'auditor': {'read', 'audit'},
}


def token_hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def private_digest(domain, value):
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode()
    return hmac.new(secret, (domain + '\0' + value).encode(), hashlib.sha256).hexdigest()


def staff_session():
    token = session.get('cdk_staff_token')
    record = db.session.get(CdkStaffSession, token_hash(token)) if token else None
    staff = db.session.get(CdkStaff, record.staff_id) if record else None
    if (not record or record.expires_at <= time.time() or not staff or not staff.enabled
            or staff.version != record.auth_version):
        raise CdkError('AUTH_REQUIRED', '请登录具名充值管理员账号', 401)
    return staff, record


def require_staff(capability='read', channel_id=None, recent=False):
    staff, record = staff_session()
    if capability not in CAPABILITIES.get(staff.role, set()):
        raise CdkError('FORBIDDEN', '当前角色没有此操作权限', 403)
    if channel_id is not None and '*' not in staff.scopes and channel_id not in staff.scopes:
        raise CdkError('NOT_FOUND', '未找到可访问的资源', 404)
    if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), record.csrf_token):
        raise CdkError('CSRF_REQUIRED', '会话安全校验失败，请重新登录', 403)
    if recent and record.authenticated_at < time.time() - 900:
        raise CdkError('REAUTH_REQUIRED', '敏感操作需要在 15 分钟内重新登录', 401)
    return staff


def staff_public(staff, record=None):
    return {'id': staff.id, 'username': staff.username, 'role': staff.role, 'scopes': staff.scopes,
            'enabled': staff.enabled, 'version': staff.version,
            'capabilities': sorted(CAPABILITIES[staff.role]),
            **({'csrf_token': record.csrf_token} if record else {})}


def create_staff(username, password, role, scopes):
    import re
    if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', username):
        raise CdkError('INVALID_USERNAME', '用户名须为 3-64 位英文字母、数字或 ._-')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise CdkError('INVALID_PASSWORD', '员工密码长度须为 12-256 字符')
    if role not in CAPABILITIES or not isinstance(scopes, list) or not scopes or len(scopes) > 100:
        raise CdkError('INVALID_ROLE', '角色或渠道范围无效')
    from app.models.cdk import CdkChannel
    if any(not isinstance(scope, str) or (scope != '*' and not db.session.get(CdkChannel, scope)) for scope in scopes):
        raise CdkError('INVALID_SCOPE', '渠道范围不存在')
    if CdkStaff.query.filter_by(username=username.lower()).first():
        raise CdkError('DUPLICATE_USER', '用户名已存在', 409)
    staff = CdkStaff(username=username.lower(), password_hash=generate_password_hash(password), role=role, scopes=sorted(set(scopes)))
    db.session.add(staff)
    db.session.flush()
    return staff


def login(payload):
    username, password = payload.get('username'), payload.get('password')
    if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
        raise CdkError('INVALID_CREDENTIALS', '用户名或密码错误', 401)
    rate_limit('staff-login', (request.remote_addr or '') + ':' + username.lower(), 10, 900)
    staff = CdkStaff.query.filter_by(username=username.lower()).first()
    valid = check_password_hash(staff.password_hash if staff else current_app.extensions['cdk_dummy_password'], password)
    if not valid or not staff or not staff.enabled:
        raise CdkError('INVALID_CREDENTIALS', '用户名或密码错误', 401)
    previous = session.get('cdk_staff_token')
    if previous:
        CdkStaffSession.query.filter_by(token_hash=token_hash(previous)).delete()
    token = secrets.token_urlsafe(32)
    record = CdkStaffSession(token_hash=token_hash(token), staff_id=staff.id, auth_version=staff.version,
                             expires_at=time.time() + 8 * 3600, csrf_token=secrets.token_hex(32))
    db.session.add(record)
    db.session.commit()
    session['cdk_staff_token'] = token
    return staff_public(staff, record)


def rate_limit(scope, value, limit, seconds=60):
    if not RequestLimit.consume('cdk:' + scope + ':' + private_digest(scope, value), limit, seconds):
        raise CdkError('RATE_LIMITED', '请求过于频繁，请稍后重试', 429)


def customer_session(required=False):
    token = session.get('cdk_customer_token')
    record = db.session.get(CdkCustomerSession, token_hash(token)) if token else None
    customer = db.session.get(CdkCustomer, record.customer_id) if record else None
    if record and record.expires_at > time.time() and customer:
        if not customer.enabled:
            raise CdkError('CUSTOMER_BLOCKED', '当前客户账号已停用', 403)
        return customer, record
    if required:
        raise CdkError('CUSTOMER_REQUIRED', '请先完成邮箱验证码登录', 401)
    return None, None


def principal():
    customer, _ = customer_session()
    if customer:
        return 'customer:' + customer.id
    if 'cdk_context' not in session:
        session['cdk_context'] = secrets.token_urlsafe(32)
    return 'guest:' + private_digest('cdk-context', session['cdk_context'])


def email_value(value):
    from app.services.recharge_service import RechargeService
    if not isinstance(value, str) or len(value) > 256 or not RechargeService.EMAIL_PATTERN.fullmatch(value.strip()):
        raise CdkError('INVALID_EMAIL', '请输入有效邮箱')
    return canonicalize_email(value)


def otp_enabled():
    return bool(current_app.config.get('CDK_SMTP_HOST') and current_app.config.get('CDK_SMTP_FROM'))


def send_otp(payload):
    if not otp_enabled():
        raise CdkError('OTP_UNAVAILABLE', '邮箱登录暂未配置，请使用不限客户的持券兑换', 503)
    email = email_value(payload.get('email'))
    rate_limit('otp-send', email, 3, 600)
    rate_limit('otp-send-ip', request.remote_addr or '', 10, 600)
    code = str(secrets.randbelow(1000000)).zfill(6)
    nonce = secrets.token_urlsafe(32)
    email_key = private_digest('customer-email', email)
    OneTimeToken.query.filter_by(namespace='cdk_otp', binding=email_key).delete()
    record = OneTimeToken(namespace='cdk_otp', token_hash=token_hash(nonce), binding=email_key,
                          payload={'email': encrypt(email), 'code_hash': private_digest('otp', nonce + ':' + code), 'attempts': 0},
                          expires_at=time.time() + 300)
    db.session.add(record)
    db.session.commit()
    message = EmailMessage()
    message['Subject'] = 'Chat GPT充值中心邮箱验证码'
    message['From'] = current_app.config['CDK_SMTP_FROM']
    message['To'] = email
    message.set_content(f'您的验证码为 {code}，5 分钟内有效。请勿向他人提供。')
    try:
        with smtplib.SMTP_SSL(current_app.config['CDK_SMTP_HOST'], int(current_app.config.get('CDK_SMTP_PORT') or 465),
                              timeout=10, context=ssl.create_default_context()) as smtp:
            if current_app.config.get('CDK_SMTP_USERNAME'):
                smtp.login(current_app.config['CDK_SMTP_USERNAME'], current_app.config.get('CDK_SMTP_PASSWORD', ''))
            smtp.send_message(message)
    except Exception as error:
        OneTimeToken.query.filter_by(namespace='cdk_otp', token_hash=token_hash(nonce)).delete()
        db.session.commit()
        raise CdkError('OTP_SEND_FAILED', '验证码发送失败，请稍后重试', 503) from error
    return {'challenge_id': nonce, 'expires_in': 300}


def verify_otp(payload):
    from app.services.cdk_service import begin_write
    nonce, code = payload.get('challenge_id'), payload.get('code')
    if not isinstance(nonce, str) or len(nonce) > 128 or not isinstance(code, str) or len(code) != 6:
        raise CdkError('OTP_INVALID', '验证码无效或已过期')
    rate_limit('otp-verify', request.remote_addr or '', 30, 300)
    begin_write()
    record = OneTimeToken.lookup('cdk_otp', nonce)
    if not record or record.consumed_at or record.expires_at <= time.time() or record.payload['attempts'] >= 5:
        raise CdkError('OTP_INVALID', '验证码无效或已过期')
    values = dict(record.payload)
    values['attempts'] += 1
    record.payload = values
    if not hmac.compare_digest(values['code_hash'], private_digest('otp', nonce + ':' + code)):
        db.session.commit()
        raise CdkError('OTP_INVALID', '验证码无效或已过期')
    email = decrypt(values['email'])
    email_key = private_digest('customer-email', email)
    customer = CdkCustomer.query.filter_by(email_digest=email_key).first()
    if not customer:
        customer = CdkCustomer(email_ciphertext=encrypt(email), email_digest=email_key)
        db.session.add(customer)
        db.session.flush()
    if not customer.enabled:
        raise CdkError('CUSTOMER_BLOCKED', '当前客户账号已停用', 403)
    record.consumed_at = time.time()
    old_token = session.get('cdk_customer_token')
    if old_token:
        CdkCustomerSession.query.filter_by(token_hash=token_hash(old_token)).delete()
    token = secrets.token_urlsafe(32)
    db.session.add(CdkCustomerSession(token_hash=token_hash(token), customer_id=customer.id, expires_at=time.time() + 86400))
    db.session.commit()
    session['cdk_customer_token'] = token
    return {'id': customer.id, 'email': email}
