import unittest
import time
from unittest.mock import patch

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService
from app.services.auth_service import AuthService


class RechargeTaskAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        self.owner = self.client()
        self.stranger = self.client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def client(self):
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        return client

    def create_task(self, suffix='ONE', client=None):
        client = client or self.owner
        payload = {
            'redeem_code': 'PLUS-ACCESS-' + suffix,
            'token_input': 'synthetic-access-credential',
            'plan_type': 'PLUS', 'account_email': 'fixture@example.test',
            'agreement_accepted': True, 'email_verified': True,
            'acknowledge_non_free': True,
        }
        challenge = client.post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(challenge.status_code, 200)
        payload['challenge_token'] = challenge.get_json()['data']['challenge_token']
        created = client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        return {
            'task_no': created.get_json()['data']['task_no'],
            'redeem_code': payload['redeem_code'],
            'email': payload['account_email'], 'confirmed': True,
        }

    def test_known_credentials_do_not_authorize_another_session(self):
        for action in ('recall', 'close'):
            with self.subTest(action=action):
                payload = self.create_task(action)
                result = self.stranger.post('/api/recharge/tasks/' + action, json=payload)
                self.assertEqual(result.status_code, 403, result.get_json())
                self.assertEqual(result.get_json()['error_code'], 'forbidden')
                self.assertEqual(RechargeTask.query.filter_by(task_no=payload['task_no']).one().status, 'processing')

    def test_creator_can_act_after_query_and_session_is_not_admin(self):
        payload = self.create_task()
        self.assertEqual(self.owner.get('/api/recharge/tasks/' + payload['task_no']).status_code, 200)
        result = self.owner.post('/api/recharge/tasks/recall', json=payload)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['data']['status'], 'recalled')
        self.assertEqual(self.owner.get('/api/accounts').status_code, 401)

    def test_missing_or_mismatched_task_number_cannot_change_task(self):
        payload = self.create_task()
        for number, expected in ((None, 400), ('TK-OTHER', 403)):
            with self.subTest(number=number):
                result = self.owner.post('/api/recharge/tasks/close', json={**payload, 'task_no': number})
                self.assertEqual(result.status_code, expected, result.get_json())
        self.assertEqual(RechargeTask.query.one().status, 'processing')

    def test_valid_task_number_cannot_authorize_another_card(self):
        first = self.create_task('FIRST')
        second = self.create_task('SECOND')
        result = self.owner.post('/api/recharge/tasks/close', json={**second, 'task_no': first['task_no']})
        self.assertEqual(result.status_code, 403, result.get_json())
        self.assertTrue(all(task.status == 'processing' for task in RechargeTask.query.all()))

    def test_legacy_task_requires_admin_and_still_checks_target(self):
        task = RechargeTask(task_no='TK-LEGACY', redeem_code='PLUS-LEGACY-ACCESS',
                            plan_type='PLUS', account_email='fixture@example.test',
                            status='processing', is_mock=True)
        db.session.add(task)
        db.session.commit()
        payload = {'task_no': task.task_no, 'redeem_code': task.redeem_code,
                   'email': task.account_email, 'confirmed': True}
        self.assertEqual(self.owner.post('/api/recharge/tasks/close', json=payload).status_code, 403)
        with self.owner.session_transaction() as client_session:
            client_session['authenticated'] = True
        legacy_cookie = self.owner.post('/api/recharge/tasks/close', json=payload)
        self.assertEqual(legacy_cookie.status_code, 403)
        login_admin(self.owner)
        wrong = self.owner.post('/api/recharge/tasks/close', json={**payload, 'task_no': 'TK-OTHER'})
        self.assertEqual(wrong.status_code, 403)
        result = self.owner.post('/api/recharge/tasks/close', json=payload)
        self.assertEqual(result.status_code, 200, result.get_json())

    def test_rejected_session_never_calls_mutation_service(self):
        payload = self.create_task()
        with patch.object(RechargeService, 'close_task') as close:
            result = self.stranger.post('/api/recharge/tasks/close', json=payload)
        self.assertEqual(result.status_code, 403)
        close.assert_not_called()

    def test_admin_login_logout_preserves_customer_ownership_and_clears_admin_state(self):
        payload = self.create_task()
        with self.owner.session_transaction() as session:
            original_context = session['recharge_context']
            session['oauth_state'] = 'synthetic-unrelated-state'
        login = self.owner.post('/api/auth/login', json={
            'password': 'admin123', 'salt': AuthService.generate_salt(int(time.time())),
        })
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.owner.post('/api/auth/logout').status_code, 200)
        with self.owner.session_transaction() as session:
            self.assertEqual(session.get('recharge_context'), original_context)
            self.assertNotIn('authenticated', session)
            self.assertNotIn('oauth_state', session)
        self.assertEqual(self.owner.get('/api/accounts').status_code, 401)
        result = self.owner.post('/api/recharge/tasks/recall', json=payload)
        self.assertEqual(result.status_code, 200, result.get_json())


if __name__ == '__main__':
    unittest.main()
