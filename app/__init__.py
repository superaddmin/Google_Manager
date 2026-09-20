"""
Flask 应用工厂模块
创建和配置 Flask 应用实例
"""
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from cryptography.fernet import Fernet
import hashlib
import os
from urllib.parse import urlsplit

# 初始化数据库扩展
db = SQLAlchemy()


def validate_recharge_upstream_url(raw_url, allowed_hosts):
    """校验 live 充值上游地址，拒绝明文、userinfo 和未授权主机。"""
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise RuntimeError('live 充值必须显式配置 RECHARGE_UPSTREAM_URL')
    value = raw_url.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # 访问 port 可触发 ValueError（例如非法端口），因此显式读取并处理。
        _ = parsed.port
    except ValueError as error:
        raise RuntimeError('RECHARGE_UPSTREAM_URL 不是合法绝对 URL') from error
    if (
        parsed.scheme != 'https'
        or not hostname
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.path and not parsed.path.startswith('/'))
    ):
        raise RuntimeError('RECHARGE_UPSTREAM_URL 必须是无 userinfo/query/fragment 的 HTTPS URL')
    allowed = {
        item.strip().lower().rstrip('.')
        for item in str(allowed_hosts or '').split(',')
        if item.strip()
    }
    if not allowed or hostname.lower().rstrip('.') not in allowed:
        raise RuntimeError('RECHARGE_UPSTREAM_URL 主机不在 RECHARGE_UPSTREAM_ALLOWED_HOSTS 白名单中')
    return value


def create_app(config_name=None):
    """
    应用工厂函数
    
    Args:
        config_name: 配置名称（development/production/testing）
    
    Returns:
        Flask 应用实例
    """
    app = Flask(__name__, 
                static_folder='../static',
                static_url_path='/static')
    
    # 加载配置
    from app.config import config
    config_name = config_name or os.environ.get('FLASK_ENV', 'development')
    base_config = config[config_name]
    if config_name == 'testing':
        recharge_mode = 'mock'
    else:
        recharge_mode = os.environ.get('RECHARGE_MODE', 'disabled').lower().strip()
    configured_upstream_url = os.environ.get('RECHARGE_UPSTREAM_URL', '').strip()
    recharge_upstream_url_explicit = bool(configured_upstream_url)
    recharge_upstream_url = configured_upstream_url or getattr(
        base_config, 'RECHARGE_UPSTREAM_URL', ''
    )
    allowed_upstream_hosts = os.environ.get(
        'RECHARGE_UPSTREAM_ALLOWED_HOSTS', 'aichong666.com'
    )
    production_secret = os.environ.get('SECRET_KEY', '')
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    if (
        config_name == 'production'
        and len(production_secret.strip().encode('utf-8')) < 32
    ):
        raise RuntimeError('生产环境 SECRET_KEY 必须至少为 32 字节')
    if config_name != 'testing' and not admin_password:
        raise RuntimeError('非测试环境必须通过 ADMIN_PASSWORD 配置管理员密码')
    if config_name == 'production' and not os.environ.get('GMAIL_TOKEN_ENCRYPTION_KEY'):
        raise RuntimeError('生产环境必须通过 GMAIL_TOKEN_ENCRYPTION_KEY 配置 Gmail Token 加密密钥')
    INSECURE_SECRET_TEMPLATES = {
        'your-production-secret-key-at-least-32-chars!',
        'ChangeMeStrongPassword123!',
        'dev-secret-key-32-chars-minimum-needed!!',
    }
    if config_name == 'production' and production_secret.strip() in INSECURE_SECRET_TEMPLATES:
        raise RuntimeError('生产环境禁止使用默认示例 SECRET_KEY')
    if config_name == 'production' and hashlib.sha256(production_secret.strip().encode()).hexdigest() == 'd1c38779422605b6ee03d0e938324928cbff477e806c77ac156cfb9eabd6bc8f':
        raise RuntimeError('生产环境禁止使用默认示例 SECRET_KEY')
    if config_name == 'production' and admin_password.strip() in {'admin', 'admin123', 'ChangeMeStrongPassword123!', 'YourComplexPassword_2026!'}:
        raise RuntimeError('生产环境禁止使用默认示例 ADMIN_PASSWORD')
    if config_name == 'production' and (
        admin_password != admin_password.strip()
        or len(admin_password) < 16
        or len(admin_password.encode('utf-8')) > 4096
    ):
        raise RuntimeError('生产环境 ADMIN_PASSWORD 须为 16 字符以上、UTF-8 不超过 4096 字节且无首尾空白')
    if config_name == 'production':
        try:
            Fernet(os.environ['GMAIL_TOKEN_ENCRYPTION_KEY'].encode('ascii'))
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('GMAIL_TOKEN_ENCRYPTION_KEY 必须是有效的 Fernet 密钥') from error
        if recharge_mode == 'mock':
            raise RuntimeError('生产环境禁止启用 mock 充值模式')
        if recharge_mode not in {'disabled', 'live'}:
            raise RuntimeError(f'生产环境不支持未知的 RECHARGE_MODE: {recharge_mode}')

    if config_name != 'testing' and recharge_mode == 'live':
        if not recharge_upstream_url_explicit:
            raise RuntimeError('非测试环境 live 充值必须显式配置 RECHARGE_UPSTREAM_URL')
        recharge_upstream_url = validate_recharge_upstream_url(
            recharge_upstream_url, allowed_upstream_hosts
        )

    app.config.from_object(base_config)
    if config_name != 'testing':
        app.config['RECHARGE_MODE'] = recharge_mode
        app.config['RECHARGE_UPSTREAM_URL'] = recharge_upstream_url
        app.config['RECHARGE_UPSTREAM_ALLOWED_HOSTS'] = allowed_upstream_hosts
    app.config['RECHARGE_UPSTREAM_URL_EXPLICIT'] = recharge_upstream_url_explicit
    if config_name == 'production':
        app.config['SECRET_KEY'] = production_secret
        app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = os.environ['GMAIL_TOKEN_ENCRYPTION_KEY']
    if config_name != 'testing':
        app.config['ADMIN_PASSWORD'] = admin_password
    
    # 初始化扩展
    db.init_app(app)
    from app.services.request_security import TrustedProxyMiddleware
    app.wsgi_app = TrustedProxyMiddleware(app.wsgi_app, app.config['TRUSTED_PROXY_CIDRS'])
    
    # 注册蓝图
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.recharge import recharge_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(recharge_bp, url_prefix='/api/recharge')

    @app.after_request
    def protect_response(response):
        if request.path.startswith(('/api/', '/health/')):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if config_name == 'production':
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "font-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'self'; form-action 'self'"
            )
        return response
    
    # 创建数据库表
    with app.app_context():
        from app.models.recharge_task import RechargeTask
        from app.models.runtime_job import RuntimeJob, RuntimeState
        from app.models.gmail_execution import GmailExecution
        if app.config['AUTO_CREATE_DB']:
            db.create_all()
    
    return app
