"""
API 路由模块
提供账号管理的 RESTful API
"""
import csv
import base64
import binascii
import hmac
import io
import json
from datetime import datetime

from flask import Blueprint, request, jsonify, session, current_app, send_file
from app import db
from app.models.account import Account
from app.services.account_service import AccountService
from app.services.auth_service import AuthService
from app.services.googlemail_service import (
    GooglemailTaskError,
    GooglemailValidationError,
    googlemail_tasks,
)
from app.models.account_history import AccountHistory
from app.models.googlemail_task import GooglemailTask
from app.models.gmail_connection import GmailConnection
from app.models.gmail_rule import GmailRule
from app.services.gmail_service import GmailService, GmailServiceError, OAuthStateManager
from app.services.gmail_rule_service import GmailRuleService, GmailRuleServiceError
from app.services.security_service import SecurityService
from app.services.batch_oauth_service import batch_oauth_manager, BatchOAuthError
from app.services.email_poller import gmail_sync_daemon

api_bp = Blueprint('api', __name__)



@api_bp.route('/gmail/oauth/start', methods=['GET'])
def gmail_oauth_start():
    try:
        authorization_url, state = GmailService.authorization_url()
        session['gmail_oauth_state'] = state
        return success_response({'authorizationUrl': authorization_url})
    except GmailServiceError as error:
        return error_response(str(error), 503)


@api_bp.route('/gmail/oauth/callback', methods=['GET'])
def gmail_oauth_callback():
    state_param = request.args.get('state')
    session_state = session.pop('gmail_oauth_state', None)

    if session_state and session_state != state_param:
        return error_response('Gmail OAuth 状态无效，请重新授权', 400)
    if not OAuthStateManager.consume(state_param):
        return error_response('Gmail OAuth 状态无效，请重新授权', 400)
    if request.args.get('error'):
        return error_response('用户取消了 Gmail 授权', 400)
    if not request.args.get('code'):
        return error_response('Gmail OAuth 缺少授权码', 400)
    try:
        connection = GmailService.complete_authorization(request.args.get('code'), state_param)
        return success_response(connection.to_dict(), 'Gmail 授权成功，请返回管理页面')
    except Exception as error:
        return server_error_response('Gmail 授权失败，请稍后重试', error)


@api_bp.route('/gmail/connections', methods=['GET'])
def gmail_connections():
    return success_response([
        item.to_dict() for item in GmailConnection.query.order_by(GmailConnection.email).all()
    ])


def _gmail_connection(connection_id):
    connection = db.session.get(GmailConnection, connection_id)
    if not connection:
        raise GmailServiceError('Gmail 账号不存在')
    return connection


@api_bp.route('/gmail/<int:connection_id>/messages', methods=['GET'])
def gmail_messages(connection_id):
    try:
        connection = _gmail_connection(connection_id)
        query = request.args.get('q', '').strip()
        if len(query) > 500:
            return error_response('Gmail 查询语句最多 500 个字符')
        max_results = min(max(int(request.args.get('maxResults', 20)), 1), 50)
        return success_response(GmailService.list_messages(
            connection, query, request.args.get('pageToken'), max_results
        ))
    except (ValueError, GmailServiceError) as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('加载 Gmail 收件箱失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/messages/<message_id>', methods=['GET'])
def gmail_message(connection_id, message_id):
    try:
        return success_response(GmailService.get_message(_gmail_connection(connection_id), message_id))
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('加载 Gmail 邮件失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/messages/<message_id>/read', methods=['PATCH'])
def gmail_mark_read(connection_id, message_id):
    try:
        return success_response(GmailService.modify_message(
            _gmail_connection(connection_id), message_id, remove=['UNREAD']
        ))
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('标记邮件失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/messages/<message_id>/archive', methods=['PATCH'])
def gmail_archive(connection_id, message_id):
    try:
        return success_response(GmailService.modify_message(
            _gmail_connection(connection_id), message_id, remove=['INBOX']
        ))
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('归档邮件失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/labels', methods=['GET'])
def gmail_labels(connection_id):
    try:
        return success_response(GmailService.list_labels(_gmail_connection(connection_id)))
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('加载 Gmail 标签失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/messages/<message_id>/labels', methods=['PATCH'])
def gmail_update_labels(connection_id, message_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')
    add_labels = data.get('addLabelIds', [])
    remove_labels = data.get('removeLabelIds', [])
    if not all(isinstance(labels, list) for labels in (add_labels, remove_labels)):
        return error_response('标签列表格式错误')
    if any(
        not isinstance(label, str) or not label.strip()
        for label in [*add_labels, *remove_labels]
    ):
        return error_response('标签 ID 必须是非空字符串')
    if len(add_labels) > 50 or len(remove_labels) > 50:
        return error_response('单次最多修改 50 个标签')
    try:
        result = GmailService.modify_message(
            _gmail_connection(connection_id),
            message_id,
            add=list(dict.fromkeys(label.strip() for label in add_labels)),
            remove=list(dict.fromkeys(label.strip() for label in remove_labels)),
        )
        return success_response(result, '邮件标签已更新')
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('更新 Gmail 标签失败，请稍后重试', error)


@api_bp.route('/gmail/<int:connection_id>/watch', methods=['POST'])
def gmail_watch(connection_id):
    try:
        return success_response(
            GmailService.watch(_gmail_connection(connection_id)).to_dict(),
            'Gmail 实时通知已启用',
        )
    except GmailServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('启用 Gmail 实时通知失败，请稍后重试', error)


def _gmail_rule(rule_id, connection_id=None):
    rule = db.session.get(GmailRule, rule_id)
    if not rule or (connection_id is not None and rule.connection_id != connection_id):
        raise GmailRuleServiceError('Gmail 规则不存在')
    return rule


@api_bp.route('/gmail/<int:connection_id>/rules', methods=['GET', 'POST'])
def gmail_rules(connection_id):
    try:
        connection = _gmail_connection(connection_id)
    except GmailServiceError as error:
        return error_response(str(error), 404)
    if request.method == 'GET':
        rules = GmailRule.query.filter_by(connection_id=connection.id).order_by(
            GmailRule.priority.asc(), GmailRule.id.asc()
        ).all()
        return success_response([rule.to_dict() for rule in rules])
    try:
        return success_response(
            GmailRuleService.create_rule(connection, request.get_json(silent=True)).to_dict(),
            'Gmail 规则已创建',
        )
    except GmailRuleServiceError as error:
        return error_response(str(error))


@api_bp.route('/gmail/rules/<int:rule_id>', methods=['PATCH', 'DELETE'])
def gmail_rule_detail(rule_id):
    try:
        rule = _gmail_rule(rule_id)
        if request.method == 'DELETE':
            GmailRuleService.delete_rule(rule)
            return success_response(message='Gmail 规则已删除')
        return success_response(
            GmailRuleService.update_rule(rule, request.get_json(silent=True)).to_dict(),
            'Gmail 规则已更新',
        )
    except GmailRuleServiceError as error:
        return error_response(str(error), 400)


@api_bp.route('/gmail/<int:connection_id>/rules/run', methods=['POST'])
def run_gmail_rules(connection_id):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')
    message_ids = data.get('messageIds')
    if message_ids is not None:
        if (
            not isinstance(message_ids, list)
            or not message_ids
            or len(message_ids) > 100
            or any(not isinstance(message_id, str) or not message_id.strip() for message_id in message_ids)
        ):
            return error_response('messageIds 格式错误')
        message_ids = list(dict.fromkeys(message_id.strip() for message_id in message_ids))
    try:
        max_messages = data.get('maxMessages', 50)
        dry_run = data.get('dryRun', False)
        if not isinstance(dry_run, bool):
            return error_response('dryRun 必须是布尔值')
        summary = GmailRuleService.run_rules(
            _gmail_connection(connection_id),
            message_ids=message_ids,
            max_messages=max_messages,
            dry_run=dry_run,
        )
        return success_response(summary, 'Gmail 规则执行完成')
    except (GmailRuleServiceError, GmailServiceError, ValueError) as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('执行 Gmail 规则失败，请稍后重试', error)


@api_bp.route('/gmail/task-logs', methods=['GET'])
def gmail_task_logs():
    connection_id = request.args.get('connectionId', type=int)
    limit = request.args.get('limit', default=100, type=int)
    try:
        logs = GmailRuleService.list_logs(
            connection_id=connection_id,
            status=request.args.get('status'),
            limit=limit,
        )
        return success_response([log.to_dict() for log in logs])
    except Exception as error:
        return server_error_response('获取 Gmail 任务日志失败，请稍后重试', error)


def _gmail_task_log(log_id):
    log = GmailRuleService.get_log(log_id)
    if not log:
        raise GmailRuleServiceError('Gmail 任务日志不存在')
    return log


@api_bp.route('/gmail/task-logs/<int:log_id>', methods=['GET'])
def gmail_task_log_detail(log_id):
    try:
        return success_response(_gmail_task_log(log_id).to_dict())
    except GmailRuleServiceError as error:
        return error_response(str(error), 404)


def _confirmation_payload():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise GmailRuleServiceError('请求数据格式错误')
    reviewer = data.get('reviewer') or 'admin'
    note = data.get('note')
    if not isinstance(reviewer, str) or not isinstance(note, (str, type(None))):
        raise GmailRuleServiceError('确认信息格式错误')
    return reviewer, note


@api_bp.route('/gmail/task-logs/<int:log_id>/confirm', methods=['POST'])
def confirm_gmail_task_log(log_id):
    try:
        reviewer, note = _confirmation_payload()
        return success_response(
            GmailRuleService.confirm_log(_gmail_task_log(log_id), reviewer, note).to_dict(),
            'Gmail 任务已确认并执行',
        )
    except GmailRuleServiceError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('确认 Gmail 任务失败，请稍后重试', error)


@api_bp.route('/gmail/task-logs/<int:log_id>/reject', methods=['POST'])
def reject_gmail_task_log(log_id):
    try:
        reviewer, note = _confirmation_payload()
        return success_response(
            GmailRuleService.reject_log(_gmail_task_log(log_id), reviewer, note).to_dict(),
            'Gmail 任务已拒绝',
        )
    except GmailRuleServiceError as error:
        return error_response(str(error), 400)


@api_bp.route('/gmail/pubsub/webhook', methods=['POST'])
def gmail_pubsub_webhook():
    expected_token = current_app.config.get('GMAIL_PUBSUB_VERIFICATION_TOKEN')
    provided_token = request.args.get('token', '')
    if expected_token and not hmac.compare_digest(provided_token, expected_token):
        return error_response('Pub/Sub 校验令牌无效', 401)
    if not expected_token and current_app.config.get('TESTING') is not True:
        return error_response('服务端未配置 Pub/Sub 校验令牌', 503)

    envelope = request.get_json(silent=True)
    if envelope is None:
        envelope = {}
    message = envelope.get('message') if isinstance(envelope, dict) else None
    encoded_data = message.get('data') if isinstance(message, dict) else None
    if not encoded_data:
        return success_response({'processed': False, 'reason': 'empty_message'})
    try:
        padding = '=' * (-len(encoded_data) % 4)
        data = json.loads(base64.urlsafe_b64decode(encoded_data + padding).decode('utf-8'))
        if not isinstance(data, dict):
            return error_response('Pub/Sub 消息数据格式错误', 400)
        email = data.get('emailAddress')
        history_id = data.get('historyId')
        if not isinstance(email, str) or not email.strip() or not history_id:
            return error_response('Pub/Sub 消息缺少 Gmail 标识', 400)
        result = GmailService.process_notification(email.strip(), str(history_id))
        return success_response(
            {'processed': result is not None, 'result': result},
            'Pub/Sub 通知已处理',
        )
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        GmailServiceError,
    ) as error:
        return error_response(f'Pub/Sub 消息处理失败: {error}', 400)
    except Exception as error:
        return server_error_response('处理 Gmail Pub/Sub 通知失败，请稍后重试', error)


def get_client_ip():
    """获取客户端真实 IP"""
    return request.remote_addr or '127.0.0.1'


def success_response(data=None, message='操作成功'):
    """成功响应格式"""
    return jsonify({
        'success': True,
        'data': data,
        'message': message
    })


def error_response(message='操作失败', code=400):
    """错误响应格式"""
    return jsonify({
        'success': False,
        'data': None,
        'message': message
    }), code


def server_error_response(message, error):
    db.session.rollback()
    current_app.logger.error('%s (%s)', message, type(error).__name__)
    return error_response(message, 500)


@api_bp.before_request
def require_authentication():
    """账号接口必须通过管理员登录会话访问。"""
    public_endpoints = {'api.login', 'api.logout', 'api.check_auth', 'api.gmail_pubsub_webhook', 'api.gmail_oauth_callback'}
    if request.endpoint not in public_endpoints and not session.get('authenticated'):
        return error_response('请先登录', 401)


@api_bp.route('/accounts', methods=['GET'])
def get_accounts():
    """
    获取所有账号列表

    Query Params:
        search: 搜索关键词（可选）

    Returns:
        账号列表
    """
    search = request.args.get('search', '')
    accounts = AccountService.get_all_accounts(search)
    return success_response(data=accounts)


EXPORT_FORMATS = ('csv', 'txt', 'json')
EXPORT_CSV_HEADERS = ['邮箱', '密码', '恢复邮箱', '2FA密钥', '备注', '状态', '出售状态', '导入时间']


def _export_filename(extension):
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return f'accounts-{timestamp}.{extension}'


@api_bp.route('/accounts/export', methods=['GET'])
def export_accounts():
    """
    导出账号列表

    Query Params:
        format: 导出格式（csv/txt/json，默认 csv）
        search: 搜索关键词（可选）
        sold: 出售状态筛选 all/sold/unsold（可选）
    """
    export_format = request.args.get('format', 'csv').strip().lower()
    if export_format not in EXPORT_FORMATS:
        return error_response('导出格式必须为 csv、txt 或 json')

    search = request.args.get('search', '')
    sold = request.args.get('sold', 'all')
    accounts = AccountService.get_all_accounts(
        search, sold_status=sold if sold in ('sold', 'unsold') else None
    )

    # 应急锁定账号脱敏：防止导出敏感密码与 2FA 密钥
    export_accounts_data = []
    for acc in accounts:
        item = dict(acc)
        if item.get('status') == 'locked':
            item['password'] = '******'
            item['secret'] = '******'
        export_accounts_data.append(item)

    if export_format == 'json':
        payload = io.BytesIO(
            json.dumps(export_accounts_data, ensure_ascii=False, indent=2).encode('utf-8')
        )
        mimetype = 'application/json'
    elif export_format == 'txt':
        # 与导入格式和 Googlemail 输入格式互通：邮箱----密码----恢复邮箱----2FA密钥
        lines = [
            '----'.join([
                account['email'],
                account['password'],
                account['recovery'],
                account['secret'],
            ])
            for account in export_accounts_data
        ]
        payload = io.BytesIO(('\n'.join(lines) + '\n').encode('utf-8'))
        mimetype = 'text/plain'
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_CSV_HEADERS)
        for account in export_accounts_data:
            writer.writerow([
                account['email'],
                account['password'],
                account['recovery'],
                account['secret'],
                account['remark'],
                account['status'],
                account['soldStatus'],
                account['createdAt'],
            ])
        payload = io.BytesIO(buffer.getvalue().encode('utf-8-sig'))
        mimetype = 'text/csv'

    payload.seek(0)
    return send_file(
        payload,
        mimetype=mimetype,
        as_attachment=True,
        download_name=_export_filename(export_format),
    )


@api_bp.route('/stats', methods=['GET'])
def get_stats():
    """获取账号资产统计信息"""
    try:
        stats = AccountService.get_statistics()
        return success_response(data=stats)
    except Exception as e:
        return server_error_response('获取统计信息失败，请稍后重试', e)


@api_bp.route('/accounts/batch-delete', methods=['POST'])
def batch_delete_accounts():
    """
    批量删除账号

    Request Body:
        accountIds: 账号 ID 数组
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')

    try:
        result = AccountService.batch_delete(data.get('accountIds'))
        return success_response(
            data=result,
            message=f"已删除 {result['deleted_count']} 个账号"
        )
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('批量删除失败，请稍后重试', e)


@api_bp.route('/accounts/batch-sold', methods=['PATCH'])
def batch_set_sold_status():
    """
    批量设置出售状态

    Request Body:
        accountIds: 账号 ID 数组
        status: 目标状态（sold/unsold）
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')

    try:
        result = AccountService.batch_set_sold_status(
            data.get('accountIds'), data.get('status')
        )
        return success_response(
            data=result,
            message=f"已更新 {result['updated_count']} 个账号的出售状态"
        )
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('批量更新出售状态失败，请稍后重试', e)


@api_bp.route('/accounts/batch-remark', methods=['PATCH'])
def batch_set_remark():
    """
    批量设置账号备注

    Request Body:
        accountIds: 账号 ID 数组
        remark: 备注内容
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')

    try:
        result = AccountService.batch_set_remark(
            data.get('accountIds'), data.get('remark')
        )
        return success_response(
            data=result,
            message=f"已更新 {result['updated_count']} 个账号的备注"
        )
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('批量更新备注失败，请稍后重试', e)


@api_bp.route('/accounts', methods=['POST'])
def create_account():
    """
    创建单个账号
    
    Request Body:
        email: 邮箱账号
        password: 登录密码
        recovery: 恢复邮箱
        secret: 2FA 密钥
        remark: 备注
    
    Returns:
        创建的账号信息
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return error_response('请求数据为空')
    
    try:
        account = AccountService.create_account(data)
        return success_response(data=account, message='账号创建成功')
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('创建失败，请稍后重试', e)


@api_bp.route('/accounts/batch', methods=['POST'])
def batch_import():
    """
    批量导入账号
    
    Request Body:
        accounts: 账号列表数组
    
    Returns:
        导入结果统计
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'accounts' not in data:
        return error_response('请求数据格式错误')
    
    accounts = data.get('accounts', [])
    if not isinstance(accounts, list):
        return error_response('导入列表必须为数组')
    if not accounts:
        return error_response('导入列表为空')
    
    try:
        result = AccountService.batch_import(accounts)
        return success_response(
            data=result,
            message=f"成功导入 {result['success_count']} 个账号"
        )
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('批量导入失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>', methods=['PUT'])
def update_account(account_id):
    """
    更新账号信息
    
    Path Params:
        account_id: 账号ID
    
    Request Body:
        email: 邮箱账号
        password: 登录密码
        recovery: 恢复邮箱
        secret: 2FA 密钥
        remark: 备注
    
    Returns:
        更新后的账号信息
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return error_response('请求数据为空')
    
    try:
        account = AccountService.update_account(account_id, data)
        if account is None:
            return error_response('账号不存在', 404)
        return success_response(data=account, message='账号更新成功')
    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        return server_error_response('更新失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """
    删除账号
    
    Path Params:
        account_id: 账号ID
    
    Returns:
        删除结果
    """
    try:
        success = AccountService.delete_account(account_id)
        if not success:
            return error_response('账号不存在', 404)
        return success_response(message='账号已删除')
    except Exception as e:
        return server_error_response('删除失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>/status', methods=['PATCH'])
def toggle_status(account_id):
    """
    切换账号状态
    
    Path Params:
        account_id: 账号ID
    
    Returns:
        更新后的账号信息
    """
    try:
        account = AccountService.toggle_status(account_id)
        if account is None:
            return error_response('账号不存在', 404)
        return success_response(data=account, message='状态已更新')
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return server_error_response('状态更新失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>/sold', methods=['PATCH'])
def toggle_sold_status(account_id):
    """
    切换账号出售状态
    
    Path Params:
        account_id: 账号ID
    
    Returns:
        更新后的账号信息
    """
    try:
        account = AccountService.toggle_sold_status(account_id)
        if account is None:
            return error_response('账号不存在', 404)
        return success_response(data=account, message='出售状态已更新')
    except Exception as e:
        return server_error_response('出售状态更新失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>/2fa', methods=['GET'])
def get_2fa_code(account_id):
    """
    获取账号的 2FA 验证码
    
    Path Params:
        account_id: 账号ID
    
    Returns:
        当前的 TOTP 验证码和剩余有效时间
    """
    try:
        result = AccountService.get_2fa_code(account_id)
        if result is None:
            return error_response('账号不存在或未配置 2FA 密钥', 404)
        return success_response(data=result)
    except ValueError as e:
        return error_response(str(e), 403)
    except Exception as e:
        return server_error_response('获取验证码失败，请稍后重试', e)


@api_bp.route('/accounts/<int:account_id>/history', methods=['GET'])
def get_account_history(account_id):
    """
    获取账号修改历史记录
    
    Path Params:
        account_id: 账号ID
    
    Returns:
        账号的修改历史列表
    """
    try:
        # 获取该账号的所有历史记录，按时间倒序
        if db.session.get(Account, account_id) is None:
            return error_response('账号不存在', 404)
        history = AccountHistory.query.filter_by(account_id=account_id)\
            .order_by(AccountHistory.changed_at.desc()).all()
        
        return success_response(data=[h.to_dict() for h in history])
    except Exception as e:
        return server_error_response('获取历史记录失败，请稍后重试', e)


@api_bp.route('/googlemail/status', methods=['GET'])
def get_googlemail_status():
    """返回 Googlemail 运行能力和当前任务。"""
    status = googlemail_tasks.availability()
    status['executionEnabled'] = current_app.config['GOOGLEMAIL_EXECUTION_ENABLED']
    status['activeTask'] = googlemail_tasks.active_task()
    status['latestTask'] = googlemail_tasks.latest_task()
    return success_response(data=status)


@api_bp.route('/googlemail/tasks', methods=['GET'])
def list_googlemail_tasks():
    """获取已结束的 Googlemail 任务历史。"""
    try:
        limit = request.args.get('limit', 20, type=int)
        limit = max(1, min(limit or 20, 100))
        rows = GooglemailTask.query.order_by(
            GooglemailTask.created_at.desc()
        ).limit(limit).all()
        return success_response(data=[row.to_dict() for row in rows])
    except Exception as e:
        return server_error_response('获取 Googlemail 任务历史失败，请稍后重试', e)


@api_bp.route('/googlemail/tasks', methods=['POST'])
def start_googlemail_task():
    """从主页选择的账号创建 Googlemail 任务。"""
    if not current_app.config['GOOGLEMAIL_EXECUTION_ENABLED']:
        return error_response('当前环境已关闭 Googlemail 实际执行', 503)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('请求数据格式错误')
    account_ids = data.get('accountIds')
    if (
        not isinstance(account_ids, list)
        or not account_ids
        or len(account_ids) > 500
        or any(not isinstance(account_id, int) or isinstance(account_id, bool) for account_id in account_ids)
    ):
        return error_response('账号 ID 列表格式错误')

    ordered_ids = list(dict.fromkeys(account_ids))
    account_map = {
        account.id: account
        for account in Account.query.filter(Account.id.in_(ordered_ids)).all()
    }
    if len(account_map) != len(ordered_ids):
        return error_response('所选账号包含不存在的记录', 404)
    accounts = [account_map[account_id] for account_id in ordered_ids]

    try:
        task = googlemail_tasks.start_task(
            current_app._get_current_object(),
            accounts,
            data.get('options'),
        )
        return success_response(data=task, message='Googlemail 任务已启动'), 202
    except GooglemailValidationError as error:
        return error_response(str(error))
    except GooglemailTaskError as error:
        error_code = str(error)
        messages = {
            'TASK_ALREADY_RUNNING': '已有 Googlemail 任务正在运行',
            'NODE_NOT_FOUND': '本机未找到 Node.js',
            'ENTRY_NOT_FOUND': 'Googlemail 启动入口不存在',
            'DEPENDENCIES_NOT_INSTALLED': 'Googlemail 依赖尚未安装',
        }
        code = 409 if error_code == 'TASK_ALREADY_RUNNING' else 503
        return error_response(messages.get(error_code, 'Googlemail 任务启动失败'), code)


@api_bp.route('/googlemail/tasks/<task_id>', methods=['GET'])
def get_googlemail_task(task_id):
    """查询 Googlemail 任务状态。"""
    task = googlemail_tasks.get_task(task_id)
    if not task:
        return error_response('Googlemail 任务不存在', 404)
    return success_response(data=task)


@api_bp.route('/googlemail/tasks/<task_id>/cancel', methods=['POST'])
def cancel_googlemail_task(task_id):
    """取消正在运行的 Googlemail 任务。"""
    task = googlemail_tasks.cancel_task(task_id)
    if not task:
        return error_response('Googlemail 任务不存在', 404)
    return success_response(data=task, message='Googlemail 任务已请求取消')


@api_bp.route('/auth/login', methods=['POST'])
def login():
    """
    管理员登录验证
    
    Request Body:
        password: 管理员密码
    
    Returns:
        登录结果
    """
    client_ip = get_client_ip()
    
    # 检查 IP 是否被封禁
    is_banned, remaining = AuthService.is_ip_banned(client_ip)
    if is_banned:
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        return error_response(f'您的 IP 已被封禁，剩余时间：{hours}小时{minutes}分钟', 403)
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'password' not in data:
        return error_response('请输入密码')
    
    password = data.get('password', '')
    salt = data.get('salt', '')
    
    # 验证盐值
    if not salt or not AuthService.verify_salt(salt):
        return error_response('安全验证失败，请刷新页面重试', 400)
    
    if AuthService.verify_password(password, current_app.config['ADMIN_PASSWORD']):
        # 登录成功，清除失败记录
        AuthService.clear_failed_attempts(client_ip)
        session.clear()
        session.permanent = True
        session['authenticated'] = True
        return success_response(message='登录成功')
    else:
        # 登录失败，记录尝试
        is_now_banned, remaining_attempts = AuthService.record_failed_attempt(client_ip)
        
        if is_now_banned:
            return error_response('密码错误次数过多，您的 IP 已被封禁 24 小时', 403)
        else:
            return error_response(f'密码错误，还剩 {remaining_attempts} 次尝试机会', 401)


@api_bp.route('/auth/logout', methods=['POST'])
def logout():
    """清除管理员登录会话。"""
    session.clear()
    return success_response(message='已退出登录')


@api_bp.route('/auth/check', methods=['GET'])
def check_auth():
    """
    检查 IP 是否被封禁（前端用于显示登录页时检查）
    
    Returns:
        封禁状态
    """
    client_ip = get_client_ip()
    is_banned, remaining = AuthService.is_ip_banned(client_ip)
    
    if is_banned:
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        return jsonify({
            'success': False,
            'banned': True,
            'authenticated': bool(session.get('authenticated')),
            'message': f'您的 IP 已被封禁，剩余时间：{hours}小时{minutes}分钟'
        })
    
    return jsonify({
        'success': True,
        'banned': False,
        'authenticated': bool(session.get('authenticated'))
    })


# ==================== 集中邮箱安全与防盗 API ====================

@api_bp.route('/security/overview', methods=['GET'])
def get_security_overview():
    """获取集中邮箱安全与防盗态势总览"""
    try:
        data = SecurityService.get_security_overview()
        return success_response(data=data)
    except Exception as e:
        return server_error_response('获取安全态势失败，请稍后重试', e)


@api_bp.route('/security/accounts', methods=['GET'])
def get_security_accounts():
    """获取带安全评级与风险维度的账号列表"""
    try:
        filter_level = request.args.get('filter', 'all')
        data = SecurityService.get_security_accounts(filter_level)
        return success_response(data=data)
    except Exception as e:
        return server_error_response('获取安全账号列表失败，请稍后重试', e)


@api_bp.route('/security/forwarding-audit', methods=['GET'])
def get_forwarding_audit():
    """扫描所有已授权 Gmail 账号的隐蔽外部转发和过滤规则"""
    try:
        data = SecurityService.audit_all_forwarding_rules()
        return success_response(data=data)
    except Exception as e:
        return server_error_response('扫描转发规则失败，请稍后重试', e)


@api_bp.route('/security/central-otps', methods=['GET'])
def get_central_otps():
    """跨所有已连接邮箱集中获取最新验证码与安全告警"""
    try:
        limit = request.args.get('limit', 10, type=int)
        limit = max(1, min(limit or 10, 50))
        data = SecurityService.get_central_otps_and_alerts(limit_per_mailbox=limit)
        return success_response(data=data)
    except Exception as e:
        return server_error_response('集中获取验证码失败，请稍后重试', e)


@api_bp.route('/security/accounts/<int:account_id>/lock', methods=['POST'])
def lock_account(account_id):
    """一键应急锁号，阻断导出并标记防盗保护"""
    try:
        payload = request.get_json(silent=True) or {}
        reason = payload.get('reason', '管理员手动触发应急锁定')
        account = SecurityService.lock_account(account_id, reason)
        risk_info = SecurityService.calculate_account_risk(account)
        return success_response(data=risk_info, message=f'账号 {account.email} 已被应急锁定保护')
    except ValueError as e:
        return error_response(str(e), 404)
    except Exception as e:
        return server_error_response('应急锁定失败，请稍后重试', e)


@api_bp.route('/security/accounts/<int:account_id>/unlock', methods=['POST'])
def unlock_account(account_id):
    """解除账号的应急锁定状态"""
    try:
        account = SecurityService.unlock_account(account_id)
        risk_info = SecurityService.calculate_account_risk(account)
        return success_response(data=risk_info, message=f'账号 {account.email} 已解除锁定')
    except ValueError as e:
        return error_response(str(e), 404)
    except Exception as e:
        return server_error_response('解除锁定失败，请稍后重试', e)


# ---------------------------------------------------------------------------
# 批量 Google OAuth 2.0 自动授权接口
# ---------------------------------------------------------------------------

@api_bp.route('/gmail/batch-authorize', methods=['POST'])
def gmail_batch_authorize():
    """启动所选账号的批量 OAuth 2.0 自动授权任务"""
    data = request.get_json(silent=True) or {}
    account_ids = data.get('accountIds') or []
    options = data.get('options') or {}
    if not account_ids or not isinstance(account_ids, list):
        return error_response('请至少提供一个待授权的账号 ID 列表')
    try:
        record = batch_oauth_manager.start_batch(
            current_app._get_current_object(),
            account_ids,
            options=options,
        )
        return success_response(data=record.to_dict(), message='批量 OAuth 自动授权任务已启动'), 201
    except BatchOAuthError as error:
        return error_response(str(error), 400)
    except Exception as error:
        return server_error_response('启动批量授权失败，请稍后重试', error)


@api_bp.route('/gmail/batch-authorize/status', methods=['GET'])
def gmail_batch_authorize_status():
    """获取当前或最新的批量授权任务进度与日志"""
    active = batch_oauth_manager.active_task()
    latest = batch_oauth_manager.latest_task()
    return success_response(data={
        'active': active.to_dict() if active else None,
        'latest': latest.to_dict() if latest else None,
    })


@api_bp.route('/gmail/batch-authorize/cancel', methods=['POST'])
def gmail_batch_authorize_cancel():
    """取消进行中的批量授权任务"""
    data = request.get_json(silent=True) or {}
    task_id = data.get('taskId')
    if not task_id:
        active = batch_oauth_manager.active_task()
        if not active:
            return error_response('当前没有正在运行的批量授权任务')
        task_id = active.task_id
    record = batch_oauth_manager.cancel_task(task_id)
    if not record:
        return error_response('任务不存在', 404)
    return success_response(data=record.to_dict(), message='批量授权任务已请求取消')


# ---------------------------------------------------------------------------
# Gmail 挂机收信守护进程 (GmailSyncDaemon) 接口
# ---------------------------------------------------------------------------

@api_bp.route('/gmail/daemon/status', methods=['GET'])
def gmail_daemon_status():
    """获取后台挂机收信守护进程的运行状态与指标"""
    return success_response(data=gmail_sync_daemon.status())


@api_bp.route('/gmail/daemon/start', methods=['POST'])
def gmail_daemon_start():
    """启动后台挂机收信守护进程"""
    data = request.get_json(silent=True) or {}
    interval = data.get('intervalSeconds', 180)
    status = gmail_sync_daemon.start(
        current_app._get_current_object(),
        interval_seconds=interval,
    )
    return success_response(data=status, message='挂机收信守护进程已启动')


@api_bp.route('/gmail/daemon/stop', methods=['POST'])
def gmail_daemon_stop():
    """停止后台挂机收信守护进程"""
    status = gmail_sync_daemon.stop()
    return success_response(data=status, message='挂机收信守护进程已停止')


@api_bp.route('/gmail/daemon/sync-now', methods=['POST'])
def gmail_daemon_sync_now():
    """立即触发一次全量邮箱同步与安全扫描"""
    try:
        result = gmail_sync_daemon.sync_once(current_app._get_current_object())
        return success_response(data=result, message='全量邮箱同步完成')
    except Exception as error:
        return server_error_response('全量邮箱同步失败，请稍后重试', error)
