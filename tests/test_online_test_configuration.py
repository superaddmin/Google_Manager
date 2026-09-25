import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app import create_app, db
from app.config import ProductionConfig
from deploy.preflight import REQUIRED_FIELDS, _configuration_checks


class OnlineTestConfigurationTestCase(unittest.TestCase):
    def setUp(self):
        self.environment = {
            'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
            'ADMIN_PASSWORD': 'test-only',
            'GMAIL_TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'RECHARGE_MODE': 'disabled',
        }

    def test_short_password_requires_explicit_opt_in(self):
        for option in (None, '0', 'true'):
            environment = dict(self.environment)
            if option is not None:
                environment['ALLOW_TEST_ADMIN_PASSWORD'] = option
            with self.subTest(option=option), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'ADMIN_PASSWORD'):
                    create_app('production')

    def test_opt_in_preserves_production_database_and_encryption_settings(self):
        with (
            patch.dict(os.environ, {**self.environment, 'ALLOW_TEST_ADMIN_PASSWORD': '1'}, clear=True),
            patch.object(ProductionConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'),
        ):
            application = create_app('production')
        try:
            self.assertEqual(application.config['ADMIN_PASSWORD'], self.environment['ADMIN_PASSWORD'])
            self.assertTrue(application.config['SENSITIVE_DATA_REQUIRE_ENCRYPTION'])
            self.assertTrue(application.config['SESSION_COOKIE_SECURE'])
            self.assertFalse(application.config['AUTO_CREATE_DB'])
            self.assertFalse(application.config['DEBUG'])
            self.assertEqual(application.config['BACKGROUND_TASK_MODE'], 'queue')
        finally:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_opt_in_rejects_live_and_mock_recharge(self):
        for mode in ('live', 'mock'):
            environment = {**self.environment, 'ALLOW_TEST_ADMIN_PASSWORD': '1', 'RECHARGE_MODE': mode}
            with self.subTest(mode=mode), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'requires RECHARGE_MODE=disabled'):
                    create_app('production')

    def test_opt_in_still_rejects_empty_whitespace_and_oversized_passwords(self):
        for password in ('', ' test-only', 'test-only ', 'x' * 4097):
            environment = {**self.environment, 'ALLOW_TEST_ADMIN_PASSWORD': '1', 'ADMIN_PASSWORD': password}
            with self.subTest(length=len(password)), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'ADMIN_PASSWORD'):
                    create_app('production')

    def test_production_preflight_rejects_test_opt_in_even_with_a_strong_password(self):
        values = {field: 'configured' for field in REQUIRED_FIELDS}
        values.update(self.environment, ADMIN_PASSWORD='synthetic-strong-password-123')
        baseline = _configuration_checks(values)
        self.assertNotIn('ADMIN_PASSWORD_INVALID', {check['code'] for check in baseline})
        values['ALLOW_TEST_ADMIN_PASSWORD'] = '1'
        checks = _configuration_checks(values)
        self.assertIn('ADMIN_PASSWORD_INVALID', {check['code'] for check in checks})


if __name__ == '__main__':
    unittest.main()
