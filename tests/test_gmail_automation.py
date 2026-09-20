import base64
import json
import unittest
from unittest.mock import patch

from app import create_app, db
from tests.auth_helpers import login_admin
from app.models.gmail_connection import GmailConnection
from app.models.gmail_task_log import GmailTaskLog


class GmailAutomationApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        login_admin(self.client)
        self.connection = GmailConnection(
            email='automation@example.test',
            token_data='encrypted-token',
            scopes='https://www.googleapis.com/auth/gmail.modify',
        )
        db.session.add(self.connection)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def create_rule(self, **overrides):
        payload = {
            'name': '自动归档',
            'query': 'from:alerts@example.test',
            'actions': {'archive': True, 'addLabelIds': ['Label_1']},
            'requiresConfirmation': False,
        }
        payload.update(overrides)
        response = self.client.post(
            f'/api/gmail/{self.connection.id}/rules',
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['data']['query'], payload['query'])
        return response.get_json()['data']

    def test_oauth_start_and_callback_keep_state_without_exposing_token(self):
        from app.services.gmail_service import OAuthStateManager
        OAuthStateManager.register('state-1')
        with patch(
            'app.routes.api.GmailService.authorization_url',
            return_value=('https://accounts.google.test/auth', 'state-1'),
        ):
            response = self.client.get('/api/gmail/oauth/start')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()['data']['authorizationUrl'],
            'https://accounts.google.test/auth',
        )

        with patch(
            'app.routes.api.GmailService.complete_authorization',
            return_value=self.connection,
        ) as complete_authorization:
            response = self.client.get('/api/gmail/oauth/callback?state=state-1&code=code-1')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('tokenData', response.get_json()['data'])
        complete_authorization.assert_called_once_with('code-1', 'state-1')

        with self.client.session_transaction() as session:
            session['gmail_oauth_state'] = 'state-2'
        OAuthStateManager.register('state-2')
        missing_code = self.client.get('/api/gmail/oauth/callback?state=state-2')
        self.assertEqual(missing_code.status_code, 400)

    def test_rule_run_writes_log_and_modifies_labels(self):
        self.create_rule()
        with (
            patch('app.services.gmail_rule_service.GmailService.list_messages', return_value={
                'messages': [{'id': 'message-1'}],
            }),
            patch('app.services.gmail_rule_service.GmailService.modify_message', return_value={
                'id': 'message-1',
                'labelIds': ['Label_1'],
            }) as modify_message,
        ):
            response = self.client.post(
                f'/api/gmail/{self.connection.id}/rules/run',
                json={},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        summary = response.get_json()['data']
        self.assertEqual(summary['matched'], 1)
        self.assertEqual(summary['succeeded'], 1)
        modify_message.assert_called_once_with(
            self.connection,
            'message-1',
            add=['Label_1'],
            remove=['INBOX'],
        )
        log = GmailTaskLog.query.one()
        self.assertEqual(log.status, 'succeeded')

    def test_confirmation_requires_review_before_gmail_call(self):
        self.create_rule(
            name='需确认',
            query='',
            actions={'markRead': True},
            requiresConfirmation=True,
        )
        with patch('app.services.gmail_rule_service.GmailService.list_messages', return_value={
            'messages': [{'id': 'message-2'}],
        }), patch('app.services.gmail_rule_service.GmailService.modify_message') as modify_message:
            response = self.client.post(
                f'/api/gmail/{self.connection.id}/rules/run',
                json={},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['pendingConfirmation'], 1)
        modify_message.assert_not_called()
        log_id = GmailTaskLog.query.one().id

        with patch('app.services.gmail_rule_service.GmailService.modify_message', return_value={
            'id': 'message-2',
            'labelIds': [],
        }) as modify_message:
            response = self.client.post(
                f'/api/gmail/task-logs/{log_id}/confirm',
                json={'reviewer': 'admin', 'note': '确认'},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['data']['status'], 'succeeded')
        modify_message.assert_called_once_with(
            self.connection,
            'message-2',
            add=[],
            remove=['UNREAD'],
        )

    def test_rule_update_persists_search_query(self):
        rule = self.create_rule()
        response = self.client.patch(
            f"/api/gmail/rules/{rule['id']}",
            json={'query': 'is:unread', 'enabled': False},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['data']['query'], 'is:unread')
        self.assertFalse(response.get_json()['data']['enabled'])

        empty_update = self.client.patch(f"/api/gmail/rules/{rule['id']}", json={})
        self.assertEqual(empty_update.status_code, 400)

    def test_label_api_and_pubsub_webhook(self):
        with patch('app.routes.api.GmailService.modify_message', return_value={
            'id': 'message-3',
            'labelIds': ['STARRED'],
        }) as modify_message:
            response = self.client.patch(
                f'/api/gmail/{self.connection.id}/messages/message-3/labels',
                json={'addLabelIds': ['STARRED'], 'removeLabelIds': []},
            )
        self.assertEqual(response.status_code, 200)
        modify_message.assert_called_once_with(
            self.connection,
            'message-3',
            add=['STARRED'],
            remove=[],
        )

        encoded = base64.urlsafe_b64encode(json.dumps({
            'emailAddress': self.connection.email,
            'historyId': '200',
        }).encode('utf-8')).decode('ascii')
        with patch('app.routes.api.GmailService.process_notification', return_value={
            'connectionId': self.connection.id,
            'messageIds': [],
            'ignored': True,
        }) as process_notification:
            response = self.app.test_client().post(
                '/api/gmail/pubsub/webhook',
                json={'message': {'data': encoded}},
            )
        self.assertEqual(response.status_code, 200)
        process_notification.assert_called_once_with(self.connection.email, '200')


if __name__ == '__main__':
    unittest.main()
