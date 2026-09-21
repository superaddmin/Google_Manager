import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app, db
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService, RechargeUpstreamError
from app.services.runtime_queue import RuntimeQueue
from app.worker import maintenance


class RechargeMaintenanceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['RECHARGE_MODE'] = 'live'
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def add_tasks(self, count, **overrides):
        first_id = RechargeTask.query.count()
        numbers = []
        for offset in range(count):
            number = f'TK-MAINTENANCE-{first_id + offset:03d}'
            values = dict(task_no=number, redeem_code=f'CDK-{number}',
                          account_email='fixture@example.test', plan_type='PLUS',
                          status='processing', is_mock=False,
                          updated_at=datetime(2026, 1, 1) + timedelta(seconds=offset))
            db.session.add(RechargeTask(**{**values, **overrides}))
            numbers.append(number)
        db.session.commit()
        return numbers

    def test_failed_old_tasks_do_not_starve_later_tasks(self):
        numbers = self.add_tasks(21)
        with patch.object(RechargeService, 'get_task_by_no',
                          side_effect=RechargeUpstreamError('Synthetic failure')) as query:
            maintenance(self.app)
            self.assertEqual(query.call_count, 20)
            maintenance(self.app)
        self.assertIn(numbers[-1], [call.args[0] for call in query.call_args_list])
        self.assertEqual(RuntimeQueue.state('maintenance')['consecutiveFailures'], 2)
        self.assertEqual(RechargeTask.query.filter_by(status='processing').count(), 21)

    def test_unchanged_tasks_rotate_and_wrap_without_duplicate_calls(self):
        numbers = self.add_tasks(21)
        with patch.object(RechargeService, 'get_task_by_no', return_value=None) as query:
            maintenance(self.app)
            first = [call.args[0] for call in query.call_args_list]
            query.reset_mock()
            maintenance(self.app)
            second = [call.args[0] for call in query.call_args_list]
        self.assertEqual(first, numbers[:20])
        self.assertEqual(second, [numbers[-1], *numbers[:19]])
        self.assertEqual(len(second), len(set(second)))

    def test_only_live_unsettled_tasks_are_reconciled(self):
        self.add_tasks(1, is_mock=True)
        self.add_tasks(1, status='completed')
        [unsettled] = self.add_tasks(1, status='completed')
        db.session.add(RechargeMutation(task_no=unsettled, action='close',
                                       operation_id='synthetic-operation', state='unknown',
                                       started_at=0))
        db.session.commit()
        with patch.object(RechargeService, 'get_task_by_no', return_value=None) as query:
            maintenance(self.app)
        query.assert_called_once_with(unsettled)

    def test_empty_and_disabled_queues_do_not_call_upstream(self):
        with patch.object(RechargeService, 'get_task_by_no') as query:
            maintenance(self.app)
            self.add_tasks(1)
            self.app.config['RECHARGE_MODE'] = 'disabled'
            maintenance(self.app)
        query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
