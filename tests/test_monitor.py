import json
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.models.recharge_task import RechargeTask
from app.models.runtime_job import RuntimeJob
from app.monitor import collect_status, probe_readiness
from app.services.runtime_queue import RuntimeQueue


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['BACKGROUND_TASK_MODE'] = 'queue'
        self.context = self.app.app_context()
        self.context.push()
        self.now = time.time()
        RuntimeQueue.set_state('worker', {'heartbeat': self.now})
        self.disk = patch('app.monitor.shutil.disk_usage', return_value=SimpleNamespace(free=2 * 1024 ** 3))
        self.disk.start()

    def tearDown(self):
        self.disk.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def check(self, **kwargs):
        return collect_status(self.app, web_ready=True, now=self.now, **kwargs)

    def test_healthy_with_no_pending_work(self):
        result = self.check()
        self.assertTrue(result['healthy'])
        self.assertEqual(result['issues'], [])

    def test_stale_worker_and_failed_web_are_detected_separately(self):
        RuntimeQueue.set_state('worker', {'heartbeat': self.now - 31})
        result = collect_status(self.app, web_ready=False, now=self.now)
        self.assertIn('worker_heartbeat_stale', result['issues'])
        self.assertIn('web_not_ready', result['issues'])
        self.assertFalse(result['healthy'])

    def test_pending_queue_threshold_and_scheduled_retry(self):
        for created, available in [(self.now - 300, self.now), (self.now - 301, self.now + 60)]:
            db.session.add(RuntimeJob(kind='gmail_sync', payload='sensitive-fixture',
                                      created_at=created, available_at=available))
        db.session.commit()
        result = self.check()
        self.assertEqual(result['counts']['queue_overdue'], 1)
        self.assertIn('queue_overdue', result['issues'])
        self.assertNotIn('sensitive-fixture', json.dumps(result))

    def test_unknown_task_remains_visible_even_after_recent_poll(self):
        db.session.add(RechargeTask(task_no='monitor-fixture', redeem_code='sensitive-fixture',
            plan_type='PLUS', account_email='fixture@example.test', status='unknown',
            created_at=datetime.fromtimestamp(self.now - 301, timezone.utc).replace(tzinfo=None)))
        db.session.commit()
        result = self.check()
        self.assertEqual(result['counts']['recharge_unknown_overdue'], 1)
        self.assertNotIn('sensitive-fixture', json.dumps(result))
        self.assertNotIn('fixture@example.test', json.dumps(result))

    def test_database_failure_does_not_echo_connection_details(self):
        with patch.object(db.session, 'execute', side_effect=RuntimeError('private-connection-string')), \
                patch.object(db.session, 'rollback', side_effect=RuntimeError('private-rollback')):
            result = self.check()
        self.assertIn('database_check_failed', result['issues'])
        self.assertNotIn('private-connection-string', json.dumps(result))
        self.assertNotIn('private-rollback', json.dumps(result))

    def test_disk_threshold_and_missing_mount_fail_closed(self):
        with patch('app.monitor.shutil.disk_usage', return_value=SimpleNamespace(free=1023)):
            self.assertIn('disk_space_low', self.check(min_free_bytes=1024)['issues'])
        with patch('app.monitor.shutil.disk_usage', side_effect=OSError('private-path')):
            result = self.check()
        self.assertIn('disk_check_failed', result['issues'])
        self.assertNotIn('private-path', json.dumps(result))

    def test_readiness_rejects_credentials_and_network_failures(self):
        with patch('app.monitor.build_opener') as opener:
            self.assertFalse(probe_readiness('http://user:password@localhost/health/ready'))
            opener.assert_not_called()
            opener.return_value.open.side_effect = TimeoutError('private-token')
            self.assertFalse(probe_readiness('http://127.0.0.1:8002/health/ready'))
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 3)

    def test_readiness_requires_boolean_true(self):
        with patch('app.monitor.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            for body, expected in [(b'{"ready": true}', True), (b'{"ready": "true"}', False), (b'[]', False), (b'html', False)]:
                response.read.return_value = body
                self.assertEqual(probe_readiness('http://127.0.0.1:8002/health/ready'), expected)


if __name__ == '__main__':
    unittest.main()
