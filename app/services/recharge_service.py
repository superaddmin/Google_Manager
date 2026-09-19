"""
充值业务服务模块
实现严格的模式隔离 (disabled / mock / live)、契约核验、
数据库持久化 (RechargeTask)、防死锁重入锁及防刷校验令牌生命周期
"""
import re
import time
import uuid
import urllib.request
import urllib.parse
import urllib.error
import json
import hashlib
import hmac
import secrets
from datetime import datetime
from threading import RLock
from flask import current_app, has_request_context, session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.recharge_task import RechargeTask
from app.models.recharge_operation import RechargeOperation
from app.models.one_time_token import OneTimeToken


class RechargeContractError(ValueError):
    """契约核验异常"""
    pass


class RechargeModeDisabledError(RuntimeError):
    """充值模式已禁用异常"""
    pass


class RechargeUpstreamError(RuntimeError):
    """上游网络或接口履约异常"""
    pass


class RechargeService:
    """充值与交付核心业务服务"""

    # 使用可重入锁 (RLock) 杜绝自锁死锁问题
    _lock = RLock()
    _mock_subscriptions = {}

    ALLOWED_MODES = {'disabled', 'mock', 'live'}
    ALLOWED_PLANS = {'PLUS', 'PRO', 'Pro 5x', 'CLAUDE_CODE', 'FINISHED', 'KYC'}
    UPSTREAM_BASE = "https://aichong666.com/api"

    @classmethod
    def get_mode(cls):
        """获取当前充值系统运行模式: disabled | mock | live"""
        try:
            if current_app:
                mode = str(current_app.config.get('RECHARGE_MODE', 'disabled')).lower().strip()
                if mode in cls.ALLOWED_MODES:
                    return mode
                return 'disabled'
        except Exception:
            pass
        return 'disabled'

    @classmethod
    def ensure_enabled(cls):
        """模式安全门禁：若为 disabled 模式则直接阻断"""
        mode = cls.get_mode()
        if mode == 'disabled':
            raise RechargeModeDisabledError("充值交付功能当前未启用 (RECHARGE_MODE=disabled)")
        return mode

    @classmethod
    def clear_local_data(cls):
        """清除本地临时数据与挑战缓存（主要用于自动化测试隔离）"""
        with cls._lock:
            cls._mock_subscriptions.clear()
            try:
                OneTimeToken.clear('recharge_submission')
                RechargeOperation.query.delete()
                RechargeTask.query.delete()
                db.session.commit()
            except Exception:
                db.session.rollback()

    @classmethod
    def get_features(cls):
        """获取系统特性与运行模式"""
        mode = cls.get_mode()
        return {
            "mode": mode,
            "enabled": mode != 'disabled',
            "is_mock": mode == 'mock',
            "renewal_enabled": True,
            "batch_lookup_enabled": True
        }

    @classmethod
    def get_agreement(cls):
        """获取下单用户服务协议与须知"""
        cls.ensure_enabled()
        return {
            "title": "充值用户服务协议与免责声明",
            "mode": "html",
            "version": "2026.09",
            "content": """
<div class="agreement-content text-sm space-y-3">
  <h3 class="font-bold text-base text-emerald-600 dark:text-emerald-400">一、充值服务须知</h3>
  <p>1. 充值提交前请确认所持 CDK 卡密类型与待充值账号状态匹配，如当前已有生效中的 Plus/Pro 订阅，升级高阶套餐可能会覆盖剩余价值且差额不退。</p>
  <p>2. 提交 Session JSON 时，必须包含完整的 accessToken、account 以及 user 信息。请勿在提交后主动登出或退出账号，否则将导致登录态失效而履约失败。</p>
  <p>3. 涉及 Claude 或 Pro 5x 模式，需按照教程完整导出 Cookie JSON 或按「邮箱----sessionKey」格式提交凭证。</p>
  <h3 class="font-bold text-base text-emerald-600 dark:text-emerald-400">二、免责与安全承诺</h3>
  <p>1. 充值凭证仅用于已确认的请求，提交挑战保存摘要而非原始凭证。请勿在模拟环境提交真实 Session 或 Cookie；模拟结果不代表真实支付或履约。</p>
  <p>2. 任务处理期间，可随时使用卡密或任务流水号查询进度。若因账号风控等不可抗力导致失败，可通过撤回功能重试或联系客服处理。</p>
</div>
            """.strip()
        }

    @classmethod
    def get_avg_processing_time(cls, product="gpt", category="card"):
        """获取各套餐平均履约耗时（近 7 天）"""
        cls.ensure_enabled()
        return [
            {"plan_type": "PLUS", "plan_name": "ChatGPT Plus", "avg_seconds": 780.0},
            {"plan_type": "PRO", "plan_name": "ChatGPT Pro", "avg_seconds": 960.0},
            {"plan_type": "Pro 5x", "plan_name": "Claude Pro 5x", "avg_seconds": 510.0},
            {"plan_type": "CLAUDE_CODE", "plan_name": "Claude Code 额度", "avg_seconds": 420.0},
            {"plan_type": "FINISHED", "plan_name": "免开卡成品号", "avg_seconds": 120.0},
            {"plan_type": "KYC", "plan_name": "KYC 人工认证", "avg_seconds": 1800.0}
        ]

    # ---------------- 挑战令牌生命周期 ----------------

    @staticmethod
    def _fingerprint(value):
        secret = current_app.secret_key
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        return hmac.new(secret, str(value).encode('utf-8'), hashlib.sha256).hexdigest()

    @staticmethod
    def _session_context(create=False):
        if not has_request_context() or not session.get('authenticated'):
            raise RechargeContractError('充值操作需要有效的登录会话')
        if create and not session.get('recharge_context'):
            session['recharge_context'] = secrets.token_urlsafe(32)
        return session.get('recharge_context', '')

    @classmethod
    def _challenge_binding(cls, redeem_code, token_input, plan_type, is_renewal):
        context = [cls.get_mode(), cls._session_context(), redeem_code, token_input, plan_type, is_renewal]
        return cls._fingerprint(json.dumps(context, ensure_ascii=False, separators=(',', ':')))

    @classmethod
    def generate_challenge(cls, redeem_code, token_input="", plan_type="", is_renewal=False):
        """生成提交防刷校验 Challenge Token（绑定卡密与套餐，有效期 300 秒）"""
        cls.ensure_enabled()
        code = str(redeem_code or "").strip()
        credential = str(token_input or '').strip()
        plan = str(plan_type or '').strip()
        if not 4 <= len(code) <= 120:
            raise RechargeContractError('CDK 卡密格式无效')
        if plan not in cls.ALLOWED_PLANS:
            raise RechargeContractError('必须提供有效的套餐类型')
        if (not credential and plan != 'FINISHED') or len(credential) > 65535:
            raise RechargeContractError('必须提供有效的充值凭证')
        if not isinstance(is_renewal, bool):
            raise RechargeContractError('续费模式必须为布尔值')
        cls._session_context(create=True)
        token = f"ch_{secrets.token_urlsafe(32)}"
        OneTimeToken.register('recharge_submission', token, {}, 300,
            binding=cls._challenge_binding(code, credential, plan, is_renewal))
        return {"challenge_token": token, "expires_in": 300}

    @classmethod
    def _verify_and_consume_challenge(cls, challenge_token, redeem_code, token_input, plan_type, is_renewal):
        """核验并一次性消费挑战令牌（单次使用，5分钟有效）"""
        if not challenge_token:
            raise RechargeContractError("缺少防刷校验令牌 (challenge_token)，请重新获取")

        record = OneTimeToken.lookup('recharge_submission', challenge_token)
        if not record:
            raise RechargeContractError('校验令牌无效或已过期，请刷新重试')
        if record.consumed_at is not None:
            raise RechargeContractError('校验令牌已被使用，不可重复提交')
        if record.expires_at <= time.time():
            raise RechargeContractError('校验令牌已超时失效（有效期 5 分钟），请重新获取')
        binding = cls._challenge_binding(redeem_code, token_input, plan_type, is_renewal)
        if not hmac.compare_digest(record.binding or '', binding):
            raise RechargeContractError('校验令牌与会话、卡密、凭证、套餐类型或续费模式不匹配')
        if OneTimeToken.consume('recharge_submission', challenge_token, binding=binding) is None:
            raise RechargeContractError('校验令牌已被使用或失效，请重新获取')

    # ---------------- CDK 验证 ----------------

    @classmethod
    def validate_redeem_code(cls, redeem_code):
        """验证 CDK 卡密有效性与对应套餐"""
        mode = cls.ensure_enabled()
        code = str(redeem_code or "").strip()
        if not code or len(code) < 4:
            raise RechargeContractError("请输入有效的 CDK 卡密")

        # 检查是否已被关闭销毁
        latest_task = cls._find_latest_task_by_code(code)
        if latest_task and latest_task.status == 'closed':
            raise RechargeContractError("该卡密已被关闭注销，无法再次使用")

        if mode == 'live':
            upstream_res = cls._upstream_post("/user/redeem-codes/validate", {"redeem_code": code})
            if not upstream_res or not upstream_res.get("ok"):
                error_msg = (upstream_res or {}).get("message") or (upstream_res or {}).get("detail") or "上游校验卡密失败"
                raise RechargeUpstreamError(error_msg)
            return upstream_res.get("result", {})

        # Mock 模式：基于规范规则生成沙箱数据
        code_upper = code.upper()
        if "PLUS" in code_upper:
            product = "gpt"
            plan_type = "PLUS"
            plan_name = "ChatGPT Plus 官方代充"
        elif "PRO5X" in code_upper or "5X" in code_upper:
            product = "claude_code"
            plan_type = "Pro 5x"
            plan_name = "Claude Pro 5x 额度"
        elif "PRO" in code_upper:
            product = "gpt"
            plan_type = "PRO"
            plan_name = "ChatGPT Pro 升级"
        elif "KYC" in code_upper:
            product = "kyc"
            plan_type = "KYC"
            plan_name = "Persona KYC 认证"
        elif "FIN" in code_upper or "FINISHED" in code_upper:
            product = "finished"
            plan_type = "FINISHED"
            plan_name = "免开卡成品账号"
        else:
            product = "gpt"
            plan_type = "PLUS"
            plan_name = "ChatGPT 会员代充"

        return {
            "redeem_code": code,
            "product": product,
            "plan_type": plan_type,
            "plan_name": plan_name,
            "status": "valid",
            "is_mock": True,
            "bound_email": latest_task.account_email if latest_task else "",
            "account_change_locked": False,
            "is_renewal_supported": True
        }

    # ---------------- 契约核验：创建任务 ----------------

    @classmethod
    def validate_task_creation_contract(cls, data):
        """严格核验创建任务的输入契约与授权确认"""
        if not isinstance(data, dict):
            raise RechargeContractError("请求格式错误，必须为 JSON 对象")

        redeem_code = str(data.get("redeem_code") or "").strip()
        if not redeem_code or len(redeem_code) < 4 or len(redeem_code) > 120:
            raise RechargeContractError("CDK 卡密格式无效，长度须在 4-120 字符之间")

        plan_type = str(data.get("plan_type") or "").strip()
        if plan_type not in cls.ALLOWED_PLANS:
            raise RechargeContractError(f"不支持的套餐类型：{plan_type}，支持的套餐：{', '.join(cls.ALLOWED_PLANS)}")

        token_input = str(data.get("token_input") or "").strip()
        if not token_input and plan_type != 'FINISHED':
            raise RechargeContractError("请提供有效的充值凭证（Session JSON / Cookie / 认证链接）")
        if len(token_input) > 65535:
            raise RechargeContractError("充值凭证内容过长，超出最大限制")

        # 针对特定套餐类型做凭证格式初筛
        if plan_type in ('PLUS', 'PRO') and token_input.startswith('{') and token_input.endswith('}'):
            try:
                parsed = json.loads(token_input)
                if not isinstance(parsed, dict) or not parsed.get('accessToken'):
                    raise RechargeContractError("Session JSON 缺少 accessToken 字段，请复制完整的 /api/auth/session 返回内容")
            except json.JSONDecodeError as error:
                raise RechargeContractError('Session JSON 格式无效') from error
        elif plan_type == 'KYC':
            if not token_input.startswith('http://') and not token_input.startswith('https://'):
                raise RechargeContractError("KYC 认证须提交以 http:// 或 https:// 开头的有效链接")

        account_email = str(data.get("account_email") or "").strip()
        if not account_email or not re.match(r"^[\w\.\-]+@[\w\.\-]+\.\w+$", account_email):
            raise RechargeContractError("请提供有效的账号接收邮箱格式")

        # 授权确认：必须明确同意协议与确认邮箱
        if data.get("agreement_accepted") is not True:
            raise RechargeContractError("必须阅读并同意《充值用户服务协议》后方可提交")

        if data.get("email_verified") is not True:
            raise RechargeContractError("请核对并确认账号邮箱无误")

        challenge_token = str(data.get("challenge_token") or "").strip()
        # 校验并消费挑战令牌
        is_renewal = data.get('is_renewal', False)
        if not isinstance(is_renewal, bool):
            raise RechargeContractError('续费模式必须为布尔值')
        cls._verify_and_consume_challenge(challenge_token, redeem_code, token_input, plan_type, is_renewal)

        return {
            "redeem_code": redeem_code,
            "token_input": token_input,
            "plan_type": plan_type,
            "account_email": account_email,
            "agreement_accepted": True,
            "email_verified": True,
            "challenge_token": challenge_token,
            "is_renewal": is_renewal,
            "acknowledge_non_free": bool(data.get("acknowledge_non_free", False)),
            "notify_channel": data.get("notify_channel", "site"),
            "notify_email": str(data.get("notify_email") or account_email).strip()
        }

    @classmethod
    def create_task(cls, payload):
        """创建充值任务（先完成契约核验）"""
        mode = cls.ensure_enabled()
        valid_data = cls.validate_task_creation_contract(payload)
        code = valid_data["redeem_code"]

        task_no = None
        with cls._lock:
            # 状态机核验：检查同一卡密的最新历史任务
            latest_task = cls._find_latest_task_by_code(code)
            if latest_task:
                if latest_task.status == 'closed':
                    raise RechargeContractError("该卡密已关闭销毁，严禁再次提交")
                if latest_task.status in ('pending', 'processing', 'unknown', 'completed', 'success'):
                    raise RechargeContractError("该卡密已有正在执行或待核对中的充值任务，请勿重复提交")

            if mode == 'mock':
                # Mock 模式：直接落库 processing 状态并标记 is_mock=True
                task_no = f"TK-MOCK-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex.upper()}"
                db_task = RechargeTask(
                    task_no=task_no,
                    redeem_code=code,
                    plan_type=valid_data["plan_type"],
                    account_email=valid_data["account_email"],
                    status="processing",
                    status_text="任务处理中",
                    card_last4="8866",
                    is_renewal=valid_data["is_renewal"],
                    is_mock=True,
                    challenge_token=None,
                    notify_email=valid_data["notify_email"],
                    notice="【模拟沙箱环境】正在模拟分配支付专卡，任务已持久化记录。"
                )
                cls._persist_new_task(db_task, mode)
                return db_task.to_dict()

            # Live 模式（SUP-05 闭环）：
            # 1. 在发起网络请求前，先在本地数据库持久化 pending 状态任务与唯一操作键，杜绝漏单与并发重入
            task_no = f"TK-LIVE-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex.upper()}"
            db_task = RechargeTask(
                task_no=task_no,
                redeem_code=code,
                plan_type=valid_data["plan_type"],
                account_email=valid_data["account_email"],
                status="pending",
                status_text="正在排队对接上游",
                card_last4="",
                is_renewal=valid_data["is_renewal"],
                is_mock=False,
                challenge_token=None,
                notify_email=valid_data["notify_email"],
                notice="任务已在本地落库，正在向上游发起履约请求。"
            )
            cls._persist_new_task(db_task, mode)

        # 2. 将网络 I/O 移出互斥锁，避免慢请求或超时阻塞所有其它请求
        upstream_payload = dict(valid_data)
        upstream_payload.pop('challenge_token', None)
        upstream_payload["idempotency_key"] = task_no
        upstream_payload["client_task_no"] = task_no

        try:
            upstream_res = cls._upstream_post("/user/tasks", upstream_payload)
        except RechargeUpstreamError as err:
            # 网络超时/连接失败：更新为 unknown 待核对状态，保留数据库凭据以便幂等查验与对账恢复
            cls._mark_unknown(task_no)
            raise
        except Exception as err:
            cls._mark_unknown(task_no)
            raise RechargeUpstreamError('请求上游接口异常，任务待核对') from err

        # 3. 校验上游业务响应结果
        if not isinstance(upstream_res, dict) or upstream_res.get('ok') is not True:
            cls._mark_unknown(task_no)
            raise RechargeUpstreamError('上游尚未确认任务结果，请查询核对，不要重复提交')

        # 4. 上游明确受理成功，更新为最终处理中状态
        persisted = RechargeTask.query.filter_by(task_no=task_no).one()
        try:
            return cls._reconcile_task(persisted, upstream_res.get('task'))
        except RechargeUpstreamError:
            cls._mark_unknown(task_no)
            raise

    @classmethod
    def _mark_unknown(cls, task_no):
        RechargeTask.query.filter(
            RechargeTask.task_no == task_no,
            RechargeTask.status.in_(('pending', 'processing', 'unknown')),
        ).update({
            'status': 'unknown',
            'status_text': '上游响应未知（待核对）',
            'notice': '请通过任务查询核对结果，请勿重复提交。',
        }, synchronize_session=False)
        db.session.commit()

    @classmethod
    def _persist_new_task(cls, task, mode):
        operation = RechargeOperation(
            task_no=task.task_no,
            active_key=cls._fingerprint(f'{mode}:{task.redeem_code}'),
        )
        try:
            db.session.add(task)
            db.session.flush()
            db.session.add(operation)
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise RechargeContractError('该卡密已有正在执行或待核对中的充值任务，请勿重复提交') from error

    @classmethod
    def _reconcile_task(cls, task, remote):
        states = {
            'pending': '等待处理', 'processing': '任务处理中', 'unknown': '待核对',
            'completed': '已完成', 'failed': '已失败', 'recalled': '已撤回', 'closed': '已关闭',
        }
        if not isinstance(remote, dict):
            raise RechargeUpstreamError('上游缺少有效的任务状态')
        status = remote.get('status')
        if status == 'success':
            status = 'completed'
        if not isinstance(status, str) or status not in states:
            raise RechargeUpstreamError('上游返回未知任务状态，需人工核对')
        if remote.get('redeem_code') not in (None, '', task.redeem_code):
            raise RechargeUpstreamError('上游任务与请求卡密不匹配')
        if remote.get('account_email') not in (None, '', task.account_email):
            raise RechargeUpstreamError('上游任务与请求账号不匹配')
        if remote.get('client_task_no') not in (None, '', task.task_no):
            raise RechargeUpstreamError('上游任务与本地操作编号不匹配')
        operation = db.session.get(RechargeOperation, task.task_no)
        upstream_no = remote.get('task_no')
        if upstream_no is not None and (not isinstance(upstream_no, str) or not upstream_no or len(upstream_no) > 128):
            raise RechargeUpstreamError('上游任务编号格式无效')
        if operation and operation.upstream_task_no and upstream_no not in (None, operation.upstream_task_no):
            raise RechargeUpstreamError('上游任务编号与已关联任务不匹配')
        if task.status in ('completed', 'success', 'failed', 'recalled', 'closed'):
            return task.to_dict()
        if operation is None:
            operation = RechargeOperation(task_no=task.task_no)
            db.session.add(operation)
            db.session.flush()
        if upstream_no:
            bound = RechargeOperation.query.filter(
                RechargeOperation.task_no == task.task_no,
                or_(RechargeOperation.upstream_task_no.is_(None), RechargeOperation.upstream_task_no == upstream_no),
            ).update({'upstream_task_no': upstream_no}, synchronize_session=False)
            if bound != 1:
                db.session.rollback()
                raise RechargeUpstreamError('上游任务编号与已关联任务不匹配')
        previous_states = ('pending', 'unknown') if status == 'pending' else ('pending', 'processing', 'unknown')
        values = {'status': status, 'status_text': states[status], 'notice': '状态已通过上游任务查询核对。'}
        last4 = remote.get('card_last4')
        if isinstance(last4, str) and re.fullmatch(r'\d{4}', last4):
            values['card_last4'] = last4
        updated = RechargeTask.query.filter(
            RechargeTask.id == task.id,
            RechargeTask.status.in_(previous_states),
        ).update(values, synchronize_session=False)
        if updated and status in ('failed', 'recalled'):
            operation.active_key = None
        db.session.commit()
        db.session.refresh(task)
        return task.to_dict()

    # ---------------- 任务查询（单卡密 / 任务号 / 批量） ----------------

    @classmethod
    def lookup_task(cls, redeem_code):
        """按卡密查询任务进度（修复死锁：不持锁调用可能加锁的子方法）"""
        mode = cls.ensure_enabled()
        code = str(redeem_code or "").strip()
        if not code:
            raise RechargeContractError("请输入要查询的 CDK 卡密或任务编号")

        # 若以 TK- 开头则按任务流水号精准查询
        if code.upper().startswith("TK-"):
            return cls.get_task_by_no(code)

        task = cls._find_latest_task_by_code(code)
        if mode == 'live':
            upstream_res = cls._upstream_post("/user/tasks/lookup", {"redeem_code": code})
            if not isinstance(upstream_res, dict) or not upstream_res.get('ok'):
                raise RechargeUpstreamError('上游任务查询失败，本地状态未变更')
            if task:
                return cls._reconcile_task(task, upstream_res.get('task'))
            remote = upstream_res.get('task')
            if not isinstance(remote, dict) or not remote.get('status'):
                raise RechargeUpstreamError('上游缺少有效的任务状态')
            return {**remote, 'is_mock': False}

        # 本地持久化查询最新任务
        task = cls._find_latest_task_by_code(code)
        if task:
            return task.to_dict()

        # 无任务记录：返回待提交初始卡密信息
        card_info = cls.validate_redeem_code(code)
        return {
            "redeem_code": code,
            "plan_type": card_info.get("plan_type", "PLUS"),
            "status": "idle",
            "status_text": "待提交凭证",
            "is_mock": mode == 'mock',
            "notice": "该卡密尚未提交充值凭证，请先在充值提交页面提交凭证。"
        }

    @classmethod
    def get_task_by_no(cls, task_no):
        """通过任务流水号获取任务详情"""
        mode = cls.ensure_enabled()
        no = str(task_no or "").strip()
        if not no:
            raise RechargeContractError("任务流水号不能为空")

        task = RechargeTask.query.filter_by(task_no=no, is_mock=mode == 'mock').first()
        if not task:
            raise RechargeContractError(f"未找到流水号为 {no} 的任务记录")
        if mode == 'live':
            operation = db.session.get(RechargeOperation, task.task_no)
            if not operation or not operation.upstream_task_no:
                upstream_res = cls._upstream_post('/user/tasks/lookup', {'redeem_code': task.redeem_code})
            else:
                upstream_res = cls._upstream_get(f"/user/tasks/{urllib.parse.quote(operation.upstream_task_no, safe='')}")
            if not isinstance(upstream_res, dict) or not upstream_res.get('ok'):
                raise RechargeUpstreamError('上游任务查询失败，本地状态未变更')
            return cls._reconcile_task(task, upstream_res.get('task'))
        return task.to_dict()

    @classmethod
    def lookup_batch_tasks(cls, redeem_codes):
        """批量查询多个卡密状态（先去重，上限 50 个）"""
        mode = cls.ensure_enabled()
        if not isinstance(redeem_codes, list):
            raise RechargeContractError("卡密列表格式错误")
        # 去重且保序
        clean_codes = list(dict.fromkeys(str(c).strip() for c in redeem_codes if str(c).strip()))
        if not clean_codes:
            raise RechargeContractError("请提供至少一个有效卡密")
        if len(clean_codes) > 50:
            raise RechargeContractError("单次批量查询最多支持 50 个卡密")

        if mode == 'live':
            upstream_res = cls._upstream_post("/user/tasks/lookup-batch", {"redeem_codes": clean_codes})
            if upstream_res and upstream_res.get("ok"):
                results = upstream_res.get('results')
                if not isinstance(results, list):
                    raise RechargeUpstreamError('上游批量查询结果格式无效')
                for result in results:
                    if not isinstance(result, dict) or result.get('redeem_code') not in clean_codes:
                        raise RechargeUpstreamError('上游批量查询任务不匹配')
                    task = cls._find_latest_task_by_code(result['redeem_code'])
                    if task and result.get('ok'):
                        result.update(cls._reconcile_task(task, result.get('task', result)))
                return results
            err_msg = (upstream_res or {}).get("message") or "批量查询上游接口返回异常"
            raise RechargeUpstreamError(err_msg)

        # Mock / 本地持久化查询
        results = []
        for code in clean_codes:
            task = cls._find_latest_task_by_code(code)
            if task:
                results.append({
                    "ok": True,
                    "redeem_code": code,
                    "plan_type": task.plan_type,
                    "status": task.status,
                    "status_text": task.status_text,
                    "account_email": task.account_email,
                    "created_at": task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else "",
                    "is_mock": True
                })
            else:
                results.append({
                    "ok": False,
                    "redeem_code": code,
                    "plan_type": "未知",
                    "status": "not_found",
                    "status_text": "未提交任务",
                    "account_email": "-",
                    "created_at": "-",
                    "is_mock": True
                })
        return results

    # ---------------- 状态机控制：写操作（撤回 / 关闭） ----------------

    @classmethod
    def recall_task(cls, data):
        """
        撤回任务（写操作）
        必须核验：卡密、邮箱与显式授权确认 (confirmed is True)，仅限未终结任务
        """
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")

        code = str(data.get("redeem_code") or "").strip()
        email = str(data.get("email") or "").strip()
        confirmed = data.get("confirmed")

        if not code or not email:
            raise RechargeContractError("撤回任务须提供完整的卡密与账号邮箱")
        if confirmed is not True:
            raise RechargeContractError("撤回操作须显式获得用户授权确认 (confirmed=True)")

        with cls._lock:
            task = cls._find_latest_task_by_code(code)
            if not task:
                raise RechargeContractError("未找到对应卡密的充值任务")
            if task.account_email != email:
                raise RechargeContractError("提供的账号邮箱与任务绑定邮箱不匹配，无权撤回")
            if task.status not in ('pending', 'processing'):
                raise RechargeContractError(f"当前任务状态为「{task.status_text}」，不允许执行撤回操作")

            if mode == 'live':
                upstream_res = cls._upstream_post("/user/tasks/recall", {"redeem_code": code, "email": email})
                if not upstream_res or not upstream_res.get("ok"):
                    err_msg = (upstream_res or {}).get("message") or "上游撤回接口调用失败"
                    raise RechargeUpstreamError(err_msg)

            task.status = "recalled"
            task.status_text = "已撤回（可重新提交）"
            task.notice = "任务已被用户主动撤回，您可以核对或修改凭证后重新提交。"
            operation = db.session.get(RechargeOperation, task.task_no)
            if operation:
                operation.active_key = None
            db.session.commit()
            return task.to_dict()

    @classmethod
    def close_task(cls, data):
        """
        关闭并销毁任务（破坏性写操作）
        必须核验：卡密、邮箱与显式销毁确认 (confirmed is True)
        """
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")

        code = str(data.get("redeem_code") or "").strip()
        email = str(data.get("email") or "").strip()
        confirmed = data.get("confirmed")

        if not code or not email:
            raise RechargeContractError("关闭任务须提供完整的卡密与账号邮箱")
        if confirmed is not True:
            raise RechargeContractError("关闭任务将销毁卡密并终结任务，须显式二次确认 (confirmed=True)")

        with cls._lock:
            task = cls._find_latest_task_by_code(code)
            if not task:
                raise RechargeContractError("未找到对应卡密的充值任务")
            if task.account_email != email:
                raise RechargeContractError("提供的账号邮箱与任务绑定邮箱不匹配，无权关闭")
            if task.status == 'closed':
                raise RechargeContractError("任务已处于关闭注销状态，无需重复关闭")

            if mode == 'live':
                upstream_res = cls._upstream_post("/user/tasks/close", {"redeem_code": code, "email": email})
                if not upstream_res or not upstream_res.get("ok"):
                    err_msg = (upstream_res or {}).get("message") or "上游关闭任务接口调用失败"
                    raise RechargeUpstreamError(err_msg)

            task.status = "closed"
            task.status_text = "已关闭（卡密已注销）"
            task.notice = "任务已终结，对应 CDK 卡密已注销作废。"
            db.session.commit()
            return task.to_dict()

    # ---------------- 账单与自动续费工作台 ----------------

    @classmethod
    def _mock_subscription_key(cls, token):
        context = cls._session_context(create=True)
        return cls._fingerprint(json.dumps([context, token], separators=(',', ':')))

    @classmethod
    def billing_query(cls, token_input):
        """查询账单与绑卡状态"""
        mode = cls.ensure_enabled()
        token = str(token_input or "").strip()
        if not token:
            raise RechargeContractError("请输入有效的 Session Token 或登录凭证")

        if mode == 'live':
            upstream_res = cls._upstream_post("/tools/billing/query", {"token_input": token})
            if not upstream_res or not upstream_res.get("ok"):
                err_msg = (upstream_res or {}).get("message") or "查询上游账单状态失败"
                raise RechargeUpstreamError(err_msg)
            return upstream_res.get("result", {})

        key = cls._mock_subscription_key(token)
        with cls._lock:
            if key not in cls._mock_subscriptions:
                invoice_id = f'inv_mock_{uuid.uuid4().hex}'
                cls._mock_subscriptions[key] = {
                    '_session_fingerprint': cls._fingerprint(cls._session_context()),
                    "has_active_subscription": True,
                    "plan_name": "ChatGPT Plus ($20/mo)",
                    "status": "active",
                    "auto_renew": True,
                    "card_brand": "Visa",
                    "card_last4": "8866",
                    "next_billing_date": "2026-10-18",
                    "is_mock": True,
                    "invoices": [
                        {
                            "id": invoice_id,
                            "slug": invoice_id,
                            "amount": "$20.00",
                            "date": "2026-09-18",
                            "status": "paid",
                            "invoice_pdf_url": "",
                            "receipt_pdf_url": ""
                        }
                    ]
                }
            return {field: value for field, value in cls._mock_subscriptions[key].items() if not field.startswith('_')}

    @classmethod
    def billing_cancel_subscription(cls, data):
        """取消自动续费（写操作，须明确授权确认）"""
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")
        token = str(data.get("token_input") or "").strip()
        confirmed = data.get("confirmed")

        if not token:
            raise RechargeContractError("请提供账号凭证")
        if confirmed is not True:
            raise RechargeContractError("取消续费属于变更订阅写操作，须明确授权确认 (confirmed=True)")

        if mode == 'live':
            upstream_res = cls._upstream_post("/tools/billing/cancel-subscription", {"token_input": token})
            if not upstream_res or not upstream_res.get("ok"):
                err_msg = (upstream_res or {}).get("message") or "上游取消自动续费失败"
                raise RechargeUpstreamError(err_msg)
            return upstream_res.get("result", {})

        key = cls._mock_subscription_key(token)
        with cls._lock:
            if key not in cls._mock_subscriptions:
                cls.billing_query(token)
            cls._mock_subscriptions[key]["auto_renew"] = False
            cls._mock_subscriptions[key]["status"] = "canceled_at_period_end"

        return {
            "success": True,
            "auto_renew": False,
            "status": "canceled_at_period_end",
            "is_mock": True,
            "message": "已成功取消自动续费，当前会员权益将保留至到期日。"
        }

    @classmethod
    def billing_resume_subscription(cls, data):
        """恢复自动续费（写操作，须明确授权确认）"""
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")
        token = str(data.get("token_input") or "").strip()
        confirmed = data.get("confirmed")

        if not token:
            raise RechargeContractError("请提供账号凭证")
        if confirmed is not True:
            raise RechargeContractError("恢复续费属于变更订阅写操作，须明确授权确认 (confirmed=True)")

        if mode == 'live':
            upstream_res = cls._upstream_post("/tools/billing/resume-subscription", {"token_input": token})
            if not upstream_res or not upstream_res.get("ok"):
                err_msg = (upstream_res or {}).get("message") or "上游恢复自动续费失败"
                raise RechargeUpstreamError(err_msg)
            return upstream_res.get("result", {})

        key = cls._mock_subscription_key(token)
        with cls._lock:
            if key not in cls._mock_subscriptions:
                cls.billing_query(token)
            cls._mock_subscriptions[key]["auto_renew"] = True
            cls._mock_subscriptions[key]["status"] = "active"

        return {
            "success": True,
            "auto_renew": True,
            "status": "active",
            "is_mock": True,
            "message": "已恢复自动续费，系统已为您重新启用预扣与发票服务。"
        }

    @classmethod
    def find_mock_invoice(cls, code):
        """查找 Mock 账单发票凭据事实"""
        if cls.get_mode() != 'mock':
            return None
        safe = str(code or "").strip()
        owner = cls._fingerprint(cls._session_context())
        with cls._lock:
            for sub in cls._mock_subscriptions.values():
                if sub.get('_session_fingerprint') != owner:
                    continue
                for inv in sub.get("invoices", []):
                    if inv.get("id") == safe or inv.get("slug") == safe:
                        return inv
        return None

    # ---------------- 辅助持久化查询 ----------------

    @classmethod
    def _find_latest_task_by_code(cls, redeem_code):
        """获取指定卡密的最新一条任务记录（按 ID 降序）"""
        return RechargeTask.query.filter_by(redeem_code=redeem_code, is_mock=cls.get_mode() == 'mock').order_by(RechargeTask.id.desc()).first()

    # ---------------- 上游通信适配层 ----------------

    @classmethod
    def _upstream_post(cls, endpoint, payload, timeout=8):
        base = current_app.config.get('RECHARGE_UPSTREAM_URL', cls.UPSTREAM_BASE) if current_app else cls.UPSTREAM_BASE
        url = f"{base}{endpoint}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GoogleManager/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.HTTPError as err:
            err.close()
            return {'ok': False, 'message': f'上游接口返回错误 HTTP {err.code}'}
        except urllib.error.URLError as err:
            raise RechargeUpstreamError('无法连接上游充值服务，请查询核对任务结果') from err
        except Exception as err:
            raise RechargeUpstreamError('请求上游接口异常，请查询核对任务结果') from err

    @classmethod
    def _upstream_get(cls, endpoint, timeout=8):
        base = current_app.config.get('RECHARGE_UPSTREAM_URL', cls.UPSTREAM_BASE) if current_app else cls.UPSTREAM_BASE
        url = f"{base}{endpoint}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GoogleManager/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.HTTPError as err:
            err.close()
            return {'ok': False, 'message': f'上游接口返回错误 HTTP {err.code}'}
        except urllib.error.URLError as err:
            raise RechargeUpstreamError('无法连接上游充值服务，本地状态未变更') from err
        except Exception as err:
            raise RechargeUpstreamError('请求上游接口异常，本地状态未变更') from err
