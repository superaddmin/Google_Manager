from app import db


class RechargeOperation(db.Model):
    __tablename__ = 'recharge_operations'

    task_no = db.Column(db.String(64), db.ForeignKey('recharge_tasks.task_no'), primary_key=True)
    active_key = db.Column(db.String(64), unique=True, nullable=True)
    # 非空远端任务号只能绑定一个本地幂等任务；数据库约束负责跨进程竞态。
    upstream_task_no = db.Column(db.String(128), nullable=True, unique=True, index=True)
