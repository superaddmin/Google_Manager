import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class RechargeReleaseServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='recharge-release-service-')
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()
        self.config_patch.stop()
        self.directory.cleanup()

    def test_live_mode_does_not_report_mock_processing_statistics(self):
        self.assertEqual(RechargeService.get_avg_processing_time(), [])

    def test_mutation_rejects_conflicting_upstream_identity_without_releasing_task(self):
        for action in ('recall', 'close'):
            for conflict in ({'task_no': 'REMOTE-OTHER'}, {'account_email': 'other@example.test'}):
                with self.subTest(action=action, conflict=conflict):
                    identifier = f'TK-{action}-{next(iter(conflict))}'
                    task = RechargeTask(
                        task_no=identifier, redeem_code='PLUS-' + identifier, plan_type='PLUS',
                        account_email='fixture@example.test', status='processing', is_mock=False,
                    )
                    db.session.add(task)
                    db.session.flush()
                    db.session.add(RechargeOperation(
                        task_no=identifier, active_key=identifier, upstream_task_no='REMOTE-' + identifier,
                    ))
                    db.session.commit()
                    remote = {
                        'task_no': 'REMOTE-' + identifier, 'client_task_no': identifier,
                        'redeem_code': task.redeem_code, 'account_email': task.account_email,
                        'status': 'recalled' if action == 'recall' else 'closed', **conflict,
                    }
                    with patch.object(RechargeService, '_upstream_post', return_value={
                        'ok': True, 'task': remote,
                    }), self.assertRaises(RechargeUpstreamError):
                        getattr(RechargeService, action + '_task')({
                            'task_no': identifier, 'redeem_code': task.redeem_code,
                            'email': task.account_email, 'confirmed': True,
                        })
                    db.session.expire_all()
                    self.assertEqual(task.status, 'processing')
                    self.assertEqual(db.session.get(RechargeMutation, identifier).state, 'unknown')
                    self.assertEqual(db.session.get(RechargeOperation, identifier).active_key, identifier)

    def test_bound_task_rejects_status_response_without_upstream_task_number(self):
        task = RechargeTask(
            task_no='TK-LIVE-BOUND-STATUS',
            redeem_code='PLUS-BOUND-STATUS',
            plan_type='PLUS',
            account_email='bound@example.test',
            status='processing',
            is_mock=False,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(
            task_no=task.task_no,
            active_key='bound-status-active-key',
            upstream_task_no='REMOTE-BOUND-STATUS',
        ))
        db.session.commit()

        with patch.object(RechargeService, '_upstream_get', return_value={
            'ok': True,
            'task': {'status': 'completed'},
        }), self.assertRaisesRegex(RechargeUpstreamError, '任务编号'):
            RechargeService.get_task_by_no(task.task_no)

        db.session.expire_all()
        self.assertEqual(
            RechargeTask.query.filter_by(task_no=task.task_no).one().status,
            'processing',
        )

    def test_bound_task_accepts_exact_client_task_identity_without_remote_number(self):
        task = RechargeTask(
            task_no='TK-LIVE-BOUND-CLIENT',
            redeem_code='PLUS-BOUND-CLIENT',
            plan_type='PLUS',
            account_email='bound-client@example.test',
            status='processing',
            is_mock=False,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(
            task_no=task.task_no,
            active_key='bound-client-active-key',
            upstream_task_no='REMOTE-BOUND-CLIENT',
        ))
        db.session.commit()

        with patch.object(RechargeService, '_upstream_get', return_value={
            'ok': True,
            'task': {
                'client_task_no': task.task_no,
                'status': 'completed',
            },
        }):
            result = RechargeService.get_task_by_no(task.task_no)

        self.assertEqual(result['status'], 'completed')

    def test_late_timeout_cannot_regress_reconciled_mutation_to_unknown(self):
        task = RechargeTask(
            task_no='TK-LIVE-LATE-TIMEOUT',
            redeem_code='PLUS-LATE-TIMEOUT',
            plan_type='PLUS',
            account_email='late-timeout@example.test',
            status='processing',
            is_mock=False,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(RechargeOperation(
            task_no=task.task_no,
            active_key='late-timeout-active-key',
        ))
        db.session.commit()

        def reconcile_then_timeout(endpoint, payload):
            mutation = db.session.get(RechargeMutation, task.task_no)
            RechargeMutation.finish(task.task_no, mutation.operation_id, 'unknown')
            db.session.commit()
            RechargeService._reconcile_task(task, {
                'client_task_no': task.task_no,
                'redeem_code': task.redeem_code,
                'status': 'recalled',
            })
            raise RechargeUpstreamError('synthetic late timeout')

        with patch.object(
            RechargeService,
            '_upstream_post',
            side_effect=reconcile_then_timeout,
        ), self.assertRaises(RechargeUpstreamError):
            RechargeService.recall_task({
                'task_no': task.task_no,
                'redeem_code': task.redeem_code,
                'email': task.account_email,
                'confirmed': True,
            })

        db.session.expire_all()
        self.assertEqual(db.session.get(RechargeTask, task.id).status, 'recalled')
        self.assertEqual(
            db.session.get(RechargeMutation, task.task_no).state,
            'done',
        )

    def test_live_upstream_acknowledgement_requires_boolean_true(self):
        cases = (
            (
                'validation',
                lambda: RechargeService.validate_redeem_code('PLUS-STRICT-ACK'),
                {
                    'ok': 1,
                    'result': {'plan_type': 'PLUS', 'status': 'unused'},
                },
            ),
            (
                'single lookup',
                lambda: RechargeService.lookup_task('PLUS-STRICT-LOOKUP'),
                {
                    'ok': 'true',
                    'task': {
                        'redeem_code': 'PLUS-STRICT-LOOKUP',
                        'plan_type': 'PLUS',
                        'status': 'processing',
                    },
                },
            ),
            (
                'batch lookup',
                lambda: RechargeService.lookup_batch_tasks(['PLUS-STRICT-BATCH']),
                {
                    'ok': 1,
                    'results': [{
                        'redeem_code': 'PLUS-STRICT-BATCH',
                        'plan_type': 'PLUS',
                        'status': 'processing',
                    }],
                },
            ),
        )

        for name, operation, response in cases:
            with self.subTest(name=name), patch.object(
                RechargeService,
                '_upstream_post',
                return_value=response,
            ), self.assertRaises(RechargeUpstreamError):
                operation()

    def test_upstream_body_read_has_absolute_deadline(self):
        class SlowBodyHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.06)
                except ConnectionError:
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowBodyHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.app.config['RECHARGE_UPSTREAM_URL'] = (
            f'http://127.0.0.1:{server.server_port}'
        )
        try:
            with self.assertRaises(RechargeUpstreamError):
                RechargeService._upstream_get('/slow-body', timeout=0.1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_upstream_header_read_shares_the_absolute_deadline(self):
        class SlowHeaderHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                response = (
                    b'HTTP/1.1 200 OK\r\n'
                    b'Content-Type: application/json\r\n'
                    b'Content-Length: 11\r\n'
                    b'Connection: close\r\n'
                    b'\r\n'
                    b'{"ok":true}'
                )
                try:
                    for byte in response:
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.03)
                except ConnectionError:
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHeaderHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.app.config['RECHARGE_UPSTREAM_URL'] = (
            f'http://127.0.0.1:{server.server_port}'
        )
        started_at = time.monotonic()
        try:
            with self.assertRaises(RechargeUpstreamError):
                RechargeService._upstream_get('/slow-header', timeout=0.1)
            self.assertLess(time.monotonic() - started_at, 0.5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
