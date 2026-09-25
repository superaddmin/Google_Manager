"""
充值业务服务模块
实现严格的模式隔离 (disabled / mock / live)、契约核验、
数据库持久化 (RechargeTask)、防死锁重入锁及防刷校验令牌生命周期
"""
import re
import time
import uuid
import io
import http.client
import urllib.request
import urllib.parse
import urllib.error
from urllib.parse import urlsplit
import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from threading import RLock
from flask import current_app, has_request_context, session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.recharge_task import RechargeTask
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_mutation_reconciliation import RechargeMutationReconciliation
from app.models.recharge_billing_mutation import RechargeBillingMutation
from app.models.recharge_task_access import RechargeTaskAccess
from app.models.recharge_reconciliation import RechargeReconciliation
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


class RechargeReconciliationConflictError(RechargeContractError):
    """人工对账结论与已保存状态冲突。"""

    pass


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so a validated upstream URL cannot leave its approved host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeadlineSocketReader(io.RawIOBase):
    """Apply one absolute deadline to every response-header/body socket read."""

    def __init__(self, sock, deadline):
        super().__init__()
        self._socket = sock
        self._deadline = deadline
        self._raw = sock.makefile('rb', buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('upstream response deadline exceeded')
        self._socket.settimeout(remaining)
        return self._raw.readinto(buffer)

    def close(self):
        try:
            if not self.closed:
                self._raw.close()
        finally:
            super().close()


class _DeadlineHTTPResponse(http.client.HTTPResponse):
    """HTTPResponse whose status line, headers, and body share one deadline."""

    def __init__(self, sock, *args, deadline, **kwargs):
        super().__init__(sock, *args, **kwargs)
        original_fp = self.fp
        try:
            self.deadline = deadline
            self.fp = io.BufferedReader(_DeadlineSocketReader(sock, deadline))
        finally:
            original_fp.close()


class _DeadlineConnectionMixin:
    def __init__(self, *args, **kwargs):
        timeout = kwargs.get('timeout')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError('upstream timeout must be a positive number')
        super().__init__(*args, **kwargs)
        deadline = time.monotonic() + timeout
        self.response_class = lambda sock, *response_args, **response_kwargs: (
            _DeadlineHTTPResponse(
                sock,
                *response_args,
                deadline=deadline,
                **response_kwargs,
            )
        )


class _DeadlineHTTPConnection(_DeadlineConnectionMixin, http.client.HTTPConnection):
    pass


class _DeadlineHTTPSConnection(_DeadlineConnectionMixin, http.client.HTTPSConnection):
    pass


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_DeadlineHTTPConnection, req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(
            _DeadlineHTTPSConnection,
            req,
            context=self._context,
        )


class RechargeService:
    """充值与交付核心业务服务"""

    # 使用可重入锁 (RLock) 杜绝自锁死锁问题
    _lock = RLock()
    _mock_subscriptions = {}

    ALLOWED_MODES = {'disabled', 'mock', 'live'}
    ALLOWED_PLANS = {'PLUS', 'PRO', 'Pro 5x', 'CLAUDE_CODE', 'FINISHED', 'KYC'}
    VALID_REDEEM_STATUSES = {'valid', 'unused'}
    TASK_STATUS_TEXT = {
        'pending': '等待处理', 'processing': '任务处理中', 'unknown': '待核对',
        'completed': '已完成', 'failed': '已失败', 'recalled': '已撤回', 'closed': '已关闭',
    }
    UPSTREAM_BASE = "https://aichong666.com/api"
    # email 通知目前没有实际投递 worker；在实现前拒绝该选项，避免 API 返回
    # 成功却丢失用户选择。
    NOTIFY_CHANNELS = {'site'}
    EMAIL_PATTERN = re.compile(r"^[\w.\-]+@[\w.\-]+\.\w+$")
    # 当前下载接口只生成纯文本凭据；PDF/HTML 等类型尚无真实生成器。
    INVOICE_FILE_TYPES = {'txt'}
    RECONCILIATION_EVIDENCE_SOURCES = {
        'upstream_api', 'provider_console', 'provider_ticket',
    }
    MAX_UPSTREAM_RESPONSE_BYTES = 1024 * 1024
    UPSTREAM_READ_CHUNK_BYTES = 64 * 1024
    VALIDATION_PUBLIC_FIELDS = {
        'product', 'plan_type', 'plan_name', 'status', 'is_mock',
        'account_change_locked', 'is_renewal_supported',
    }
    BILLING_PUBLIC_FIELDS = {
        'has_active_subscription', 'plan_name', 'status', 'auto_renew',
        'card_brand', 'card_last4', 'next_billing_date', 'is_mock', 'invoices',
    }
    PUBLIC_TASK_FIELDS = {
        'id', 'task_no', 'plan_type', 'status', 'status_text', 'card_last4',
        'is_renewal', 'is_mock', 'notice', 'created_at', 'updated_at',
    }

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
                RechargeBillingMutation.query.delete()
                RechargeMutation.query.delete()
                RechargeReconciliation.query.delete()
                RechargeOperation.query.delete()
                RechargeTaskAccess.query.delete()
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
        """返回 mock 沙箱参考耗时；live 无真实统计源时返回空列表。"""
        mode = cls.ensure_enabled()
        if mode != 'mock':
            return []
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
        if not has_request_context():
            raise RechargeContractError('充值操作需要有效的客户会话')
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
        if not isinstance(redeem_code, str):
            raise RechargeContractError('CDK 卡密必须是文本')
        if not isinstance(token_input, str):
            raise RechargeContractError('充值凭证必须是文本')
        if not isinstance(plan_type, str):
            raise RechargeContractError('套餐类型必须是文本')
        code = redeem_code.strip()
        credential = token_input.strip()
        plan = plan_type.strip()
        if not 4 <= len(code) <= 120:
            raise RechargeContractError('CDK 卡密格式无效')
        if plan not in cls.ALLOWED_PLANS:
            raise RechargeContractError('必须提供有效的套餐类型')
        if (not credential and plan != 'FINISHED') or len(credential) > 65535:
            raise RechargeContractError('必须提供有效的充值凭证')
        if not isinstance(is_renewal, bool):
            raise RechargeContractError('续费模式必须为布尔值')
        card_info = cls.validate_redeem_code(code)
        actual_plan = card_info.get('plan_type')
        if actual_plan != plan:
            raise RechargeContractError(f'CDK 实际套餐为 {actual_plan}，与提交套餐不匹配')
        if is_renewal and card_info.get('is_renewal_supported') is not True:
            raise RechargeContractError('该卡密不支持续费模式')
        cls._session_context(create=True)
        token = f"ch_{secrets.token_urlsafe(32)}"
        OneTimeToken.register('recharge_submission', token, {
            'plan_type': actual_plan,
            'validation_status': card_info.get('status'),
        }, 300,
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
        token_payload = record.payload if isinstance(record.payload, dict) else {}
        if token_payload.get('plan_type') != plan_type:
            raise RechargeContractError('校验令牌中的 CDK 套餐与提交套餐不匹配')
        consumed = OneTimeToken.consume('recharge_submission', challenge_token, binding=binding)
        if consumed is None:
            raise RechargeContractError('校验令牌已被使用或失效，请重新获取')
        if consumed.get('plan_type') != plan_type:
            raise RechargeContractError('校验令牌中的 CDK 套餐与提交套餐不匹配')

    # ---------------- CDK 验证 ----------------

    @classmethod
    def validate_redeem_code(cls, redeem_code):
        from app.services.cdk_service import guard_legacy_code
        guard_legacy_code(redeem_code)
        return cls._validate_provider_code(redeem_code)

    @classmethod
    def _validate_provider_code(cls, redeem_code):
        """验证 CDK 卡密有效性与对应套餐"""
        mode = cls.ensure_enabled()
        if not isinstance(redeem_code, str):
            raise RechargeContractError("CDK 卡密必须是文本")
        code = redeem_code.strip()
        if not 4 <= len(code) <= 120:
            raise RechargeContractError("请输入有效的 CDK 卡密（长度须在 4-120 字符之间）")

        # 检查是否已被关闭销毁
        latest_task = cls._find_latest_task_by_code(code)
        if latest_task and latest_task.status == 'closed':
            raise RechargeContractError("该卡密已被关闭注销，无法再次使用")

        if mode == 'live':
            upstream_res = cls._upstream_post("/user/redeem-codes/validate", {"redeem_code": code})
            if not isinstance(upstream_res, dict) or upstream_res.get("ok") is not True:
                raise RechargeUpstreamError("上游校验卡密失败，请稍后查询")
            return cls._public_validation_result(upstream_res.get("result", {}))

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
            "account_change_locked": False,
            "is_renewal_supported": True
        }

    # ---------------- 契约核验：创建任务 ----------------

    @classmethod
    def validate_task_creation_contract(cls, data):
        """严格核验创建任务的输入契约与授权确认"""
        if not isinstance(data, dict):
            raise RechargeContractError("请求格式错误，必须为 JSON 对象")

        raw_redeem_code = data.get("redeem_code")
        if not isinstance(raw_redeem_code, str):
            raise RechargeContractError("CDK 卡密必须是文本")
        redeem_code = raw_redeem_code.strip()
        if not redeem_code or len(redeem_code) < 4 or len(redeem_code) > 120:
            raise RechargeContractError("CDK 卡密格式无效，长度须在 4-120 字符之间")

        raw_plan_type = data.get("plan_type")
        if not isinstance(raw_plan_type, str):
            raise RechargeContractError("套餐类型必须是文本")
        plan_type = raw_plan_type.strip()
        if plan_type not in cls.ALLOWED_PLANS:
            raise RechargeContractError(f"不支持的套餐类型：{plan_type}，支持的套餐：{', '.join(cls.ALLOWED_PLANS)}")

        raw_token_input = data.get("token_input", '')
        if raw_token_input is None:
            raw_token_input = ''
        if not isinstance(raw_token_input, str):
            raise RechargeContractError("充值凭证必须是文本")
        token_input = raw_token_input.strip()
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

        raw_account_email = data.get("account_email")
        if not isinstance(raw_account_email, str):
            raise RechargeContractError("账号邮箱必须是文本")
        account_email = raw_account_email.strip()
        if len(account_email) > 256 or not account_email or not cls.EMAIL_PATTERN.fullmatch(account_email):
            raise RechargeContractError("请提供有效的账号接收邮箱格式")

        # 授权确认：必须明确同意协议与确认邮箱
        if data.get("agreement_accepted") is not True:
            raise RechargeContractError("必须阅读并同意《充值用户服务协议》后方可提交")

        if data.get("email_verified") is not True:
            raise RechargeContractError("请核对并确认账号邮箱无误")

        raw_challenge_token = data.get("challenge_token")
        if not isinstance(raw_challenge_token, str):
            raise RechargeContractError("缺少防刷校验令牌 (challenge_token)，请重新获取")
        challenge_token = raw_challenge_token.strip()
        if not challenge_token or len(challenge_token) > 128:
            raise RechargeContractError("防刷校验令牌格式无效")
        # 校验并消费挑战令牌
        is_renewal = data.get('is_renewal', False)
        if not isinstance(is_renewal, bool):
            raise RechargeContractError('续费模式必须为布尔值')
        acknowledge_non_free = data.get('acknowledge_non_free', False)
        if not isinstance(acknowledge_non_free, bool):
            raise RechargeContractError('非免费套餐确认字段必须为布尔值')
        if plan_type != 'FINISHED' and not acknowledge_non_free:
            raise RechargeContractError('非免费套餐必须明确确认服务价格与覆盖规则')
        notify_channel = data.get('notify_channel', 'site')
        if not isinstance(notify_channel, str) or notify_channel not in cls.NOTIFY_CHANNELS:
            raise RechargeContractError('通知渠道无效，仅支持 site')
        raw_notify_email = data.get('notify_email', account_email)
        if not isinstance(raw_notify_email, str):
            raise RechargeContractError('通知邮箱必须是文本')
        notify_email = raw_notify_email.strip()
        if len(notify_email) > 256 or not cls.EMAIL_PATTERN.fullmatch(notify_email):
            raise RechargeContractError('通知邮箱格式无效')
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
            "acknowledge_non_free": acknowledge_non_free,
            "notify_channel": notify_channel,
            "notify_email": notify_email
        }

    @classmethod
    def create_task(cls, payload):
        """创建充值任务（先完成契约核验）"""
        mode = cls.ensure_enabled()
        from app.services.cdk_service import guard_legacy_code
        guard_legacy_code(payload.get('redeem_code') if isinstance(payload, dict) else None)
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
                return db_task.to_public_dict()

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
        snapshot = cls._task_snapshot(db_task)

        try:
            upstream_res = cls._upstream_post("/user/tasks", upstream_payload)
        except RechargeUpstreamError as err:
            # 网络超时/连接失败：更新为 unknown 待核对状态，保留数据库凭据以便幂等查验与对账恢复
            cls._mark_unknown(task_no, snapshot)
            raise
        except Exception as err:
            cls._mark_unknown(task_no, snapshot)
            raise RechargeUpstreamError('请求上游接口异常，任务待核对') from err

        # 3. 校验上游业务响应结果
        if not isinstance(upstream_res, dict) or upstream_res.get('ok') is not True:
            cls._mark_unknown(task_no, snapshot)
            raise RechargeUpstreamError('上游尚未确认任务结果，请查询核对，不要重复提交')

        # 4. 上游明确受理成功，更新为最终处理中状态
        persisted = RechargeTask.query.filter_by(task_no=task_no).one()
        try:
            return cls._reconcile_task(
                persisted, upstream_res.get('task'), require_client_task_no=True, snapshot=snapshot
            )
        except RechargeUpstreamError:
            cls._mark_unknown(task_no, snapshot)
            raise

    @classmethod
    def _mark_unknown(cls, task_no, snapshot=None):
        if snapshot is not None:
            task = RechargeTask.query.filter_by(task_no=task_no).one()
            if not cls._lock_task_snapshot(task, snapshot):
                db.session.rollback()
                return
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
    def _normalize_reconciliation_payload(cls, task_no, payload):
        """校验受信管理员提交的归档元数据；这里不宣称完成了上游验签。"""
        if not isinstance(task_no, str) or not 1 <= len(task_no.strip()) <= 64:
            raise RechargeContractError('任务编号格式无效')
        if not isinstance(payload, dict):
            raise RechargeContractError('请求数据格式错误')
        if payload.get('confirmed') is not True:
            raise RechargeContractError('人工对账必须显式确认 confirmed=True')

        resolution = payload.get('resolution')
        if not isinstance(resolution, str) or resolution not in {'created', 'not_created'}:
            raise RechargeContractError('对账结论仅支持 created 或 not_created')

        evidence = payload.get('evidence')
        if not isinstance(evidence, dict):
            raise RechargeContractError('必须提供已归档的上游证据')
        source = evidence.get('source')
        reference = evidence.get('reference')
        evidence_sha256 = evidence.get('sha256')
        observed_at = evidence.get('observed_at')
        if not isinstance(source, str) or source not in cls.RECONCILIATION_EVIDENCE_SOURCES:
            raise RechargeContractError('上游证据来源无效')
        if (
            not isinstance(reference, str)
            or not 8 <= len(reference.strip()) <= 256
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in reference)
        ):
            raise RechargeContractError('上游证据引用格式无效')
        if not isinstance(evidence_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', evidence_sha256):
            raise RechargeContractError('上游证据必须提供有效的 SHA-256')
        if not isinstance(observed_at, str) or not 1 <= len(observed_at) <= 40:
            raise RechargeContractError('上游证据观测时间格式无效')
        try:
            parsed_observed_at = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        except ValueError as error:
            raise RechargeContractError('上游证据观测时间格式无效') from error
        if parsed_observed_at.tzinfo is None:
            raise RechargeContractError('上游证据观测时间必须包含时区')
        observed_utc = parsed_observed_at.astimezone(timezone.utc)
        if observed_utc > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise RechargeContractError('上游证据观测时间不能晚于当前时间')

        normalized = {
            'task_no': task_no.strip(),
            'resolution': resolution,
            'evidence_source': source,
            'evidence_reference': reference.strip(),
            'evidence_sha256': evidence_sha256.lower(),
            'evidence_observed_at': observed_utc.replace(tzinfo=None),
            'remote_task': None,
        }
        remote = payload.get('upstream_task')
        if resolution == 'not_created':
            if remote not in (None, {}):
                raise RechargeContractError('未创建结论不得携带上游任务')
            return normalized

        if not isinstance(remote, dict):
            raise RechargeContractError('已创建结论必须提供可关联的上游任务')
        status = remote.get('status')
        if status == 'success':
            status = 'completed'
        if not isinstance(status, str) or status not in {'pending', 'processing', 'completed', 'failed', 'recalled', 'closed'}:
            raise RechargeContractError('上游任务状态不能证明任务已创建')
        upstream_task_no = remote.get('task_no')
        client_task_no = remote.get('client_task_no')
        if (
            not isinstance(upstream_task_no, str)
            or not 1 <= len(upstream_task_no.strip()) <= 128
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in upstream_task_no)
        ):
            raise RechargeContractError('上游任务编号格式无效')
        if not isinstance(client_task_no, str) or client_task_no != normalized['task_no']:
            raise RechargeContractError('上游证据未精确关联本地幂等任务')
        remote_code = remote.get('redeem_code')
        remote_email = remote.get('account_email')
        if remote_code is not None and not isinstance(remote_code, str):
            raise RechargeContractError('上游任务卡密格式无效')
        if remote_email is not None and not isinstance(remote_email, str):
            raise RechargeContractError('上游任务账号格式无效')
        card_last4 = remote.get('card_last4')
        if card_last4 is not None and (
            not isinstance(card_last4, str) or not re.fullmatch(r'\d{4}', card_last4)
        ):
            raise RechargeContractError('上游任务卡号尾号格式无效')
        normalized['remote_task'] = {
            'task_no': upstream_task_no.strip(),
            'client_task_no': client_task_no,
            'status': status,
            'redeem_code': remote_code,
            'account_email': remote_email,
            'card_last4': card_last4,
        }
        return normalized

    @staticmethod
    def _reconciliation_fingerprint(normalized):
        fingerprint_payload = dict(normalized)
        fingerprint_payload['evidence_observed_at'] = normalized[
            'evidence_observed_at'
        ].replace(tzinfo=timezone.utc).isoformat()
        encoded = json.dumps(
            fingerprint_payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        ).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _reconciliation_evidence_fingerprint(normalized):
        evidence_identity = [
            normalized['evidence_source'],
            normalized['evidence_reference'],
            normalized['evidence_sha256'],
        ]
        encoded = json.dumps(
            evidence_identity, separators=(',', ':'), ensure_ascii=False,
        ).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _existing_reconciliation_result(cls, task_no, request_fingerprint):
        existing = RechargeReconciliation.query.filter_by(task_no=task_no).first()
        if existing is None:
            return None
        if not hmac.compare_digest(existing.request_fingerprint, request_fingerprint):
            raise RechargeReconciliationConflictError('该任务已有不同的人工对账结论')
        task = RechargeTask.query.filter_by(task_no=task_no).one()
        return {
            'task': task.to_public_dict(),
            'reconciliation': existing.to_dict(),
            'replayed': True,
        }

    @classmethod
    def reconcile_unknown_task(cls, task_no, payload, actor_id):
        """依据已归档上游证据，一次性关闭 unknown 创建请求。"""
        mode = cls.ensure_enabled()
        if mode != 'live':
            raise RechargeContractError('人工对账仅适用于 live 充值任务')
        if not isinstance(actor_id, str) or not re.fullmatch(r'admin:[0-9a-f]{24}', actor_id):
            raise RechargeContractError('管理员审计身份无效')

        normalized = cls._normalize_reconciliation_payload(task_no, payload)
        task_no = normalized['task_no']
        request_fingerprint = cls._reconciliation_fingerprint(normalized)
        evidence_fingerprint = cls._reconciliation_evidence_fingerprint(normalized)
        try:
            return cls._apply_unknown_task_reconciliation(
                normalized, request_fingerprint, evidence_fingerprint, actor_id,
            )
        except RechargeReconciliationConflictError:
            # Another identical request may commit between the audit lookup and
            # any later state check. Read its result from a fresh transaction.
            db.session.rollback()
            replay = cls._existing_reconciliation_result(task_no, request_fingerprint)
            if replay is not None:
                return replay
            raise

    @classmethod
    def _apply_unknown_task_reconciliation(
        cls, normalized, request_fingerprint, evidence_fingerprint, actor_id,
    ):
        task_no = normalized['task_no']
        replay = cls._existing_reconciliation_result(task_no, request_fingerprint)
        if replay is not None:
            return replay

        reused_evidence = RechargeReconciliation.query.filter(
            RechargeReconciliation.evidence_fingerprint == evidence_fingerprint,
            RechargeReconciliation.task_no != task_no,
        ).first()
        if reused_evidence is not None:
            raise RechargeReconciliationConflictError('该上游证据已绑定其他本地任务')

        task = RechargeTask.query.filter_by(task_no=task_no, is_mock=False).first()
        if task is None:
            raise RechargeContractError('未找到待对账的 live 任务')
        if task.status != 'unknown':
            raise RechargeReconciliationConflictError('仅 unknown 任务允许人工对账')
        if task.created_at:
            earliest = task.created_at.replace(tzinfo=timezone.utc) - timedelta(minutes=5)
            observed = normalized['evidence_observed_at'].replace(tzinfo=timezone.utc)
            if observed < earliest:
                raise RechargeContractError('上游证据早于任务创建时间，不能用于对账')

        operation = db.session.get(RechargeOperation, task_no)
        if operation is None:
            raise RechargeReconciliationConflictError('任务缺少原始幂等操作记录，禁止人工改写')
        mutation = db.session.get(RechargeMutation, task_no)
        if mutation is not None and mutation.state in {'pending', 'unknown'}:
            raise RechargeReconciliationConflictError('任务仍有执行中或待核对操作，禁止人工解锁')

        remote = normalized['remote_task']
        if remote is not None:
            if remote['redeem_code'] not in (None, '', task.redeem_code):
                raise RechargeContractError('上游任务与本地卡密不匹配')
            if remote['account_email'] not in (None, '', task.account_email):
                raise RechargeContractError('上游任务与本地账号不匹配')
            if operation.upstream_task_no not in (None, remote['task_no']):
                raise RechargeReconciliationConflictError('上游任务编号与原幂等记录冲突')
            occupied_operation = RechargeOperation.query.filter(
                RechargeOperation.upstream_task_no == remote['task_no'],
                RechargeOperation.task_no != task_no,
            ).first()
            if occupied_operation is not None:
                raise RechargeReconciliationConflictError('该上游任务编号已绑定其他本地任务')
            final_status = remote['status']
            upstream_task_no = remote['task_no']
        else:
            if operation.upstream_task_no is not None:
                raise RechargeReconciliationConflictError('原幂等记录已关联上游任务，不能判定未创建')
            final_status = 'failed'
            upstream_task_no = None

        reconciliation = RechargeReconciliation(
            task_no=task_no,
            resolution=normalized['resolution'],
            request_fingerprint=request_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
            evidence_source=normalized['evidence_source'],
            evidence_reference=normalized['evidence_reference'],
            evidence_sha256=normalized['evidence_sha256'],
            evidence_observed_at=normalized['evidence_observed_at'],
            upstream_task_no=upstream_task_no,
            upstream_status=remote['status'] if remote else None,
            previous_status='unknown',
            final_status=final_status,
            actor_id=actor_id,
        )

        try:
            db.session.add(reconciliation)
            db.session.flush()
            active_mutation = RechargeMutation.query.filter(
                RechargeMutation.task_no == task_no,
                RechargeMutation.state.in_(('pending', 'unknown')),
            ).exists()
            values = {
                'status': final_status,
                'status_text': cls.TASK_STATUS_TEXT.get(final_status, final_status),
                'notice': (
                    '状态已由管理员依据已归档的上游证据完成人工核对。'
                    if remote else '上游已明确确认任务未创建；原幂等记录保留，可重新提交。'
                ),
                'updated_at': datetime.now(timezone.utc).replace(tzinfo=None),
            }
            if remote and remote['card_last4']:
                values['card_last4'] = remote['card_last4']
            updated = RechargeTask.query.filter(
                RechargeTask.id == task.id,
                RechargeTask.status == 'unknown',
                ~active_mutation,
            ).update(values, synchronize_session=False)
            if updated != 1:
                raise RechargeReconciliationConflictError('任务状态已变化或仍有待核对操作')

            if remote:
                bound = RechargeOperation.query.filter(
                    RechargeOperation.task_no == task_no,
                    or_(
                        RechargeOperation.upstream_task_no.is_(None),
                        RechargeOperation.upstream_task_no == upstream_task_no,
                    ),
                ).update({'upstream_task_no': upstream_task_no}, synchronize_session=False)
                if bound != 1:
                    raise RechargeReconciliationConflictError('上游任务编号绑定失败')
                if final_status in {'failed', 'recalled'}:
                    operation.active_key = None
            else:
                operation.active_key = None

            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            replay = cls._existing_reconciliation_result(task_no, request_fingerprint)
            if replay is not None:
                return replay
            raise RechargeReconciliationConflictError('该任务已被其他对账请求处理') from error
        except Exception:
            db.session.rollback()
            raise

        db.session.refresh(task)
        db.session.refresh(reconciliation)
        return {
            'task': task.to_public_dict(),
            'reconciliation': reconciliation.to_dict(),
            'replayed': False,
        }

    @classmethod
    def get_task_mutation(cls, task_no):
        if cls.ensure_enabled() != 'live':
            raise RechargeContractError('操作对账仅适用于 live 充值任务')
        if not isinstance(task_no, str) or not 1 <= len(task_no) <= 64:
            raise RechargeContractError('任务编号格式无效')
        row = db.session.query(RechargeTask, RechargeMutation).join(
            RechargeMutation, RechargeMutation.task_no == RechargeTask.task_no,
        ).filter(RechargeTask.task_no == task_no, RechargeTask.is_mock.is_(False)).first()
        if row is None:
            raise RechargeContractError('未找到该 live 任务的操作记录')
        task, mutation = row
        return {
            'task': task.to_public_dict(),
            'mutation': {
                'task_no': task.task_no, 'operation_id': mutation.operation_id,
                'action': mutation.action, 'state': mutation.state,
                'started_at': datetime.fromtimestamp(mutation.started_at, timezone.utc).isoformat(),
            },
        }

    @classmethod
    def _existing_mutation_reconciliation_result(cls, operation_id, request_fingerprint):
        audit = db.session.get(RechargeMutationReconciliation, operation_id)
        if audit is None:
            return None
        if not hmac.compare_digest(audit.request_fingerprint, request_fingerprint):
            raise RechargeReconciliationConflictError('该操作已有不同的人工对账结论')
        task = RechargeTask.query.filter_by(task_no=audit.task_no).populate_existing().one()
        return {'task': task.to_public_dict(), 'reconciliation': audit.to_dict(), 'replayed': True}

    @classmethod
    def reconcile_unknown_mutation(cls, task_no, operation_id, payload, actor_id):
        if cls.ensure_enabled() != 'live':
            raise RechargeContractError('人工对账仅适用于 live 充值任务')
        if not isinstance(actor_id, str) or not re.fullmatch(r'admin:[0-9a-f]{24}', actor_id):
            raise RechargeContractError('管理员审计身份无效')
        if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', operation_id):
            raise RechargeContractError('操作编号格式无效')
        if not isinstance(payload, dict):
            raise RechargeContractError('请求数据格式错误')
        action = payload.get('action')
        if not isinstance(action, str) or action not in {'recall', 'close'}:
            raise RechargeContractError('操作类型仅支持 recall 或 close')
        if payload.get('resolution') != 'not_applied':
            raise RechargeContractError('操作失败结案必须明确确认 not_applied')
        basis = payload.get('basis')
        if (
            not isinstance(basis, str) or not 8 <= len(basis.strip()) <= 1024
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in basis)
        ):
            raise RechargeContractError('必须提供该操作未执行的归档证据依据，不能仅凭超时或任务查询结案')
        normalized = cls._normalize_reconciliation_payload(task_no, {**payload, 'resolution': 'created'})
        normalized.update(operation_id=operation_id, action=action, resolution='not_applied', basis=basis.strip())
        fingerprint = cls._reconciliation_fingerprint(normalized)
        try:
            return cls._apply_unknown_mutation_reconciliation(normalized, fingerprint, actor_id)
        except (RechargeReconciliationConflictError, IntegrityError) as error:
            db.session.rollback()
            replay = cls._existing_mutation_reconciliation_result(operation_id, fingerprint)
            if replay is not None:
                return replay
            raise RechargeReconciliationConflictError('操作状态已变化或证据已被使用，请重新核对') from error
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def _apply_unknown_mutation_reconciliation(cls, normalized, fingerprint, actor_id):
        task_no = normalized['task_no']
        operation_id = normalized['operation_id']
        action = normalized['action']
        replay = cls._existing_mutation_reconciliation_result(operation_id, fingerprint)
        if replay is not None:
            return replay
        task = RechargeTask.query.filter_by(task_no=task_no, is_mock=False).first()
        if task is None:
            raise RechargeContractError('未找到待对账的 live 任务')
        snapshot = cls._task_snapshot(task)
        if snapshot[2:5] != (operation_id, action, 'unknown'):
            raise RechargeReconciliationConflictError('任务、操作编号、类型或 unknown 状态不匹配')
        if not cls._lock_task_snapshot(task, snapshot):
            raise RechargeReconciliationConflictError('任务或操作状态已变化')
        db.session.expire_all()
        mutation = db.session.get(RechargeMutation, task_no)
        operation = db.session.get(RechargeOperation, task_no)
        if operation is None:
            raise RechargeReconciliationConflictError('任务缺少原始幂等操作记录')
        if normalized['evidence_observed_at'].replace(tzinfo=timezone.utc).timestamp() < mutation.started_at:
            raise RechargeContractError('上游证据早于本次操作，不能用于失败结案')
        remote = normalized['remote_task']
        try:
            upstream_no = cls._validate_upstream_task_identity(task, remote, operation, require_client_task_no=True)
        except RechargeUpstreamError as error:
            raise RechargeContractError(str(error)) from error
        final_status = remote['status']
        if final_status == {'recall': 'recalled', 'close': 'closed'}[action]:
            raise RechargeContractError('上游任务已处于该操作成功终态，不能判定未执行')
        previous_status = task.status
        if (
            previous_status in {'completed', 'success', 'failed', 'recalled', 'closed'}
            and final_status != ('completed' if previous_status == 'success' else previous_status)
        ) or (previous_status == 'processing' and final_status == 'pending'):
            raise RechargeReconciliationConflictError('证据与本地已确认状态冲突，不能回退任务状态')
        evidence_fingerprint = cls._reconciliation_evidence_fingerprint(normalized)
        if RechargeReconciliation.query.filter_by(evidence_fingerprint=evidence_fingerprint).first():
            raise RechargeReconciliationConflictError('创建对账证据不能用于本次操作失败结案')
        audit = RechargeMutationReconciliation(
            operation_id=operation_id, task_no=task_no, action=action,
            resolution='not_applied', basis=normalized['basis'], request_fingerprint=fingerprint,
            evidence_fingerprint=evidence_fingerprint,
            evidence_source=normalized['evidence_source'], evidence_reference=normalized['evidence_reference'],
            evidence_sha256=normalized['evidence_sha256'], evidence_observed_at=normalized['evidence_observed_at'],
            mutation_started_at=mutation.started_at, previous_state='unknown', final_state='rejected',
            upstream_task_no=upstream_no, previous_status=previous_status, final_status=final_status,
            actor_id=actor_id,
        )
        updated = RechargeMutation.query.filter_by(
            task_no=task_no, operation_id=operation_id, action=action,
            state='unknown', started_at=mutation.started_at,
        ).update({'state': 'rejected'}, synchronize_session=False)
        if updated != 1:
            raise RechargeReconciliationConflictError('操作状态已变化，禁止覆盖')
        task.status = final_status
        task.status_text = cls.TASK_STATUS_TEXT[final_status]
        task.notice = '管理员已依据归档证据确认本次操作未执行；后续操作将使用新的操作编号。'
        task.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if remote['card_last4']:
            task.card_last4 = remote['card_last4']
        operation.upstream_task_no = upstream_no
        if final_status in {'failed', 'recalled'}:
            operation.active_key = None
        db.session.add(audit)
        db.session.commit()
        return {'task': task.to_public_dict(), 'reconciliation': audit.to_dict(), 'replayed': False}

    @classmethod
    def _persist_new_task(cls, task, mode):
        operation = RechargeOperation(
            task_no=task.task_no,
            active_key=cls._fingerprint(f'{mode}:{task.redeem_code}'),
        )
        try:
            db.session.add(task)
            db.session.flush()
            from app.services.cdk_service import guard_legacy_code
            guard_legacy_code(task.redeem_code)
            RechargeTaskAccess.bind(task.task_no)
            db.session.add(operation)
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            raise RechargeContractError('该卡密已有正在执行或待核对中的充值任务，请勿重复提交') from error
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def _public_remote_task(cls, remote, is_mock=False):
        """仅暴露任务状态字段，避免把上游回传的卡密、邮箱或凭证透传给匿名查询者。"""
        if not isinstance(remote, dict):
            raise RechargeUpstreamError('上游任务结果格式无效')
        plan_type = remote.get('plan_type')
        if plan_type not in cls.ALLOWED_PLANS:
            raise RechargeUpstreamError('上游任务套餐类型无效')
        status = remote.get('status')
        if status == 'success':
            status = 'completed'
        if status not in cls.TASK_STATUS_TEXT:
            raise RechargeUpstreamError('上游任务状态无效')
        safe = {'plan_type': plan_type, 'status': status, 'is_mock': bool(is_mock)}
        if 'id' in remote:
            if isinstance(remote['id'], bool) or not isinstance(remote['id'], (int, str)):
                raise RechargeUpstreamError('上游任务标识格式无效')
            if isinstance(remote['id'], str) and not cls._is_safe_public_text(remote['id'], 64):
                raise RechargeUpstreamError('上游任务标识格式无效')
            safe['id'] = remote['id']
        for field, limit in (
            ('task_no', 128), ('status_text', 128), ('notice', 1024),
            ('created_at', 64), ('updated_at', 64),
        ):
            if field in remote:
                if not cls._is_safe_public_text(remote[field], limit):
                    raise RechargeUpstreamError('上游任务文本字段格式无效')
                safe[field] = remote[field]
        if 'card_last4' in remote:
            if not isinstance(remote['card_last4'], str) or not re.fullmatch(r'\d{4}|\*{4}', remote['card_last4']):
                raise RechargeUpstreamError('上游任务卡号尾号格式无效')
            safe['card_last4'] = remote['card_last4']
        if 'is_renewal' in remote:
            if not isinstance(remote['is_renewal'], bool):
                raise RechargeUpstreamError('上游任务续费字段格式无效')
            safe['is_renewal'] = remote['is_renewal']
        return safe

    @classmethod
    def _public_validation_result(cls, result):
        """过滤 live 卡密校验结果，禁止上游把凭证或 PII 透传给匿名客户端。"""
        if not isinstance(result, dict):
            raise RechargeUpstreamError('上游卡密校验结果格式无效')
        plan_type = result.get('plan_type')
        status = result.get('status')
        if plan_type not in cls.ALLOWED_PLANS or status not in cls.VALID_REDEEM_STATUSES:
            raise RechargeUpstreamError('上游卡密校验结果缺少必要字段')
        for field in ('product', 'plan_name'):
            if field in result and not cls._is_safe_public_text(result[field], 128):
                raise RechargeUpstreamError('上游卡密校验文本字段格式无效')
        for field in ('account_change_locked', 'is_renewal_supported'):
            if field in result and not isinstance(result[field], bool):
                raise RechargeUpstreamError('上游卡密校验布尔字段格式无效')
        public = {key: result[key] for key in cls.VALIDATION_PUBLIC_FIELDS if key in result}
        # 上游响应不可信，live 结果必须由本地运行模式决定，不能透传 is_mock。
        public['is_mock'] = False
        return public

    @classmethod
    def _public_billing_result(cls, result, require_subscription=True):
        """过滤 live 账单结果及发票字段，只保留页面所需的显示数据。"""
        if not isinstance(result, dict):
            raise RechargeUpstreamError('上游账单结果格式无效')
        status = result.get('status')
        if not cls._is_safe_public_text(status, 128):
            raise RechargeUpstreamError('上游账单结果缺少状态字段')
        if require_subscription and not isinstance(result.get('auto_renew'), bool):
            raise RechargeUpstreamError('上游账单结果缺少自动续费状态')
        for field in ('has_active_subscription', 'auto_renew'):
            if field in result and not isinstance(result[field], bool):
                raise RechargeUpstreamError('上游账单布尔字段格式无效')
        for field in ('plan_name', 'card_brand', 'next_billing_date'):
            if field in result and not cls._is_safe_public_text(result[field], 128):
                raise RechargeUpstreamError('上游账单文本字段格式无效')
        if 'card_last4' in result:
            card_last4 = result['card_last4']
            if not isinstance(card_last4, str) or not re.fullmatch(r'\d{4}|\*{4}', card_last4):
                raise RechargeUpstreamError('上游账单卡号尾号格式无效')
        public = {key: result[key] for key in cls.BILLING_PUBLIC_FIELDS if key in result}
        public['status'] = status
        # 上游响应不可信，live 结果必须由本地运行模式决定，不能透传 is_mock。
        public['is_mock'] = False
        invoices = result.get('invoices', [])
        if not isinstance(invoices, list):
            raise RechargeUpstreamError('上游账单发票列表格式无效')
        public['invoices'] = []
        for invoice in invoices[:100]:
            if not isinstance(invoice, dict):
                raise RechargeUpstreamError('上游账单发票项格式无效')
            safe_invoice = {}
            for field in ('id', 'slug', 'date', 'status'):
                if field in invoice:
                    if not cls._is_safe_public_text(invoice[field], 128):
                        raise RechargeUpstreamError('上游账单发票文本字段格式无效')
                    safe_invoice[field] = invoice[field]
            if 'amount' in invoice:
                amount = invoice['amount']
                if isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
                    raise RechargeUpstreamError('上游账单金额字段格式无效')
                if isinstance(amount, str) and not cls._is_safe_public_text(amount, 64):
                    raise RechargeUpstreamError('上游账单金额字段格式无效')
                if isinstance(amount, float) and (amount != amount or amount in (float('inf'), float('-inf'))):
                    raise RechargeUpstreamError('上游账单金额字段格式无效')
                safe_invoice['amount'] = amount
            public['invoices'].append(safe_invoice)
        return public

    @staticmethod
    def _is_safe_public_text(value, max_length):
        """仅允许有限长度、无控制字符的上游展示文本。"""
        return (
            isinstance(value, str)
            and 0 < len(value) <= max_length
            and not any(ord(char) < 0x20 or ord(char) == 0x7f for char in value)
        )

    @classmethod
    def _public_billing_mutation_result(cls, result, action=None):
        """过滤取消/恢复续费结果，兼容只返回状态字段的上游 mutation 响应。"""
        if not isinstance(result, dict):
            raise RechargeUpstreamError('上游账单变更结果格式无效')
        status = result.get('status')
        auto_renew = result.get('auto_renew')
        if status is not None and not cls._is_safe_public_text(status, 128):
            raise RechargeUpstreamError('上游账单变更状态格式无效')
        if auto_renew is not None and not isinstance(auto_renew, bool):
            raise RechargeUpstreamError('上游自动续费状态格式无效')
        if status is None and auto_renew is None:
            raise RechargeUpstreamError('上游账单变更结果缺少状态字段')
        normalized_status = status.casefold() if isinstance(status, str) else None
        if action == 'cancel':
            if auto_renew is True or normalized_status in {'active', 'renewing', 'enabled'}:
                raise RechargeUpstreamError('上游取消续费结果与请求动作不一致')
            if auto_renew is not False and normalized_status not in {
                'canceled', 'cancelled', 'canceled_at_period_end', 'disabled',
            }:
                raise RechargeUpstreamError('上游未确认自动续费已取消')
        elif action == 'resume':
            if auto_renew is False or normalized_status in {
                'canceled', 'cancelled', 'canceled_at_period_end', 'disabled',
            }:
                raise RechargeUpstreamError('上游恢复续费结果与请求动作不一致')
            if auto_renew is not True and normalized_status not in {'active', 'renewing', 'enabled'}:
                raise RechargeUpstreamError('上游未确认自动续费已恢复')
        public = {}
        if status is not None:
            public['status'] = status
        if auto_renew is not None:
            public['auto_renew'] = auto_renew
        public['is_mock'] = False
        return public

    @classmethod
    def _billing_credential_hash(cls, token):
        secret = current_app.config.get('GMAIL_TOKEN_ENCRYPTION_KEY')
        if not secret and current_app.testing:
            # Existing isolated tests may not exercise Gmail configuration. Production
            # always requires the long-lived Fernet key before live mode can start.
            secret = current_app.secret_key
        if isinstance(secret, str):
            try:
                secret = secret.encode('ascii')
            except UnicodeEncodeError as error:
                raise RechargeContractError(
                    'GMAIL_TOKEN_ENCRYPTION_KEY 必须是有效的 Fernet 密钥'
                ) from error
        if not isinstance(secret, bytes) or not secret:
            raise RechargeContractError(
                '账单操作需要配置长期稳定的 GMAIL_TOKEN_ENCRYPTION_KEY'
            )
        billing_hmac_key = hmac.new(
            secret,
            b'google-manager:key-derivation:billing-credential:v1',
            hashlib.sha256,
        ).digest()
        return hmac.new(
            billing_hmac_key,
            b'credential\0' + token.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def _billing_live_mutation(cls, token, action):
        endpoint = {
            'cancel': '/tools/billing/cancel-subscription',
            'resume': '/tools/billing/resume-subscription',
        }[action]
        credential_hash = cls._billing_credential_hash(token)
        claim = RechargeBillingMutation.claim(credential_hash, action)
        if claim['status'] == 'busy':
            raise RechargeContractError('订阅变更正在处理中，请稍后查询状态')
        if claim['status'] != 'acquired':
            raise RechargeContractError('前次订阅变更结果待核对，请先查询账单状态')

        operation_id = claim['operation_id']
        lease_token = claim['lease_token']
        try:
            upstream_res = cls._upstream_post(endpoint, {
                'token_input': token,
                'idempotency_key': operation_id,
            })
            if not isinstance(upstream_res, dict) or upstream_res.get('ok') is not True:
                raise RechargeUpstreamError('上游订阅变更结果未知，请查询核对后再操作')
            public = cls._public_billing_mutation_result(
                upstream_res.get('result', {}), action=action
            )
        except Exception as error:
            db.session.rollback()
            RechargeBillingMutation.mark_unknown(
                credential_hash,
                operation_id,
                lease_token,
            )
            if isinstance(error, (RechargeContractError, RechargeUpstreamError)):
                raise
            raise RechargeUpstreamError('请求上游订阅变更接口异常，结果待核对') from error

        target_auto_renew = RechargeBillingMutation.ACTION_TARGETS[action]
        result_status = public.get('status') or (
            'active' if target_auto_renew else 'canceled_at_period_end'
        )
        if not RechargeBillingMutation.finish(
            credential_hash,
            operation_id,
            action,
            target_auto_renew,
            result_status,
        ):
            raise RechargeUpstreamError('订阅变更已确认，但本地操作状态发生变化，请立即查询核对')
        return public

    @classmethod
    def _validate_upstream_task_identity(cls, task, remote, operation, require_client_task_no=False):
        # 初次受理必须回显本地幂等号；后续尚未绑定远端编号时，至少
        # 回显本地任务号或精确卡密，避免仅凭状态把其它任务写回本地。
        client_matches = remote.get('client_task_no') == task.task_no
        code_matches = remote.get('redeem_code') == task.redeem_code
        if not operation or not operation.upstream_task_no:
            if require_client_task_no and not client_matches:
                raise RechargeUpstreamError('上游任务缺少本地关联编号，拒绝回写状态')
            if not require_client_task_no and not (client_matches or code_matches):
                raise RechargeUpstreamError('上游任务缺少可验证的本地身份，拒绝回写状态')
        if remote.get('redeem_code') not in (None, '', task.redeem_code):
            raise RechargeUpstreamError('上游任务与请求卡密不匹配')
        if remote.get('account_email') not in (None, '', task.account_email):
            raise RechargeUpstreamError('上游任务与请求账号不匹配')
        if remote.get('client_task_no') not in (None, '', task.task_no):
            raise RechargeUpstreamError('上游任务与本地操作编号不匹配')
        upstream_no = remote.get('task_no')
        if upstream_no is not None:
            if (
                not isinstance(upstream_no, str)
                or not 1 <= len(upstream_no.strip()) <= 128
                or any(ord(char) < 0x20 or ord(char) == 0x7f for char in upstream_no)
            ):
                raise RechargeUpstreamError('上游任务编号格式无效')
            upstream_no = upstream_no.strip()
        if operation and operation.upstream_task_no:
            if upstream_no is None and not (client_matches or code_matches):
                raise RechargeUpstreamError('上游任务缺少已关联任务编号或可验证的本地身份，拒绝回写状态')
            if upstream_no is not None and upstream_no != operation.upstream_task_no:
                raise RechargeUpstreamError('上游任务编号与已关联任务不匹配')
        if upstream_no and RechargeOperation.query.filter(
            RechargeOperation.upstream_task_no == upstream_no,
            RechargeOperation.task_no != task.task_no,
        ).first() is not None:
            raise RechargeUpstreamError('上游任务编号已绑定其他本地任务，拒绝回写状态')
        return upstream_no

    @classmethod
    def _task_snapshot(cls, task):
        return tuple(db.session.query(
            RechargeTask.status, RechargeTask.updated_at,
            RechargeMutation.operation_id, RechargeMutation.action,
            RechargeMutation.state, RechargeMutation.started_at,
        ).outerjoin(RechargeMutation, RechargeMutation.task_no == RechargeTask.task_no).filter(
            RechargeTask.id == task.id,
        ).one())

    @classmethod
    def _lock_task_snapshot(cls, task, snapshot):
        status, updated_at, operation_id, action, state, started_at = snapshot
        mutation = RechargeMutation.query.filter_by(task_no=task.task_no)
        if operation_id is None:
            mutation_matches = ~mutation.exists()
        else:
            mutation_matches = mutation.filter_by(
                operation_id=operation_id, action=action, state=state, started_at=started_at,
            ).exists()
        return RechargeTask.query.filter(
            RechargeTask.id == task.id, RechargeTask.status == status,
            RechargeTask.updated_at == updated_at, mutation_matches,
        ).update({'updated_at': RechargeTask.updated_at}, synchronize_session=False) == 1

    @classmethod
    def _reconcile_task(cls, task, remote, require_client_task_no=False, snapshot=None):
        snapshot = snapshot if snapshot is not None else cls._task_snapshot(task)
        try:
            if not cls._lock_task_snapshot(task, snapshot):
                db.session.rollback()
                db.session.refresh(task)
                return task.to_public_dict()
            db.session.expire_all()
            result = cls._apply_upstream_task(task, remote, require_client_task_no)
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise

    @classmethod
    def _apply_upstream_task(cls, task, remote, require_client_task_no=False):
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
        operation = db.session.get(RechargeOperation, task.task_no)
        upstream_no = cls._validate_upstream_task_identity(task, remote, operation, require_client_task_no)
        mutation = db.session.get(RechargeMutation, task.task_no)
        if mutation and mutation.state == 'pending':
            return task.to_public_dict()
        if mutation and mutation.state == 'unknown':
            expected_mutation_status = {
                'recall': 'recalled',
                'close': 'closed',
            }.get(mutation.action)
            if status != expected_mutation_status:
                raise RechargeUpstreamError('上游操作结果与本地操作不一致，需要人工核对')
        if task.status in ('completed', 'success', 'failed', 'recalled', 'closed'):
            if mutation and mutation.state == 'unknown':
                if mutation.action == 'close' and status == 'closed':
                    RechargeTask.query.filter_by(id=task.id, status=task.status).filter(
                        ~RechargeMutation.query.filter_by(task_no=task.task_no, state='pending').exists(),
                    ).update({'status': 'closed', 'status_text': states['closed']}, synchronize_session=False)
                    RechargeMutation.finish(task.task_no, mutation.operation_id, 'done')
                    db.session.commit()
                    db.session.refresh(task)
                    return task.to_public_dict()
                if status != task.status:
                    raise RechargeUpstreamError('上下游终态不一致，需要人工核对')
                if mutation.action == 'close' and status != 'closed':
                    raise RechargeUpstreamError('关闭操作结果尚未确认，需要人工核对')
                RechargeMutation.finish(task.task_no, mutation.operation_id, 'done')
                if operation and status in ('failed', 'recalled'):
                    operation.active_key = None
                db.session.commit()
            return task.to_public_dict()
        try:
            if operation is None:
                operation = RechargeOperation(task_no=task.task_no)
                db.session.add(operation)
                db.session.flush()
            if upstream_no:
                bound = RechargeOperation.query.filter(
                    RechargeOperation.task_no == task.task_no,
                    or_(RechargeOperation.upstream_task_no.is_(None),
                        RechargeOperation.upstream_task_no == upstream_no),
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
                ~RechargeMutation.query.filter_by(task_no=task.task_no, state='pending').exists(),
            ).update(values, synchronize_session=False)
            if updated and status in ('failed', 'recalled'):
                operation.active_key = None
            if updated and mutation and status in ('completed', 'failed', 'recalled', 'closed'):
                if mutation.action != 'close' or status == 'closed':
                    RechargeMutation.finish(task.task_no, mutation.operation_id, 'done')
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if upstream_no and RechargeOperation.query.filter(
                RechargeOperation.upstream_task_no == upstream_no,
                RechargeOperation.task_no != task.task_no,
            ).first() is not None:
                raise RechargeUpstreamError(
                    '上游任务编号已绑定其他本地任务，拒绝回写状态'
                ) from error
            raise
        db.session.refresh(task)
        return task.to_public_dict()

    # ---------------- 任务查询（单卡密 / 任务号 / 批量） ----------------

    @classmethod
    def lookup_task(cls, redeem_code):
        """按卡密查询任务进度（修复死锁：不持锁调用可能加锁的子方法）"""
        mode = cls.ensure_enabled()
        from app.services.cdk_service import guard_legacy_code
        guard_legacy_code(redeem_code)
        if not isinstance(redeem_code, str):
            raise RechargeContractError("CDK 卡密或任务编号必须是文本")
        code = redeem_code.strip()
        if not code or len(code) > 128:
            raise RechargeContractError("请输入有效的 CDK 卡密或任务编号")
        if not code.upper().startswith("TK-") and len(code) < 4:
            raise RechargeContractError("CDK 卡密长度须在 4-120 字符之间")
        if not code.upper().startswith("TK-") and len(code) > 120:
            raise RechargeContractError("CDK 卡密长度须在 4-120 字符之间")

        # 若以 TK- 开头则按任务流水号精准查询
        if code.upper().startswith("TK-"):
            return cls.get_task_by_no(code)

        task = cls._find_latest_task_by_code(code)
        if mode == 'live':
            snapshot = cls._task_snapshot(task) if task else None
            upstream_res = cls._upstream_post("/user/tasks/lookup", {"redeem_code": code})
            if not isinstance(upstream_res, dict) or upstream_res.get('ok') is not True:
                raise RechargeUpstreamError('上游任务查询失败，本地状态未变更')
            if task:
                return cls._reconcile_task(task, upstream_res.get('task'), snapshot=snapshot)
            remote = upstream_res.get('task')
            if not isinstance(remote, dict) or not remote.get('status'):
                raise RechargeUpstreamError('上游缺少有效的任务状态')
            if remote.get('redeem_code') != code:
                raise RechargeUpstreamError('上游任务与请求卡密不匹配')
            return cls._public_remote_task(remote, is_mock=False)

        # 本地持久化查询最新任务
        task = cls._find_latest_task_by_code(code)
        if task:
            return task.to_public_dict()

        # 无任务记录：返回待提交初始卡密信息
        card_info = cls.validate_redeem_code(code)
        return {
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
        if not isinstance(task_no, str):
            raise RechargeContractError("任务流水号必须是文本")
        no = task_no.strip()
        if not no or len(no) > 128:
            raise RechargeContractError("任务流水号格式无效")

        task = RechargeTask.query.filter_by(task_no=no, is_mock=mode == 'mock').first()
        if not task:
            raise RechargeContractError(f"未找到流水号为 {no} 的任务记录")
        from app.models.cdk import CdkRedemption
        managed = CdkRedemption.query.filter_by(task_no=no).first()
        if managed:
            from app.services.cdk_redemption import refresh
            return refresh(managed.id)['task']
        if mode == 'live':
            snapshot = cls._task_snapshot(task)
            operation = db.session.get(RechargeOperation, task.task_no)
            if not operation or not operation.upstream_task_no:
                upstream_res = cls._upstream_post('/user/tasks/lookup', {'redeem_code': task.redeem_code})
            else:
                upstream_res = cls._upstream_get(f"/user/tasks/{urllib.parse.quote(operation.upstream_task_no, safe='')}")
            if not isinstance(upstream_res, dict) or upstream_res.get('ok') is not True:
                raise RechargeUpstreamError('上游任务查询失败，本地状态未变更')
            cls._reconcile_task(task, upstream_res.get('task'), snapshot=snapshot)
        return task.to_public_dict()

    @classmethod
    def lookup_batch_tasks(cls, redeem_codes):
        """批量查询多个卡密状态（先去重，上限 50 个）"""
        mode = cls.ensure_enabled()
        if not isinstance(redeem_codes, list):
            raise RechargeContractError("卡密列表格式错误")
        # 去重且保序
        clean_codes = []
        for raw_code in redeem_codes:
            if not isinstance(raw_code, str):
                raise RechargeContractError("卡密列表中的每一项都必须是文本")
            code = raw_code.strip()
            if not code:
                continue
            if not 4 <= len(code) <= 120:
                raise RechargeContractError("每个 CDK 卡密长度须在 4-120 字符之间")
            if code not in clean_codes:
                clean_codes.append(code)
        if not clean_codes:
            raise RechargeContractError("请提供至少一个有效卡密")
        if len(clean_codes) > 50:
            raise RechargeContractError("单次批量查询最多支持 50 个卡密")

        if mode == 'live':
            local_tasks = {code: cls._find_latest_task_by_code(code) for code in clean_codes}
            snapshots = {
                code: cls._task_snapshot(task) for code, task in local_tasks.items() if task is not None
            }
            upstream_res = cls._upstream_post("/user/tasks/lookup-batch", {"redeem_codes": clean_codes})
            if isinstance(upstream_res, dict) and upstream_res.get("ok") is True:
                results = upstream_res.get('results')
                if not isinstance(results, list):
                    raise RechargeUpstreamError('上游批量查询结果格式无效')
                # 上游必须对每个去重后的请求卡密返回且只返回一次；否则不能
                # 将部分或错配的结果作为成功响应交给调用方。
                result_by_code = {}
                for result in results:
                    if not isinstance(result, dict):
                        raise RechargeUpstreamError('上游批量查询结果格式无效')
                    code = result.get('redeem_code')
                    if code not in clean_codes:
                        raise RechargeUpstreamError('上游批量查询任务不匹配')
                    if code in result_by_code:
                        raise RechargeUpstreamError('上游批量查询结果包含重复卡密')
                    result_by_code[code] = result
                if set(result_by_code) != set(clean_codes):
                    raise RechargeUpstreamError('上游批量查询结果缺少请求卡密')

                safe_results = []
                # 以请求侧去重后的顺序输出，避免上游返回顺序变化导致前端
                # 将状态误绑定到另一张卡密。
                for code in clean_codes:
                    result = result_by_code[code]
                    task = local_tasks[code]
                    safe_result = {
                        'ok': result.get('ok') is True,
                        'redeem_code': code,
                    }
                    if task and result.get('ok') is True:
                        remote_task = result.get('task', result)
                        if isinstance(remote_task, dict) and remote_task is not result:
                            remote_task = dict(remote_task)
                            remote_task.setdefault('redeem_code', code)
                        safe_result.update(cls._reconcile_task(task, remote_task, snapshot=snapshots[code]))
                    else:
                        remote_task = result.get('task', result)
                        if isinstance(remote_task, dict):
                            safe_result.update(cls._public_remote_task(remote_task, is_mock=False))
                        if result.get('message') is not None:
                            # 上游错误文本可能包含凭证或邮箱，匿名批量查询只返回固定提示。
                            safe_result['message'] = '上游未确认该卡密的任务状态'
                    safe_results.append(safe_result)
                return safe_results
            raise RechargeUpstreamError("批量查询上游接口返回异常，请稍后重试")

        # Mock / 本地持久化查询
        results = []
        for code in clean_codes:
            task = cls._find_latest_task_by_code(code)
            if task:
                public = task.to_public_dict()
                results.append({
                    "ok": True,
                    "redeem_code": code,
                    "plan_type": public["plan_type"],
                    "status": public["status"],
                    "status_text": public["status_text"],
                    "created_at": public["created_at"] or "",
                    "is_mock": True
                })
            else:
                results.append({
                    "ok": False,
                    "redeem_code": code,
                    "plan_type": "未知",
                    "status": "not_found",
                    "status_text": "未提交任务",
                    "created_at": "-",
                    "is_mock": True
                })
        return results

    # ---------------- 状态机控制：写操作（撤回 / 关闭） ----------------

    @classmethod
    def _request_mutation(cls, task, action, mode, operation_id=None, expected_status=None):
        expected_status = expected_status or task.status
        if operation_id is None:
            operation_id = RechargeMutation.claim(task, action, expected_status=expected_status)
        try:
            if mode == 'live':
                response = cls._upstream_post('/user/tasks/' + action, {
                    'redeem_code': task.redeem_code, 'email': task.account_email,
                    'client_task_no': task.task_no, 'idempotency_key': operation_id,
                    'expected_status': expected_status,
                })
                if not isinstance(response, dict) or response.get('ok') is not True:
                    raise RechargeUpstreamError('上游尚未确认操作结果，请查询对账，禁止重复操作')
                remote = response.get('task')
                expected_terminal = {'recall': 'recalled', 'close': 'closed'}.get(action)
                if (
                    not isinstance(remote, dict)
                    or remote.get('client_task_no') != task.task_no
                    or remote.get('status') != expected_terminal
                    or remote.get('redeem_code') not in (None, '', task.redeem_code)
                ):
                    raise RechargeUpstreamError('上游操作结果缺少可验证的本地身份或终态')
                cls._validate_upstream_task_identity(
                    task, remote, db.session.get(RechargeOperation, task.task_no), require_client_task_no=True,
                )
            return operation_id
        except Exception:
            db.session.rollback()
            RechargeMutation.finish(task.task_no, operation_id, 'unknown')
            db.session.commit()
            raise

    @classmethod
    def recall_task(cls, data):
        """
        撤回任务（写操作）
        必须核验：卡密、邮箱与显式授权确认 (confirmed is True)，仅限未终结任务
        """
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")

        raw_code = data.get("redeem_code")
        raw_email = data.get("email")
        from app.services.cdk_service import guard_legacy_code
        guard_legacy_code(raw_code)
        if not isinstance(raw_code, str) or not isinstance(raw_email, str):
            raise RechargeContractError("卡密和账号邮箱必须是文本")
        code = raw_code.strip()
        email = raw_email.strip()
        confirmed = data.get("confirmed")

        if not code or not email:
            raise RechargeContractError("撤回任务须提供完整的卡密与账号邮箱")
        if confirmed is not True:
            raise RechargeContractError("撤回操作须显式获得用户授权确认 (confirmed=True)")

        with cls._lock:
            task = cls._find_latest_task_by_code(code)
            if not task:
                raise RechargeContractError("未找到对应卡密的充值任务")
            if data.get('task_no') is not None and data.get('task_no') != task.task_no:
                raise RechargeContractError('任务已变化，请重新查询后操作')
            if task.account_email != email:
                raise RechargeContractError("提供的账号邮箱与任务绑定邮箱不匹配，无权撤回")
            if task.status not in ('pending', 'processing'):
                raise RechargeContractError(f"当前任务状态为「{task.status_text}」，不允许执行撤回操作")
            expected_status = task.status
            operation_id = RechargeMutation.claim(task, 'recall', expected_status=expected_status)

        # 上游网络 I/O 必须在进程锁外执行，避免一个慢请求阻塞同进程其它任务。
        cls._request_mutation(task, 'recall', mode, operation_id, expected_status)

        with cls._lock:
            updated = RechargeTask.query.filter(
                RechargeTask.id == task.id,
                RechargeTask.status == expected_status,
                RechargeMutation.query.filter_by(
                    task_no=task.task_no, operation_id=operation_id, action='recall',
                ).filter(RechargeMutation.state.in_(('pending', 'unknown'))).exists(),
            ).update({'status': 'recalled', 'status_text': '已撤回（可重新提交）',
                      'notice': '任务已撤回，您可以核对凭证后重新提交。'}, synchronize_session=False)
            if updated != 1:
                db.session.rollback()
                RechargeMutation.finish(task.task_no, operation_id, 'unknown')
                db.session.commit()
                raise RechargeContractError('任务状态已变化，请刷新查询后再操作')
            operation = db.session.get(RechargeOperation, task.task_no)
            if operation:
                operation.active_key = None
            if not RechargeMutation.finish(task.task_no, operation_id, 'done'):
                db.session.rollback()
                raise RechargeContractError('操作状态已变化，请重新查询')
            db.session.commit()
            db.session.refresh(task)
            return task.to_public_dict()

    @classmethod
    def close_task(cls, data):
        """
        关闭并销毁任务（破坏性写操作）
        必须核验：卡密、邮箱与显式销毁确认 (confirmed is True)
        """
        mode = cls.ensure_enabled()
        if not isinstance(data, dict):
            raise RechargeContractError("请求数据格式错误")

        raw_code = data.get("redeem_code")
        raw_email = data.get("email")
        from app.services.cdk_service import guard_legacy_code
        guard_legacy_code(raw_code)
        if not isinstance(raw_code, str) or not isinstance(raw_email, str):
            raise RechargeContractError("卡密和账号邮箱必须是文本")
        code = raw_code.strip()
        email = raw_email.strip()
        confirmed = data.get("confirmed")

        if not code or not email:
            raise RechargeContractError("关闭任务须提供完整的卡密与账号邮箱")
        if confirmed is not True:
            raise RechargeContractError("关闭任务将销毁卡密并终结任务，须显式二次确认 (confirmed=True)")

        with cls._lock:
            task = cls._find_latest_task_by_code(code)
            if not task:
                raise RechargeContractError("未找到对应卡密的充值任务")
            if data.get('task_no') is not None and data.get('task_no') != task.task_no:
                raise RechargeContractError('任务已变化，请重新查询后操作')
            if task.account_email != email:
                raise RechargeContractError("提供的账号邮箱与任务绑定邮箱不匹配，无权关闭")
            if task.status == 'closed':
                raise RechargeContractError("任务已处于关闭注销状态，无需重复关闭")
            expected_status = task.status
            operation_id = RechargeMutation.claim(task, 'close', expected_status=expected_status)

        # 上游网络 I/O 必须在进程锁外执行，避免一个慢请求阻塞同进程其它任务。
        cls._request_mutation(task, 'close', mode, operation_id, expected_status)

        with cls._lock:
            updated = RechargeTask.query.filter_by(id=task.id, status=expected_status).filter(
                RechargeMutation.query.filter_by(
                    task_no=task.task_no, operation_id=operation_id, action='close',
                ).filter(RechargeMutation.state.in_(('pending', 'unknown'))).exists(),
            ).update(
                {'status': 'closed', 'status_text': '已关闭（卡密已注销）',
                 'notice': '任务已终结，对应 CDK 卡密已注销作废。'}, synchronize_session=False)
            if updated != 1:
                db.session.rollback()
                RechargeMutation.finish(task.task_no, operation_id, 'unknown')
                db.session.commit()
                raise RechargeContractError('任务状态已变化，请刷新查询后再操作')
            if not RechargeMutation.finish(task.task_no, operation_id, 'done'):
                db.session.rollback()
                raise RechargeContractError('操作状态已变化，请重新查询')
            db.session.commit()
            db.session.refresh(task)
            return task.to_public_dict()

    # ---------------- 账单与自动续费工作台 ----------------

    @classmethod
    def _mock_subscription_key(cls, token):
        context = cls._session_context(create=True)
        return cls._fingerprint(json.dumps([context, token], separators=(',', ':')))

    @classmethod
    def billing_query(cls, token_input):
        """查询账单与绑卡状态"""
        mode = cls.ensure_enabled()
        if not isinstance(token_input, str):
            raise RechargeContractError("Session Token 或登录凭证必须是文本")
        token = token_input.strip()
        if not token or len(token) > 65535:
            raise RechargeContractError("请输入有效的 Session Token 或登录凭证")

        if mode == 'live':
            credential_hash = cls._billing_credential_hash(token)
            mutation_snapshot = RechargeBillingMutation.snapshot(credential_hash)
            upstream_res = cls._upstream_post("/tools/billing/query", {"token_input": token})
            if not isinstance(upstream_res, dict) or upstream_res.get("ok") is not True:
                raise RechargeUpstreamError("查询上游账单状态失败，请稍后重试")
            public = cls._public_billing_result(upstream_res.get("result", {}))
            RechargeBillingMutation.reconcile_query(
                credential_hash,
                mutation_snapshot,
                public['auto_renew'],
                public['status'],
            )
            return public

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
        raw_token = data.get("token_input")
        if not isinstance(raw_token, str):
            raise RechargeContractError("账号凭证必须是文本")
        token = raw_token.strip()
        if len(token) > 65535:
            raise RechargeContractError("账号凭证内容过长")
        confirmed = data.get("confirmed")

        if not token:
            raise RechargeContractError("请提供账号凭证")
        if confirmed is not True:
            raise RechargeContractError("取消续费属于变更订阅写操作，须明确授权确认 (confirmed=True)")

        if mode == 'live':
            return cls._billing_live_mutation(token, 'cancel')

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
        raw_token = data.get("token_input")
        if not isinstance(raw_token, str):
            raise RechargeContractError("账号凭证必须是文本")
        token = raw_token.strip()
        if len(token) > 65535:
            raise RechargeContractError("账号凭证内容过长")
        confirmed = data.get("confirmed")

        if not token:
            raise RechargeContractError("请提供账号凭证")
        if confirmed is not True:
            raise RechargeContractError("恢复续费属于变更订阅写操作，须明确授权确认 (confirmed=True)")

        if mode == 'live':
            return cls._billing_live_mutation(token, 'resume')

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
    def _read_upstream_json(cls, response, timeout=8):
        content_length = response.headers.get('Content-Length') if response.headers else None
        try:
            if content_length is not None and int(content_length) > cls.MAX_UPSTREAM_RESPONSE_BYTES:
                raise RechargeUpstreamError('上游响应过大，已拒绝读取')
        except (TypeError, ValueError):
            # 非法 Content-Length 交给实际读取上限处理，不信任该 header。
            pass
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise RechargeUpstreamError('上游响应读取超时配置无效')
        deadline = getattr(response, 'deadline', time.monotonic() + timeout)
        reader = getattr(response, 'read1', None)
        if not callable(reader):
            reader = response.read
        raw = bytearray()
        while len(raw) <= cls.MAX_UPSTREAM_RESPONSE_BYTES:
            if time.monotonic() >= deadline:
                raise RechargeUpstreamError('上游响应读取超时')
            chunk = reader(min(
                cls.UPSTREAM_READ_CHUNK_BYTES,
                cls.MAX_UPSTREAM_RESPONSE_BYTES + 1 - len(raw),
            ))
            if not isinstance(chunk, (bytes, bytearray)):
                raise RechargeUpstreamError('上游响应过大或格式无效')
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > cls.MAX_UPSTREAM_RESPONSE_BYTES:
                raise RechargeUpstreamError('上游响应过大或格式无效')
            # read1 performs at most one raw socket read. That in-flight read may
            # cross the deadline once, but remains bounded by urllib's socket timeout.
            if time.monotonic() >= deadline:
                raise RechargeUpstreamError('上游响应读取超时')
        try:
            return json.loads(bytes(raw).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RechargeUpstreamError('上游响应不是有效 JSON') from error

    @classmethod
    def _upstream_base_url(cls):
        """读取并在非测试运行时再次校验 live 上游地址。

        工厂启动时已经执行非测试配置门禁，但配置对象可能在进程运行期间被
        覆盖。运行时复核避免把客户凭证发送到明文或未授权主机；测试环境
        保留隔离的 loopback HTTP 上游契约测试。
        """
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            app = None
        base = app.config.get('RECHARGE_UPSTREAM_URL') if app else None
        if (
            app is not None
            and not app.testing
            and str(app.config.get('RECHARGE_MODE', 'disabled')).lower().strip() == 'live'
            and app.config.get('RECHARGE_UPSTREAM_URL_EXPLICIT') is not True
        ):
            raise RechargeUpstreamError('非测试环境 live 充值必须显式配置 RECHARGE_UPSTREAM_URL')
        if not isinstance(base, str) or not base.strip():
            raise RechargeUpstreamError('充值上游地址未配置')
        base = base.strip().rstrip('/')
        if app is None or app.testing:
            return base
        try:
            parsed = urlsplit(base)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            raise RechargeUpstreamError('充值上游地址未通过安全校验') from error
        allowed_hosts = {
            item.strip().lower().rstrip('.')
            for item in str(app.config.get('RECHARGE_UPSTREAM_ALLOWED_HOSTS', '')).split(',')
            if item.strip()
        }
        if (
            parsed.scheme != 'https'
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or hostname.lower().rstrip('.') not in allowed_hosts
        ):
            raise RechargeUpstreamError('充值上游地址未通过 HTTPS/主机白名单校验')
        return base

    @classmethod
    def _upstream_post(cls, endpoint, payload, timeout=8):
        base = cls._upstream_base_url()
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
            opener = urllib.request.build_opener(
                _RejectRedirectHandler(),
                _DeadlineHTTPHandler(),
                _DeadlineHTTPSHandler(),
            )
            with opener.open(req, timeout=timeout) as resp:
                return cls._read_upstream_json(resp, timeout=timeout)
        except urllib.error.HTTPError as err:
            err.close()
            return {'ok': False, 'message': f'上游接口返回错误 HTTP {err.code}'}
        except urllib.error.URLError as err:
            raise RechargeUpstreamError('无法连接上游充值服务，请查询核对任务结果') from err
        except Exception as err:
            raise RechargeUpstreamError('请求上游接口异常，请查询核对任务结果') from err

    @classmethod
    def _upstream_get(cls, endpoint, timeout=8):
        base = cls._upstream_base_url()
        url = f"{base}{endpoint}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GoogleManager/1.0"
                }
            )
            opener = urllib.request.build_opener(
                _RejectRedirectHandler(),
                _DeadlineHTTPHandler(),
                _DeadlineHTTPSHandler(),
            )
            with opener.open(req, timeout=timeout) as resp:
                return cls._read_upstream_json(resp, timeout=timeout)
        except urllib.error.HTTPError as err:
            err.close()
            return {'ok': False, 'message': f'上游接口返回错误 HTTP {err.code}'}
        except urllib.error.URLError as err:
            raise RechargeUpstreamError('无法连接上游充值服务，本地状态未变更') from err
        except Exception as err:
            raise RechargeUpstreamError('请求上游接口异常，本地状态未变更') from err
