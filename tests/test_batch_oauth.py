import json
import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.models.account import Account
from app.services.batch_oauth_service import (
    BatchOAuthError,
    BatchOAuthManager,
    batch_oauth_manager,
)
from app.services.gmail_service import OAuthStateManager


class BatchOAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['authenticated'] = True
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        # 创建测试账号
        self.account1 = Account(
            email='test_oauth1@gmail.com',
            password='Password123!',
            recovery='rec1@example.com',
            secret='JBSWY3DPEHPK3PXP',
            status='inactive',
        )
        self.account2 = Account(
            email='test_oauth2@gmail.com',
            password='Password456!',
            recovery='rec2@example.com',
            secret='JBSWY3DPEHPK3PXP',
            status='inactive',
        )
        db.session.add_all([self.account1, self.account2])
        db.session.commit()

    def tearDown(self):
        OAuthStateManager.clear()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_oauth_state_manager_lifecycle(self):
        state = 'test-state-token-123'
        OAuthStateManager.register(state, account_id=self.account1.id, metadata={'ip': '127.0.0.1'})

        self.assertTrue(OAuthStateManager.validate(state))

        consumed = OAuthStateManager.consume(state)
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed['account_id'], self.account1.id)
        self.assertEqual(consumed['metadata']['ip'], '127.0.0.1')

        # 消费后不再存在
        self.assertFalse(OAuthStateManager.validate(state))
        self.assertIsNone(OAuthStateManager.consume(state))

    def test_oauth_state_manager_expiration(self):
        state = 'expiring-state'
        with patch('time.time', return_value=time.time() - 3600):
            OAuthStateManager.register(state, account_id=1)

        self.assertFalse(OAuthStateManager.validate(state))
        self.assertIsNone(OAuthStateManager.consume(state))

    @patch('app.services.gmail_service.GmailService.authorization_url')
    def test_batch_oauth_start_and_status(self, mock_auth_url):
        mock_auth_url.return_value = ('https://accounts.google.com/o/oauth2/v2/auth?test=1', 'state-123')

        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.wait.return_value = 0
        mock_process.stdout = [
            '__OAUTH_EVENT__' + json.dumps({'event': 'batch_started', 'total': 2}),
            '__OAUTH_EVENT__' + json.dumps({'event': 'account_started', 'accountId': self.account1.id, 'email': 't***@gmail.com', 'index': 0}),
            '__OAUTH_EVENT__' + json.dumps({'event': 'account_completed', 'accountId': self.account1.id, 'success': True}),
            '__OAUTH_EVENT__' + json.dumps({'event': 'batch_finished', 'completed': 1, 'failed': 0}),
        ]

        manager = BatchOAuthManager(popen_factory=lambda *a, **kw: mock_process)
        manager.availability = lambda: {'available': True, 'reasons': []}

        record = manager.start_batch(self.app, [self.account1.id], options={'headless': True})
        self.assertIsNotNone(record)
        self.assertEqual(record.total_count, 1)

        # 验证能够查询到任务
        retrieved = manager.get_task(record.task_id)
        self.assertEqual(retrieved.task_id, record.task_id)

    def test_batch_oauth_empty_accounts_rejected(self):
        with self.assertRaises(BatchOAuthError):
            batch_oauth_manager.start_batch(self.app, [])

    def test_batch_oauth_api_endpoints(self):
        # 1. 启动批量授权 API
        with patch.object(batch_oauth_manager, 'availability', return_value={'available': True, 'reasons': []}):
            with patch.object(batch_oauth_manager, 'start_batch') as mock_start:
                mock_start.return_value = MagicMock(to_dict=lambda: {'taskId': 'task-abc', 'status': 'pending'})
                res = self.client.post(
                    '/api/gmail/batch-authorize',
                    json={'accountIds': [self.account1.id], 'options': {'headless': True}},
                )
                self.assertEqual(res.status_code, 201)
                data = res.get_json()
                self.assertTrue(data['success'])
                self.assertEqual(data['data']['taskId'], 'task-abc')

        # 2. 查询状态 API
        res_status = self.client.get('/api/gmail/batch-authorize/status')
        self.assertEqual(res_status.status_code, 200)
        status_data = res_status.get_json()
        self.assertTrue(status_data['success'])
        self.assertIn('active', status_data['data'])
        self.assertIn('latest', status_data['data'])

    def test_batch_oauth_callback_consumes_statemanager_without_session(self):
        """SUP-02: 无管理员 session 时，有效批量 OAuth state 成功回调、被单次消费且防重放"""
        unauthed_client = self.app.test_client()
        state = 'valid-batch-state-999'
        OAuthStateManager.register(state, account_id=self.account1.id)

        mock_conn = MagicMock()
        mock_conn.to_dict.return_value = {'id': 1, 'email': self.account1.email}

        with patch('app.services.gmail_service.GmailService.complete_authorization', return_value=mock_conn) as mock_auth:
            # 1. 首次有效回调
            res = unauthed_client.get(f'/api/gmail/oauth/callback?state={state}&code=auth-code-123')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data['success'])
            mock_auth.assert_called_once_with('auth-code-123', state)

            # 2. 状态已被单次消费，状态管理器中已清除
            self.assertFalse(OAuthStateManager.validate(state))

            # 3. 重放相同回调请求必须被 400 阻断
            res_replay = unauthed_client.get(f'/api/gmail/oauth/callback?state={state}&code=auth-code-123')
            self.assertEqual(res_replay.status_code, 400)
            self.assertIn('状态无效', res_replay.get_json()['message'])

    def test_batch_oauth_callback_expired_statemanager_rejected(self):
        """SUP-02: 过期批量 OAuth state 必须被 400 阻断"""
        unauthed_client = self.app.test_client()
        state = 'expired-batch-state'
        with patch('time.time', return_value=time.time() - 3600):
            OAuthStateManager.register(state, account_id=self.account1.id)

        res = unauthed_client.get(f'/api/gmail/oauth/callback?state={state}&code=auth-code-123')
        self.assertEqual(res.status_code, 400)
        self.assertIn('状态无效', res.get_json()['message'])


if __name__ == '__main__':
    unittest.main()
