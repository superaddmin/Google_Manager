from flask import current_app, jsonify, request

from app import db
from app.models.account import Account
from app.models.runtime_job import RuntimeJob
from app.services.googlemail_service import googlemail_tasks, normalize_googlemail_options
from app.services.runtime_queue import RuntimeQueue, QueueConflict
from app.services.gmail_service import GmailServiceError


def reply(data=None, message='操作成功', status=200):
    return jsonify(success=status < 400, data=data, message=message), status


def queued_task_request():
    if current_app.config['BACKGROUND_TASK_MODE'] != 'queue':
        return None
    endpoint = request.endpoint
    supported = {'api.start_googlemail_task', 'api.get_googlemail_status', 'api.get_googlemail_task',
                 'api.cancel_googlemail_task', 'api.gmail_batch_authorize', 'api.gmail_batch_authorize_status',
                 'api.gmail_batch_authorize_cancel', 'api.gmail_daemon_status', 'api.gmail_daemon_start',
                 'api.gmail_daemon_stop', 'api.gmail_daemon_sync_now'}
    if endpoint not in supported:
        return None
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return reply(message='请求数据必须为对象', status=400)
    try:
        kind = 'oauth' if 'batch_authorize' in endpoint else 'googlemail'
        if endpoint in {'api.start_googlemail_task', 'api.gmail_batch_authorize'}:
            if not current_app.config['GOOGLEMAIL_EXECUTION_ENABLED']:
                return reply(message='当前环境已关闭实际执行', status=503)
            identifiers = payload.get('accountIds')
            if (not isinstance(identifiers, list) or not identifiers or len(identifiers) > 500
                    or any(type(identifier) is not int for identifier in identifiers)):
                return reply(message='账号 ID 列表格式错误', status=400)
            identifiers = list(dict.fromkeys(identifiers))
            accounts = Account.query.filter(Account.id.in_(identifiers)).all()
            if len(accounts) != len(identifiers):
                return reply(message='所选账号不存在', status=404)
            googlemail_tasks._serialize_accounts(accounts)
            options = payload.get('options') or {}
            if not isinstance(options, dict):
                return reply(message='任务参数格式错误', status=400)
            if kind == 'googlemail':
                options = normalize_googlemail_options(options)
            job = RuntimeQueue.enqueue(kind, {'accountIds': identifiers, 'options': options}, active_key='account-automation')
            return reply(job.to_dict(), '任务已排队', 201 if kind == 'oauth' else 202)
        if endpoint in {'api.get_googlemail_status', 'api.gmail_batch_authorize_status'}:
            active, latest = RuntimeQueue.latest(kind, True), RuntimeQueue.latest(kind)
            if kind == 'oauth':
                return reply({'active': active.to_dict() if active else None, 'latest': latest.to_dict() if latest else None})
            return reply({**googlemail_tasks.availability(), 'executionEnabled': current_app.config['GOOGLEMAIL_EXECUTION_ENABLED'],
                          'activeTask': active.to_dict() if active else None, 'latestTask': latest.to_dict() if latest else None})
        if endpoint in {'api.get_googlemail_task', 'api.cancel_googlemail_task', 'api.gmail_batch_authorize_cancel'}:
            identifier = request.view_args.get('task_id') if request.view_args else None
            identifier = identifier or payload.get('taskId')
            job = db.session.get(RuntimeJob, identifier) if identifier else RuntimeQueue.latest(kind, True)
            if job is None or job.kind != kind:
                return reply(message='任务不存在', status=404)
            if request.method == 'POST':
                RuntimeQueue.cancel(job)
            return reply(job.to_dict())
        if endpoint in {'api.gmail_daemon_start', 'api.gmail_daemon_stop'}:
            interval = payload.get('intervalSeconds', 180)
            if type(interval) is not int or not 30 <= interval <= 86400:
                return reply(message='收信间隔必须为 30 至 86400 秒', status=400)
            RuntimeQueue.set_state('gmail_daemon', {'enabled': endpoint.endswith('_start'), 'intervalSeconds': interval})
        if endpoint == 'api.gmail_daemon_sync_now':
            job = RuntimeQueue.enqueue('gmail_sync', {}, active_key='gmail-sync')
            return reply(job.to_dict(), '同步任务已排队', 202)
        return reply(RuntimeQueue.daemon_status())
    except QueueConflict as error:
        return reply(message=str(error), status=409)
    except GmailServiceError:
        return reply(message='任务凭据加密未配置，无法排队', status=503)
    except ValueError as error:
        return reply(message=str(error), status=400)
