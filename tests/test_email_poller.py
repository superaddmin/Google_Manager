import unittest
from unittest.mock import MagicMock, patch

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.gmail_connection import GmailConnection
from app.services.email_poller import GmailSyncDaemon, gmail_sync_daemon


class EmailPollerTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(self.client)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        # 创建一个测试 Gmail 连接
        self.conn = GmailConnection(
            email='poller_test@gmail.com',
            token_data='encrypted_dummy',
            scopes='https://www.googleapis.com/auth/gmail.modify',
        )
        db.session.add(self.conn)
        db.session.commit()

    def tearDown(self):
        gmail_sync_daemon.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_daemon_status_and_lifecycle(self):
        daemon = GmailSyncDaemon()
        status = daemon.status()
        self.assertFalse(status['isRunning'])
        self.assertEqual(status['totalRuns'], 0)

        # 启动
        started = daemon.start(self.app, interval_seconds=60)
        self.assertTrue(started['isRunning'])
        self.assertEqual(started['intervalSeconds'], 60)

        # 停止
        stopped = daemon.stop()
        self.assertFalse(stopped['isRunning'])

    @patch('app.services.gmail_service.GmailService.list_messages')
    @patch('app.services.security_service.SecurityService.audit_forwarding_and_filters')
    def test_sync_once_execution(self, mock_audit, mock_list):
        mock_list.return_value = {
            'messages': [
                {'id': 'msg-1', 'snippet': '您的 Google 验证码是 839201'},
            ],
            'nextPageToken': None,
        }
        mock_audit.return_value = {
            'email': self.conn.email,
            'forwardingAddresses': [],
            'suspiciousRulesCount': 0,
        }

        daemon = GmailSyncDaemon()
        result = daemon.sync_once(self.app)

        self.assertEqual(result['connectionsSynced'], 1)
        self.assertEqual(result['messagesPolled'], 1)
        self.assertEqual(result['alertsDetected'], 0)

        status = daemon.status()
        self.assertEqual(status['totalRuns'], 1)
        self.assertEqual(status['connectionsSynced'], 1)
        self.assertEqual(status['messagesPolled'], 1)

    @patch('app.services.gmail_service.GmailService.list_messages', return_value={
        'messages': [{'id': 'msg-audit-error'}], 'nextPageToken': None,
    })
    @patch('app.services.security_service.SecurityService.audit_forwarding_and_filters', return_value={
        'hasErrors': True, 'status': 'error', 'suspiciousFiltersCount': 0,
    })
    def test_audit_errors_are_reported_as_failed_sync(self, mock_audit, mock_list):
        daemon = GmailSyncDaemon()
        result = daemon.sync_once(self.app)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['connectionsSynced'], 0)

    def test_daemon_api_endpoints(self):
        # 1. 状态接口
        res = self.client.get('/api/gmail/daemon/status')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertIn('isRunning', data['data'])

        # 2. 启动接口
        res_start = self.client.post('/api/gmail/daemon/start', json={'intervalSeconds': 120})
        self.assertEqual(res_start.status_code, 200)
        start_data = res_start.get_json()
        self.assertTrue(start_data['success'])
        self.assertTrue(start_data['data']['isRunning'])

        # 3. 停止接口
        res_stop = self.client.post('/api/gmail/daemon/stop')
        self.assertEqual(res_stop.status_code, 200)
        stop_data = res_stop.get_json()
        self.assertTrue(stop_data['success'])
        self.assertFalse(stop_data['data']['isRunning'])

        # 4. 立即同步接口
        with patch.object(gmail_sync_daemon, 'sync_once', return_value={'connectionsSynced': 1, 'messagesPolled': 0}):
            res_sync = self.client.post('/api/gmail/daemon/sync-now')
            self.assertEqual(res_sync.status_code, 200)
            sync_data = res_sync.get_json()
            self.assertTrue(sync_data['success'])


if __name__ == '__main__':
    unittest.main()
