from contextlib import redirect_stderr, redirect_stdout
import io
import time
import unittest
from unittest.mock import patch

from app import create_app, db, manage
from app.models.request_limit import LoginAttempt
from app.services.auth_service import AuthService


class AdminLoginResetTestCase(unittest.TestCase):
    def setUp(self):
        self.application = create_app('testing')
        self.context = self.application.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.application.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def run_command(self, *options):
        with (
            patch('sys.argv', ['app.manage', 'reset-admin-login', *options]),
            patch.object(manage, 'create_app', return_value=self.application),
            redirect_stdout(io.StringIO()) as output,
        ):
            manage.main()
        return output.getvalue()

    def test_password_change_ban_can_be_reset_and_correct_password_can_log_in(self):
        for status in (401, 401, 403):
            response = self.client.post('/api/auth/login', json={
                'password': 'wrong-old-password',
                'salt': AuthService.generate_salt(int(time.time())),
            })
            self.assertEqual(response.status_code, status)
        self.application.config['ADMIN_PASSWORD'] = 'changed-admin-test-password'
        self.assertTrue(self.client.get('/api/auth/check').get_json()['banned'])
        self.run_command('--ip', '127.0.0.1')
        self.assertFalse(self.client.get('/api/auth/check').get_json()['banned'])
        response = self.client.post('/api/auth/login', json={
            'password': self.application.config['ADMIN_PASSWORD'],
            'salt': AuthService.generate_salt(int(time.time())),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get('/api/auth/check').get_json()['authenticated'])

    def test_reset_one_ip_preserves_other_bans(self):
        for address in ('192.0.2.10', '192.0.2.11'):
            for unused_attempt in range(3):
                AuthService.record_failed_attempt(address)
        self.run_command('--ip', '192.0.2.10')
        self.assertFalse(AuthService.is_ip_banned('192.0.2.10')[0])
        self.assertTrue(AuthService.is_ip_banned('192.0.2.11')[0])

    def test_explicit_all_clears_failed_attempts_and_is_idempotent(self):
        AuthService.record_failed_attempt('192.0.2.10')
        self.run_command('--all')
        self.run_command('--all')
        record = db.session.get(LoginAttempt, '192.0.2.10')
        self.assertEqual((record.attempts, record.last_attempt, record.banned_until), (0, 0, 0))

    def test_invalid_or_missing_target_is_rejected_before_opening_database(self):
        for options in ([], ['--ip', 'invalid'], ['--all', '--ip', '192.0.2.10']):
            with (
                self.subTest(options=options),
                patch('sys.argv', ['app.manage', 'reset-admin-login', *options]),
                patch.object(manage, 'create_app') as factory,
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as raised:
                    manage.main()
                self.assertEqual(raised.exception.code, 2)
                factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
