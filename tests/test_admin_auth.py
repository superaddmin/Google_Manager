import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import create_app, db
from app.config import ProductionConfig, TestingConfig
from app.models.admin_session import AdminSession
from app.services.auth_service import AuthService


class AdminAuthenticationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    @staticmethod
    def salt():
        return AuthService.generate_salt(int(time.time()))

    def login(self, client=None, password='admin123'):
        client = client or self.client
        response = client.post('/api/auth/login', json={
            'password': password,
            'salt': self.salt(),
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response

    def replay_session_cookie(self, source_client):
        cookie_name = self.app.config['SESSION_COOKIE_NAME']
        cookie = source_client.get_cookie(cookie_name)
        self.assertIsNotNone(cookie)
        replay = self.app.test_client()
        replay.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        replay.set_cookie(cookie_name, cookie.value)
        return replay

    def test_logout_revokes_a_replayed_cookie(self):
        self.login()
        replay = self.replay_session_cookie(self.client)
        self.assertEqual(replay.get('/api/accounts').status_code, 200)

        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(replay.get('/api/accounts').status_code, 401)
        self.assertFalse(replay.get('/api/auth/check').get_json()['authenticated'])

    def test_authenticated_only_legacy_cookie_is_rejected(self):
        legacy = self.app.test_client()
        with legacy.session_transaction() as cookie_session:
            cookie_session['authenticated'] = True
        response = legacy.get('/api/accounts')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(legacy.get('/api/auth/check').get_json()['authenticated'])

    def test_password_rotation_and_expiration_revoke_existing_sessions(self):
        self.login()
        rotated = self.replay_session_cookie(self.client)
        self.app.config['ADMIN_PASSWORD'] = '新的管理员密码-2026'
        self.assertEqual(rotated.get('/api/accounts').status_code, 401)

        self.login(password='新的管理员密码-2026')
        expiring = self.replay_session_cookie(self.client)
        AdminSession.query.update({'expires_at': time.time() - 1})
        db.session.commit()
        self.assertEqual(expiring.get('/api/accounts').status_code, 401)

    def test_unicode_password_and_invalid_shapes_return_json(self):
        unicode_password = '管理员强密码_满足十六字符_2026🔒'
        self.app.config['ADMIN_PASSWORD'] = unicode_password
        failed = self.client.post('/api/auth/login', json={
            'password': '错误密码🔒',
            'salt': self.salt(),
        })
        self.assertEqual(failed.status_code, 401)
        self.assertEqual(failed.content_type, 'application/json')
        self.login(password=unicode_password)

        for password in (None, 42, ['admin123'], 'x' * 4097):
            with self.subTest(password_type=type(password).__name__):
                response = self.client.post('/api/auth/login', json={
                    'password': password,
                    'salt': self.salt(),
                })
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content_type, 'application/json')

    def test_session_store_failures_fail_closed_with_json_503(self):
        self.login()
        database_error = OperationalError('SELECT admin_sessions', {}, Exception('offline'))
        with patch.object(
            AuthService,
            'validate_admin_session',
            side_effect=database_error,
        ):
            protected = self.client.get('/api/accounts')
        self.assertEqual(protected.status_code, 503)
        self.assertEqual(protected.content_type, 'application/json')
        self.assertFalse(protected.get_json()['success'])
        self.assertNotIn('offline', protected.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/accounts').status_code, 401)

        self.login()
        with patch.object(
            AuthService,
            'revoke_admin_session',
            side_effect=database_error,
        ):
            logout = self.client.post('/api/auth/logout')
        self.assertEqual(logout.status_code, 503)
        self.assertEqual(logout.content_type, 'application/json')
        self.assertFalse(logout.get_json()['success'])
        self.assertNotIn('offline', logout.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/accounts').status_code, 401)

        with patch.object(
            AuthService,
            'create_admin_session',
            side_effect=database_error,
        ):
            create_failed = self.client.post('/api/auth/login', json={
                'password': 'admin123',
                'salt': self.salt(),
            })
        self.assertEqual(create_failed.status_code, 503)
        self.assertEqual(create_failed.content_type, 'application/json')
        self.assertNotIn('offline', create_failed.get_data(as_text=True))

    def test_production_rejects_password_larger_than_login_limit(self):
        environment = {
            'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
            'ADMIN_PASSWORD': 'x' * 4097,
            'GMAIL_TOKEN_ENCRYPTION_KEY': 'unused-because-password-validation-fails',
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                ProductionConfig,
                'SQLALCHEMY_DATABASE_URI',
                'sqlite:///:memory:',
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, '4096'):
                create_app('production')


class SharedAdminAuthenticationTestCase(unittest.TestCase):
    def test_session_is_shared_across_app_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            database = 'sqlite:///' + str(Path(directory) / 'shared-auth.db')
            with patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', database):
                first_app = create_app('testing')
                second_app = create_app('testing')
            shared_secret = 'synthetic-shared-session-secret'
            first_app.config['SECRET_KEY'] = shared_secret
            second_app.config['SECRET_KEY'] = shared_secret

            with first_app.app_context():
                db.create_all()
            first = first_app.test_client()
            first.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
            login = first.post('/api/auth/login', json={
                'password': 'admin123',
                'salt': AuthService.generate_salt(int(time.time())),
            })
            self.assertEqual(login.status_code, 200, login.get_json())

            cookie_name = first_app.config['SESSION_COOKIE_NAME']
            second = second_app.test_client()
            second.set_cookie(cookie_name, first.get_cookie(cookie_name).value)
            self.assertEqual(second.get('/api/accounts').status_code, 200)

            with first_app.app_context():
                db.session.remove()
                db.engine.dispose()
            with second_app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
