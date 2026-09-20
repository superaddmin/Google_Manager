"""
账号数据模型
定义谷歌账号的数据库结构
"""
from datetime import datetime, timezone
from app import db
from app.services.field_encryption import EncryptedText


def utc_now():
    """返回与现有 SQLite 字段兼容的 UTC 无时区时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(db.Model):
    """
    谷歌账号模型
    
    Attributes:
        id: 主键ID
        email: 谷歌邮箱账号
        password: 登录密码
        recovery: 恢复邮箱
        secret: 2FA TOTP 密钥
        remark: 备注信息
        status: 状态 (pro/inactive)
        sold_status: 出售状态 (sold/unsold)
        created_at: 创建时间
        updated_at: 更新时间
    """
    __tablename__ = 'accounts'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password = db.Column(EncryptedText(), nullable=False)
    recovery = db.Column(EncryptedText(), nullable=True)
    secret = db.Column(EncryptedText(), nullable=True)
    remark = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='inactive')
    sold_status = db.Column(db.String(20), default='unsold')  # 出售状态: sold/unsold
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    
    def to_dict(self):
        """
        将模型转换为字典
        
        Returns:
            包含所有字段的字典
        """
        return {
            'id': self.id,
            'email': self.email,
            'password': '' if self.status == 'locked' else self.password,
            'recovery': '' if self.status == 'locked' else self.recovery or '',
            'secret': '' if self.status == 'locked' else self.secret or '',
            'remark': self.remark or '',
            'status': self.status,
            'soldStatus': self.sold_status or 'unsold',
            'createdAt': self.created_at.strftime('%Y-%m-%d') if self.created_at else ''
        }
    
    def __repr__(self):
        return f'<Account {self.email}>'
