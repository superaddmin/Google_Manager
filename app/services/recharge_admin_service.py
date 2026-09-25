from sqlalchemy import func, or_
from werkzeug.exceptions import NotFound

from app import db
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_mutation_reconciliation import RechargeMutationReconciliation
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_reconciliation import RechargeReconciliation
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeContractError, RechargeService
from app.models.cdk import CdkRedemption


TASK_STATUSES = {'pending', 'processing', 'unknown', 'completed', 'failed', 'recalled', 'closed'}


class RechargeAdminService:
    @staticmethod
    def overview():
        unmanaged = ~CdkRedemption.query.filter(CdkRedemption.task_no == RechargeTask.task_no).exists()
        counts = dict(db.session.query(RechargeTask.status, func.count(RechargeTask.id)).filter(unmanaged).group_by(RechargeTask.status).all())
        return {
            'mode': RechargeService.get_mode(),
            'features': RechargeService.get_features(),
            'total': sum(counts.values()),
            'counts': counts,
            'unknown_operations': RechargeMutation.query.filter_by(state='unknown').count(),
            'pending_review': RechargeTask.query.filter(or_(
                RechargeTask.status == 'unknown',
                RechargeTask.task_no.in_(db.session.query(RechargeMutation.task_no).filter_by(state='unknown')),
            )).count(),
        }

    @staticmethod
    def task_summary(task):
        return {
            **task.to_public_dict(),
            'account_email': task.account_email,
            'redeem_code_last4': task.redeem_code[-4:],
        }

    @classmethod
    def list_tasks(cls, parameters):
        try:
            page = int(parameters.get('page', '1'))
            page_size = int(parameters.get('page_size', '20'))
        except (TypeError, ValueError):
            raise RechargeContractError('分页参数必须是整数') from None
        if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise RechargeContractError('页码须为 1-100000，每页数量须为 1-100')
        status = parameters.get('status', '')
        source = parameters.get('source', '')
        query = parameters.get('q', '').strip()
        if status and status not in TASK_STATUSES:
            raise RechargeContractError('订单状态无效')
        if source not in {'', 'live', 'mock'}:
            raise RechargeContractError('订单来源无效')
        if len(query) > 256 or any(ord(character) < 32 for character in query):
            raise RechargeContractError('查询条件过长或包含无效字符')
        tasks = RechargeTask.query.filter(~CdkRedemption.query.filter(CdkRedemption.task_no == RechargeTask.task_no).exists())
        if status:
            tasks = tasks.filter_by(status=status)
        if source:
            tasks = tasks.filter_by(is_mock=source == 'mock')
        if query:
            tasks = tasks.filter(or_(
                RechargeTask.task_no.contains(query, autoescape=True),
                RechargeTask.account_email.in_({query, query.lower()}),
                RechargeTask.redeem_code == query,
            ))
        total = tasks.count()
        items = tasks.order_by(RechargeTask.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {'items': [cls.task_summary(task) for task in items], 'total': total, 'page': page, 'page_size': page_size}

    @staticmethod
    def task_record(task_no):
        task = RechargeTask.query.filter_by(task_no=task_no).first()
        if task is None:
            raise NotFound('未找到充值订单')
        if CdkRedemption.query.filter_by(task_no=task_no).first():
            raise NotFound('平台卡密订单请使用卡密工作台')
        return task

    @classmethod
    def task_detail(cls, task_no):
        task = cls.task_record(task_no)
        operation = db.session.get(RechargeOperation, task_no)
        mutation = db.session.get(RechargeMutation, task_no)
        reconciliation = RechargeReconciliation.query.filter_by(task_no=task_no).first()
        audits = RechargeMutationReconciliation.query.filter_by(task_no=task_no).order_by(
            RechargeMutationReconciliation.created_at.desc(),
        ).limit(50).all()
        mode = RechargeService.get_mode()
        mutable = mode != 'disabled' and task.is_mock == (mode == 'mock')
        pending_mutation = mutation is not None and mutation.state in {'pending', 'unknown'}
        return {
            'task': cls.task_summary(task),
            'upstream_task_no': operation.upstream_task_no if operation else None,
            'mutation': {name: getattr(mutation, name) for name in (
                'operation_id', 'action', 'state', 'started_at',
            )} if mutation else None,
            'reconciliation': reconciliation.to_dict() if reconciliation else None,
            'mutation_reconciliations': [audit.to_dict() for audit in audits],
            'actions': {
                'refresh': mutable,
                'recall': mutable and not pending_mutation and task.status in {'pending', 'processing'},
                'close': mutable and not pending_mutation and task.status == 'completed',
                'reconcile_task': mode == 'live' and not task.is_mock and not pending_mutation and task.status == 'unknown',
                'reconcile_mutation': mode == 'live' and not task.is_mock and mutation is not None and mutation.state == 'unknown',
            },
        }

    @classmethod
    def perform_action(cls, task_no, action, payload):
        task = cls.task_record(task_no)
        mode = RechargeService.ensure_enabled()
        if task.is_mock != (mode == 'mock'):
            raise RechargeContractError('当前运行模式与订单来源不匹配')
        if action == 'refresh':
            RechargeService.get_task_by_no(task_no)
        else:
            if payload.get('confirmed') is not True:
                raise RechargeContractError('订单操作必须明确确认')
            operation_payload = {
                'task_no': task_no, 'redeem_code': task.redeem_code,
                'email': task.account_email, 'confirmed': True,
            }
            if action == 'recall':
                RechargeService.recall_task(operation_payload)
            elif action == 'close':
                RechargeService.close_task(operation_payload)
            else:
                raise RechargeContractError('不支持的订单操作')
        return cls.task_detail(task_no)
