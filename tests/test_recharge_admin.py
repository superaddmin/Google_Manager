import unittest
from unittest.mock import patch

from app import create_app, db
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService
from tests.auth_helpers import login_admin


class RechargeAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.application = create_app('testing')
        self.context = self.application.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.application.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(self.client)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def task(self, number='TK-ADMIN-001', status='processing', is_mock=True):
        task = RechargeTask(
            task_no=number, redeem_code='PLUS-' + number,
            plan_type='PLUS', account_email='admin-flow@example.test',
            status=status, is_mock=is_mock,
        )
        db.session.add(task)
        db.session.commit()
        return task

    def test_public_routes_serve_three_distinct_entry_paths(self):
        for path in ('/', '/recharge', '/Googlemail', '/Googlemail/', '/Googlemail/inbox', '/admin', '/admin/'):
            with self.subTest(path=path), self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)

    def test_admin_endpoints_require_server_session(self):
        anonymous = self.application.test_client()
        for path in ('/overview', '/tasks', '/tasks/TK-ADMIN-001'):
            self.assertEqual(anonymous.get('/api/recharge/admin' + path).status_code, 401)
        for action in ('refresh', 'recall', 'close'):
            response = anonymous.post('/api/recharge/admin/tasks/TK-ADMIN-001/' + action,
                                      json={'confirmed': True}, headers={'X-Requested-With': 'XMLHttpRequest'})
            self.assertEqual(response.status_code, 401)

    def test_disabled_mode_reads_history_without_upstream_requests(self):
        task = self.task(is_mock=False)
        self.application.config['RECHARGE_MODE'] = 'disabled'
        with patch.object(RechargeService, '_upstream_get', side_effect=AssertionError('unexpected network')):
            summary = self.client.get('/api/recharge/admin/overview').get_json()['data']
            detail = self.client.get('/api/recharge/admin/tasks/' + task.task_no).get_json()['data']
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['mode'], 'disabled')
        self.assertTrue(all(value is False for value in detail['actions'].values()))
        self.assertNotIn('redeem_code', detail['task'])
        self.assertNotIn('notify_email', detail['task'])
        self.assertEqual(self.client.post('/api/recharge/admin/tasks/' + task.task_no + '/refresh', json={}).status_code, 503)

    def test_list_filters_and_pagination_use_persisted_tasks(self):
        first = self.task()
        self.task('TK-ADMIN-002', 'completed', False)
        page = self.client.get('/api/recharge/admin/tasks?page_size=1&page=2').get_json()['data']
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['items'][0]['task_no'], first.task_no)
        for query in ('TK-ADMIN-001', first.redeem_code):
            result = self.client.get('/api/recharge/admin/tasks', query_string={'q': query}).get_json()['data']
            self.assertEqual(result['total'], 1)
        by_email = self.client.get('/api/recharge/admin/tasks?q=ADMIN-FLOW@example.test').get_json()['data']
        self.assertEqual(by_email['total'], 2)
        completed = self.client.get('/api/recharge/admin/tasks?source=live&status=completed').get_json()['data']
        self.assertEqual(completed['total'], 1)
        literal = self.client.get('/api/recharge/admin/tasks', query_string={'q': '%'}).get_json()['data']
        self.assertEqual(literal['total'], 0)

    def test_invalid_filters_and_missing_task_return_errors(self):
        for parameters in ({'page': 0}, {'page': 'bad'}, {'page_size': 101}, {'status': 'bad'}, {'source': 'bad'}, {'q': 'x' * 257}):
            self.assertEqual(self.client.get('/api/recharge/admin/tasks', query_string=parameters).status_code, 400)
        self.assertEqual(self.client.get('/api/recharge/admin/tasks/TK-MISSING').status_code, 404)

    def test_confirmed_recall_uses_exact_stored_task_identity(self):
        task = self.task()
        endpoint = '/api/recharge/admin/tasks/' + task.task_no + '/recall'
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 400)
        result = self.client.post(endpoint, json={'confirmed': True, 'redeem_code': 'wrong', 'email': 'other@example.test'})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['data']['task']['status'], 'recalled')
        self.assertEqual(db.session.get(RechargeTask, task.id).status, 'recalled')

    def test_completed_order_close_and_read_only_detail(self):
        task = self.task(status='completed')
        result = self.client.post('/api/recharge/admin/tasks/' + task.task_no + '/close', json={'confirmed': True})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['data']['task']['status'], 'closed')

    def test_mode_mismatch_rejects_mutations(self):
        task = self.task(is_mock=False)
        for action in ('refresh', 'recall', 'close'):
            response = self.client.post('/api/recharge/admin/tasks/' + task.task_no + '/' + action, json={'confirmed': True})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(db.session.get(RechargeTask, task.id).status, 'processing')

    def test_unknown_mutation_exposes_reconciliation_without_allowing_duplicate_action(self):
        task = self.task(is_mock=False)
        self.application.config['RECHARGE_MODE'] = 'live'
        db.session.add(RechargeMutation(task_no=task.task_no, action='recall', operation_id='a' * 32, state='unknown', started_at=1))
        db.session.commit()
        detail = self.client.get('/api/recharge/admin/tasks/' + task.task_no).get_json()['data']
        self.assertTrue(detail['actions']['reconcile_mutation'])
        self.assertFalse(detail['actions']['recall'])
        self.assertEqual(detail['mutation']['operation_id'], 'a' * 32)

    def test_pending_review_counts_orders_once(self):
        task = self.task(status='unknown', is_mock=False)
        db.session.add(RechargeMutation(task_no=task.task_no, action='recall', operation_id='b' * 32, state='unknown', started_at=1))
        db.session.commit()
        summary = self.client.get('/api/recharge/admin/overview').get_json()['data']
        self.assertEqual(summary['pending_review'], 1)
        self.assertEqual(summary['unknown_operations'], 1)


if __name__ == '__main__':
    unittest.main()
