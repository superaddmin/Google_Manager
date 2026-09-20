import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_billing_mutation import RechargeBillingMutation
from app.services.recharge_service import (
    RechargeContractError,
    RechargeService,
    RechargeUpstreamError,
)


class BillingIdempotencyTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='billing-idempotency-')
        database_path = (Path(self.directory.name) / 'isolated.db').as_posix()
        self.database_uri = f'sqlite:///{database_path}'
        self.config_patch = patch.object(
            TestingConfig, 'SQLALCHEMY_DATABASE_URI', self.database_uri
        )
        self.config_patch.start()
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        self.app.config['RECHARGE_MODE'] = 'live'
        self.billing_hmac_key = base64.urlsafe_b64encode(b'B' * 32).decode('ascii')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = self.billing_hmac_key

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.config_patch.stop()
        self.directory.cleanup()

    @staticmethod
    def cancel_result():
        return {'ok': True, 'result': {
            'status': 'canceled_at_period_end', 'auto_renew': False,
        }}

    @staticmethod
    def resume_result():
        return {'ok': True, 'result': {
            'status': 'active', 'auto_renew': True,
        }}

    def worker_claim(self, credential_hash, action):
        environment = os.environ.copy()
        environment['BILLING_TEST_DATABASE'] = self.database_uri
        environment['BILLING_TEST_SECRET'] = self.app.secret_key
        environment['BILLING_TEST_HASH'] = credential_hash
        environment['BILLING_TEST_ACTION'] = action
        source = """
import json, os
from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_billing_mutation import RechargeBillingMutation
TestingConfig.SQLALCHEMY_DATABASE_URI = os.environ['BILLING_TEST_DATABASE']
TestingConfig.SECRET_KEY = os.environ['BILLING_TEST_SECRET']
application = create_app('testing')
with application.app_context():
    result = RechargeBillingMutation.claim(
        os.environ['BILLING_TEST_HASH'], os.environ['BILLING_TEST_ACTION'])
    print(json.dumps(result))
    db.session.remove()
    db.engine.dispose()
"""
        result = subprocess.run(
            [sys.executable, '-c', source],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def test_cancel_resume_cancel_uses_a_new_key_for_each_business_intent(self):
        calls = []

        def upstream(endpoint, payload):
            calls.append((endpoint, payload.copy()))
            if endpoint.endswith('cancel-subscription'):
                return {'ok': True, 'result': {
                    'status': 'canceled_at_period_end', 'auto_renew': False,
                }}
            return {'ok': True, 'result': {
                'status': 'active', 'auto_renew': True,
            }}

        token = 'synthetic-billing-token'
        with patch.object(RechargeService, '_upstream_post', side_effect=upstream):
            RechargeService.billing_cancel_subscription({
                'token_input': token, 'confirmed': True,
            })
            RechargeService.billing_resume_subscription({
                'token_input': token, 'confirmed': True,
            })
            RechargeService.billing_cancel_subscription({
                'token_input': token, 'confirmed': True,
            })

        keys = [payload['idempotency_key'] for _, payload in calls]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3)
        self.assertNotIn(token, keys)

    def test_confirmed_replay_reuses_the_same_operation_key(self):
        calls = []

        def upstream(endpoint, payload):
            calls.append(payload.copy())
            return self.cancel_result()

        token = 'synthetic-confirmed-replay'
        with patch.object(RechargeService, '_upstream_post', side_effect=upstream):
            for _ in range(2):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['idempotency_key'], calls[1]['idempotency_key'])

    def test_timeout_retry_reuses_key_without_persisting_raw_token(self):
        calls = []

        def timeout(endpoint, payload):
            calls.append(payload.copy())
            raise TimeoutError('synthetic timeout')

        token = 'credential-that-must-never-be-stored'
        with patch.object(RechargeService, '_upstream_post', side_effect=timeout):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })

        record = RechargeBillingMutation.query.one()
        self.assertEqual(record.state, 'unknown')
        self.assertNotEqual(record.credential_hash, token)
        first_key = calls[0]['idempotency_key']

        with patch.object(
            RechargeService, '_upstream_post', return_value=self.cancel_result()
        ) as upstream:
            RechargeService.billing_cancel_subscription({
                'token_input': token, 'confirmed': True,
            })

        self.assertEqual(upstream.call_args.args[1]['idempotency_key'], first_key)
        db.session.expire_all()
        self.assertEqual(RechargeBillingMutation.query.one().state, 'done')

    def test_session_secret_rotation_preserves_unknown_operation_gate(self):
        token = 'credential-across-session-secret-rotation'
        self.app.secret_key = 'session-secret-before-rotation'
        credential_hash = RechargeService._billing_credential_hash(token)

        with patch.object(
            RechargeService, '_upstream_post', side_effect=TimeoutError('timeout')
        ):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })
        operation_id = RechargeBillingMutation.query.one().operation_id

        self.app.secret_key = 'session-secret-after-rotation'
        self.assertEqual(RechargeService._billing_credential_hash(token), credential_hash)
        with patch.object(RechargeService, '_upstream_post') as upstream:
            with self.assertRaises(RechargeContractError):
                RechargeService.billing_resume_subscription({
                    'token_input': token, 'confirmed': True,
                })
        upstream.assert_not_called()
        db.session.expire_all()
        record = RechargeBillingMutation.query.one()
        self.assertEqual(record.operation_id, operation_id)
        self.assertEqual(record.state, 'unknown')

    def test_opposite_query_keeps_unknown_and_blocks_reverse_action(self):
        token = 'synthetic-unknown-billing-token'
        with patch.object(
            RechargeService, '_upstream_post', side_effect=TimeoutError('timeout')
        ):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'result': {'status': 'active', 'auto_renew': True, 'invoices': []},
        }):
            RechargeService.billing_query(token)

        db.session.expire_all()
        self.assertEqual(RechargeBillingMutation.query.one().state, 'unknown')
        with patch.object(RechargeService, '_upstream_post') as upstream:
            with self.assertRaises(RechargeContractError):
                RechargeService.billing_resume_subscription({
                    'token_input': token, 'confirmed': True,
                })
        upstream.assert_not_called()

    def test_target_query_resolves_unknown_and_allows_next_intent(self):
        token = 'synthetic-reconciled-billing-token'
        calls = []

        def timeout(endpoint, payload):
            calls.append(payload.copy())
            raise TimeoutError('timeout')

        with patch.object(RechargeService, '_upstream_post', side_effect=timeout):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'result': {
                'status': 'canceled_at_period_end',
                'auto_renew': False,
                'invoices': [],
            },
        }):
            RechargeService.billing_query(token)

        db.session.expire_all()
        self.assertEqual(RechargeBillingMutation.query.one().state, 'done')
        first_key = calls[0]['idempotency_key']
        with patch.object(
            RechargeService, '_upstream_post', return_value=self.resume_result()
        ) as upstream:
            RechargeService.billing_resume_subscription({
                'token_input': token, 'confirmed': True,
            })
        self.assertNotEqual(upstream.call_args.args[1]['idempotency_key'], first_key)

    def test_late_query_snapshot_cannot_resolve_reclaimed_operation(self):
        credential_hash = RechargeService._billing_credential_hash('late-query-token')
        claim = RechargeBillingMutation.claim(credential_hash, 'cancel')
        RechargeBillingMutation.mark_unknown(
            credential_hash,
            claim['operation_id'],
            claim['lease_token'],
        )
        snapshot = RechargeBillingMutation.snapshot(credential_hash)
        cached_record = db.session.get(RechargeBillingMutation, credential_hash)
        self.assertEqual(cached_record.state, 'unknown')

        # Simulate another process reclaiming the same operation while this
        # session still has the old unknown row in its identity map.
        with db.engine.begin() as connection:
            connection.execute(
                RechargeBillingMutation.__table__.update().where(
                    RechargeBillingMutation.credential_hash == credential_hash
                ).values(
                    state='pending',
                    lease_until=time.time() + 30,
                    updated_at=snapshot['updated_at'] + 1,
                )
            )
        reconciled = RechargeBillingMutation.reconcile_query(
            credential_hash,
            snapshot,
            False,
            'canceled_at_period_end',
        )

        self.assertFalse(reconciled)
        db.session.expire_all()
        self.assertEqual(RechargeBillingMutation.query.one().state, 'pending')

    def test_expired_pending_is_reclaimed_with_same_key_after_restart_boundary(self):
        credential_hash = RechargeService._billing_credential_hash('restart-token')
        first = RechargeBillingMutation.claim(credential_hash, 'cancel')
        record = RechargeBillingMutation.query.one()
        record.lease_until = time.time() - 1
        db.session.commit()
        db.session.remove()

        recovered = RechargeBillingMutation.claim(credential_hash, 'cancel')
        self.assertEqual(recovered['status'], 'acquired')
        self.assertEqual(recovered['operation_id'], first['operation_id'])

    def test_stale_failure_cannot_clear_a_reclaimed_lease(self):
        credential_hash = RechargeService._billing_credential_hash('lease-generation-token')
        first = RechargeBillingMutation.claim(credential_hash, 'cancel')
        record = RechargeBillingMutation.query.one()
        record.lease_until = time.time() - 1
        db.session.commit()

        second = RechargeBillingMutation.claim(credential_hash, 'cancel')
        self.assertEqual(second['operation_id'], first['operation_id'])
        self.assertNotEqual(second['lease_token'], first['lease_token'])
        RechargeBillingMutation.mark_unknown(
            credential_hash,
            first['operation_id'],
            first['lease_token'],
        )

        db.session.expire_all()
        current = RechargeBillingMutation.query.one()
        self.assertEqual(current.state, 'pending')
        self.assertEqual(current.lease_token, second['lease_token'])
        self.assertEqual(
            RechargeBillingMutation.claim(credential_hash, 'cancel')['status'],
            'busy',
        )

    def test_expired_query_cannot_overwrite_newer_opposite_completion(self):
        credential_hash = RechargeService._billing_credential_hash('two-phase-query-token')
        first = RechargeBillingMutation.claim(credential_hash, 'cancel')
        record = RechargeBillingMutation.query.one()
        record.lease_until = time.time() - 1
        db.session.commit()
        snapshot = RechargeBillingMutation.snapshot(credential_hash)
        replacement_operation = 'f' * 32
        original_commit = db.session.commit
        injected = False

        def commit_and_advance_generation():
            nonlocal injected
            original_commit()
            if injected:
                return
            injected = True
            with db.engine.begin() as connection:
                connection.execute(
                    RechargeBillingMutation.__table__.update().where(
                        RechargeBillingMutation.credential_hash == credential_hash
                    ).values(
                        action='resume',
                        operation_id=replacement_operation,
                        state='done',
                        lease_until=None,
                        updated_at=time.time() + 1,
                        confirmed_auto_renew=True,
                        result_status='active',
                    )
                )

        with patch.object(db.session, 'commit', side_effect=commit_and_advance_generation):
            RechargeBillingMutation.reconcile_query(
                credential_hash,
                snapshot,
                False,
                'canceled_at_period_end',
            )

        db.session.expire_all()
        current = RechargeBillingMutation.query.one()
        self.assertEqual(current.operation_id, replacement_operation)
        self.assertEqual(current.action, 'resume')
        self.assertTrue(current.confirmed_auto_renew)
        self.assertEqual(current.result_status, 'active')

    def test_cross_process_claim_allows_only_one_upstream_owner(self):
        credential_hash = RechargeService._billing_credential_hash('shared-process-token')
        db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(
                lambda unused: self.worker_claim(credential_hash, 'cancel'),
                range(2),
            ))

        self.assertEqual(sorted(result['status'] for result in results), ['acquired', 'busy'])
        operation_ids = {result['operation_id'] for result in results}
        self.assertEqual(len(operation_ids), 1)

    def test_unconfirmed_success_payload_becomes_unknown(self):
        token = 'ambiguous-upstream-result'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'status': 'queued'},
        }):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.billing_cancel_subscription({
                    'token_input': token, 'confirmed': True,
                })

        db.session.expire_all()
        self.assertEqual(RechargeBillingMutation.query.one().state, 'unknown')

    def test_query_observed_external_state_invalidates_old_replay_target(self):
        token = 'external-state-change-token'
        calls = []

        def cancel(endpoint, payload):
            calls.append(payload.copy())
            return self.cancel_result()

        with patch.object(RechargeService, '_upstream_post', side_effect=cancel):
            RechargeService.billing_cancel_subscription({
                'token_input': token, 'confirmed': True,
            })
        first_key = calls[0]['idempotency_key']

        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True,
            'result': {'status': 'active', 'auto_renew': True, 'invoices': []},
        }):
            RechargeService.billing_query(token)

        with patch.object(RechargeService, '_upstream_post', side_effect=cancel):
            RechargeService.billing_cancel_subscription({
                'token_input': token, 'confirmed': True,
            })
        self.assertNotEqual(calls[-1]['idempotency_key'], first_key)


if __name__ == '__main__':
    unittest.main()
