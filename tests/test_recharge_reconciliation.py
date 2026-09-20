import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Event, local
from unittest.mock import patch

from sqlalchemy import event

from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_reconciliation import RechargeReconciliation
from app.models.recharge_task import RechargeTask
from app.services.auth_service import AuthService
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class RechargeReconciliationTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='recharge-reconciliation-')
        database_path = (Path(self.directory.name) / 'isolated.db').as_posix()
        self.config_patch = patch.object(
            TestingConfig,
            'SQLALCHEMY_DATABASE_URI',
            f'sqlite:///{database_path}',
        )
        self.config_patch.start()
        self.app = create_app('testing')
        self.app.config['RECHARGE_MODE'] = 'live'
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.network = patch(
            'socket.socket.connect',
            side_effect=AssertionError('External HTTP forbidden'),
        )
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
        login = client.post('/api/auth/login', json={
            'password': 'admin123',
            'salt': AuthService.generate_salt(int(time.time())),
        })
        self.assertEqual(login.status_code, 200, login.get_json())
        return client

    def create_unknown_task(self, task_no='TK-LIVE-RECONCILE-1'):
        task = RechargeTask(
            task_no=task_no,
            redeem_code=f'PLUS-{task_no}',
            plan_type='PLUS',
            account_email='fixture@example.test',
            status='unknown',
            status_text='上游响应未知（待核对）',
            card_last4='',
            is_mock=False,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(
            task_no=task_no,
            active_key=f'active-{task_no}',
        ))
        db.session.commit()
        return task

    @staticmethod
    def evidence(reference='PROVIDER-TICKET-1001', sha256=None):
        return {
            'source': 'provider_ticket',
            'reference': reference,
            'sha256': sha256 or ('a' * 64),
            'observed_at': datetime.now(timezone.utc).isoformat(),
        }

    def created_payload(self, task, status='completed', evidence=None, **overrides):
        remote = {
            'task_no': 'UPSTREAM-RECONCILE-1001',
            'client_task_no': task.task_no,
            'redeem_code': task.redeem_code,
            'account_email': task.account_email,
            'status': status,
            'card_last4': '1234',
        }
        remote.update(overrides)
        return {
            'confirmed': True,
            'resolution': 'created',
            'evidence': evidence or self.evidence(),
            'upstream_task': remote,
        }

    def not_created_payload(self):
        return {
            'confirmed': True,
            'resolution': 'not_created',
            'evidence': self.evidence(),
        }

    @staticmethod
    def endpoint(task):
        return f'/api/recharge/admin/tasks/{task.task_no}/reconcile'

    def test_confirmed_created_task_is_reconciled_and_audited(self):
        task = self.create_unknown_task()
        response = self.admin_client().post(
            self.endpoint(task),
            json=self.created_payload(task),
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()['data']
        self.assertFalse(body['replayed'])
        self.assertEqual(body['task']['status'], 'completed')
        self.assertNotIn('redeem_code', body['task'])
        db.session.expire_all()
        persisted = RechargeTask.query.filter_by(task_no=task.task_no).one()
        operation = db.session.get(RechargeOperation, task.task_no)
        audit = RechargeReconciliation.query.one()
        self.assertEqual(persisted.status, 'completed')
        self.assertEqual(persisted.card_last4, '1234')
        self.assertEqual(operation.upstream_task_no, 'UPSTREAM-RECONCILE-1001')
        self.assertIsNotNone(operation.active_key)
        self.assertEqual(audit.previous_status, 'unknown')
        self.assertEqual(audit.final_status, 'completed')
        self.assertRegex(audit.actor_id, r'^admin:[0-9a-f]{24}$')

    def test_confirmed_not_created_unlocks_without_deleting_history(self):
        task = self.create_unknown_task()
        response = self.admin_client().post(
            self.endpoint(task),
            json=self.not_created_payload(),
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        db.session.expire_all()
        persisted = RechargeTask.query.filter_by(task_no=task.task_no).one()
        operation = db.session.get(RechargeOperation, task.task_no)
        audit = RechargeReconciliation.query.one()
        self.assertEqual(persisted.status, 'failed')
        self.assertIsNone(operation.active_key)
        self.assertEqual(operation.task_no, task.task_no)
        self.assertEqual(audit.resolution, 'not_created')
        self.assertEqual(audit.final_status, 'failed')

    def test_unknown_or_unlinked_upstream_result_is_rejected(self):
        task = self.create_unknown_task()
        client = self.admin_client()
        invalid_payloads = (
            {**self.not_created_payload(), 'resolution': 'unknown'},
            self.created_payload(task, status='unknown'),
            self.created_payload(task, client_task_no='TK-OTHER-TASK'),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = client.post(self.endpoint(task), json=payload)
                self.assertEqual(response.status_code, 400, response.get_json())
        db.session.expire_all()
        self.assertEqual(RechargeTask.query.one().status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 0)

    def test_exact_replay_is_idempotent_and_conflicting_replay_is_blocked(self):
        task = self.create_unknown_task()
        client = self.admin_client()
        payload = self.created_payload(task, status='processing')

        first = client.post(self.endpoint(task), json=payload)
        replay = client.post(self.endpoint(task), json=payload)
        conflicting = self.not_created_payload()
        conflicting['evidence'] = self.evidence('PROVIDER-TICKET-OTHER')
        conflict = client.post(self.endpoint(task), json=conflicting)

        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['data']['replayed'])
        self.assertEqual(conflict.status_code, 409, conflict.get_json())
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(RechargeTask.query.one().status, 'processing')

    def test_concurrent_exact_requests_create_one_audit_record(self):
        task = self.create_unknown_task()
        task_no = task.task_no
        payload = self.created_payload(task, status='processing')
        clients = (self.admin_client(), self.admin_client())
        db.session.remove()

        def submit(client):
            response = client.post(
                f'/api/recharge/admin/tasks/{task_no}/reconcile',
                json=payload,
            )
            return response.status_code, response.get_json()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, clients))

        self.assertEqual([status for status, unused in results], [200, 200])
        self.assertEqual(
            sorted(body['data']['replayed'] for unused, body in results),
            [False, True],
        )
        db.session.expire_all()
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(RechargeTask.query.one().status, 'processing')

    def test_concurrent_replay_after_initial_audit_miss_returns_success(self):
        task = self.create_unknown_task()
        endpoint = self.endpoint(task)
        payload = self.created_payload(task, status='processing')
        delayed_client, winning_client = self.admin_client(), self.admin_client()
        audit_missed = Event()
        winner_committed = Event()
        request_state = local()
        original_lookup = RechargeService._existing_reconciliation_result
        db.session.remove()

        def delayed_lookup(task_no, request_fingerprint):
            result = original_lookup(task_no, request_fingerprint)
            if getattr(request_state, 'delay_once', False) and result is None:
                request_state.delay_once = False
                audit_missed.set()
                self.assertTrue(winner_committed.wait(timeout=5))
            return result

        def submit_delayed():
            request_state.delay_once = True
            response = delayed_client.post(endpoint, json=payload)
            return response.status_code, response.get_json()

        with patch.object(RechargeService, '_existing_reconciliation_result', side_effect=delayed_lookup):
            with ThreadPoolExecutor(max_workers=1) as workers:
                delayed = workers.submit(submit_delayed)
                try:
                    self.assertTrue(audit_missed.wait(timeout=5))
                    winning = winning_client.post(endpoint, json=payload)
                    self.assertEqual(winning.status_code, 200, winning.get_json())
                finally:
                    winner_committed.set()
                status, body = delayed.result(timeout=5)

        self.assertEqual(status, 200, body)
        self.assertTrue(body['data']['replayed'])
        db.session.expire_all()
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(RechargeTask.query.one().status, 'processing')

    def test_cross_task_upstream_number_reuse_is_rejected(self):
        first = self.create_unknown_task('TK-LIVE-UPSTREAM-ONE')
        second = self.create_unknown_task('TK-LIVE-UPSTREAM-TWO')
        client = self.admin_client()
        upstream_task_no = 'UPSTREAM-SHARED-SERIAL'

        accepted = client.post(self.endpoint(first), json=self.created_payload(
            first,
            status='completed',
            evidence=self.evidence('PROVIDER-TICKET-UPSTREAM-ONE', '1' * 64),
            task_no=upstream_task_no,
        ))
        rejected = client.post(self.endpoint(second), json=self.created_payload(
            second,
            status='completed',
            evidence=self.evidence('PROVIDER-TICKET-UPSTREAM-TWO', '2' * 64),
            task_no=upstream_task_no,
        ))

        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual(rejected.status_code, 409, rejected.get_json())
        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeTask, second.id).status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 1)

    def test_automatic_reconciliation_rejects_cross_task_upstream_number(self):
        first = self.create_unknown_task('TK-LIVE-AUTO-ONE')
        second = self.create_unknown_task('TK-LIVE-AUTO-TWO')
        upstream_task_no = 'UPSTREAM-SHARED-AUTO'

        RechargeService._reconcile_task(first, {
            'task_no': upstream_task_no,
            'client_task_no': first.task_no,
            'redeem_code': first.redeem_code,
            'status': 'processing',
        })
        with self.assertRaises(RechargeUpstreamError):
            RechargeService._reconcile_task(second, {
                'task_no': upstream_task_no,
                'client_task_no': second.task_no,
                'redeem_code': second.redeem_code,
                'status': 'processing',
            })

        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeTask, second.id).status, 'unknown')

    def test_cross_task_evidence_reuse_is_rejected(self):
        first = self.create_unknown_task('TK-LIVE-EVIDENCE-ONE')
        second = self.create_unknown_task('TK-LIVE-EVIDENCE-TWO')
        client = self.admin_client()
        evidence = self.evidence('PROVIDER-TICKET-SHARED', '3' * 64)

        accepted = client.post(self.endpoint(first), json=self.created_payload(
            first, evidence=evidence, task_no='UPSTREAM-EVIDENCE-ONE',
        ))
        rejected = client.post(self.endpoint(second), json=self.created_payload(
            second, evidence=evidence, task_no='UPSTREAM-EVIDENCE-TWO',
        ))

        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual(rejected.status_code, 409, rejected.get_json())
        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeTask, second.id).status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 1)

    def test_concurrent_cross_task_upstream_number_claim_has_one_winner(self):
        first = self.create_unknown_task('TK-LIVE-RACE-UPSTREAM-ONE')
        second = self.create_unknown_task('TK-LIVE-RACE-UPSTREAM-TWO')
        clients = (self.admin_client(), self.admin_client())
        upstream_task_no = 'UPSTREAM-SHARED-RACE'
        submissions = (
            (clients[0], first.task_no, self.created_payload(
                first,
                status='processing',
                evidence=self.evidence('PROVIDER-TICKET-RACE-ONE', '4' * 64),
                task_no=upstream_task_no,
            )),
            (clients[1], second.task_no, self.created_payload(
                second,
                status='processing',
                evidence=self.evidence('PROVIDER-TICKET-RACE-TWO', '5' * 64),
                task_no=upstream_task_no,
            )),
        )
        db.session.remove()

        def submit(item):
            client, task_no, payload = item
            response = client.post(
                f'/api/recharge/admin/tasks/{task_no}/reconcile',
                json=payload,
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(submit, submissions))

        self.assertEqual(sorted(statuses), [200, 409])
        db.session.expire_all()
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(
            sorted(task.status for task in RechargeTask.query.all()),
            ['processing', 'unknown'],
        )

    def test_concurrent_automatic_reconciliation_conflict_is_controlled(self):
        first = self.create_unknown_task('TK-LIVE-AUTO-RACE-ONE')
        second = self.create_unknown_task('TK-LIVE-AUTO-RACE-TWO')
        update_barrier = Barrier(2)
        submissions = (
            (first.task_no, {
                'task_no': 'UPSTREAM-AUTO-RACE',
                'client_task_no': first.task_no,
                'redeem_code': first.redeem_code,
                'status': 'processing',
            }),
            (second.task_no, {
                'task_no': 'UPSTREAM-AUTO-RACE',
                'client_task_no': second.task_no,
                'redeem_code': second.redeem_code,
                'status': 'processing',
            }),
        )
        db.session.remove()

        def synchronize_operation_binding(
            connection, cursor, statement, parameters, context, executemany,
        ):
            if (
                statement.lstrip().upper().startswith('UPDATE RECHARGE_OPERATIONS')
                and 'upstream_task_no' in statement
            ):
                update_barrier.wait(timeout=5)

        event.listen(db.engine, 'before_cursor_execute', synchronize_operation_binding)

        def reconcile(item):
            task_no, remote = item
            with self.app.app_context():
                try:
                    task = RechargeTask.query.filter_by(task_no=task_no).one()
                    RechargeService._reconcile_task(task, remote)
                    return 'ok'
                except RechargeUpstreamError:
                    return 'controlled_error'
                finally:
                    db.session.remove()

        try:
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(reconcile, submissions))
        finally:
            event.remove(db.engine, 'before_cursor_execute', synchronize_operation_binding)

        self.assertEqual(sorted(results), ['controlled_error', 'ok'])
        db.session.expire_all()
        self.assertEqual(
            sorted(task.status for task in RechargeTask.query.all()),
            ['processing', 'unknown'],
        )

    def test_concurrent_live_creation_conflict_marks_loser_unknown(self):
        update_barrier = Barrier(2)
        upstream_task_no = 'UPSTREAM-CREATE-RACE'
        payloads = tuple({
            'redeem_code': f'PLUS-CREATE-RACE-{suffix}',
            'token_input': f'token-{suffix.lower()}',
            'plan_type': 'PLUS',
            'account_email': f'{suffix.lower()}@example.test',
            'agreement_accepted': True,
            'email_verified': True,
            'challenge_token': f'challenge-{suffix.lower()}',
            'is_renewal': False,
            'acknowledge_non_free': True,
            'notify_channel': 'site',
            'notify_email': f'{suffix.lower()}@example.test',
        } for suffix in ('ONE', 'TWO'))

        def synchronize_operation_binding(
            connection, cursor, statement, parameters, context, executemany,
        ):
            if (
                statement.lstrip().upper().startswith('UPDATE RECHARGE_OPERATIONS')
                and 'upstream_task_no' in statement
            ):
                update_barrier.wait(timeout=5)

        def upstream_post(endpoint, payload, timeout=8):
            self.assertEqual(endpoint, '/user/tasks')
            return {
                'ok': True,
                'task': {
                    'task_no': upstream_task_no,
                    'client_task_no': payload['client_task_no'],
                    'redeem_code': payload['redeem_code'],
                    'account_email': payload['account_email'],
                    'status': 'processing',
                },
            }

        def create(payload):
            with self.app.app_context():
                try:
                    RechargeService.create_task(payload)
                    return 'ok'
                except RechargeUpstreamError:
                    return 'controlled_error'
                finally:
                    db.session.remove()

        db.session.remove()
        event.listen(db.engine, 'before_cursor_execute', synchronize_operation_binding)
        try:
            with patch.object(
                RechargeService,
                'validate_task_creation_contract',
                side_effect=lambda payload: dict(payload),
            ), patch.object(RechargeService, '_upstream_post', side_effect=upstream_post):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    results = list(workers.map(create, payloads))
        finally:
            event.remove(db.engine, 'before_cursor_execute', synchronize_operation_binding)

        self.assertEqual(sorted(results), ['controlled_error', 'ok'])
        db.session.expire_all()
        self.assertEqual(
            sorted(task.status for task in RechargeTask.query.all()),
            ['processing', 'unknown'],
        )
        self.assertEqual(
            RechargeOperation.query.filter_by(upstream_task_no=upstream_task_no).count(),
            1,
        )

    def test_concurrent_cross_task_evidence_claim_has_one_winner(self):
        first = self.create_unknown_task('TK-LIVE-RACE-EVIDENCE-ONE')
        second = self.create_unknown_task('TK-LIVE-RACE-EVIDENCE-TWO')
        clients = (self.admin_client(), self.admin_client())
        evidence = self.evidence('PROVIDER-TICKET-SHARED-RACE', '6' * 64)
        submissions = (
            (clients[0], first.task_no, self.created_payload(
                first, status='processing', evidence=evidence,
                task_no='UPSTREAM-RACE-EVIDENCE-ONE',
            )),
            (clients[1], second.task_no, self.created_payload(
                second, status='processing', evidence=evidence,
                task_no='UPSTREAM-RACE-EVIDENCE-TWO',
            )),
        )
        db.session.remove()

        def submit(item):
            client, task_no, payload = item
            response = client.post(
                f'/api/recharge/admin/tasks/{task_no}/reconcile',
                json=payload,
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(submit, submissions))

        self.assertEqual(sorted(statuses), [200, 409])
        db.session.expire_all()
        self.assertEqual(RechargeReconciliation.query.count(), 1)
        self.assertEqual(
            sorted(task.status for task in RechargeTask.query.all()),
            ['processing', 'unknown'],
        )

    def test_anonymous_client_cannot_submit_reconciliation(self):
        task = self.create_unknown_task()
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        response = client.post(
            self.endpoint(task),
            json=self.not_created_payload(),
        )

        self.assertEqual(response.status_code, 401, response.get_json())
        self.assertEqual(RechargeTask.query.one().status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 0)

    def test_legacy_authenticated_cookie_cannot_submit_reconciliation(self):
        task = self.create_unknown_task()
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        with client.session_transaction() as client_session:
            client_session['authenticated'] = True

        response = client.post(
            self.endpoint(task),
            json=self.not_created_payload(),
        )

        self.assertEqual(response.status_code, 401, response.get_json())
        self.assertEqual(RechargeTask.query.one().status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 0)

    def test_pending_mutation_prevents_manual_unlock(self):
        task = self.create_unknown_task()
        db.session.add(RechargeMutation(
            task_no=task.task_no,
            action='close',
            operation_id='pending-operation',
            state='pending',
            started_at=time.time(),
        ))
        db.session.commit()

        response = self.admin_client().post(
            self.endpoint(task),
            json=self.not_created_payload(),
        )

        self.assertEqual(response.status_code, 409, response.get_json())
        db.session.expire_all()
        self.assertEqual(RechargeTask.query.one().status, 'unknown')
        self.assertIsNotNone(RechargeOperation.query.one().active_key)
        self.assertEqual(RechargeReconciliation.query.count(), 0)

    def test_invalid_evidence_does_not_change_task(self):
        task = self.create_unknown_task()
        client = self.admin_client()
        payloads = []
        for field in ('source', 'reference', 'sha256', 'observed_at'):
            payload = self.not_created_payload()
            payload['evidence'].pop(field)
            payloads.append(payload)
        payloads.append({**self.not_created_payload(), 'confirmed': False})

        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post(self.endpoint(task), json=payload)
                self.assertEqual(response.status_code, 400, response.get_json())
        db.session.expire_all()
        self.assertEqual(RechargeTask.query.one().status, 'unknown')
        self.assertEqual(RechargeReconciliation.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
