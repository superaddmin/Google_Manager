import unittest
from unittest.mock import patch, MagicMock

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.gmail_connection import GmailConnection
from app.services.security_service import SecurityService


class SecurityServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(self.client)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_otp_extraction_and_service_detection(self):
        # 1. Google 验证码
        g_result = SecurityService.extract_otp_from_text('您的 Google 验证码为 G-948201。请勿向他人透露。')
        self.assertIsNotNone(g_result)
        self.assertEqual(g_result['code'], 'G-948201')
        self.assertEqual(g_result['type'], 'Google')

        # 2. 6 位标准验证码
        std_result = SecurityService.extract_otp_from_text('Telegram code 829103. Do not share.')
        self.assertIsNotNone(std_result)
        self.assertEqual(std_result['code'], '829103')

        # 3. 常见服务识别
        self.assertEqual(SecurityService.detect_service_name('service@telegram.org', 'Login code'), 'Telegram')
        self.assertEqual(SecurityService.detect_service_name('no-reply@accounts.google.com', 'Security Alert'), 'Google')
        self.assertEqual(SecurityService.detect_service_name('verify@x.com', 'Verification Code'), 'Twitter / X')

        # 4. 安全告警识别
        self.assertTrue(SecurityService.is_security_alert('新设备登录提醒', '我们检测到您的账号在新的 Windows 设备上登录'))
        self.assertTrue(SecurityService.is_security_alert('Security alert for your account', 'Password was changed'))
        self.assertFalse(SecurityService.is_security_alert('Newsletter #10', 'Welcome to our weekly updates'))

    def test_account_risk_scoring(self):
        # 高危账号：无 2FA、无恢复邮箱、弱密码
        acc_danger = Account(
            email='danger@example.com',
            password='123',
            recovery='',
            secret='',
            status='inactive',
        )
        db.session.add(acc_danger)

        # 安全账号：有 2FA、有恢复邮箱、高强度密码
        acc_safe = Account(
            email='safe@example.com',
            password='ComplexPassword#2026',
            recovery='backup@example.com',
            secret='JBSWY3DPEHPK3PXP',
            status='pro',
        )
        db.session.add(acc_safe)
        db.session.commit()

        risk_danger = SecurityService.calculate_account_risk(acc_danger)
        self.assertEqual(risk_danger['riskLevel'], 'critical')
        self.assertGreaterEqual(risk_danger['riskScore'], 60)
        self.assertFalse(risk_danger['has2FA'])
        self.assertFalse(risk_danger['hasRecovery'])

        risk_safe = SecurityService.calculate_account_risk(acc_safe)
        self.assertEqual(risk_safe['riskLevel'], 'safe')
        self.assertLess(risk_safe['riskScore'], 30)
        self.assertTrue(risk_safe['has2FA'])
        self.assertTrue(risk_safe['hasRecovery'])

    def test_emergency_lock_and_unlock(self):
        acc = Account(
            email='suspect@example.com',
            password='Password123!',
            status='inactive',
        )
        db.session.add(acc)
        db.session.commit()

        # 应急锁定
        locked_acc = SecurityService.lock_account(acc.id, reason='发现异地黑客尝试爆破')
        self.assertEqual(locked_acc.status, 'locked')
        risk = SecurityService.calculate_account_risk(locked_acc)
        self.assertTrue(risk['isLocked'])
        self.assertEqual(risk['riskLevel'], 'locked')

        # 历史记录中记录锁定
        histories = AccountHistory.query.filter_by(account_id=acc.id).all()
        self.assertTrue(any(h.field_name == 'security_action' and 'EMERGENCY_LOCKED' in h.new_value for h in histories))

        # 解除锁定
        unlocked_acc = SecurityService.unlock_account(acc.id)
        self.assertEqual(unlocked_acc.status, 'inactive')
        risk_unlocked = SecurityService.calculate_account_risk(unlocked_acc)
        self.assertFalse(risk_unlocked['isLocked'])

    def test_emergency_unlock_restores_pro_status(self):
        account = Account(
            email='pro-suspect@example.com',
            password='Password123!',
            status='pro',
        )
        db.session.add(account)
        db.session.commit()

        SecurityService.lock_account(account.id, reason='功能回归测试')
        unlocked_account = SecurityService.unlock_account(account.id)

        self.assertEqual(unlocked_account.status, 'pro')

    def test_security_overview_api(self):
        acc1 = Account(email='a1@example.com', password='123', status='inactive')
        acc2 = Account(email='a2@example.com', password='ComplexPass123!', recovery='rec@example.com', secret='JBSWY3DPEHPK3PXP')
        db.session.add_all([acc1, acc2])
        db.session.commit()

        response = self.client.get('/api/security/overview')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data']
        self.assertEqual(data['totalAccounts'], 2)
        self.assertIn('healthIndex', data)
        self.assertIn('criticalCount', data)
        self.assertIn('safeCount', data)

    def test_security_accounts_and_lock_api(self):
        acc = Account(email='victim@example.com', password='123', status='inactive')
        db.session.add(acc)
        db.session.commit()

        # 锁定
        lock_res = self.client.post(f'/api/security/accounts/{acc.id}/lock', json={'reason': '被盗预警'})
        self.assertEqual(lock_res.status_code, 200)
        self.assertTrue(lock_res.get_json()['data']['isLocked'])

        # 过滤查询
        filter_res = self.client.get('/api/security/accounts?filter=locked')
        self.assertEqual(filter_res.status_code, 200)
        locked_list = filter_res.get_json()['data']
        self.assertEqual(len(locked_list), 1)
        self.assertEqual(locked_list[0]['email'], 'victim@example.com')

        # 解锁
        unlock_res = self.client.post(f'/api/security/accounts/{acc.id}/unlock')
        self.assertEqual(unlock_res.status_code, 200)
        self.assertFalse(unlock_res.get_json()['data']['isLocked'])

    def test_forwarding_audit_with_mock(self):
        conn = GmailConnection(
            email='audit@example.test',
            token_data='token',
            scopes='https://www.googleapis.com/auth/gmail.modify',
        )
        db.session.add(conn)
        db.session.commit()

        mock_service = MagicMock()
        mock_settings = mock_service.users().settings()

        # 模拟存在黑客静默转发
        mock_settings.getAutoForwarding().execute.return_value = {
            'enabled': True,
            'email': 'hacker@malicious.test'
        }
        mock_settings.forwardingAddresses().list().execute.return_value = {
            'forwardingAddresses': [{'forwardingEmail': 'hacker@malicious.test'}]
        }
        mock_settings.filters().list().execute.return_value = {
            'filter': [{
                'id': 'filter-999',
                'criteria': {'query': 'verification code'},
                'action': {'forward': 'hacker@malicious.test', 'removeLabelIds': ['INBOX']}
            }]
        }
        mock_settings.getImap().execute.return_value = {'enabled': True}
        mock_settings.getPop().execute.return_value = {'accessWindow': 'allMail'}

        with patch('app.services.gmail_service.GmailService._service', return_value=mock_service):
            report = SecurityService.audit_forwarding_and_filters(conn)

        self.assertTrue(report['hasSuspiciousForwarding'])
        self.assertEqual(report['forwardingAddress'], 'hacker@malicious.test')
        self.assertFalse(report['isClean'])
        self.assertEqual(report['suspiciousFiltersCount'], 1)
        self.assertTrue(any(f['type'] == 'auto_forwarding_enabled' for f in report['findings']))


    def test_forwarding_audit_with_errors_not_clean(self):
        """测试 Issue 114：当 settings 查询异常时，不能吞掉异常并虚假报告 isClean=True"""
        conn = GmailConnection(
            email='error_audit@example.test',
            token_data='token',
            scopes='https://www.googleapis.com/auth/gmail.modify',
        )
        db.session.add(conn)
        db.session.commit()

        mock_service = MagicMock()
        mock_settings = mock_service.users().settings()
        # 模拟 API 报错（如权限不足或网络异常）
        mock_settings.getAutoForwarding().execute.side_effect = RuntimeError("403 Forbidden: Insufficient Permission")
        mock_settings.forwardingAddresses().list().execute.side_effect = RuntimeError("403 Forbidden")
        mock_settings.filters().list().execute.side_effect = RuntimeError("500 Backend Error")
        mock_settings.getImap().execute.return_value = {'enabled': False}
        mock_settings.getPop().execute.return_value = {'accessWindow': 'disabled'}

        with patch('app.services.gmail_service.GmailService._service', return_value=mock_service):
            report = SecurityService.audit_forwarding_and_filters(conn)

        # 重点断言：必须判定为不干净 (isClean=False)，且记录错误
        self.assertFalse(report['isClean'])
        self.assertTrue(report['hasErrors'])
        self.assertEqual(report['status'], 'error')
        self.assertGreaterEqual(len(report['scanErrors']), 1)
        self.assertTrue(any(e['step'] == 'auto_forwarding' for e in report['scanErrors']))

if __name__ == '__main__':
    unittest.main()
