import unittest
from unittest.mock import patch

from app import create_app, db
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class RechargeSafetyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        RechargeService.clear_local_data()
        self.client = self.authenticated_client()
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('External HTTP forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def authenticated_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['authenticated'] = True
        return client

    def payload(self, code='PLUS-SYNTHETIC-SAFETY', **overrides):
        return {
            'redeem_code': code,
            'token_input': 'synthetic-credential',
            'plan_type': 'PLUS',
            'account_email': 'fixture@example.test',
            'agreement_accepted': True,
            'email_verified': True,
            'is_renewal': False,
            **overrides,
        }

    def challenge(self, payload, client=None):
        response = (client or self.client).post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return {**payload, 'challenge_token': response.get_json()['data']['challenge_token']}

    def create_unknown_task(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        payload = self.challenge(self.payload())
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('offline timeout')):
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 502)
        task = RechargeTask.query.filter_by(redeem_code=payload['redeem_code']).one()
        self.assertEqual(task.status, 'unknown')
        return task

    def test_challenge_requires_plan_and_credential(self):
        for missing in ('plan_type', 'token_input'):
            with self.subTest(missing=missing):
                payload = self.payload()
                payload.pop(missing)
                response = self.client.post('/api/recharge/submission-challenges', json=payload)
                self.assertEqual(response.status_code, 400)

    def test_challenge_is_bound_to_session(self):
        payload = self.challenge(self.payload())
        other_client = self.authenticated_client()
        response = other_client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RechargeTask.query.count(), 0)
        valid = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(valid.status_code, 201)

    def test_challenge_is_bound_to_credential_and_renewal(self):
        for change in ({'token_input': 'different-credential'}, {'is_renewal': True}):
            with self.subTest(change=list(change)):
                payload = self.challenge(self.payload())
                response = self.client.post('/api/recharge/tasks', json={**payload, **change})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(RechargeTask.query.count(), 0)

    def test_challenge_cannot_cross_modes(self):
        payload = self.challenge(self.payload())
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post') as upstream:
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 400)
        upstream.assert_not_called()

    def test_live_receipt_never_uses_mock_invoice(self):
        self.client.post('/api/recharge/billing/query', json={'token_input': 'synthetic-billing-token'})
        self.app.config['RECHARGE_MODE'] = 'live'
        response = self.client.get('/api/recharge/tasks/invoice/download?slug=inv_slug_001')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['success'])

    def test_mock_invoice_requires_existing_record(self):
        response = self.client.get('/api/recharge/tasks/invoice/download?slug=inv_slug_001')
        self.assertEqual(response.status_code, 404)

    def test_mock_invoice_is_not_visible_in_another_session(self):
        response = self.client.post('/api/recharge/billing/query', json={'token_input': 'synthetic-billing-token'})
        invoice = response.get_json()['data']['invoices'][0]
        response = self.client.get('/api/recharge/tasks/invoice/download', query_string={'slug': invoice['slug']})
        self.assertEqual(response.status_code, 200)
        other = self.authenticated_client().get('/api/recharge/tasks/invoice/download', query_string={'slug': invoice['slug']})
        self.assertEqual(other.status_code, 404)

    def test_lookup_persists_unknown_resolution(self):
        task = self.create_unknown_task()
        remote = {'task_no': 'UPSTREAM-SYNTHETIC', 'redeem_code': task.redeem_code, 'status': 'completed'}
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}):
            response = self.client.post('/api/recharge/tasks/lookup', json={'redeem_code': task.redeem_code})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['task_no'], task.task_no)
        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeTask, task.id).status, 'completed')

    def test_local_task_number_resolves_via_card_until_upstream_id_known(self):
        task = self.create_unknown_task()
        remote = {'task_no': 'UPSTREAM-SYNTHETIC', 'redeem_code': task.redeem_code, 'status': 'processing'}
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}) as lookup, patch.object(RechargeService, '_upstream_get') as wrong_lookup:
            response = self.client.get(f'/api/recharge/tasks/{task.task_no}')
        self.assertEqual(response.status_code, 200)
        lookup.assert_called_once_with('/user/tasks/lookup', {'redeem_code': task.redeem_code})
        wrong_lookup.assert_not_called()
        self.assertEqual(response.get_json()['data']['status'], 'processing')

    def test_upstream_task_mapping_survives_new_session(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        payload = self.challenge(self.payload())
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': {'task_no': 'UPSTREAM-MAPPED', 'status': 'processing'}}):
            created = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(created.status_code, 201)
        task_no = created.get_json()['data']['task_no']
        db.session.remove()
        with patch.object(RechargeService, '_upstream_get', return_value={'ok': True, 'task': {'task_no': 'UPSTREAM-MAPPED', 'status': 'completed'}}) as upstream:
            response = self.client.get(f'/api/recharge/tasks/{task_no}')
        upstream.assert_called_once_with('/user/tasks/UPSTREAM-MAPPED')
        self.assertEqual(response.get_json()['data']['task_no'], task_no)
        self.assertEqual(RechargeTask.query.filter_by(task_no=task_no).one().status, 'completed')

    def test_upstream_error_does_not_fallback_to_local_success(self):
        task = self.create_unknown_task()
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': False}), patch.object(RechargeService, '_upstream_get', return_value={'ok': False}):
            response = self.client.get(f'/api/recharge/tasks/{task.task_no}')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.filter_by(task_no=task.task_no).one().status, 'unknown')

    def test_reconciliation_rejects_mismatched_identity(self):
        task = self.create_unknown_task()
        remote = {'task_no': 'UNRELATED-UPSTREAM', 'redeem_code': 'OTHER-CDK', 'status': 'completed'}
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}):
            response = self.client.post('/api/recharge/tasks/lookup', json={'redeem_code': task.redeem_code})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.filter_by(task_no=task.task_no).one().status, 'unknown')

    def test_mock_lookup_does_not_return_live_task(self):
        task = self.create_unknown_task()
        self.app.config['RECHARGE_MODE'] = 'mock'
        response = self.client.get(f'/api/recharge/tasks/{task.task_no}')
        self.assertEqual(response.status_code, 404)

    def test_completed_task_does_not_regress_on_stale_response(self):
        task = self.create_unknown_task()
        task.status = 'completed'
        db.session.commit()
        remote = {'task_no': 'UPSTREAM-SYNTHETIC', 'status': 'processing'}
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}):
            response = self.client.post('/api/recharge/tasks/lookup', json={'redeem_code': task.redeem_code})
        self.assertEqual(response.get_json()['data']['status'], 'completed')
        self.assertEqual(RechargeTask.query.filter_by(task_no=task.task_no).one().status, 'completed')

    def test_malformed_success_keeps_task_unknown(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        payload = self.challenge(self.payload())
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': {'status': 'unrecognized'}}):
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.one().status, 'unknown')

    def test_challenge_expiry_and_finished_plan_empty_credential(self):
        payload = self.challenge(self.payload())
        import time
        with patch('time.time', return_value=time.time() + 301):
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 400)
        finished = self.challenge(self.payload('FINISHED-SYNTHETIC', plan_type='FINISHED', token_input=''))
        response = self.client.post('/api/recharge/tasks', json=finished)
        self.assertEqual(response.status_code, 201)

    def test_raw_challenge_is_not_persisted_in_task(self):
        payload = self.challenge(self.payload())
        response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(RechargeTask.query.one().challenge_token)

    def test_late_timeout_does_not_overwrite_completed_task(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        payload = self.challenge(self.payload())

        def completed_then_timeout(endpoint, submitted):
            task = RechargeTask.query.filter_by(task_no=submitted['client_task_no']).one()
            task.status = 'completed'
            db.session.commit()
            raise RechargeUpstreamError('synthetic delayed timeout')

        with patch.object(RechargeService, '_upstream_post', side_effect=completed_then_timeout):
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.one().status, 'completed')

    def test_json_array_is_rejected_without_server_error(self):
        response = self.client.post('/api/recharge/submission-challenges', json=['synthetic'])
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
