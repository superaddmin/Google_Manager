"""
充值交付路由控制器
提供卡密验证、任务创建流转、契约核验与账单管理接口
"""
from datetime import datetime
from werkzeug.exceptions import HTTPException
from flask import Blueprint, request, jsonify, current_app, Response
from app import db
from app.models.recharge_task import RechargeTask
from app.models.recharge_task_access import RechargeTaskAccess
from app.services.recharge_service import (
    RechargeService,
    RechargeContractError,
    RechargeModeDisabledError,
    RechargeUpstreamError,
    RechargeReconciliationConflictError,
)

recharge_bp = Blueprint('recharge', __name__)


def success_response(data=None, message='操作成功'):
    """统一成功响应格式"""
    return jsonify({
        'success': True,
        'data': data,
        'message': message
    })


def error_response(message='操作失败', code=400):
    """统一错误响应格式"""
    error_codes = {
        400: 'invalid_request',
        401: 'unauthorized',
        402: 'payment_required',
        403: 'forbidden',
        404: 'not_found',
        405: 'method_not_allowed',
        406: 'not_acceptable',
        408: 'timeout',
        409: 'conflict',
        410: 'gone',
        411: 'length_required',
        412: 'precondition_failed',
        413: 'request_entity_too_large',
        414: 'uri_too_long',
        415: 'unsupported_media_type',
        416: 'range_not_satisfiable',
        417: 'expectation_failed',
        422: 'unprocessable_entity',
        423: 'locked',
        424: 'failed_dependency',
        428: 'precondition_required',
        429: 'rate_limited',
        431: 'request_header_fields_too_large',
        451: 'unavailable_for_legal_reasons',
        500: 'internal_error',
        501: 'not_implemented',
        502: 'upstream_error',
        503: 'service_unavailable',
        504: 'gateway_timeout',
    }
    return jsonify({
        'success': False,
        'data': None,
        'message': message,
        'error_code': error_codes.get(code, 'http_error'),
    }), code


@recharge_bp.errorhandler(RechargeModeDisabledError)
def handle_recharge_mode_disabled(err):
    return error_response(str(err), 503)


@recharge_bp.errorhandler(RechargeUpstreamError)
def handle_recharge_upstream_error(err):
    return error_response(str(err), 502)


@recharge_bp.errorhandler(RechargeContractError)
def handle_recharge_contract_error(err):
    return error_response(str(err), 400)


@recharge_bp.errorhandler(RechargeReconciliationConflictError)
def handle_recharge_reconciliation_conflict(err):
    return error_response(str(err), 409)


@recharge_bp.app_errorhandler(HTTPException)
def handle_recharge_http_error(err):
    """充值接口的 4xx/5xx 统一返回 JSON，避免路由阶段返回 Flask HTML。"""
    if request.path != '/api/recharge' and not request.path.startswith('/api/recharge/'):
        return err
    status = err.code or 500
    message = err.description if status < 500 else '充值服务暂不可用，请稍后重试'
    return error_response(message, status)


@recharge_bp.errorhandler(Exception)
def handle_recharge_unexpected_error(err):
    """兜底处理数据库/序列化异常，日志不写入凭证、卡密或异常原文。"""
    if isinstance(err, HTTPException):
        return handle_recharge_http_error(err)
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error('充值接口未处理异常 type=%s', type(err).__name__)
    return error_response('充值服务暂不可用，请稍后重试', 500)


@recharge_bp.before_request
def enforce_request_safety():
    """
    C 端充值接口允许匿名访问；挑战令牌仍绑定当前签名会话。
    对修改状态的写操作检查 JSON 格式与 Origin，阻止跨站伪造。
    """
    from app.services.request_security import protect_write_request, limit_recharge_request
    rejected = protect_write_request()
    if rejected:
        return rejected
    if request.method in ('POST', 'PUT', 'PATCH'):
        if not request.is_json:
            return error_response('请求媒体类型错误，必须使用 application/json', 415)
        if not isinstance(request.get_json(silent=True), dict):
            return error_response('请求格式错误，必须为 JSON 对象', 400)

    if request.path.startswith('/api/recharge/admin/'):
        from app.services.auth_service import is_admin_authenticated
        if not is_admin_authenticated():
            return error_response('请先登录管理员账号', 401)

    from app.models.cdk import CdkRedemption
    from app.services.cdk_service import guard_legacy_code
    values = request.get_json(silent=True) or {}
    if isinstance(values, dict):
        for value in (values.get('redeem_code'), values.get('slug')):
            guard_legacy_code(value)
        for value in values.get('redeem_codes', []) if isinstance(values.get('redeem_codes'), list) else []:
            guard_legacy_code(value)
    task_no = (request.view_args or {}).get('task_no') or values.get('task_no')
    if not task_no and isinstance(values.get('redeem_code'), str) and values['redeem_code'].startswith('TK-'):
        task_no = values['redeem_code']
    if task_no and isinstance(task_no, str) and CdkRedemption.query.filter_by(task_no=task_no).first():
        return error_response('平台卡密订单请使用卡密工作台或本人核销记录入口', 403)

    if request.endpoint not in {'recharge.get_config', 'recharge.get_agreement', 'recharge.get_features'}:
        return limit_recharge_request()


@recharge_bp.route('/config', methods=['GET'])
def get_config():
    """获取充值系统基础配置与运行模式"""
    return success_response({
        'features': RechargeService.get_features(),
        'dual_mode': True,
        'mode': RechargeService.get_mode(),
        'version': '1.0.0'
    })


@recharge_bp.route('/agreement', methods=['GET'])
def get_agreement():
    """获取充值服务协议与下单须知"""
    return success_response(RechargeService.get_agreement())


@recharge_bp.route('/features', methods=['GET'])
def get_features():
    """获取特性开关"""
    return success_response(RechargeService.get_features())


@recharge_bp.route('/stats/avg-processing-time', methods=['GET'])
def get_avg_processing_time():
    """返回沙箱参考耗时；live 未接入真实统计时返回空列表。"""
    product = request.args.get('product', 'gpt')
    category = request.args.get('category', 'card')
    items = RechargeService.get_avg_processing_time(product, category)
    return success_response(items)


@recharge_bp.route('/redeem-codes/validate', methods=['POST'])
def validate_redeem_code():
    """验证 CDK 卡密有效性与对应套餐"""
    data = request.get_json(silent=True) or {}
    code = data.get('redeem_code')
    try:
        result = RechargeService.validate_redeem_code(code)
        return success_response(result)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        db.session.rollback()
        current_app.logger.error('验证 CDK 异常 type=%s', type(err).__name__)
        return error_response('CDK 验证服务暂不可用，请稍后重试', 500)


@recharge_bp.route('/submission-challenges', methods=['POST'])
def get_submission_challenge():
    """获取提交防刷校验令牌"""
    data = request.get_json(silent=True) or {}
    code = data.get('redeem_code')
    try:
        challenge = RechargeService.generate_challenge(
            code,
            token_input=data.get('token_input', ''),
            plan_type=data.get('plan_type', ''),
            is_renewal=data.get('is_renewal', False)
        )
        return success_response(challenge)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


@recharge_bp.route('/tasks', methods=['POST'])
def create_task():
    """
    创建充值任务（写操作）
    严格执行契约核验：卡密有效性、凭证长度、邮箱格式、协议同意确认与防刷令牌
    """
    data = request.get_json(silent=True) or {}
    try:
        task = RechargeService.create_task(data)
        return success_response(task, '充值任务提交成功'), 201
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        db.session.rollback()
        current_app.logger.error('创建充值任务失败 type=%s', type(err).__name__)
        return error_response('创建任务失败，请检查凭证内容', 500)


@recharge_bp.route('/tasks/<task_no>', methods=['GET'])
def get_task(task_no):
    """根据任务编号查询详情"""
    try:
        task = RechargeService.get_task_by_no(task_no)
        return success_response(task)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 404)


@recharge_bp.route('/admin/tasks/<task_no>/reconcile', methods=['POST'])
def reconcile_unknown_task(task_no):
    """管理员依据已归档上游证据关闭 unknown 创建请求。"""
    from app.services.auth_service import get_admin_session_actor_id

    actor_id = get_admin_session_actor_id()
    if actor_id is None:
        return error_response('请先登录管理员账号', 401)
    result = RechargeService.reconcile_unknown_task(
        task_no,
        request.get_json(silent=True) or {},
        actor_id,
    )
    return success_response(result, '人工对账已完成')


@recharge_bp.route('/admin/overview', methods=['GET'])
def admin_overview():
    from app.services.recharge_admin_service import RechargeAdminService
    return success_response(RechargeAdminService.overview())


@recharge_bp.route('/admin/tasks', methods=['GET'])
def admin_tasks():
    from app.services.recharge_admin_service import RechargeAdminService
    return success_response(RechargeAdminService.list_tasks(request.args))


@recharge_bp.route('/admin/tasks/<task_no>', methods=['GET'])
def admin_task_detail(task_no):
    from app.services.recharge_admin_service import RechargeAdminService
    return success_response(RechargeAdminService.task_detail(task_no))


@recharge_bp.route('/admin/tasks/<task_no>/refresh', methods=['POST'], defaults={'action': 'refresh'})
@recharge_bp.route('/admin/tasks/<task_no>/recall', methods=['POST'], defaults={'action': 'recall'})
@recharge_bp.route('/admin/tasks/<task_no>/close', methods=['POST'], defaults={'action': 'close'})
def admin_task_action(task_no, action):
    from app.services.recharge_admin_service import RechargeAdminService
    return success_response(RechargeAdminService.perform_action(task_no, action, request.get_json()))


@recharge_bp.route('/admin/tasks/<task_no>/mutations/<operation_id>/reconcile', methods=['POST'])
def reconcile_unknown_mutation(task_no, operation_id):
    from app.services.auth_service import get_admin_session_actor_id

    actor_id = get_admin_session_actor_id()
    if actor_id is None:
        return error_response('请先登录管理员账号', 401)
    result = RechargeService.reconcile_unknown_mutation(
        task_no, operation_id, request.get_json(silent=True) or {}, actor_id,
    )
    return success_response(result, '操作人工对账已完成')


@recharge_bp.route('/admin/tasks/<task_no>/mutations/current', methods=['GET'])
def get_task_mutation(task_no):
    from app.services.auth_service import get_admin_session_actor_id

    if get_admin_session_actor_id() is None:
        return error_response('请先登录管理员账号', 401)
    return success_response(RechargeService.get_task_mutation(task_no))


@recharge_bp.route('/tasks/lookup', methods=['POST'])
def lookup_task():
    """根据 CDK 卡密查询关联任务或状态"""
    data = request.get_json(silent=True) or {}
    code = data.get('redeem_code')
    try:
        task = RechargeService.lookup_task(code)
        return success_response(task)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


@recharge_bp.route('/tasks/lookup-batch', methods=['POST'])
def lookup_batch():
    """批量查询多个卡密状态"""
    data = request.get_json(silent=True) or {}
    codes = data.get('redeem_codes', [])
    try:
        results = RechargeService.lookup_batch_tasks(codes)
        return success_response(results)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


def authorize_task_action(data):
    """Bind public destructive actions to both the displayed task and its owner."""
    mode = RechargeService.ensure_enabled()
    fields = {'task_no': 64, 'redeem_code': 120, 'email': 256}
    for field, maximum in fields.items():
        value = data.get(field)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
            return error_response('请提供有效的任务编号、完整卡密和账号邮箱', 400)
        data[field] = value.strip()
    task = RechargeTask.query.filter_by(task_no=data['task_no'], is_mock=mode == 'mock').first()
    if (
        task is None
        or task.redeem_code != data['redeem_code']
        or task.account_email != data['email']
        or not RechargeTaskAccess.permits_current_session(task.task_no)
    ):
        return error_response('任务信息不匹配或当前会话无操作权限，请使用创建任务的浏览器或联系管理员', 403)
    return None


@recharge_bp.route('/tasks/recall', methods=['POST'])
def recall_task():
    """
    撤回进行中的充值任务（写操作）
    契约核验：须提供卡密、邮箱并包含授权确认标志
    """
    data = request.get_json(silent=True) or {}
    try:
        rejected = authorize_task_action(data)
        if rejected:
            return rejected
        task = RechargeService.recall_task(data)
        return success_response(task, '任务已成功撤回')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        db.session.rollback()
        current_app.logger.error('撤回任务异常 type=%s', type(err).__name__)
        return error_response('撤回任务失败，请稍后重试', 500)


@recharge_bp.route('/tasks/close', methods=['POST'])
def close_task():
    """
    关闭任务并销毁卡密（破坏性写操作）
    契约核验：须提供卡密、邮箱且 confirmed 必须为 True
    """
    data = request.get_json(silent=True) or {}
    try:
        rejected = authorize_task_action(data)
        if rejected:
            return rejected
        task = RechargeService.close_task(data)
        return success_response(task, '任务已成功关闭，卡密已注销')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        db.session.rollback()
        current_app.logger.error('关闭任务异常 type=%s', type(err).__name__)
        return error_response('关闭任务失败，请稍后重试', 500)


@recharge_bp.route('/tasks/invoice/download', methods=['POST'])
def download_invoice():
    """通过 JSON POST 下载对账凭证，避免敏感标识进入 URL 与代理日志。"""
    mode = RechargeService.ensure_enabled()
    if mode != 'mock':
        return error_response('真实账单下载尚未完成授权契约验收，当前不可用', 503)

    data = request.get_json(silent=True) or {}
    redeem_code = data.get('redeem_code')
    invoice_slug = data.get('slug')
    if redeem_code is not None and invoice_slug is not None:
        return error_response('卡密与账单标识不能同时提交', 400)
    code = redeem_code if redeem_code is not None else invoice_slug
    if not isinstance(code, str) or not 1 <= len(code.strip()) <= 128:
        return error_response('缺少卡密或任务编号参数', 400)

    file_type = data.get('file_type', 'txt')
    if not isinstance(file_type, str) or file_type not in RechargeService.INVOICE_FILE_TYPES:
        return error_response('账单文件类型无效', 400)

    safe_code = code.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in safe_code):
        return error_response('账单标识格式无效', 400)
    task = RechargeTask.query.filter(
        RechargeTask.redeem_code == safe_code
    ).filter_by(is_mock=True).order_by(RechargeTask.id.desc()).first()

    if not task:
        billing_inv = RechargeService.find_mock_invoice(safe_code)
        if billing_inv:
            content = (
                f"Chat GPT充值中心 账单收据凭据\n"
                f"----------------------------------------\n"
                f"账单编号: {billing_inv.get('id', safe_code)}\n"
                f"对账标识: {billing_inv.get('slug', safe_code)}\n"
                f"结算金额: {billing_inv.get('amount', '$20.00')}\n"
                f"账单日期: {billing_inv.get('date', '2026-09-18')}\n"
                f"支付状态: {billing_inv.get('status', 'paid')}\n"
                f"凭证生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"运行模式: 模拟沙箱 (MOCK)\n"
                f"服务平台: Google Manager Billing Center\n"
                f"----------------------------------------\n"
            )
            filename = f"receipt-{billing_inv.get('id', safe_code)[:16]}.txt"
            return Response(
                content.encode('utf-8'),
                content_type='text/plain; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'}
            )
        return error_response('未找到关联任务，无法生成对账凭据', 404)

    if not RechargeTaskAccess.permits_current_session(task.task_no):
        return error_response('当前会话无权下载该任务的对账凭据', 403)

    if task.status != 'completed':
        return error_response('充值任务尚未完成，暂无法生成对账凭据', 409)

    created_time_str = task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else '-'
    masked_code = (
        f'{task.redeem_code[:4]}...{task.redeem_code[-4:]}'
        if task.redeem_code and len(task.redeem_code) > 8 else '已隐藏'
    )
    content = (
        f"Chat GPT充值中心 充值任务对账凭据\n"
        f"----------------------------------------\n"
        f"任务编号: {task.task_no}\n"
        f"关联卡密: {masked_code}\n"
        f"充值套餐: {task.plan_type}\n"
        f"目标账号: 已隐藏\n"
        f"任务状态: {task.status} ({task.status_text})\n"
        f"支付卡号: {task.card_last4}\n"
        f"创建时间: {created_time_str}\n"
        f"凭证生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"运行模式: {'模拟沙箱 (MOCK)' if task.is_mock else '正式交付 (LIVE)'}\n"
        f"服务平台: Google Manager Recharge Center\n"
        f"----------------------------------------\n"
    )
    filename = f"receipt-{task.task_no[:16]}.txt"
    return Response(
        content.encode('utf-8'),
        content_type='text/plain; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ---------------- 账单与自动续费工作台 ----------------

@recharge_bp.route('/billing/query', methods=['POST'])
def billing_query():
    """查询账户绑卡与订阅信息"""
    data = request.get_json(silent=True) or {}
    token = data.get('token_input')
    try:
        result = RechargeService.billing_query(token)
        return success_response(result)
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


@recharge_bp.route('/billing/cancel-subscription', methods=['POST'])
def billing_cancel_subscription():
    """
    取消订阅（自动续费）：写操作
    契约核验：须提供凭证并显式确认 confirmed=True
    """
    data = request.get_json(silent=True) or {}
    try:
        result = RechargeService.billing_cancel_subscription(data)
        return success_response(result, '已成功取消自动续费')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


@recharge_bp.route('/billing/resume-subscription', methods=['POST'])
def billing_resume_subscription():
    """
    恢复订阅（自动续费）：写操作
    契约核验：须提供凭证并显式确认 confirmed=True
    """
    data = request.get_json(silent=True) or {}
    try:
        result = RechargeService.billing_resume_subscription(data)
        return success_response(result, '已成功恢复自动续费')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)


@recharge_bp.route('/billing/invoice-file', methods=['POST'])
def billing_invoice_file():
    """获取不携带敏感 query 的账单文件 POST 请求描述。"""
    mode = RechargeService.ensure_enabled()
    if mode != 'mock':
        return error_response('真实账单文件尚未完成授权契约验收，当前不可用', 503)
    data = request.get_json(silent=True) or {}
    slug = data.get('slug')
    file_type = data.get('file_type', 'txt')
    if not isinstance(slug, str) or not 1 <= len(slug.strip()) <= 128:
        return error_response('账单标识必须是 1-128 个字符的文本', 400)
    if not isinstance(file_type, str) or file_type not in RechargeService.INVOICE_FILE_TYPES:
        return error_response('账单文件类型无效', 400)
    slug = slug.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in slug):
        return error_response('账单标识格式无效', 400)
    return success_response({
        'url': '/api/recharge/tasks/invoice/download',
        'method': 'POST',
        'payload': {'slug': slug, 'file_type': file_type},
    })
