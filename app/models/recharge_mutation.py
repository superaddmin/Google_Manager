import time
import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app import db


class RechargeMutation(db.Model):
    __tablename__ = 'recharge_mutations'

    task_no = db.Column(db.String(64), primary_key=True)
    action = db.Column(db.String(20), nullable=False)
    operation_id = db.Column(db.String(32), nullable=False)
    state = db.Column(db.String(20), nullable=False)
    started_at = db.Column(db.Float, nullable=False)

    @classmethod
    def claim(cls, task, action, expected_status=None):
        from app.models.recharge_task import RechargeTask
        from app.services.recharge_service import RechargeContractError
        if action not in {'recall', 'close'}:
            raise RechargeContractError('不支持的任务操作')
        expected_status = expected_status or task.status
        if not isinstance(expected_status, str) or not expected_status:
            raise RechargeContractError('任务状态无效，请重新查询')

        # 先用条件更新确认读取到的任务状态仍然有效；真正的互斥由下方
        # mutation 行的 done -> pending 原子转换完成，避免两个进程同时放行。
        updated = RechargeTask.query.filter_by(id=task.id, status=expected_status).update(
            {'updated_at': datetime.utcnow()}, synchronize_session=False)
        if updated != 1:
            db.session.rollback()
            raise RechargeContractError('任务状态已变化，请重新查询')

        operation_id = uuid.uuid4().hex
        now = time.time()
        try:
            record = db.session.get(cls, task.task_no)
            if record is None:
                # task_no 是主键，两个进程首次领取时只有一个 INSERT 能成功。
                record = cls(task_no=task.task_no, action=action,
                             operation_id=operation_id, state='pending', started_at=now)
                db.session.add(record)
                db.session.flush()
            else:
                # 只有已完成的上一次操作允许开启下一次；pending/unknown 必须先对账。
                claimed = cls.query.filter(
                    cls.task_no == task.task_no,
                    cls.state == 'done',
                ).update({
                    'action': action,
                    'operation_id': operation_id,
                    'state': 'pending',
                    'started_at': now,
                }, synchronize_session=False)
                if claimed != 1:
                    db.session.rollback()
                    raise RechargeContractError('已有操作正在执行或等待对账，请勿重复操作')
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise RechargeContractError('已有操作正在执行，请稍后查询') from error
        return operation_id

    @classmethod
    def finish(cls, task_no, operation_id, state):
        source_states = {
            'unknown': ('pending',),
            'done': ('pending', 'unknown'),
        }.get(state)
        if source_states is None:
            raise ValueError('unsupported recharge mutation state')
        return cls.query.filter_by(
            task_no=task_no,
            operation_id=operation_id,
        ).filter(
            cls.state.in_(source_states),
        ).update({'state': state}) == 1

    @classmethod
    def recover_expired(cls):
        cls.query.filter(cls.state == 'pending', cls.started_at < time.time() - 30).update({'state': 'unknown'})
        db.session.commit()
