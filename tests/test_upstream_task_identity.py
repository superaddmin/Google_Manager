import unittest

from app import create_app, db
from app.models.recharge_operation import RechargeOperation
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class UpstreamTaskIdentityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        self.task = RechargeTask(
            task_no='LOCAL-IDENTITY', redeem_code='PLUS-IDENTITY',
            plan_type='PLUS', account_email='identity@example.test',
            status='unknown', is_mock=False,
        )
        db.session.add(self.task)
        db.session.add(RechargeOperation(task_no=self.task.task_no, active_key='identity-active'))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def remote(self, task_no):
        return {'task_no': task_no, 'client_task_no': self.task.task_no, 'status': 'processing'}

    def test_invalid_remote_identity_never_changes_local_state(self):
        for value in ('', ' ', '\t', '\r\n', '\u3000', 'REMOTE\nID', 'REMOTE\x7fID', 'x' * 129, 123, []):
            with self.subTest(value=repr(value)):
                with self.assertRaises(RechargeUpstreamError):
                    RechargeService._reconcile_task(self.task, self.remote(value))
                db.session.expire_all()
                self.assertEqual(self.task.status, 'unknown')
                operation = db.session.get(RechargeOperation, self.task.task_no)
                self.assertIsNone(operation.upstream_task_no)
                self.assertEqual(operation.active_key, 'identity-active')

    def test_identity_normalization_cannot_bypass_existing_binding(self):
        other_task = RechargeTask(
            task_no='OTHER-LOCAL', redeem_code='PLUS-OTHER-IDENTITY',
            plan_type='PLUS', account_email='other@example.test', status='processing', is_mock=False,
        )
        db.session.add(other_task)
        db.session.add(RechargeOperation(
            task_no=other_task.task_no, upstream_task_no='REMOTE-SHARED', active_key='other-active',
        ))
        db.session.commit()
        with self.assertRaises(RechargeUpstreamError):
            RechargeService._reconcile_task(self.task, self.remote(' REMOTE-SHARED '))
        self.assertEqual(self.task.status, 'unknown')
        self.assertIsNone(db.session.get(RechargeOperation, self.task.task_no).upstream_task_no)

    def test_valid_identity_is_normalized_like_manual_reconciliation(self):
        RechargeService._reconcile_task(self.task, self.remote(' REMOTE-VALID '))
        self.assertEqual(self.task.status, 'processing')
        self.assertEqual(db.session.get(RechargeOperation, self.task.task_no).upstream_task_no, 'REMOTE-VALID')


if __name__ == '__main__':
    unittest.main()
