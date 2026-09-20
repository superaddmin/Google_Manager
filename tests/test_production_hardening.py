import os
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app import create_app, db
from tests.auth_helpers import login_admin
from app.config import DevelopmentConfig, ProductionConfig, TestingConfig
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_task import RechargeTask
from app.services.auth_service import AuthService
from app.services.recharge_service import RechargeService, RechargeContractError, RechargeUpstreamError


class ProductionHardeningTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def payload(self):
        return {'redeem_code': 'PLUS-SYNTHETIC-PORTAL', 'token_input': 'synthetic-credential',
                'plan_type': 'PLUS', 'account_email': 'fixture@example.test',
                'agreement_accepted': True, 'email_verified': True,
                'acknowledge_non_free': True, 'is_renewal': False}

    def test_anonymous_customer_can_complete_flow_without_admin_privileges(self):
        payload = self.payload()
        validation = self.client.post('/api/recharge/redeem-codes/validate', json=payload)
        self.assertEqual(validation.status_code, 200)
        challenge = self.client.post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(challenge.status_code, 200)
        payload['challenge_token'] = challenge.get_json()['data']['challenge_token']
        created = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        number = created.get_json()['data']['task_no']
        reader = self.app.test_client()
        result = reader.get('/api/recharge/tasks/' + number)
        self.assertEqual(result.status_code, 200)
        for field in ('redeem_code', 'account_email', 'notify_email'):
            self.assertNotIn(field, result.get_json()['data'])
        self.assertEqual(self.client.get('/api/accounts').status_code, 401)
        self.assertEqual(reader.get('/api/recharge/tasks/invoice/download?task_no=' + number).status_code, 405)
        reader.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        self.assertEqual(reader.post('/api/recharge/tasks/recall', json={
            **result.get_json()['data'], 'confirmed': True,
        }).status_code, 400)

    def test_admin_writes_reject_missing_header_and_untrusted_origins(self):
        client = self.app.test_client()
        login_admin(client)
        self.assertEqual(client.post('/api/auth/logout').status_code, 403)
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        for origin in ('https://untrusted.example.test', 'null', 'https://localhost'):
            self.assertEqual(client.post('/api/auth/logout', headers={'Origin': origin}).status_code, 403)
        self.assertEqual(client.post('/api/auth/logout', headers={'Origin': 'http://localhost'}).status_code, 200)

    def test_trusted_proxy_keeps_login_bans_separate(self):
        with patch.object(TestingConfig, 'TRUSTED_PROXY_CIDRS', '127.0.0.1/32'):
            app = create_app('testing')
        with app.app_context():
            client = app.test_client()
            client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
            salt = AuthService.generate_salt(int(time.time()))
            for expected in (401, 401, 403):
                response = client.post('/api/auth/login', json={'password': 'wrong', 'salt': salt},
                                       headers={'X-Forwarded-For': '192.0.2.1'})
                self.assertEqual(response.status_code, expected)
            response = client.post('/api/auth/login', json={'password': 'admin123', 'salt': salt},
                                   headers={'X-Forwarded-For': '192.0.2.2'})
            self.assertEqual(response.status_code, 200)
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_rate_limit_stops_upstream_calls(self):
        self.app.config.update(RECHARGE_MODE='live', RECHARGE_RATE_LIMIT=2)
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }) as upstream:
            results = [self.client.post('/api/recharge/redeem-codes/validate', json=self.payload()) for unused in range(3)]
        self.assertEqual([result.status_code for result in results], [200, 200, 429])
        self.assertEqual(upstream.call_count, 2)

    def test_locked_account_never_returns_credentials_or_history(self):
        account = Account(email='locked@example.test', password='synthetic-password', secret='synthetic-secret', status='locked')
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountHistory(account_id=account.id, field_name='password', new_value='synthetic-password'))
        db.session.commit()
        login_admin(self.client)
        response = self.client.get('/api/accounts')
        self.assertNotIn(b'synthetic-password', response.data)
        self.assertNotIn(b'synthetic-secret', response.data)
        self.assertEqual(self.client.get(f'/api/accounts/{account.id}/history').status_code, 403)

    def test_late_recall_cannot_overwrite_completed_task(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        task = RechargeTask(task_no='TK-SYNTHETIC-RACE', redeem_code='PLUS-SYNTHETIC', plan_type='PLUS',
                            account_email='fixture@example.test', status='processing', is_mock=False)
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(task_no=task.task_no, active_key='synthetic-gate'))
        db.session.commit()
        def complete_elsewhere(*args, **kwargs):
            with Session(db.engine) as other:
                other.query(RechargeTask).filter_by(task_no=task.task_no).update({'status': 'completed'})
                other.commit()
            return {'ok': True, 'task': {
                'client_task_no': task.task_no,
                'redeem_code': task.redeem_code,
                'status': 'recalled',
            }}
        with patch.object(RechargeService, '_upstream_post', side_effect=complete_elsewhere):
            with self.assertRaises(RechargeContractError):
                RechargeService.recall_task({'redeem_code':task.redeem_code, 'email':task.account_email, 'confirmed':True})
        self.assertEqual(db.session.get(RechargeTask, task.id).status, 'completed')
        self.assertIsNotNone(db.session.get(RechargeOperation, task.task_no).active_key)

    def test_production_validates_encryption_key_at_startup(self):
        environment = {'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
                       'ADMIN_PASSWORD': 'synthetic-production-password', 'GMAIL_TOKEN_ENCRYPTION_KEY': 'invalid'}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Fernet'):
                create_app('production')
        environment['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        with patch.dict(os.environ, environment, clear=True), patch.object(ProductionConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'):
            app = create_app('production')
            self.assertTrue(app.config['SESSION_COOKIE_SECURE'])
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()

    def test_production_rejects_short_whitespace_and_example_passwords(self):
        environment = {'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
                       'GMAIL_TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode()}
        for password in ('short-password', ' ' * 20, ' synthetic-production-password',
                         'synthetic-production-password ', 'YourComplexPassword_2026!'):
            with self.subTest(password=password), patch.dict(os.environ, {**environment, 'ADMIN_PASSWORD': password}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'ADMIN_PASSWORD'):
                    create_app('production')

    def test_production_pages_block_inline_scripts_and_cross_origin_connections(self):
        environment = {
            'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
            'ADMIN_PASSWORD': 'synthetic-production-password',
            'GMAIL_TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'RECHARGE_MODE': 'disabled',
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            ProductionConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'
        ):
            application = create_app('production')
            try:
                with application.test_client().get('/recharge/') as response:
                    self.assertEqual(response.status_code, 200)
                    policy = response.headers.get('Content-Security-Policy', '')
                    directives = dict(item.strip().split(' ', 1) for item in policy.split(';') if item.strip())
                    self.assertEqual(directives.get('script-src'), "'self'")
                    self.assertEqual(directives.get('connect-src'), "'self'")
                    self.assertEqual(directives.get('object-src'), "'none'")
                    self.assertEqual(directives.get('base-uri'), "'none'")
                    self.assertEqual(directives.get('frame-ancestors'), "'self'")
            finally:
                with application.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_production_live_recharge_upstream_requires_https_and_allowlisted_host(self):
        base_environment = {
            'SECRET_KEY': 'synthetic-production-secret-with-sufficient-length',
            'ADMIN_PASSWORD': 'synthetic-production-password',
            'GMAIL_TOKEN_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'RECHARGE_MODE': 'live',
            'RECHARGE_UPSTREAM_ALLOWED_HOSTS': 'aichong666.com',
        }
        invalid_urls = (
            'http://aichong666.com/api',
            'https://evil.example.test/api',
            'https://user:password@aichong666.com/api',
            'https://aichong666.com/api?redirect=https://evil.example.test',
        )
        for url in invalid_urls:
            with self.subTest(url=url), patch.dict(
                os.environ, {**base_environment, 'RECHARGE_UPSTREAM_URL': url}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, 'RECHARGE_UPSTREAM_URL'):
                    create_app('production')

        with patch.dict(
            os.environ,
            {**base_environment, 'RECHARGE_UPSTREAM_URL': 'https://aichong666.com/api'},
            clear=True,
        ), patch.object(ProductionConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'):
            application = create_app('production')
            self.assertEqual(application.config['RECHARGE_MODE'], 'live')
            with application.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()

    def test_non_testing_live_recharge_requires_explicit_upstream_url(self):
        environment = {
            'ADMIN_PASSWORD': 'synthetic-development-password',
            'RECHARGE_MODE': 'live',
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            DevelopmentConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'
        ):
            with self.assertRaisesRegex(RuntimeError, '显式配置 RECHARGE_UPSTREAM_URL'):
                create_app('development')

    def test_non_testing_live_recharge_accepts_explicit_upstream_url(self):
        environment = {
            'ADMIN_PASSWORD': 'synthetic-development-password',
            'RECHARGE_MODE': 'live',
            'RECHARGE_UPSTREAM_URL': 'https://aichong666.com/api',
            'RECHARGE_UPSTREAM_ALLOWED_HOSTS': 'aichong666.com',
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            DevelopmentConfig, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:'
        ):
            application = create_app('development')
            self.assertEqual(application.config['RECHARGE_MODE'], 'live')
            self.assertTrue(application.config['RECHARGE_UPSTREAM_URL_EXPLICIT'])
            with application.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()

    def test_runtime_live_recharge_rechecks_upstream_url_after_config_drift(self):
        self.app.config.update(
            TESTING=False,
            RECHARGE_MODE='live',
            RECHARGE_UPSTREAM_URL='https://aichong666.com/api',
            RECHARGE_UPSTREAM_ALLOWED_HOSTS='aichong666.com',
            RECHARGE_UPSTREAM_URL_EXPLICIT=False,
        )
        with patch('urllib.request.build_opener') as build_opener:
            with self.assertRaisesRegex(RechargeUpstreamError, '显式配置'):
                RechargeService._upstream_post('/user/tasks', {'synthetic': True})
        self.app.config['RECHARGE_UPSTREAM_URL'] = 'http://evil.example.test/api'
        self.app.config['RECHARGE_UPSTREAM_URL_EXPLICIT'] = True
        with patch('urllib.request.build_opener') as build_opener:
            with self.assertRaisesRegex(RechargeUpstreamError, 'HTTPS/主机白名单'):
                RechargeService._upstream_post('/user/tasks', {'synthetic': True})
        build_opener.assert_not_called()

    def test_recharge_routes_return_json_for_unexpected_errors(self):
        with patch.object(RechargeService, 'billing_query', side_effect=RuntimeError('secret-token-value')):
            response = self.client.post('/api/recharge/billing/query', json={'token_input': 'secret-token-value'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, 'application/json')
        self.assertNotIn('secret-token-value', response.get_data(as_text=True))
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(response.get_json()['error_code'], 'internal_error')

    def create_live_task(self, status='processing'):
        self.app.config['RECHARGE_MODE'] = 'live'
        task = RechargeTask(task_no='TK-SYNTHETIC-MUTATION', redeem_code='PLUS-SYNTHETIC', plan_type='PLUS',
                            account_email='fixture@example.test', status=status, is_mock=False)
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(task_no=task.task_no, active_key='synthetic-mutation-gate'))
        db.session.commit()
        return task

    def test_uncertain_recall_blocks_second_upstream_mutation_and_reconciles(self):
        task = self.create_live_task()
        request = {'redeem_code': task.redeem_code, 'email': task.account_email, 'confirmed': True}
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('synthetic timeout')) as upstream:
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.recall_task(request)
            with self.assertRaises(RechargeContractError):
                RechargeService.recall_task(request)
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'unknown')
        RechargeService._reconcile_task(task, {'status': 'recalled', 'client_task_no': task.task_no})
        self.assertEqual(task.status, 'recalled')
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'done')
        self.assertIsNone(db.session.get(RechargeOperation, task.task_no).active_key)

    def test_pending_mutation_blocks_read_reconciliation_until_recovery(self):
        task = self.create_live_task()
        RechargeMutation.claim(task, 'recall')
        RechargeService._reconcile_task(task, {'status': 'recalled', 'client_task_no': task.task_no})
        self.assertEqual(task.status, 'processing')
        mutation = db.session.get(RechargeMutation, task.task_no)
        mutation.started_at = time.time() - 60
        db.session.commit()
        RechargeMutation.recover_expired()
        RechargeService._reconcile_task(task, {'status': 'recalled', 'client_task_no': task.task_no})
        self.assertEqual(task.status, 'recalled')
        self.assertEqual(mutation.state, 'done')

    def test_uncertain_close_keeps_gate_until_closed_is_confirmed(self):
        task = self.create_live_task(status='completed')
        request = {'redeem_code': task.redeem_code, 'email': task.account_email, 'confirmed': True}
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('synthetic timeout')):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.close_task(request)
        with self.assertRaises(RechargeUpstreamError):
            RechargeService._reconcile_task(task, {'status': 'completed', 'client_task_no': task.task_no})
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'unknown')
        RechargeService._reconcile_task(task, {'status': 'closed', 'client_task_no': task.task_no})
        self.assertEqual(task.status, 'closed')
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'done')
        self.assertIsNotNone(db.session.get(RechargeOperation, task.task_no).active_key)
