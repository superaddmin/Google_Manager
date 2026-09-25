import json
import unittest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError
from httplib2 import Response

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.account import Account
from app.models.gmail_connection import GmailConnection
from app.models.gmail_rule import GmailRule
from app.models.gmail_task_log import GmailTaskLog
from app.models.gmail_watch import GmailWatch
from app.models.runtime_job import RuntimeJob
from app.services.gmail_service import GmailService, GmailServiceError
from app.services.gmail_rule_service import GmailRuleService
from app.services.gmail_rule_service import GmailRuleServiceError
from app.services.runtime_queue import RuntimeQueue, QueueConflict
from app.worker import recover_interrupted_jobs


class RuntimeReliabilityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        self.context = self.app.app_context()
        self.context.push()
        self.connection = GmailConnection(email='synthetic@example.test', token_data='synthetic', scopes='synthetic')
        db.session.add(self.connection)
        db.session.flush()
        self.watch = GmailWatch(connection_id=self.connection.id, topic_name='synthetic', history_id='100', active=True)
        self.rule = GmailRule(connection_id=self.connection.id, name='synthetic-rule', actions=json.dumps({'markRead': True}))
        db.session.add_all([self.watch, self.rule])
        db.session.commit()
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def history_service(self, responses):
        service = MagicMock()
        service.users.return_value.history.return_value.list.return_value.execute.side_effect = responses
        return service

    @staticmethod
    def history_page(identifier, **extra):
        return {'history': [{'messagesAdded': [{'message': {'id': identifier}}]}], 'historyId': '200', **extra}

    def test_notification_processes_all_pages_before_advancing_cursor(self):
        service = self.history_service([self.history_page('message-1', nextPageToken='page-2'), self.history_page('message-2')])
        with patch.object(GmailService, '_service', return_value=service), patch.object(GmailService, 'modify_message', return_value={'ok': True}) as modify:
            result = GmailService.process_notification(self.connection.email, '200')
        self.assertEqual(result['messageIds'], ['message-1', 'message-2'])
        self.assertEqual(modify.call_count, 2)
        self.assertEqual(db.session.get(GmailWatch, self.watch.id).history_id, '200')
        history_calls = service.users.return_value.history.return_value.list.call_args_list
        self.assertEqual(history_calls[1].kwargs['pageToken'], 'page-2')

    def test_failed_action_keeps_cursor_and_retry_does_not_repeat_success(self):
        page = {'history': [{'messagesAdded': [{'message': {'id': 'message-1'}}, {'message': {'id': 'message-2'}}]}]}
        service = self.history_service([page, page])
        with patch.object(GmailService, '_service', return_value=service), patch.object(GmailService, 'modify_message', side_effect=[{'ok': True}, RuntimeError('synthetic timeout'), {'ok': True}]) as modify:
            with self.assertRaises(GmailServiceError):
                GmailService.process_notification(self.connection.email, '200')
            self.assertEqual(db.session.get(GmailWatch, self.watch.id).history_id, '100')
            GmailService.process_notification(self.connection.email, '200')
        self.assertEqual(modify.call_count, 3)
        self.assertEqual(GmailTaskLog.query.count(), 2)
        self.assertEqual(GmailTaskLog.query.filter_by(status='succeeded').count(), 2)

    def test_repeat_scan_creates_only_one_confirmation(self):
        self.rule.requires_confirmation = True
        db.session.commit()
        for unused in range(2):
            GmailRuleService.run_rules(self.connection, message_ids=['message-1'])
        self.assertEqual(GmailTaskLog.query.filter_by(status='pending_confirmation').count(), 1)

    def test_failed_confirmation_can_be_queued_again_without_duplicate_record(self):
        self.rule.requires_confirmation = True
        db.session.commit()
        GmailRuleService.run_rules(self.connection, message_ids=['message-1'])
        log = GmailTaskLog.query.one()
        with patch.object(GmailService, 'modify_message', side_effect=RuntimeError('synthetic timeout')):
            with self.assertRaises(GmailRuleServiceError):
                GmailRuleService.confirm_log(log, 'synthetic-reviewer')
        GmailRuleService.run_rules(self.connection, message_ids=['message-1'])
        self.assertEqual(GmailTaskLog.query.count(), 1)
        self.assertEqual(log.status, 'pending_confirmation')
        self.assertEqual(log.confirmation.status, 'pending')

    def test_older_notification_cannot_move_cursor_backwards(self):
        with patch.object(GmailService, '_service') as service:
            result = GmailService.process_notification(self.connection.email, '99')
        self.assertTrue(result['ignored'])
        service.assert_not_called()
        self.assertEqual(self.watch.history_id, '100')

    def test_deleted_message_is_skipped_without_blocking_later_notifications(self):
        service = self.history_service([
            self.history_page('deleted-message', nextPageToken='next'),
            self.history_page('existing-message'), self.history_page('deleted-message'),
        ])
        missing = HttpError(Response({'status': '404'}), b'{"error":{"message":"Not found"}}')
        with patch.object(GmailService, '_service', return_value=service), patch.object(
            GmailService, 'modify_message', side_effect=[missing, {'id': 'existing-message'}],
        ) as modify, patch.object(GmailService, 'get_message', side_effect=missing):
            result = GmailService.process_notification(self.connection.email, '200')
            self.assertEqual(result['ruleSummary']['failed'], 0)
            self.assertEqual(result['ruleSummary']['succeeded'], 1)
            self.assertEqual(self.watch.history_id, '200')
            GmailService.process_notification(self.connection.email, '201')
        self.assertEqual(modify.call_count, 2)
        skipped = GmailTaskLog.query.filter_by(message_id='deleted-message').one()
        self.assertEqual(skipped.status, 'skipped')
        self.assertEqual(skipped.to_dict()['resultData'], {'reason': 'message_not_found'})
        self.assertEqual(self.watch.history_id, '201')

    def test_missing_label_or_forbidden_message_does_not_advance_cursor(self):
        for status_code in (404, 403):
            with self.subTest(status=status_code):
                service = self.history_service([self.history_page('existing-message')])
                error = HttpError(Response({'status': str(status_code)}), b'{"error":{"message":"Denied"}}')
                with patch.object(GmailService, '_service', return_value=service), patch.object(
                    GmailService, 'modify_message', side_effect=error,
                ), patch.object(GmailService, 'get_message', return_value={'id': 'existing-message'}) as lookup:
                    with self.assertRaises(GmailServiceError):
                        GmailService.process_notification(self.connection.email, '200')
                self.assertEqual(lookup.call_count, 1 if status_code == 404 else 0)
                self.assertEqual(self.watch.history_id, '100')
                self.assertEqual(GmailTaskLog.query.one().status, 'failed')

    def test_renew_watch_preserves_unprocessed_cursor(self):
        self.app.config['GMAIL_PUBSUB_TOPIC'] = 'synthetic-topic'
        service = MagicMock()
        service.users.return_value.watch.return_value.execute.return_value = {'historyId': '200', 'expiration': '1900000000000'}
        with patch.object(GmailService, '_service', return_value=service):
            GmailService.watch(self.connection)
        self.assertEqual(self.watch.history_id, '100')

    def test_queue_encrypts_payload_and_enforces_shared_exclusion(self):
        job = RuntimeQueue.enqueue('oauth', {'accountIds': [1], 'options': {'proxy': 'synthetic-secret'}}, active_key='account-automation')
        self.assertNotIn('synthetic-secret', job.payload)
        self.assertNotIn('synthetic-secret', json.dumps(job.to_dict()))
        with self.assertRaises(QueueConflict):
            RuntimeQueue.enqueue('googlemail', {}, active_key='account-automation')
        identifier = job.id
        db.session.remove()
        claimed = RuntimeQueue.claim()
        self.assertEqual(claimed.id, identifier)
        self.assertEqual(claimed.status, 'running')
        self.assertIsNone(RuntimeQueue.claim())

    def test_pending_job_cancel_is_durable(self):
        job = RuntimeQueue.enqueue('oauth', {}, active_key='account-automation')
        identifier = job.id
        RuntimeQueue.cancel(job)
        db.session.remove()
        self.assertEqual(db.session.get(RuntimeJob, identifier).status, 'cancelled')
        self.assertIsNone(RuntimeQueue.claim())

    def test_restart_does_not_replay_uncertain_account_changes(self):
        job = RuntimeQueue.enqueue('googlemail', {}, active_key='account-automation')
        job.status = 'running'
        notification = RuntimeQueue.enqueue('gmail_notification', {})
        notification.status = 'running'
        db.session.commit()
        recover_interrupted_jobs()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.active_key, 'account-automation')
        self.assertEqual(notification.status, 'pending')

    def test_pubsub_acknowledges_only_persisted_job_and_deduplicates(self):
        import base64
        self.app.config['BACKGROUND_TASK_MODE'] = 'queue'
        data = base64.urlsafe_b64encode(json.dumps({'emailAddress': self.connection.email, 'historyId': '200'}).encode()).decode()
        client = self.app.test_client()
        with patch.object(GmailService, 'process_notification') as processing:
            for unused in range(2):
                response = client.post('/api/gmail/pubsub/webhook', json={'message': {'data': data}})
                self.assertEqual(response.status_code, 202)
        processing.assert_not_called()
        self.assertEqual(RuntimeJob.query.count(), 1)

    def test_daemon_desired_state_survives_session_and_worker_restart(self):
        RuntimeQueue.set_state('gmail_daemon', {'enabled': True, 'intervalSeconds': 60})
        db.session.remove()
        self.assertTrue(RuntimeQueue.daemon_status()['enabled'])
        self.assertFalse(RuntimeQueue.daemon_status()['isRunning'])
        import time
        RuntimeQueue.set_state('worker', {'heartbeat': time.time()})
        self.assertTrue(RuntimeQueue.daemon_status()['isRunning'])

    def test_web_queue_routes_do_not_launch_browser(self):
        self.app.config.update(BACKGROUND_TASK_MODE='queue', GOOGLEMAIL_EXECUTION_ENABLED=True)
        account = Account(email='account@example.test', password='synthetic-password', status='inactive')
        db.session.add(account)
        db.session.commit()
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(client)
        with patch('app.services.batch_oauth_service.batch_oauth_manager.start_batch') as start:
            response = client.post('/api/gmail/batch-authorize', json={'accountIds': [account.id]})
        self.assertEqual(response.status_code, 201, response.get_json())
        start.assert_not_called()
        db.session.remove()
        other = self.app.test_client()
        login_admin(other)
        response = other.get('/api/gmail/batch-authorize/status')
        self.assertEqual(response.get_json()['data']['active']['status'], 'pending')
