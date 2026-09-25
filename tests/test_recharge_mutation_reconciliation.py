import copy
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import text

from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_mutation_reconciliation import RechargeMutationReconciliation
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_reconciliation import RechargeReconciliation
from app.models.recharge_task import RechargeTask
from app.services.auth_service import AuthService
from app.services.recharge_service import (
    RechargeContractError, RechargeReconciliationConflictError, RechargeService, RechargeUpstreamError,
)
from app.services.schema_migration import MIGRATIONS, initialize_database, validate_schema


class RechargeMutationReconciliationTestCase(unittest.TestCase):
    actor = 'admin:' + 'a' * 24

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='mutation-reconciliation-')
        self.config_patch = patch.object(
            TestingConfig, 'SQLALCHEMY_DATABASE_URI',
            'sqlite:///' + (Path(self.directory.name) / 'isolated.db').as_posix(),
        )
        self.config_patch.start()
        self.app = create_app('testing')
        self.app.config['RECHARGE_MODE'] = 'live'
        self.context = self.app.app_context()
        self.context.push()
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External HTTP forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.config_patch.stop()
        self.directory.cleanup()

    def admin_client(self):
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        response = client.post('/api/auth/login', json={
            'password': 'admin123', 'salt': AuthService.generate_salt(int(time.time())),
        })
        self.assertEqual(response.status_code, 200)
        return client

    def task(self, task_no='TK-MUTATION-1', action='recall', state='unknown', status='processing'):
        task = RechargeTask(
            task_no=task_no, redeem_code='PLUS-' + task_no, plan_type='PLUS',
            account_email='fixture@example.test', status=status, is_mock=False,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(
            task_no=task_no, upstream_task_no='REMOTE-' + task_no, active_key='active-' + task_no,
        ))
        db.session.commit()
        operation_id = RechargeMutation.claim(task, action)
        if state != 'pending':
            RechargeMutation.finish(task_no, operation_id, state)
            db.session.commit()
        return task, operation_id

    def payload(self, task, action='recall', status=None, reference='TICKET-MUTATION-001'):
        return {
            'confirmed': True, 'action': action, 'resolution': 'not_applied',
            'basis': 'Provider ticket confirms this exact operation was rejected and will not execute.',
            'evidence': {
                'source': 'provider_ticket', 'reference': reference, 'sha256': 'b' * 64,
                'observed_at': datetime.now(timezone.utc).isoformat(),
            },
            'upstream_task': {
                'task_no': 'REMOTE-' + task.task_no, 'client_task_no': task.task_no,
                'redeem_code': task.redeem_code, 'account_email': task.account_email,
                'status': status or task.status,
            },
        }

    @staticmethod
    def endpoint(task_no, operation_id):
        return f'/api/recharge/admin/tasks/{task_no}/mutations/{operation_id}/reconcile'

    def settle(self, task, operation_id, payload=None):
        return RechargeService.reconcile_unknown_mutation(
            task.task_no, operation_id, payload or self.payload(task), self.actor,
        )

    @staticmethod
    def intent(task):
        return {'task_no': task.task_no, 'redeem_code': task.redeem_code,
                'email': task.account_email, 'confirmed': True}

    def test_admin_resolution_is_audited_without_network_and_allows_new_intent(self):
        for action in ('recall', 'close'):
            with self.subTest(action=action):
                task, operation_id = self.task('TK-' + action, action=action)
                payload = self.payload(task, action, reference='TICKET-' + action)
                with patch.object(RechargeService, '_upstream_post') as upstream:
                    response = self.admin_client().post(self.endpoint(task.task_no, operation_id), json=payload)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    upstream.assert_not_called()
                result = response.get_json()['data']
                self.assertFalse(result['replayed'])
                self.assertEqual(result['reconciliation']['operation_id'], operation_id)
                self.assertEqual(result['reconciliation']['final_state'], 'rejected')
                self.assertNotIn('redeem_code', result['task'])
                db.session.expire_all()
                self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'rejected')
                self.assertIsNotNone(db.session.get(RechargeOperation, task.task_no).active_key)
                remote = {**payload['upstream_task'], 'status': 'recalled' if action == 'recall' else 'closed'}
                with patch.object(RechargeService, '_upstream_post', return_value={'ok': True, 'task': remote}) as upstream:
                    getattr(RechargeService, action + '_task')(self.intent(task))
                sent = upstream.call_args.args[1]
                self.assertNotEqual(sent['idempotency_key'], operation_id)
                self.assertEqual(upstream.call_count, 1)
                self.assertEqual(RechargeMutationReconciliation.query.filter_by(operation_id=operation_id).count(), 1)
                self.assertRegex(result['reconciliation']['actor_id'], r'^admin:[0-9a-f]{24}$')

    def test_authorization_and_mode_gates(self):
        task, operation_id = self.task()
        endpoint = self.endpoint(task.task_no, operation_id)
        payload = self.payload(task)
        anonymous = self.app.test_client()
        anonymous.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        self.assertEqual(anonymous.post(endpoint, json=payload).status_code, 401)
        client = self.admin_client()
        for mode, expected in (('mock', 400), ('disabled', 503)):
            self.app.config['RECHARGE_MODE'] = mode
            self.assertEqual(client.post(endpoint, json=payload).status_code, expected)
        self.app.config['RECHARGE_MODE'] = 'live'
        self.app.config['ADMIN_PASSWORD'] = 'changed-password'
        self.assertEqual(client.post(endpoint, json=payload).status_code, 401)
        self.assertEqual(RechargeMutationReconciliation.query.count(), 0)

    def test_admin_get_discovers_exact_operation_without_upstream_or_private_fields(self):
        task, operation_id = self.task()
        endpoint = f'/api/recharge/admin/tasks/{task.task_no}/mutations/current'
        self.assertEqual(self.app.test_client().get(endpoint).status_code, 401)
        client = self.admin_client()
        with patch.object(RechargeService, '_upstream_get') as upstream:
            response = client.get(endpoint)
            upstream.assert_not_called()
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()['data']
        self.assertEqual(result['mutation']['operation_id'], operation_id)
        self.assertEqual(result['mutation']['task_no'], task.task_no)
        self.assertEqual(result['mutation']['action'], 'recall')
        self.assertEqual(result['mutation']['state'], 'unknown')
        self.assertIsNotNone(datetime.fromisoformat(result['mutation']['started_at']).tzinfo)
        self.assertNotIn(task.redeem_code, response.get_data(as_text=True))
        self.assertNotIn(task.account_email, response.get_data(as_text=True))
        self.assertEqual(client.get(endpoint.replace(task.task_no, 'TK-MISSING')).status_code, 400)
        self.app.config['RECHARGE_MODE'] = 'disabled'
        self.assertEqual(client.get(endpoint).status_code, 503)

    def test_invalid_or_insufficient_evidence_never_changes_state(self):
        task, operation_id = self.task()
        client = self.admin_client()
        base = self.payload(task)
        cases = [
            ('confirmed', False), ('resolution', 'timeout'), ('basis', ''), ('basis', 'bad\ntext'),
            ('action', []), ('action', 'close'), ('evidence.sha256', 'wrong'),
            ('evidence.source', []), ('evidence.observed_at', '2026-01-01T00:00:00'),
            ('evidence.observed_at', (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()),
            ('evidence.observed_at', (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
            ('upstream_task.client_task_no', 'TK-OTHER'), ('upstream_task.task_no', 'REMOTE-OTHER'),
            ('upstream_task.redeem_code', 'OTHER'), ('upstream_task.account_email', 'other@example.test'),
            ('upstream_task.status', 'recalled'), ('upstream_task.status', []),
            ('upstream_task.status', 'unknown'), ('upstream_task.status', 'pending'),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(base)
                if '.' in field:
                    parent, name = field.split('.')
                    payload[parent][name] = value
                else:
                    payload[field] = value
                response = client.post(self.endpoint(task.task_no, operation_id), json=payload)
                self.assertIn(response.status_code, (400, 409), response.get_json())
                db.session.expire_all()
                self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'unknown')
                self.assertEqual(task.status, 'processing')
                self.assertEqual(RechargeMutationReconciliation.query.count(), 0)

    def test_wrong_operation_task_or_non_unknown_state_is_rejected(self):
        client = self.admin_client()
        for state in ('pending', 'done', 'unknown'):
            task, operation_id = self.task('TK-' + state, state=state)
            payload = self.payload(task)
            identifiers = [operation_id] if state != 'unknown' else ['invalid', 'f' * 32]
            for identifier in identifiers:
                response = client.post(self.endpoint(task.task_no, identifier), json=payload)
                self.assertIn(response.status_code, (400, 409))
        response = client.post(self.endpoint('TK-MISSING', operation_id), json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RechargeMutationReconciliation.query.count(), 0)

    def test_exact_replay_conflict_and_multiple_operation_history(self):
        task, operation_id = self.task()
        payload = self.payload(task)
        self.settle(task, operation_id, payload)
        replay = self.settle(task, operation_id, payload)
        self.assertTrue(replay['replayed'])
        with self.assertRaises(RechargeReconciliationConflictError):
            self.settle(task, operation_id, {**payload, 'basis': 'Different provider conclusion.'})
        new_id = RechargeMutation.claim(task, 'recall')
        RechargeMutation.finish(task.task_no, new_id, 'unknown')
        db.session.commit()
        self.assertTrue(self.settle(task, operation_id, payload)['replayed'])
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).operation_id, new_id)
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'unknown')
        with self.assertRaises(RechargeReconciliationConflictError):
            self.settle(task, new_id, self.payload(task))
        self.settle(task, new_id, self.payload(task, reference='TICKET-MUTATION-002'))
        self.assertEqual(RechargeMutationReconciliation.query.count(), 2)

    def test_terminal_evidence_releases_only_failed_or_recalled_tasks(self):
        for status in ('processing', 'completed', 'failed', 'recalled'):
            task, operation_id = self.task('TK-' + status, action='close')
            result = self.settle(task, operation_id, self.payload(
                task, 'close', status, reference='TICKET-' + status,
            ))
            self.assertEqual(result['task']['status'], status)
            active = db.session.get(RechargeOperation, task.task_no).active_key
            self.assertEqual(active is None, status in ('failed', 'recalled'))

    def test_close_unknown_on_completed_task_can_be_rejected_without_state_regression(self):
        task, operation_id = self.task(action='close', status='completed')
        with self.assertRaises(RechargeReconciliationConflictError):
            self.settle(task, operation_id, self.payload(task, 'close', 'processing'))
        result = self.settle(task, operation_id, self.payload(task, 'close'))
        self.assertEqual(result['task']['status'], 'completed')
        self.assertEqual(result['reconciliation']['final_state'], 'rejected')

    def test_timeout_or_single_query_never_infers_rejection(self):
        task, operation_id = self.task()
        remote = self.payload(task, status='completed')['upstream_task']
        with patch.object(RechargeService, '_upstream_get', return_value={'ok': True, 'task': remote}):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService.get_task_by_no(task.task_no)
        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeMutation, task.task_no).state, 'unknown')
        self.assertEqual(task.status, 'processing')
        self.assertEqual(RechargeMutationReconciliation.query.count(), 0)
        with self.assertRaises(RechargeContractError):
            RechargeMutation.claim(task, 'recall')

    def test_late_mutation_success_and_timeout_cannot_override_rejection_or_new_intent(self):
        for action in ('recall', 'close'):
            for new_intent in (False, True):
                for timeout in (False, True):
                    with self.subTest(action=action, new_intent=new_intent, timeout=timeout):
                        task, old_id = self.task(f'TK-{action}-{new_intent}-{timeout}', action, state='done')
                        intent = self.intent(task)
                        new_ids = []

                        def late_response(endpoint, request):
                            operation_id = request['idempotency_key']
                            RechargeMutation.finish(task.task_no, operation_id, 'unknown')
                            db.session.commit()
                            payload = self.payload(task, action, reference='TICKET-' + task.task_no)
                            self.settle(task, operation_id, payload)
                            if new_intent:
                                new_ids.append(RechargeMutation.claim(task, 'close'))
                            if timeout:
                                raise RechargeUpstreamError('synthetic late timeout')
                            return {'ok': True, 'task': {
                                **payload['upstream_task'], 'status': 'recalled' if action == 'recall' else 'closed',
                            }}

                        with patch.object(RechargeService, '_upstream_post', side_effect=late_response):
                            with self.assertRaises(RechargeUpstreamError if timeout else RechargeContractError):
                                getattr(RechargeService, action + '_task')(intent)
                        db.session.expire_all()
                        self.assertEqual(task.status, 'processing')
                        current = db.session.get(RechargeMutation, task.task_no)
                        self.assertEqual(current.state, 'pending' if new_intent else 'rejected')
                        if new_ids:
                            self.assertEqual(current.operation_id, new_ids[0])
                        self.assertIsNotNone(db.session.get(RechargeOperation, task.task_no).active_key)

    def test_late_single_and_batch_queries_cannot_override_manual_resolution(self):
        for query in ('get', 'lookup', 'batch'):
            for new_intent in (False, True):
                with self.subTest(query=query, new_intent=new_intent):
                    task, operation_id = self.task(f'TK-{query}-{new_intent}')
                    task_no, code = task.task_no, task.redeem_code

                    def delayed_response(*args):
                        payload = self.payload(task, reference='TICKET-' + task_no)
                        self.settle(task, operation_id, payload)
                        if new_intent:
                            new_id = RechargeMutation.claim(task, 'recall')
                            RechargeMutation.finish(task_no, new_id, 'unknown')
                            db.session.commit()
                        remote = {**payload['upstream_task'], 'status': 'recalled'}
                        return {'ok': True, 'task': remote, 'results': [{'ok': True, 'redeem_code': code, 'task': remote}]}

                    with patch.object(RechargeService, '_upstream_get', side_effect=delayed_response), patch.object(
                        RechargeService, '_upstream_post', side_effect=delayed_response,
                    ):
                        if query == 'get':
                            result = RechargeService.get_task_by_no(task_no)
                        elif query == 'lookup':
                            result = RechargeService.lookup_task(code)
                        else:
                            result = RechargeService.lookup_batch_tasks([code])[0]
                    self.assertEqual(result['status'], 'processing')
                    db.session.expire_all()
                    self.assertEqual(db.session.get(RechargeMutation, task_no).state, 'unknown' if new_intent else 'rejected')
                    self.assertIsNotNone(db.session.get(RechargeOperation, task_no).active_key)

    def concurrent_submissions(self, task_no, operation_id, payloads):
        clients = [self.admin_client() for payload in payloads]
        barrier = Barrier(2)
        original = RechargeService._lock_task_snapshot

        def synchronized_lock(task, snapshot):
            barrier.wait(timeout=10)
            return original(task, snapshot)

        def submit(item):
            client, payload = item
            response = client.post(self.endpoint(task_no, operation_id), json=payload)
            return response.status_code, response.get_json()

        db.session.remove()
        with patch.object(RechargeService, '_lock_task_snapshot', side_effect=synchronized_lock):
            with ThreadPoolExecutor(max_workers=2) as workers:
                return list(workers.map(submit, zip(clients, payloads)))

    def test_concurrent_identical_requests_commit_one_audit_and_replay(self):
        task, operation_id = self.task()
        payload = self.payload(task)
        results = self.concurrent_submissions(task.task_no, operation_id, [payload, payload])
        self.assertEqual([status for status, body in results], [200, 200], results)
        self.assertEqual(sorted(body['data']['replayed'] for status, body in results), [False, True])
        self.assertEqual(RechargeMutationReconciliation.query.count(), 1)

    def test_concurrent_conflicting_requests_have_one_winner(self):
        task, operation_id = self.task()
        payloads = [self.payload(task, reference='TICKET-' + str(number)) for number in (1001, 1002)]
        results = self.concurrent_submissions(task.task_no, operation_id, payloads)
        self.assertEqual(sorted(status for status, body in results), [200, 409], results)
        self.assertEqual(RechargeMutationReconciliation.query.count(), 1)

    def test_manual_resolution_racing_automatic_receipt_has_one_winner(self):
        task, operation_id = self.task()
        task_no = task.task_no
        payload = self.payload(task)
        remote = {**payload['upstream_task'], 'status': 'recalled'}
        snapshot = RechargeService._task_snapshot(task)
        barrier = Barrier(2)
        original = RechargeService._lock_task_snapshot

        def synchronized_lock(task, snapshot):
            barrier.wait(timeout=10)
            return original(task, snapshot)

        def apply(kind):
            with self.app.app_context():
                try:
                    current = RechargeTask.query.filter_by(task_no=task_no).one()
                    if kind == 'manual':
                        RechargeService.reconcile_unknown_mutation(task_no, operation_id, payload, self.actor)
                    else:
                        RechargeService._reconcile_task(current, remote, snapshot=snapshot)
                    return 'ok'
                except RechargeReconciliationConflictError:
                    return 'conflict'
                finally:
                    db.session.remove()

        db.session.remove()
        with patch.object(RechargeService, '_lock_task_snapshot', side_effect=synchronized_lock):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(apply, ('manual', 'automatic')))
        self.assertIn(results, (['ok', 'ok'], ['conflict', 'ok']))
        current = db.session.get(RechargeMutation, task_no)
        task = RechargeTask.query.filter_by(task_no=task_no).one()
        self.assertIn((current.state, task.status, RechargeMutationReconciliation.query.count()),
                      (('rejected', 'processing', 1), ('done', 'recalled', 0)))

    def test_migrated_database_adds_audit_table_idempotently_and_preserves_data(self):
        initialize_database(db.engine)
        task, operation_id = self.task()
        pending, pending_id = self.task('TK-PENDING', state='pending')
        db.session.add(RechargeReconciliation(
            task_no=task.task_no, resolution='created', request_fingerprint='c' * 64,
            evidence_fingerprint='d' * 64, evidence_source='provider_ticket',
            evidence_reference='CREATION-TICKET', evidence_sha256='e' * 64,
            evidence_observed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            previous_status='unknown', final_status='processing', actor_id=self.actor,
        ))
        db.session.commit()
        task_no, pending_no = task.task_no, pending.task_no
        db.session.remove()
        with db.engine.begin() as connection:
            versions = connection.execute(text('SELECT version, applied_at FROM schema_migrations ORDER BY version')).all()
            self.assertEqual([row.version for row in versions], [version for version, migration in MIGRATIONS])
            connection.exec_driver_sql('DROP TABLE recharge_mutation_reconciliations')
        with self.assertRaisesRegex(RuntimeError, 'recharge_mutation_reconciliations'):
            validate_schema(db.engine)
        self.assertEqual(initialize_database(db.engine), [])
        self.assertEqual(initialize_database(db.engine), [])
        validate_schema(db.engine)
        with db.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT version, applied_at FROM schema_migrations ORDER BY version')).all(), versions)
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(db.session.get(RechargeMutation, task_no).operation_id, operation_id)
        self.assertEqual(db.session.get(RechargeMutation, task_no).state, 'unknown')
        self.assertEqual(db.session.get(RechargeMutation, pending_no).operation_id, pending_id)
        self.assertEqual(db.session.get(RechargeMutation, pending_no).state, 'pending')
        self.settle(RechargeTask.query.filter_by(task_no=task_no).one(), operation_id)
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(RechargeMutationReconciliation.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
