"""
Flask 应用配置模块
包含开发、生产和测试环境的配置
"""
import os
import secrets
from datetime import timedelta

# 获取项目根目录
basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


class Config:
    """基础配置类"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    GOOGLEMAIL_EXECUTION_ENABLED = True
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GMAIL_CLIENT_SECRET_FILE = os.environ.get('GMAIL_CLIENT_SECRET_FILE')
    GMAIL_TOKEN_ENCRYPTION_KEY = os.environ.get('GMAIL_TOKEN_ENCRYPTION_KEY')
    GMAIL_PUBSUB_TOPIC = os.environ.get('GMAIL_PUBSUB_TOPIC')
    GMAIL_PUBSUB_VERIFICATION_TOKEN = os.environ.get('GMAIL_PUBSUB_VERIFICATION_TOKEN')
    
    # 数据库配置
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'accounts.db')


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SESSION_COOKIE_SECURE = True
    

class TestingConfig(Config):
    """测试环境配置"""
    TESTING = True
    ADMIN_PASSWORD = 'admin123'
    GOOGLEMAIL_EXECUTION_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


# 配置映射
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
