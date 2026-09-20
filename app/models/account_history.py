"""
账号历史记录模型
记录账号字段的修改历史
"""
from app import db
from datetime import datetime
from sqlalchemy import event

from app.services.field_encryption import decrypt_value, encrypt_value, is_encrypted


SENSITIVE_HISTORY_FIELDS = {'password', 'secret', 'recovery'}


class AccountHistory(db.Model):
    """账号修改历史记录"""
    __tablename__ = 'account_history'
    
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False)
    field_name = db.Column(db.String(50), nullable=False)  # 字段名：password/secret/recovery
    old_value = db.Column(db.Text)  # 修改前的值
    new_value = db.Column(db.Text)  # 修改后的值
    changed_at = db.Column(db.DateTime, default=datetime.now)  # 修改时间
    
    # 关联账号
    account = db.relationship(
        'Account',
        backref=db.backref('history', lazy='dynamic', cascade='all, delete-orphan')
    )
    
    def to_dict(self):
        """转换为字典"""
        old_value = decrypt_value(self.old_value) if self.field_name in SENSITIVE_HISTORY_FIELDS else self.old_value
        new_value = decrypt_value(self.new_value) if self.field_name in SENSITIVE_HISTORY_FIELDS else self.new_value
        return {
            'id': self.id,
            'accountId': self.account_id,
            'fieldName': self.field_name,
            'oldValue': '[已隐藏]' if self.field_name in {'password', 'secret'} else old_value,
            'newValue': '[已隐藏]' if self.field_name in {'password', 'secret'} else new_value,
            'changedAt': self.changed_at.strftime('%Y-%m-%d %H:%M:%S') if self.changed_at else None
        }
    
    @staticmethod
    def get_field_display_name(field_name):
        """获取字段的中文显示名称"""
        names = {
            'password': '密码',
            'secret': '2FA密钥',
            'recovery': '恢复邮箱',
            'status': '状态',
            'sold_status': '出售状态',
            'security_action': '安全应急处置',
            'gmail_oauth': 'Gmail授权'
        }
        return names.get(field_name, field_name)


@event.listens_for(AccountHistory, 'before_insert')
@event.listens_for(AccountHistory, 'before_update')
def protect_sensitive_history_values(mapper, connection, target):
    if target.field_name not in SENSITIVE_HISTORY_FIELDS:
        return
    if not is_encrypted(target.old_value):
        target.old_value = encrypt_value(target.old_value)
    if not is_encrypted(target.new_value):
        target.new_value = encrypt_value(target.new_value)
