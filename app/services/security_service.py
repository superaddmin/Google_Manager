"""
集中邮箱安全与防盗服务模块
提供全库安全体检、Gmail 隐蔽转发与恶意规则排查、集中验证码与安全告警提取、以及一键应急防盗锁号
"""
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from app import db
from app.models.account import Account, utc_now
from app.models.account_history import AccountHistory
from app.models.gmail_connection import GmailConnection
from app.services.gmail_service import GmailService, GmailServiceError

# 常见验证码正则
OTP_PATTERNS = [
    # Google 验证码格式 G-123456
    (re.compile(r'\b(G-\d{6})\b', re.IGNORECASE), 'Google'),
    # 纯 6 位数字验证码 (常见如 Telegram, Twitter, 各种短信/邮件 OTP)
    (re.compile(r'(?:验证码|code|pin|verification code|verification)\D{0,10}(\b\d{6}\b)', re.IGNORECASE), 'General 6-Digit'),
    # 独立 6 位连续数字
    (re.compile(r'\b(\d{6})\b'), '6-Digit OTP'),
    # 4 位数字或 8 位代码
    (re.compile(r'(?:验证码|code)\D{0,10}(\b\d{4,8}\b)', re.IGNORECASE), 'Variable OTP'),
]

# 常见发件服务识别特征
KNOWN_SERVICES = [
    ('google', 'Google'),
    ('telegram', 'Telegram'),
    ('twitter', 'Twitter / X'),
    ('x.com', 'Twitter / X'),
    ('discord', 'Discord'),
    ('github', 'GitHub'),
    ('microsoft', 'Microsoft'),
    ('apple', 'Apple'),
    ('openai', 'OpenAI'),
    ('binance', 'Binance'),
    ('okx', 'OKX'),
    ('facebook', 'Facebook / Meta'),
    ('instagram', 'Instagram'),
    ('amazon', 'Amazon'),
    ('netflix', 'Netflix'),
    ('steampowered', 'Steam'),
]

# 安全告警关键词
SECURITY_ALERT_KEYWORDS = [
    '安全提醒', 'security alert', '新设备登录', 'new sign-in',
    '密码已被更改', 'password changed', '异常活动', 'suspicious activity',
    '已授予访问权限', 'access granted', '二次验证', '2-step verification',
    '已停用', 'account disabled', '恢复邮箱已更改', 'recovery email changed'
]


class SecurityService:
    """集中邮箱安全与防盗服务"""

    @classmethod
    def calculate_account_risk(cls, account: Account) -> Dict[str, Any]:
        """
        评估单个账号的安全风险评级
        """
        risk_score = 0
        risk_factors = []

        # 1. 是否配置 2FA
        has_2fa = bool(account.secret and len(account.secret.strip()) >= 16)
        if not has_2fa:
            risk_score += 40
            risk_factors.append('未配置或未绑定有效 2FA 密钥')

        # 2. 是否配置恢复邮箱
        has_recovery = bool(account.recovery and '@' in account.recovery)
        if not has_recovery:
            risk_score += 35
            risk_factors.append('缺失安全恢复邮箱，无法在异常时找回')

        # 3. 密码安全分析
        pwd = account.password or ''
        if len(pwd) < 8:
            risk_score += 25
            risk_factors.append('密码长度不足 8 位，易遭受暴力破解')
        elif pwd.isalnum() and (pwd.isdigit() or pwd.isalpha()):
            risk_score += 15
            risk_factors.append('密码字符复杂度偏低')

        # 4. 账号状态检查
        is_locked = (account.status == 'locked')
        if is_locked:
            risk_factors.append('账号已处于应急锁定保护状态')

        # 评定风险等级
        if is_locked:
            level = 'locked'
            level_label = '已应急锁定'
        elif risk_score >= 60:
            level = 'critical'
            level_label = '高危被盗风险'
        elif risk_score >= 30:
            level = 'warning'
            level_label = '中度风险'
        else:
            level = 'safe'
            level_label = '安全良好'

        return {
            'accountId': account.id,
            'email': account.email,
            'riskScore': min(risk_score, 100),
            'riskLevel': level,
            'riskLevelLabel': level_label,
            'riskFactors': risk_factors,
            'has2FA': has_2fa,
            'hasRecovery': has_recovery,
            'isLocked': is_locked,
            'soldStatus': account.sold_status or 'unsold',
            'createdAt': account.created_at.strftime('%Y-%m-%d') if account.created_at else '',
        }

    @classmethod
    def get_security_overview(cls) -> Dict[str, Any]:
        """
        获取全库集中邮箱安全与防盗态势总览
        """
        accounts = Account.query.all()
        total_accounts = len(accounts)

        safe_count = 0
        warning_count = 0
        critical_count = 0
        locked_count = 0
        without_2fa_count = 0
        without_recovery_count = 0

        high_risk_list = []

        for acc in accounts:
            risk = cls.calculate_account_risk(acc)
            if not risk['has2FA']:
                without_2fa_count += 1
            if not risk['hasRecovery']:
                without_recovery_count += 1

            if risk['isLocked']:
                locked_count += 1
            elif risk['riskLevel'] == 'critical':
                critical_count += 1
                high_risk_list.append(risk)
            elif risk['riskLevel'] == 'warning':
                warning_count += 1
            else:
                safe_count += 1

        # Gmail 已授权连接数
        gmail_connections_count = GmailConnection.query.count()

        # 计算全库安全指数 (0 - 100)
        if total_accounts > 0:
            health_index = max(0, int(100 - (critical_count * 60 + warning_count * 25) / total_accounts))
        else:
            health_index = 100

        return {
            'totalAccounts': total_accounts,
            'healthIndex': health_index,
            'safeCount': safe_count,
            'warningCount': warning_count,
            'criticalCount': critical_count,
            'lockedCount': locked_count,
            'without2FACount': without_2fa_count,
            'withoutRecoveryCount': without_recovery_count,
            'gmailConnectionsCount': gmail_connections_count,
            'highRiskAccounts': high_risk_list[:20],
        }

    @classmethod
    def get_security_accounts(cls, filter_level: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        获取带安全评级的账号列表，支持按风险等级筛选
        """
        accounts = Account.query.order_by(Account.created_at.desc()).all()
        results = []
        for acc in accounts:
            risk = cls.calculate_account_risk(acc)
            if filter_level and filter_level != 'all':
                if risk['riskLevel'] != filter_level:
                    continue
            results.append(risk)
        return results

    @classmethod
    def audit_forwarding_and_filters(cls, connection: GmailConnection) -> Dict[str, Any]:
        """
        通过 Gmail API 扫描账号是否存在隐蔽外部自动转发、恶意过滤器以及 POP/IMAP 异常
        黑客常利用此手段偷盗验证码并销毁痕迹
        """
        service = GmailService._service(connection)
        findings = []
        has_suspicious_forwarding = False
        forwarding_address = None
        scan_errors = []

        # 1. 检查 Auto-Forwarding 设置
        try:
            auto_forward = service.users().settings().getAutoForwarding(userId='me').execute()
            is_enabled = auto_forward.get('enabled', False)
            forwarding_email = auto_forward.get('email', '')
            if is_enabled and forwarding_email:
                has_suspicious_forwarding = True
                forwarding_address = forwarding_email
                findings.append({
                    'type': 'auto_forwarding_enabled',
                    'severity': 'critical',
                    'title': '启用了静默自动转发',
                    'description': f'所有进入本邮箱的邮件正在自动转发至: {forwarding_email}',
                })
        except Exception as e:
            # 记录权限拒绝、企业策略或接口异常，不当作“安全/干净”
            scan_errors.append({'step': 'auto_forwarding', 'error': str(e)})

        # 2. 检查已配置的转发地址列表 (Forwarding Addresses)
        forwarding_addresses_list = []
        try:
            resp = service.users().settings().forwardingAddresses().list(userId='me').execute()
            forwarding_addresses_list = [
                item.get('forwardingEmail')
                for item in resp.get('forwardingAddresses', [])
                if item.get('forwardingEmail')
            ]
            if forwarding_addresses_list and not has_suspicious_forwarding:
                findings.append({
                    'type': 'forwarding_addresses_configured',
                    'severity': 'warning',
                    'title': '存在配置的转发邮箱白名单',
                    'description': f'已登记转发地址: {", ".join(forwarding_addresses_list)}',
                })
        except Exception as e:
            scan_errors.append({'step': 'forwarding_addresses', 'error': str(e)})

        # 3. 检查邮件过滤规则 (Filters) - 黑客常常设置规则：转发验证码并自动移入垃圾箱
        filters_list = []
        try:
            resp = service.users().settings().filters().list(userId='me').execute()
            for flt in resp.get('filter', []):
                action = flt.get('action', {})
                criteria = flt.get('criteria', {})

                is_suspicious = False
                reasons = []

                if action.get('forward'):
                    is_suspicious = True
                    has_suspicious_forwarding = True
                    reasons.append(f'包含转发动作 -> {action.get("forward")}')

                # 检查是否静默销毁敏感邮件
                labels_removed = action.get('removeLabelIds', [])
                labels_added = action.get('addLabelIds', [])
                if 'INBOX' in labels_removed or 'TRASH' in labels_added:
                    # 检查匹配条件是否针对验证码或官方安全邮件
                    query_text = (criteria.get('query', '') + ' ' + criteria.get('subject', '')).lower()
                    if any(k in query_text for k in ['code', 'verify', 'verification', 'security', 'alert', '验证码', '安全']):
                        is_suspicious = True
                        reasons.append('匹配验证码/安全邮件并静默跳过收件箱或直接移入垃圾箱')

                filter_info = {
                    'id': flt.get('id'),
                    'criteria': criteria,
                    'action': action,
                    'isSuspicious': is_suspicious,
                    'reasons': reasons,
                }
                filters_list.append(filter_info)
                if is_suspicious:
                    findings.append({
                        'type': 'suspicious_filter',
                        'severity': 'critical',
                        'title': f'发现可疑黑客规则 (Filter #{flt.get("id")})',
                        'description': '；'.join(reasons),
                    })
        except Exception as e:
            scan_errors.append({'step': 'filters', 'error': str(e)})

        # 4. 检查 POP / IMAP 状态
        imap_status = False
        pop_status = 'disabled'
        try:
            imap_resp = service.users().settings().getImap(userId='me').execute()
            imap_status = imap_resp.get('enabled', False)
        except Exception as e:
            scan_errors.append({'step': 'imap', 'error': str(e)})

        try:
            pop_resp = service.users().settings().getPop(userId='me').execute()
            pop_status = pop_resp.get('accessWindow', 'disabled')
        except Exception as e:
            scan_errors.append({'step': 'pop', 'error': str(e)})

        has_errors = len(scan_errors) > 0
        is_clean = (len(findings) == 0) and (not has_errors)
        status = 'error' if has_errors else ('warning' if findings else 'clean')

        return {
            'connectionId': connection.id,
            'email': connection.email,
            'hasSuspiciousForwarding': has_suspicious_forwarding,
            'forwardingAddress': forwarding_address,
            'forwardingAddressesList': forwarding_addresses_list,
            'totalFilters': len(filters_list),
            'suspiciousFiltersCount': sum(1 for f in filters_list if f['isSuspicious']),
            'imapEnabled': imap_status,
            'popStatus': pop_status,
            'findings': findings,
            'scanErrors': scan_errors,
            'hasErrors': has_errors,
            'status': status,
            'isClean': is_clean,
        }

    @classmethod
    def audit_all_forwarding_rules(cls) -> List[Dict[str, Any]]:
        """
        扫描全部已授权 Gmail 账号的隐蔽转发与过滤规则
        """
        connections = GmailConnection.query.order_by(GmailConnection.email).all()
        reports = []
        for conn in connections:
            try:
                reports.append(cls.audit_forwarding_and_filters(conn))
            except Exception as e:
                reports.append({
                    'connectionId': conn.id,
                    'email': conn.email,
                    'error': str(e),
                    'isClean': False,
                    'findings': [{
                        'type': 'connection_error',
                        'severity': 'warning',
                        'title': '检查连接失败',
                        'description': f'无法连接 Gmail API 进行扫描: {str(e)}',
                    }]
                })
        return reports

    @classmethod
    def extract_otp_from_text(cls, text: str) -> Optional[Dict[str, str]]:
        """
        从文本或邮件主题/正文中提取验证码和类型
        """
        if not text:
            return None

        # 优先匹配 Google G- 验证码
        g_match = re.search(r'\b(G-\d{6})\b', text, re.IGNORECASE)
        if g_match:
            return {'code': g_match.group(1).upper(), 'type': 'Google'}

        # 匹配上下文带 code/验证码 的数字
        ctx_match = re.search(r'(?:验证码|code|pin|verification code|verification|security code)[:：\s\t]{0,5}(\d{4,8})', text, re.IGNORECASE)
        if ctx_match:
            return {'code': ctx_match.group(1), 'type': 'Standard'}

        # 兜底匹配单独出现的 6 位数字
        pure_6 = re.search(r'(?<![0-9a-zA-Z])(\d{6})(?![0-9a-zA-Z])', text)
        if pure_6:
            return {'code': pure_6.group(1), 'type': '6-Digit'}

        return None

    @classmethod
    def detect_service_name(cls, sender: str, subject: str) -> str:
        """
        根据发件人与主题识别所属第三方服务
        """
        combined = f"{sender} {subject}".lower()
        for key, name in KNOWN_SERVICES:
            if key in combined:
                return name
        return '其他服务'

    @classmethod
    def is_security_alert(cls, subject: str, snippet: str) -> bool:
        """
        判断是否属于安全预警/异地登录等邮件
        """
        combined = f"{subject} {snippet}".lower()
        return any(keyword in combined for keyword in SECURITY_ALERT_KEYWORDS)

    @classmethod
    def get_central_otps_and_alerts(cls, limit_per_mailbox: int = 10) -> List[Dict[str, Any]]:
        """
        跨所有已授权 Gmail 集中拉取最新验证码 (OTP) 及官方安全告警
        运营无需登网页即可快速查码或监控异常登录
        """
        connections = GmailConnection.query.order_by(GmailConnection.email).all()
        aggregated_items = []

        query = 'subject:(验证码 OR code OR verification OR verify OR 安全 OR security OR alert OR "Sign-in")'

        for conn in connections:
            try:
                res = GmailService.list_messages(conn, query=query, max_results=limit_per_mailbox)
                messages = res.get('messages', [])
                for msg_summary in messages:
                    msg = GmailService.get_message(conn, msg_summary['id'])
                    subject = msg.get('subject', '')
                    sender = msg.get('from', '')
                    body = msg.get('body') or msg.get('snippet') or ''
                    date = msg.get('date', '')

                    extracted = cls.extract_otp_from_text(f"{subject}\n{body}")
                    service_name = cls.detect_service_name(sender, subject)
                    is_alert = cls.is_security_alert(subject, msg.get('snippet', ''))

                    aggregated_items.append({
                        'id': msg['id'],
                        'connectionId': conn.id,
                        'mailbox': conn.email,
                        'subject': subject,
                        'from': sender,
                        'date': date,
                        'snippet': msg.get('snippet', ''),
                        'extractedOtp': extracted.get('code') if extracted else None,
                        'otpType': extracted.get('type') if extracted else None,
                        'serviceName': service_name,
                        'isSecurityAlert': is_alert,
                    })
            except Exception:
                continue

        return aggregated_items

    @classmethod
    def lock_account(cls, account_id: int, reason: str = '异常安全防护锁定') -> Account:
        """
        一键应急锁号：将账号标记为 locked，阻止导出，并记录安全审计历史
        """
        account = db.session.get(Account, account_id)
        if not account:
            raise ValueError('账号不存在')

        old_status = account.status
        account.status = 'locked'

        history = AccountHistory(
            account_id=account.id,
            field_name='status',
            old_value=old_status,
            new_value='locked',
        )
        db.session.add(history)

        reason_history = AccountHistory(
            account_id=account.id,
            field_name='security_action',
            old_value='normal',
            new_value=f'EMERGENCY_LOCKED: {reason}',
        )
        db.session.add(reason_history)
        db.session.commit()
        return account

    @classmethod
    def unlock_account(cls, account_id: int) -> Account:
        """
        解除账号的应急锁定状态
        """
        account = db.session.get(Account, account_id)
        if not account:
            raise ValueError('账号不存在')

        if account.status != 'locked':
            return account

        account.status = 'inactive'
        history = AccountHistory(
            account_id=account.id,
            field_name='status',
            old_value='locked',
            new_value='inactive',
        )
        db.session.add(history)

        unlock_history = AccountHistory(
            account_id=account.id,
            field_name='security_action',
            old_value='EMERGENCY_LOCKED',
            new_value='UNLOCKED',
        )
        db.session.add(unlock_history)
        db.session.commit()
        return account
