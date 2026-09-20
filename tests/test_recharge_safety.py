import unittest
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import patch
from werkzeug.exceptions import (
    MethodNotAllowed,
    RequestEntityTooLarge,
    UnsupportedMediaType,
    UnprocessableEntity,
)

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_task import RechargeTask
from app.routes.recharge import error_response, handle_recharge_http_error
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class RechargeSafetyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        RechargeService.clear_local_data()
        self.client = self.authenticated_client()
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External HTTP forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def authenticated_client(self):
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(client)
        return client

    def payload(self, code='PLUS-SYNTHETIC-SAFETY', **overrides):
        return {
            'redeem_code': code,
            'token_input': 'synthetic-credential',
            'plan_type': 'PLUS',
            'account_email': 'fixture@example.test',
            'agreement_accepted': True,
            'email_verified': True,
            'acknowledge_non_free': True,
            'is_renewal': False,
            **overrides,
        }

    def challenge(self, payload, client=None):
        response = (client or self.client).post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return {**payload, 'challenge_token': response.get_json()['data']['challenge_token']}

    def create_unknown_task(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
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

    def test_http_error_statuses_have_stable_recharge_error_codes(self):
        cases = (
            (MethodNotAllowed(), 405, 'method_not_allowed'),
            (RequestEntityTooLarge(), 413, 'request_entity_too_large'),
            (UnsupportedMediaType(), 415, 'unsupported_media_type'),
            (UnprocessableEntity(), 422, 'unprocessable_entity'),
        )
        for exception, status, error_code in cases:
            with self.subTest(status=status):
                with self.app.test_request_context('/api/recharge/test'):
                    response, response_status = handle_recharge_http_error(exception)
                self.assertEqual(response_status, status)
                self.assertEqual(response.content_type, 'application/json')
                self.assertEqual(response.get_json(), {
                    'success': False,
                    'data': None,
                    'message': exception.description,
                    'error_code': error_code,
                })

        response, response_status = error_response('synthetic unknown status', 599)
        self.assertEqual(response_status, 599)
        self.assertEqual(response.get_json()['error_code'], 'http_error')

        route_response = self.client.get('/api/recharge/tasks/invoice/download')
        self.assertEqual(route_response.status_code, 405)
        self.assertEqual(route_response.get_json()['error_code'], 'method_not_allowed')

        original_limit = self.app.config['MAX_CONTENT_LENGTH']
        self.app.config['MAX_CONTENT_LENGTH'] = 16
        try:
            oversized = self.client.post(
                '/api/recharge/tasks',
                data=b'{"payload":"exceeds-limit"}',
                content_type='application/json',
            )
        finally:
            self.app.config['MAX_CONTENT_LENGTH'] = original_limit
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.get_json()['error_code'], 'request_entity_too_large')

    def test_challenge_is_bound_to_session(self):
        payload = self.challenge(self.payload())
        other_client = self.authenticated_client()
        response = other_client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RechargeTask.query.count(), 0)
        valid = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(valid.status_code, 201)

    def test_mock_task_creation_returns_public_fields_only(self):
        payload = self.challenge(self.payload())
        response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        body = response.get_json()['data']
        for field in ('redeem_code', 'account_email', 'notify_email'):
            self.assertNotIn(field, body)

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
        response = self.client.post('/api/recharge/tasks/invoice/download', json={'slug': 'inv_slug_001'})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['success'])

    def test_mock_invoice_requires_existing_record(self):
        response = self.client.post('/api/recharge/tasks/invoice/download', json={'slug': 'inv_slug_001'})
        self.assertEqual(response.status_code, 404)

    def test_mock_invoice_is_not_visible_in_another_session(self):
        response = self.client.post('/api/recharge/billing/query', json={'token_input': 'synthetic-billing-token'})
        invoice = response.get_json()['data']['invoices'][0]
        response = self.client.post('/api/recharge/tasks/invoice/download', json={'slug': invoice['slug']})
        self.assertEqual(response.status_code, 200)
        other = self.authenticated_client().post(
            '/api/recharge/tasks/invoice/download', json={'slug': invoice['slug']}
        )
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
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
            payload = self.challenge(self.payload())

        # 创建接口必须回显本地任务号，以保证上下文切换后仍能凭远端任务号查询。
        def upstream(endpoint, submitted):
            return {'ok': True, 'task': {
                'task_no': 'UPSTREAM-MAPPED', 'client_task_no': submitted['client_task_no'],
                'status': 'processing',
            }}

        with patch.object(RechargeService, '_upstream_post', side_effect=upstream):
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

    def test_reconciliation_rejects_unidentified_unknown_task_response(self):
        """未绑定远端编号的 unknown 任务不能接受只有状态的响应。"""
        task = self.create_unknown_task()
        remote = {'status': 'completed'}
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}):
            response = self.client.post('/api/recharge/tasks/lookup', json={'redeem_code': task.redeem_code})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.filter_by(task_no=task.task_no).one().status, 'unknown')

    def test_live_lookup_without_local_task_requires_exact_remote_redeem_code(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'task': {
                'redeem_code': 'OTHER-CDK',
                'status': 'processing',
            },
        }):
            response = self.client.post('/api/recharge/tasks/lookup', json={
                'redeem_code': 'PLUS-NO-LOCAL-TASK',
            })
        self.assertEqual(response.status_code, 502)

    def test_live_mutation_requires_matching_client_task_and_terminal_status(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        cases = (
            ('recall', 'processing', 'recalled', 'recalled'),
            ('close', 'completed', 'completed', 'closed'),
        )
        for action, local_status, remote_status, expected_status in cases:
            with self.subTest(action=action, remote_status=remote_status):
                task = RechargeTask(
                    task_no=f'TK-MUTATION-{action.upper()}',
                    redeem_code=f'PLUS-MUTATION-{action.upper()}',
                    plan_type='PLUS', account_email='fixture@example.test',
                    status=local_status, status_text=local_status,
                    is_mock=False,
                )
                db.session.add(task)
                db.session.flush()
                db.session.add(RechargeOperation(task_no=task.task_no, active_key=f'mutation-gate-{action}'))
                db.session.commit()
                request = {
                    'redeem_code': task.redeem_code,
                    'email': task.account_email,
                    'confirmed': True,
                }

                with patch.object(RechargeService, '_upstream_post', return_value={
                    'ok': True,
                    'task': {
                        'client_task_no': 'WRONG-CLIENT-TASK',
                        'redeem_code': task.redeem_code,
                        'status': expected_status,
                    },
                }), self.assertRaises(RechargeUpstreamError):
                    getattr(RechargeService, f'{action}_task')(request)

                mutation = RechargeMutation.query.filter_by(task_no=task.task_no).one()
                self.assertEqual(mutation.state, 'unknown')
                self.assertEqual(task.status, local_status)
                self.assertIsNotNone(RechargeOperation.query.filter_by(task_no=task.task_no).one().active_key)

    def test_unknown_recall_mutation_does_not_finish_on_non_terminal_status(self):
        task = self.create_unknown_task()
        task.status = 'processing'
        db.session.commit()
        operation_id = RechargeMutation.claim(task, 'recall', expected_status='processing')
        RechargeMutation.finish(task.task_no, operation_id, 'unknown')
        db.session.commit()

        with self.assertRaises(RechargeUpstreamError):
            RechargeService._reconcile_task(task, {
                'client_task_no': task.task_no,
                'redeem_code': task.redeem_code,
                'status': 'completed',
            })
        self.assertEqual(RechargeMutation.query.filter_by(task_no=task.task_no).one().state, 'unknown')
        self.assertIsNotNone(RechargeOperation.query.filter_by(task_no=task.task_no).one().active_key)

    def test_mock_lookup_does_not_return_live_task(self):
        task = self.create_unknown_task()
        self.app.config['RECHARGE_MODE'] = 'mock'
        response = self.client.get(f'/api/recharge/tasks/{task.task_no}')
        self.assertEqual(response.status_code, 404)

    def test_completed_task_does_not_regress_on_stale_response(self):
        task = self.create_unknown_task()
        task.status = 'completed'
        db.session.commit()
        remote = {
            'task_no': 'UPSTREAM-SYNTHETIC', 'redeem_code': task.redeem_code,
            'status': 'processing',
        }
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}):
            response = self.client.post('/api/recharge/tasks/lookup', json={'redeem_code': task.redeem_code})
        self.assertEqual(response.get_json()['data']['status'], 'completed')
        self.assertEqual(RechargeTask.query.filter_by(task_no=task.task_no).one().status, 'completed')

    def test_malformed_success_keeps_task_unknown(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
            payload = self.challenge(self.payload())
        with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': {'status': 'unrecognized'}}):
            response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(RechargeTask.query.one().status, 'unknown')

    def test_initial_live_response_requires_exact_client_task_number(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
            payload = self.challenge(self.payload())
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'task': {
                'task_no': 'UPSTREAM-WITHOUT-CLIENT-ID',
                'redeem_code': payload['redeem_code'],
                'status': 'processing',
            },
        }):
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

    def test_renewal_challenge_requires_upstream_capability(self):
        payload = self.payload(is_renewal=True)
        with patch.object(RechargeService, 'validate_redeem_code', return_value={
            'plan_type': 'PLUS', 'status': 'valid', 'is_renewal_supported': False,
        }):
            response = self.client.post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('不支持续费', response.get_json()['message'])

    def test_raw_challenge_is_not_persisted_in_task(self):
        payload = self.challenge(self.payload())
        response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(RechargeTask.query.one().challenge_token)

    def test_late_timeout_does_not_overwrite_completed_task(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
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

    def test_email_notification_channel_is_rejected_until_delivery_is_supported(self):
        payload = self.challenge(self.payload(notify_channel='email'))
        response = self.client.post('/api/recharge/tasks', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['message'], '通知渠道无效，仅支持 site')
        self.assertEqual(RechargeTask.query.count(), 0)

    def test_explicit_non_free_acknowledgement_cannot_be_bypassed(self):
        for value in (False, None, 'missing'):
            with self.subTest(value=value):
                payload = self.payload(acknowledge_non_free=value)
                if value == 'missing':
                    payload.pop('acknowledge_non_free')
                payload = self.challenge(payload)
                response = self.client.post('/api/recharge/tasks', json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn('非免费套餐', response.get_json()['message'])
        self.assertEqual(RechargeTask.query.count(), 0)

    def test_unsubmitted_lookup_does_not_echo_redeem_code(self):
        response = self.client.post('/api/recharge/tasks/lookup', json={
            'redeem_code': 'UNSUBMITTED-SENSITIVE-CARD',
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()['data']
        self.assertEqual(body['status'], 'idle')
        self.assertNotIn('redeem_code', body)

    def test_live_results_are_schema_checked_and_sensitive_fields_are_filtered(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'result': {
                'plan_type': 'PLUS', 'status': 'unused',
                'is_mock': True,
                'session_token': 'secret-token', 'account_email': 'secret@example.test',
            },
        }):
            response = self.client.post('/api/recharge/redeem-codes/validate', json={
                'redeem_code': 'PLUS-LIVE-SCHEMA',
            })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()['data']
        self.assertEqual(body['plan_type'], 'PLUS')
        self.assertFalse(body['is_mock'])
        self.assertNotIn('session_token', body)
        self.assertNotIn('account_email', body)

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'session_token': 'secret-token'},
        }):
            malformed = self.client.post('/api/recharge/redeem-codes/validate', json={
                'redeem_code': 'PLUS-LIVE-MALFORMED',
            })
        self.assertEqual(malformed.status_code, 502)

    def test_live_upstream_errors_and_billing_fields_are_redacted_and_bounded(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        secret_message = 'session_token=super-secret@example.test ' + ('x' * 400)
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': False, 'message': secret_message,
        }):
            response = self.client.post('/api/recharge/redeem-codes/validate', json={
                'redeem_code': 'PLUS-LIVE-ERROR-MESSAGE',
            })
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('super-secret', response.get_json()['message'])
        self.assertLessEqual(len(response.get_json()['message']), 128)

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'result': {
                'status': 'active', 'auto_renew': True,
                'card_last4': 'not-a-card',
            },
        }):
            malformed = self.client.post('/api/recharge/billing/query', json={
                'token_input': 'synthetic-billing-token',
            })
        self.assertEqual(malformed.status_code, 502)

    def test_live_billing_mutations_accept_minimal_status_or_auto_renew_results(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        cases = [
            ('cancel-subscription', {'status': 'canceled_at_period_end'}),
            ('cancel-subscription', {'auto_renew': False}),
            ('resume-subscription', {'status': 'active'}),
            ('resume-subscription', {'auto_renew': True}),
        ]
        for action, result in cases:
            with self.subTest(action=action, result=result):
                with patch.object(RechargeService, '_upstream_post', return_value={
                    'ok': True, 'result': {**result, 'is_mock': True},
                }):
                    response = self.client.post(f'/api/recharge/billing/{action}', json={
                        'token_input': f"synthetic-{action}-{result}", 'confirmed': True,
                    })
                self.assertEqual(response.status_code, 200, response.get_json())
                data = response.get_json()['data']
                self.assertFalse(data['is_mock'])
                for key, value in result.items():
                    self.assertEqual(data[key], value)

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'is_mock': True},
        }):
            malformed = self.client.post('/api/recharge/billing/cancel-subscription', json={
                'token_input': 'synthetic-empty-mutation', 'confirmed': True,
            })
        self.assertEqual(malformed.status_code, 502)

    def test_live_billing_mutations_reject_action_inconsistent_results(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        cases = (
            ('cancel-subscription', {'auto_renew': True}),
            ('cancel-subscription', {'status': 'active'}),
            ('resume-subscription', {'auto_renew': False}),
            ('resume-subscription', {'status': 'canceled_at_period_end'}),
        )
        for action, result in cases:
            with self.subTest(action=action, result=result):
                with patch.object(RechargeService, '_upstream_post', return_value={
                    'ok': True, 'result': result,
                }):
                    response = self.client.post(f'/api/recharge/billing/{action}', json={
                        'token_input': f'inconsistent-{action}-{result}', 'confirmed': True,
                    })
                self.assertEqual(response.status_code, 502, response.get_json())

    def test_batch_lookup_applies_ip_total_and_per_card_rate_limits(self):
        with patch('app.services.request_security.RequestLimit.consume', return_value=True) as consume:
            response = self.client.post('/api/recharge/tasks/lookup-batch', json={
                'redeem_codes': ['PLUS-BATCH-A', 'PLUS-BATCH-B', 'PLUS-BATCH-A'],
            })

        self.assertEqual(response.status_code, 200, response.get_json())
        keys = [call.args[0] for call in consume.call_args_list]
        ip_keys = [key for key in keys if key.startswith('recharge:')]
        card_keys = [key for key in keys if key.startswith('card:')]
        self.assertEqual(len(ip_keys), 1, keys)
        self.assertEqual(len(card_keys), 2, keys)
        self.assertEqual(len(set(card_keys)), 2, keys)

    def test_batch_lookup_rejects_when_one_card_exceeds_rate_limit(self):
        def consume(key, limit, window):
            return not key.startswith('card:')

        with patch('app.services.request_security.RequestLimit.consume', side_effect=consume):
            response = self.client.post('/api/recharge/tasks/lookup-batch', json={
                'redeem_codes': ['PLUS-BATCH-BLOCKED'],
            })

        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertIn('卡密', response.get_json()['message'])
        self.assertEqual(response.get_json()['error_code'], 'rate_limited')

    def test_live_batch_lookup_requires_exact_unique_result_set(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        codes = ['PLUS-BATCH-ONE', 'PLUS-BATCH-TWO']

        # The upstream may return rows in another order, but the public response
        # must follow the request-side de-duplicated order.
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'results': [
                {
                    'redeem_code': codes[1], 'plan_type': 'PRO',
                    'status': 'completed', 'task_no': 'TASK-TWO',
                },
                {
                    'redeem_code': codes[0], 'plan_type': 'PLUS',
                    'status': 'processing', 'task_no': 'TASK-ONE',
                },
            ],
        }):
            response = self.client.post('/api/recharge/tasks/lookup-batch', json={
                'redeem_codes': [codes[0], codes[1], codes[0]],
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        results = response.get_json()['data']
        self.assertEqual([result['redeem_code'] for result in results], codes)
        self.assertEqual([result['status'] for result in results], ['processing', 'completed'])
        self.assertTrue(all('account_email' not in result for result in results))

        invalid_results = {
            'missing': [
                {'redeem_code': codes[0], 'status': 'processing'},
            ],
            'duplicate': [
                {'redeem_code': codes[0], 'status': 'processing'},
                {'redeem_code': codes[0], 'status': 'completed'},
            ],
            'unknown': [
                {'redeem_code': codes[0], 'status': 'processing'},
                {'redeem_code': 'PLUS-BATCH-UNKNOWN', 'status': 'completed'},
            ],
            'non_object': [
                {'redeem_code': codes[0], 'status': 'processing'},
                'not-an-object',
            ],
        }
        for case, results in invalid_results.items():
            with self.subTest(case=case), patch.object(
                RechargeService, '_upstream_post', return_value={'ok': True, 'results': results}
            ):
                response = self.client.post('/api/recharge/tasks/lookup-batch', json={
                    'redeem_codes': codes,
                })
            self.assertEqual(response.status_code, 502, response.get_json())
            self.assertFalse(response.get_json()['success'])

    def test_live_batch_lookup_rejects_remote_task_without_plan_type(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'results': [{
                'redeem_code': 'PLUS-BATCH-NO-PLAN',
                'status': 'processing',
                'task_no': 'TASK-NO-PLAN',
            }],
        }):
            response = self.client.post('/api/recharge/tasks/lookup-batch', json={
                'redeem_codes': ['PLUS-BATCH-NO-PLAN'],
            })
        self.assertEqual(response.status_code, 502, response.get_json())
        self.assertEqual(response.get_json()['error_code'], 'upstream_error')

    def test_credential_rate_limit_uses_hmac_key_and_blocks_service(self):
        credential = 'synthetic-sensitive-credential'
        with patch('app.services.request_security.RequestLimit.consume', return_value=True) as consume:
            response = self.client.post('/api/recharge/billing/query', json={
                'token_input': credential,
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        keys = [call.args[0] for call in consume.call_args_list]
        credential_keys = [key for key in keys if key.startswith('credential:')]
        self.assertEqual(len(credential_keys), 1, keys)
        self.assertNotIn(credential, credential_keys[0])

        def consume_limit(key, limit, window):
            return not key.startswith('credential:')

        with patch(
            'app.services.request_security.RequestLimit.consume', side_effect=consume_limit
        ), patch.object(RechargeService, 'billing_query') as billing:
            blocked = self.client.post('/api/recharge/billing/query', json={
                'token_input': credential,
            })
        self.assertEqual(blocked.status_code, 429, blocked.get_json())
        self.assertIn('凭证', blocked.get_json()['message'])
        self.assertEqual(blocked.get_json()['error_code'], 'rate_limited')
        billing.assert_not_called()

    def test_live_billing_mutations_send_stable_idempotency_key(self):
        self.app.config['RECHARGE_MODE'] = 'live'
        calls = []

        def upstream(endpoint, payload):
            calls.append((endpoint, payload))
            return {'ok': True, 'result': {
                'status': 'canceled_at_period_end', 'auto_renew': False,
                'is_mock': False,
            }}

        with patch.object(RechargeService, '_upstream_post', side_effect=upstream):
            first = self.client.post('/api/recharge/billing/cancel-subscription', json={
                'token_input': 'synthetic-billing-token', 'confirmed': True,
            })
            second = self.client.post('/api/recharge/billing/cancel-subscription', json={
                'token_input': 'synthetic-billing-token', 'confirmed': True,
            })
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(calls[0][1]['idempotency_key'], calls[1][1]['idempotency_key'])
        self.assertNotEqual(calls[0][1]['idempotency_key'], 'synthetic-billing-token')

    def test_upstream_response_size_is_bounded(self):
        class OversizedResponse:
            headers = {'Content-Length': str(RechargeService.MAX_UPSTREAM_RESPONSE_BYTES + 1)}

            def read(self, size):
                raise AssertionError('oversized response must be rejected before read')

        with self.assertRaises(RechargeUpstreamError):
            RechargeService._read_upstream_json(OversizedResponse())

    def test_upstream_redirects_are_not_followed(self):
        redirect = urllib.error.HTTPError(
            'https://aichong666.com/api/user/tasks',
            302,
            'Found',
            {'Location': 'https://evil.example.test/collect'},
            BytesIO(b''),
        )
        with patch('urllib.request.build_opener') as build_opener:
            build_opener.return_value.open.side_effect = redirect
            result = RechargeService._upstream_post('/user/tasks', {'synthetic': True})

        self.assertFalse(result['ok'])
        self.assertIn('HTTP 302', result['message'])
        handler = build_opener.call_args.args[0]
        self.assertIsNone(handler.redirect_request(
            urllib.request.Request('https://aichong666.com/api/user/tasks'),
            None,
            302,
            'Found',
            {},
            'https://evil.example.test/collect',
        ))


if __name__ == '__main__':
    unittest.main()
