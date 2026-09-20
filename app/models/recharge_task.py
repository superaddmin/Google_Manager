"""
充值任务持久化数据模型
用于跨 Gunicorn 进程、服务重启持久化充值任务状态
"""
from datetime import datetime
from app import db
from app.services.field_encryption import DeterministicEncryptedText, EncryptedText


class RechargeTask(db.Model):
    """充值任务模型"""
    __tablename__ = 'recharge_tasks'

    id = db.Column(db.Integer, primary_key=True)
    task_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    redeem_code = db.Column(
        DeterministicEncryptedText('recharge-task:redeem-code'),
        nullable=False,
        index=True,
    )
    plan_type = db.Column(db.String(64), nullable=False)
    account_email = db.Column(
        DeterministicEncryptedText('recharge-task:account-email'),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(32), default='processing', nullable=False)  # pending, processing, completed, recalled, closed, failed
    status_text = db.Column(db.String(64), default='任务处理中')
    card_last4 = db.Column(db.String(16), default='8866')
    is_renewal = db.Column(db.Boolean, default=False)
    is_mock = db.Column(db.Boolean, default=False)
    challenge_token = db.Column(db.String(64), nullable=True)
    notify_email = db.Column(EncryptedText(), nullable=True)
    notice = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'task_no': self.task_no,
            'redeem_code': self.redeem_code,
            'plan_type': self.plan_type,
            'account_email': self.account_email,
            'status': self.status,
            'status_text': self.status_text,
            'card_last4': self.card_last4,
            'is_renewal': self.is_renewal,
            'is_mock': self.is_mock,
            'notify_email': self.notify_email,
            'notice': self.notice,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
        }

    def to_public_dict(self):
        """返回匿名查询可见字段，避免以卡密作为 bearer 时泄露邮箱和通知地址。"""
        data = self.to_dict()
        for field in ('redeem_code', 'account_email', 'notify_email'):
            data.pop(field, None)
        return data
