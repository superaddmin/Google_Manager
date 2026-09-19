"""
充值交付路由控制器
提供卡密验证、任务创建流转、契约核验与账单管理接口
"""
from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify, session, current_app, Response
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import (
    RechargeService,
    RechargeContractError,
    RechargeModeDisabledError,
    RechargeUpstreamError,
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
    return jsonify({
        'success': False,
        'data': None,
        'message': message
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


@recharge_bp.before_request
def require_authentication():
    """
    蓝图鉴权：充值与交付服务接口均属于受保护资源，
    必须通过管理员登录会话（session['authenticated']）方可访问。
    对破坏性/修改状态的写操作检查 Origin 头防止跨站伪造。
    """
    if not session.get('authenticated'):
        return error_response('请先登录系统', 401)

    if request.method in ('POST', 'PUT', 'PATCH') and not isinstance(request.get_json(silent=True), dict):
        return error_response('请求格式错误，必须为 JSON 对象', 400)

    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('Origin')
        if origin:
            parsed = urlparse(origin)
            if parsed.netloc and parsed.netloc != request.host:
                return error_response('跨站请求被拦截 (Invalid Origin)', 403)


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
    """获取各套餐平均履约耗时（近 7 天）"""
    product = request.args.get('product', 'gpt')
    category = request.args.get('category', 'card')
    items = RechargeService.get_avg_processing_time(product, category)
    return success_response(items)


@recharge_bp.route('/redeem-codes/validate', methods=['POST'])
def validate_redeem_code():
    """验证 CDK 卡密有效性与对应套餐"""
    data = request.get_json(silent=True) or {}
    code = data.get('redeem_code') or request.args.get('redeem_code')
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
        current_app.logger.error('验证 CDK 异常: %s', err)
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
        current_app.logger.error('创建充值任务失败: %s', err)
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


@recharge_bp.route('/tasks/recall', methods=['POST'])
def recall_task():
    """
    撤回进行中的充值任务（写操作）
    契约核验：须提供卡密、邮箱并包含授权确认标志
    """
    data = request.get_json(silent=True) or {}
    try:
        task = RechargeService.recall_task(data)
        return success_response(task, '任务已成功撤回')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        current_app.logger.error('撤回任务异常: %s', err)
        return error_response('撤回任务失败，请稍后重试', 500)


@recharge_bp.route('/tasks/close', methods=['POST'])
def close_task():
    """
    关闭任务并销毁卡密（破坏性写操作）
    契约核验：须提供卡密、邮箱且 confirmed 必须为 True
    """
    data = request.get_json(silent=True) or {}
    try:
        task = RechargeService.close_task(data)
        return success_response(task, '任务已成功关闭，卡密已注销')
    except RechargeModeDisabledError as err:
        return error_response(str(err), 503)
    except RechargeUpstreamError as err:
        return error_response(str(err), 502)
    except RechargeContractError as err:
        return error_response(str(err), 400)
    except Exception as err:
        current_app.logger.error('关闭任务异常: %s', err)
        return error_response('关闭任务失败，请稍后重试', 500)


@recharge_bp.route('/tasks/invoice/download', methods=['GET', 'POST'])
def download_invoice():
    """下载对账发票/收据凭证 (支持 GET/POST，读取卡密或任务号，需核实任务真实存在)"""
    mode = RechargeService.ensure_enabled()
    if mode != 'mock':
        return error_response('真实账单下载尚未完成授权契约验收，当前不可用', 503)

    data = request.get_json(silent=True) or {}
    code = (
        data.get('task_no')
        or request.args.get('task_no')
        or data.get('redeem_code')
        or request.args.get('redeem_code')
        or data.get('slug')
        or request.args.get('slug')
    )
    if not code:
        return error_response('缺少卡密或任务编号参数', 400)

    safe_code = str(code).strip()
    task = RechargeTask.query.filter(
        (RechargeTask.task_no == safe_code) | (RechargeTask.redeem_code == safe_code)
    ).filter_by(is_mock=True).order_by(RechargeTask.id.desc()).first()

    if not task:
        billing_inv = RechargeService.find_mock_invoice(safe_code)
        if billing_inv:
            content = (
                f"GoogleManager 账单收据凭据\n"
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
                mimetype='text/plain; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'}
            )
        return error_response('未找到关联任务，无法生成对账凭据', 404)

    created_time_str = task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else '-'
    content = (
        f"GoogleManager 充值任务对账凭据\n"
        f"----------------------------------------\n"
        f"任务编号: {task.task_no}\n"
        f"关联卡密: {task.redeem_code}\n"
        f"充值套餐: {task.plan_type}\n"
        f"目标账号: {task.account_email}\n"
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
        mimetype='text/plain; charset=utf-8',
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
    """获取账单文件链接"""
    data = request.get_json(silent=True) or {}
    slug = data.get('slug', 'default')
    file_type = data.get('file_type', 'invoice')
    url = f"/api/recharge/tasks/invoice/download?slug={slug}&type={file_type}"
    return success_response({'url': url})
