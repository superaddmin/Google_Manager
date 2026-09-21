import hashlib
import hmac
import ipaddress
from urllib.parse import urlsplit

from flask import current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.models.request_limit import RequestLimit


class TrustedProxyMiddleware:
    def __init__(self, application, networks):
        self.application = application
        self.networks = [ipaddress.ip_network(value.strip()) for value in networks.split(',') if value.strip()]
        self.forwarded = ProxyFix(application, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        try:
            address = ipaddress.ip_address(environ.get('REMOTE_ADDR', ''))
            trusted = any(address in network for network in self.networks)
        except ValueError:
            trusted = False
        return (self.forwarded if trusted else self.application)(environ, start_response)


def protect_write_request():
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return None
    origin = request.headers.get('Origin')
    if origin:
        try:
            parsed = urlsplit(origin)
            expected = urlsplit(request.host_url)
        except ValueError:
            return jsonify(success=False, data=None, message='请求来源格式无效', error_code='forbidden'), 403
        if parsed.scheme not in {'http', 'https'} or (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            return jsonify(success=False, data=None, message='跨站请求被拦截 (Invalid Origin)', error_code='forbidden'), 403
    if request.headers.get('Sec-Fetch-Site') in {'cross-site', 'same-site'}:
        return jsonify(success=False, data=None, message='请求来源不可信', error_code='forbidden'), 403
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return jsonify(success=False, data=None, message='缺少安全请求标记', error_code='forbidden'), 403
    return None


def _sensitive_rate_limit(scope, value, limit, window, message):
    """Consume a rate-limit bucket without storing the sensitive value in its key."""
    if not isinstance(value, str) or not value.strip():
        return None
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode()
    scoped_value = f'{scope}\0{value.strip()}'.encode()
    digest = hmac.new(secret, scoped_value, hashlib.sha256).hexdigest()
    if RequestLimit.consume(f'{scope}:{digest}', limit, window):
        return None
    response = jsonify(success=False, data=None, message=message, error_code='rate_limited')
    response.headers['Retry-After'] = '60'
    return response, 429


def limit_recharge_request():
    if not current_app.config['RECHARGE_RATE_LIMIT_ENABLED']:
        return None
    limit = current_app.config['RECHARGE_RATE_LIMIT']
    window = 60
    identity = hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
    if not RequestLimit.consume('recharge:' + identity, limit, window):
        response = jsonify(
            success=False,
            data=None,
            message='请求过于频繁，请稍后重试',
            error_code='rate_limited',
        )
        response.headers['Retry-After'] = '60'
        return response, 429
    payload = request.get_json(silent=True) or {}

    # 批量查询的业务上限是 50 个去重后的卡密。IP 桶按 HTTP 请求计数保持兼容，
    # 对有效且去重后的卡密逐项限流；不能按原始数组长度提前跳过，否则攻击者可
    # 通过填充重复卡密绕过卡密桶。
    batch_codes = []
    raw_batch = payload.get('redeem_codes') if isinstance(payload, dict) else None
    if isinstance(raw_batch, list):
        seen = set()
        for raw_code in raw_batch:
            if not isinstance(raw_code, str):
                continue
            code = raw_code.strip()
            if code and code not in seen:
                seen.add(code)
                batch_codes.append(code)
                if len(batch_codes) > 50:
                    # 业务层会拒绝超过 50 个不同卡密的请求；这里不消耗任一卡密
                    # 的额度，避免无效请求污染合法卡密的独立限流桶。
                    batch_codes.clear()
                    break

    if batch_codes:
        for code in batch_codes:
            rejected = _sensitive_rate_limit(
                'card', code, limit, window, '此卡密请求过于频繁，请稍后重试'
            )
            if rejected:
                return rejected
    else:
        code = payload.get('redeem_code') if isinstance(payload, dict) else None
        rejected = _sensitive_rate_limit(
            'card', code, limit, window, '此卡密请求过于频繁，请稍后重试'
        )
        if rejected:
            return rejected

    token = payload.get('token_input') if isinstance(payload, dict) else None
    rejected = _sensitive_rate_limit(
        'credential', token, limit, window, '此凭证请求过于频繁，请稍后重试'
    )
    if rejected:
        return rejected
    return None
