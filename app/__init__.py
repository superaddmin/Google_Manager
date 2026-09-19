"""
Flask 应用工厂模块
创建和配置 Flask 应用实例
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import os

# 初始化数据库扩展
db = SQLAlchemy()


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
    if config_name == 'production' and admin_password.strip() in {'admin', 'admin123', 'ChangeMeStrongPassword123!', 'YourComplexPassword_2026!'}:
        raise RuntimeError('生产环境禁止使用默认示例 ADMIN_PASSWORD')
    if config_name == 'production':
        recharge_mode = os.environ.get('RECHARGE_MODE', 'disabled').lower().strip()
        if recharge_mode == 'mock':
            raise RuntimeError('生产环境禁止启用 mock 充值模式')
        if recharge_mode not in {'disabled', 'live'}:
            raise RuntimeError(f'生产环境不支持未知的 RECHARGE_MODE: {recharge_mode}')
    app.config.from_object(config[config_name])
    if config_name == 'production':
        app.config['SECRET_KEY'] = production_secret
    if config_name != 'testing':
        app.config['ADMIN_PASSWORD'] = admin_password
    
    # 初始化扩展
    db.init_app(app)
    CORS(app)  # 开发阶段允许跨域
    
    # 注册蓝图
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.recharge import recharge_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(recharge_bp, url_prefix='/api/recharge')
    
    # 创建数据库表
    with app.app_context():
        from app.models.recharge_task import RechargeTask
        db.create_all()
    
    return app
