import json
import socket
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError
from httplib2 import Response

from app import create_app, db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.gmail_connection import GmailConnection
from app.models.gmail_execution import GmailExecution
from app.models.gmail_rule import GmailRule
from app.models.gmail_task_log import GmailActionConfirmation, GmailTaskLog
from app.models.runtime_job import RuntimeJob
from app.services.account_service import AccountService
from app.services.gmail_rule_service import GmailRuleService, GmailRuleServiceError
from app.services.gmail_service import GmailService
from app.services.runtime_queue import RuntimeQueue
from app.services.security_service import SecurityService
from app.worker import maintenance, run_job


class RuntimeRecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        self.context = self.app.app_context()
        self.context.push()
        self.connection = GmailConnection(
            email='runtime-recovery@example.test',
            token_data='synthetic',
            scopes='synthetic',
        )
        db.session.add(self.connection)
        db.session.flush()
        self.rule = GmailRule(
            connection_id=self.connection.id,
            name='runtime-recovery-rule',
            actions=json.dumps({'markRead': True}),
            requires_confirmation=True,
        )
        db.session.add(self.rule)
        db.session.commit()
        self.network = patch.object(
            socket.socket,
            'connect',
            side_effect=AssertionError('External network forbidden'),
        )
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def create_account(self, *, status='inactive', secret='OLDSECRET'):
        account = Account(
            email=f'account-{time.time_ns()}@example.test',
            password='synthetic-password',
            recovery='recovery@example.test',
            secret=secret,
            status=status,
        )
        db.session.add(account)
        db.session.commit()
        return account

    def acquire_confirmation(self, message_id='message-confirmation'):
        return GmailExecution.acquire(
            self.connection,
            self.rule,
            message_id,
            ([], ['UNREAD']),
            'pending_confirmation',
        )

    def test_pending_confirmation_is_created_before_acquire_commits(self):
        original_commit = db.session.commit
        confirmation_counts = []

        def commit_with_confirmation_check():
            confirmation_counts.append(GmailActionConfirmation.query.count())
            self.assertEqual(GmailTaskLog.query.filter_by(status='pending_confirmation').count(), 1)
            original_commit()

        with patch.object(db.session, 'commit', side_effect=commit_with_confirmation_check):
            log = self.acquire_confirmation()

        self.assertEqual(confirmation_counts, [1])
        log_id = log.id
        db.session.remove()
        persisted = db.session.get(GmailTaskLog, log_id)
        self.assertIsNotNone(persisted.confirmation)
        self.assertEqual(persisted.confirmation.status, 'pending')
        self.assertEqual(GmailExecution.query.filter_by(log_id=log_id).count(), 1)

    def test_expired_confirming_execution_returns_to_pending_confirmation(self):
        log = self.acquire_confirmation('message-expired-confirmation')
        execution = GmailExecution.query.filter_by(log_id=log.id).one()
        log.status = 'confirming'
        log.confirmation.status = 'approved'
        log.confirmation.reviewer = 'synthetic-reviewer'
        log.confirmation.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.lease_until = time.time() - 1
        log_id = log.id
        db.session.commit()

        GmailExecution.recover_expired_confirmations()
        db.session.remove()

        recovered = db.session.get(GmailTaskLog, log_id)
        self.assertEqual(recovered.status, 'pending_confirmation')
        self.assertEqual(recovered.confirmation.status, 'pending')
        self.assertIsNone(recovered.confirmation.reviewer)
        self.assertIsNone(recovered.confirmation.reviewed_at)

    def test_lock_account_persists_cancellation_for_pending_and_running_jobs(self):
        account = self.create_account()
        pending = RuntimeQueue.enqueue(
            'googlemail',
            {'accountIds': [account.id], 'options': {}},
            active_key='pending-account-automation',
        )
        running = RuntimeQueue.enqueue(
            'oauth',
            {'accountIds': [account.id], 'options': {}},
            active_key='running-account-automation',
        )
        running.status = 'running'
        db.session.commit()
        pending_id = pending.id
        running_id = running.id

        with patch('app.services.googlemail_service.googlemail_tasks.cancel_for_account') as cancel_googlemail, patch(
            'app.services.batch_oauth_service.batch_oauth_manager.cancel_for_account'
        ) as cancel_oauth:
            SecurityService.lock_account(account.id, reason='synthetic recovery test')

        cancel_googlemail.assert_called_once_with(account.id)
        cancel_oauth.assert_called_once_with(account.id)
        db.session.remove()

        persisted_pending = db.session.get(RuntimeJob, pending_id)
        persisted_running = db.session.get(RuntimeJob, running_id)
        persisted_account = db.session.get(Account, account.id)
        self.assertEqual(persisted_account.status, 'locked')
        self.assertEqual(persisted_pending.status, 'cancelled')
        self.assertTrue(persisted_pending.cancel_requested)
        self.assertIsNone(persisted_pending.active_key)
        self.assertEqual(persisted_running.status, 'running')
        self.assertTrue(persisted_running.cancel_requested)
        self.assertEqual(persisted_running.active_key, 'running-account-automation')

    def test_delete_account_cancels_associated_persistent_automation_first(self):
        account = self.create_account()
        job = RuntimeQueue.enqueue(
            'googlemail',
            {'accountIds': [account.id], 'options': {}},
            active_key='delete-account-automation',
        )
        job_id = job.id
        with patch('app.services.googlemail_service.googlemail_tasks.cancel_for_account') as cancel_googlemail, patch(
            'app.services.batch_oauth_service.batch_oauth_manager.cancel_for_account'
        ) as cancel_oauth:
            self.assertTrue(AccountService.delete_account(account.id))
        cancel_googlemail.assert_called_once_with(account.id)
        cancel_oauth.assert_called_once_with(account.id)
        self.assertIsNone(db.session.get(Account, account.id))
        persisted_job = db.session.get(RuntimeJob, job_id)
        self.assertEqual(persisted_job.status, 'cancelled')
        self.assertTrue(persisted_job.cancel_requested)

    def test_automation_result_updates_secret_without_unlocking_account(self):
        account = self.create_account(status='locked')
        account_id = account.id

        result = AccountService.update_account(
            account_id,
            {'secret': 'NEWSECRET'},
            automation_result=True,
        )

        self.assertEqual(result['status'], 'locked')
        self.assertEqual(result['secret'], '')
        db.session.remove()
        persisted = db.session.get(Account, account_id)
        self.assertEqual(persisted.secret, 'NEWSECRET')
        self.assertEqual(persisted.status, 'locked')
        self.assertEqual(
            AccountHistory.query.filter_by(account_id=account_id, field_name='secret').count(),
            1,
        )

    def test_pre_cancelled_jobs_never_start_automation_managers(self):
        account = self.create_account()
        account_id = account.id
        cases = (
            ('googlemail', 'app.worker.googlemail_tasks.start_task'),
            ('oauth', 'app.worker.batch_oauth_manager.start_batch'),
        )

        for kind, manager_path in cases:
            with self.subTest(kind=kind):
                job = RuntimeQueue.enqueue(
                    kind,
                    {'accountIds': [account_id], 'options': {}},
                    active_key=f'pre-cancel-{kind}',
                )
                job.status = 'running'
                job.cancel_requested = True
                db.session.commit()
                job_id = job.id

                with patch(manager_path) as start_manager:
                    run_job(self.app, job_id, threading.Event())

                start_manager.assert_not_called()
                db.session.remove()
                persisted = db.session.get(RuntimeJob, job_id)
                self.assertEqual(persisted.status, 'cancelled')
                self.assertIsNone(persisted.active_key)

    def test_poll_failure_releases_gate_after_manager_reaches_terminal_state(self):
        account = self.create_account()
        job = RuntimeQueue.enqueue(
            'googlemail',
            {'accountIds': [account.id], 'options': {}},
            active_key='recoverable-manager-failure',
        )
        job.status = 'running'
        db.session.commit()
        job_id = job.id
        manager = MagicMock()
        manager.start_task.return_value = {'taskId': 'synthetic-internal-task'}
        manager.get_task.side_effect = [
            RuntimeError('synthetic persistence failure'),
            {'status': 'cancelled'},
        ]

        with patch('app.worker.googlemail_tasks', manager), patch('app.worker.time.sleep') as sleep:
            run_job(self.app, job_id, threading.Event())

        manager.start_task.assert_called_once()
        manager.cancel_task.assert_called_once_with('synthetic-internal-task')
        sleep.assert_not_called()
        db.session.remove()
        persisted = db.session.get(RuntimeJob, job_id)
        self.assertEqual(persisted.status, 'failed')
        self.assertIsNone(persisted.active_key)
        self.assertFalse(persisted.cancel_requested)
        self.assertIn('RuntimeError', persisted.snapshot['errorMessage'])

    def test_cleanup_failure_retains_gate_and_fails_automation_readiness(self):
        self.app.config.update(BACKGROUND_TASK_MODE='inline', GMAIL_CLIENT_SECRET_FILE=None)
        account = self.create_account()
        job = RuntimeQueue.enqueue(
            'googlemail',
            {'accountIds': [account.id], 'options': {}},
            active_key='uncertain-manager-failure',
        )
        job.status = 'running'
        db.session.commit()
        job_id = job.id
        manager = MagicMock()
        manager.start_task.return_value = {'taskId': 'synthetic-uncertain-task'}
        manager.get_task.side_effect = [
            RuntimeError('synthetic poll failure'),
            RuntimeError('synthetic cleanup failure'),
        ]

        with patch('app.worker.googlemail_tasks', manager), patch(
            'app.worker.time.monotonic', side_effect=[100.0, 101.0]
        ), patch('app.worker.time.sleep') as sleep:
            run_job(self.app, job_id, threading.Event())

        manager.start_task.assert_called_once()
        manager.cancel_task.assert_called_once_with('synthetic-uncertain-task')
        sleep.assert_not_called()
        db.session.remove()
        persisted = db.session.get(RuntimeJob, job_id)
        self.assertEqual(persisted.status, 'failed')
        self.assertEqual(persisted.active_key, 'uncertain-manager-failure')
        self.assertTrue(persisted.cancel_requested)
        self.assertIn('保留任务锁', persisted.snapshot['errorMessage'])

        response = self.app.test_client().get('/health/ready')
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertFalse(response.get_json()['automation'])

    def test_expired_action_retries_original_log_after_external_read(self):
        self.rule.requires_confirmation = False
        db.session.commit()
        external_state = {'labelIds': ['INBOX', 'UNREAD']}

        def timeout_after_external_read(connection, message_id, *, add, remove):
            external_state['labelIds'].remove('UNREAD')
            raise TimeoutError('synthetic timeout after external read')

        modify_message = MagicMock(side_effect=timeout_after_external_read)
        with patch('app.services.gmail_rule_service.GmailService.list_messages') as list_messages, patch(
            'app.services.gmail_rule_service.GmailService.modify_message', new=modify_message
        ):
            summary = GmailRuleService.run_rules(self.connection, message_ids=['already-read-message'])
            self.assertEqual(summary['failed'], 1)
            self.assertNotIn('UNREAD', external_state['labelIds'])

            log = GmailTaskLog.query.one()
            execution = GmailExecution.query.filter_by(log_id=log.id).one()
            execution.lease_until = time.time() - 1
            db.session.commit()

            modify_message.reset_mock()
            modify_message.side_effect = None
            modify_message.return_value = {'id': 'already-read-message', 'labelIds': ['INBOX']}
            retry_summary = GmailRuleService.retry_due_actions()

        self.assertEqual(retry_summary, {'succeeded': 1, 'failed': 0, 'pendingConfirmation': 0})
        list_messages.assert_not_called()
        modify_message.assert_called_once_with(
            self.connection,
            'already-read-message',
            add=[],
            remove=['UNREAD'],
        )
        self.assertEqual(GmailTaskLog.query.one().status, 'succeeded')

    def test_deleted_message_stops_background_action_retries(self):
        self.rule.requires_confirmation = False
        db.session.commit()
        with patch.object(GmailService, 'modify_message', side_effect=RuntimeError('initial timeout')):
            GmailRuleService.run_rules(self.connection, message_ids=['deleted-after-timeout'])
        execution = GmailExecution.query.one()
        execution.lease_until = time.time() - 1
        db.session.commit()
        missing = HttpError(Response({'status': '404'}), b'{"error":{"message":"Not found"}}')
        with patch.object(GmailService, 'modify_message', side_effect=missing) as modify, patch.object(
            GmailService, 'get_message', side_effect=missing,
        ):
            self.assertEqual(GmailRuleService.retry_due_actions()['failed'], 0)
            self.assertEqual(GmailRuleService.retry_due_actions()['failed'], 0)
        self.assertEqual(modify.call_count, 1)
        db.session.expire_all()
        self.assertEqual(GmailTaskLog.query.one().status, 'skipped')

    def test_deleted_message_confirmation_keeps_review_and_skips_action(self):
        log = self.acquire_confirmation('deleted-before-confirmation')
        missing = HttpError(Response({'status': '404'}), b'{"error":{"message":"Not found"}}')
        with patch.object(GmailService, 'modify_message', side_effect=missing), patch.object(
            GmailService, 'get_message', side_effect=missing,
        ):
            confirmed = GmailRuleService.confirm_log(log, 'synthetic-reviewer')
        db.session.expire_all()
        self.assertEqual(confirmed.status, 'skipped')
        self.assertEqual(confirmed.confirmation.status, 'approved')
        self.assertEqual(confirmed.confirmation.reviewer, 'synthetic-reviewer')

    def test_stale_retry_result_cannot_overwrite_newer_execution_lease(self):
        self.rule.requires_confirmation = False
        db.session.commit()

        def stale_call(connection, message_id, *, add, remove):
            execution = GmailExecution.query.one()
            execution.lease_token = 'newer-execution-token'
            db.session.commit()
            return {'id': message_id}

        with patch('app.services.gmail_rule_service.GmailService.modify_message', side_effect=RuntimeError('initial failure')):
            summary = GmailRuleService.run_rules(self.connection, message_ids=['fenced-message'])
        self.assertEqual(summary['failed'], 1)
        execution = GmailExecution.query.one()
        execution.lease_until = time.time() - 1
        db.session.commit()

        with patch('app.services.gmail_rule_service.GmailService.modify_message', side_effect=stale_call):
            retry_summary = GmailRuleService.retry_due_actions()

        self.assertEqual(retry_summary, {'succeeded': 0, 'failed': 0, 'pendingConfirmation': 0})
        self.assertEqual(GmailTaskLog.query.one().status, 'running')

    def test_failed_human_confirmation_retries_as_pending_without_google_call(self):
        log = self.acquire_confirmation('message-failed-confirmation')
        log_id = log.id
        modify_message = MagicMock(side_effect=RuntimeError('synthetic confirmation failure'))
        with patch('app.services.gmail_rule_service.GmailService.modify_message', new=modify_message):
            with self.assertRaises(GmailRuleServiceError):
                GmailRuleService.confirm_log(log, 'synthetic-reviewer')

        modify_message.reset_mock()
        modify_message.side_effect = None

        retry_summary = GmailRuleService.retry_due_actions()

        self.assertEqual(retry_summary, {'succeeded': 0, 'failed': 0, 'pendingConfirmation': 0})
        modify_message.assert_not_called()
        db.session.remove()
        persisted = db.session.get(GmailTaskLog, log_id)
        self.assertEqual(persisted.status, 'pending_confirmation')
        self.assertEqual(persisted.confirmation.status, 'pending')
        self.assertIsNone(persisted.confirmation.reviewer)
        self.assertIsNone(persisted.confirmation.reviewed_at)

    def test_readiness_stays_failed_while_gmail_action_retry_is_unresolved(self):
        self.rule.requires_confirmation = False
        db.session.commit()
        log = GmailExecution.acquire(self.connection, self.rule, 'unresolved-message', ([], ['UNREAD']), 'running')
        log.status = 'failed'
        db.session.commit()

        response = self.app.test_client().get('/health/ready')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['gmailActions'])

    def test_failed_sync_result_requeues_persistent_job_with_backoff(self):
        self.app.config['BACKGROUND_TASK_MODE'] = 'inline'
        job = RuntimeQueue.enqueue('gmail_sync', {}, active_key='gmail-sync')
        job.status = 'running'
        job.attempts = 1
        db.session.commit()
        job_id = job.id

        with patch('app.worker.gmail_sync_daemon.sync_once', return_value={'failed': 1}) as sync_once, patch(
            'app.worker.time.time', return_value=1000.0
        ):
            run_job(self.app, job_id, threading.Event())

        sync_once.assert_called_once_with(self.app)
        db.session.remove()
        persisted = db.session.get(RuntimeJob, job_id)
        self.assertEqual(persisted.status, 'pending')
        self.assertEqual(persisted.active_key, 'gmail-sync')
        self.assertEqual(persisted.attempts, 1)
        self.assertEqual(persisted.available_at, 1030.0)
        self.assertIn('RuntimeError', persisted.snapshot['errorMessage'])

    def test_readiness_accepts_a_readable_gmail_credential_path(self):
        self.app.config['BACKGROUND_TASK_MODE'] = 'inline'
        self.app.config['GMAIL_CLIENT_SECRET_FILE'] = str(Path(__file__).resolve())

        response = self.app.test_client().get('/health/ready')

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()['ready'])
        self.assertTrue(response.get_json()['gmailConfiguration'])

    def test_readiness_fails_after_three_consecutive_maintenance_failures(self):
        self.app.config.update(BACKGROUND_TASK_MODE='inline', RECHARGE_MODE='mock')
        RuntimeQueue.set_state('gmail_daemon', {'enabled': True, 'intervalSeconds': 30})

        with patch('app.worker.gmail_sync_daemon.sync_once', side_effect=RuntimeError('synthetic failure')), patch(
            'app.worker.time.time'
        ) as current_time:
            for expected_failures in range(1, 4):
                current_time.return_value = expected_failures * 100
                maintenance(self.app)
                daemon_state = RuntimeQueue.state('gmail_daemon_status')
                self.assertEqual(daemon_state['consecutiveFailures'], expected_failures)
                response = self.app.test_client().get('/health/ready')
                self.assertEqual(response.status_code, 503 if expected_failures == 3 else 200)

        payload = response.get_json()
        self.assertFalse(payload['ready'])
        self.assertFalse(payload['maintenance'])
        self.assertIn('gmail_sync:RuntimeError', RuntimeQueue.state('maintenance')['errors'])


if __name__ == '__main__':
    unittest.main()
