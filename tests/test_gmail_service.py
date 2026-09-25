import base64
import unittest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from app import create_app, db
from app.models.gmail_connection import GmailConnection
from app.services.gmail_service import GmailService, GmailServiceError


class GmailServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode('ascii')
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def test_token_round_trip(self):
        payload = {'refresh_token': 'not-a-real-token', 'token': 'access-token'}
        encrypted = GmailService._encrypt(payload)
        self.assertNotEqual(encrypted, str(payload))
        self.assertEqual(GmailService._decrypt(encrypted), payload)

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(GmailServiceError):
            GmailService._decrypt('invalid-token')

    def test_message_parser_extracts_headers_and_body(self):
        encoded = base64.urlsafe_b64encode('纯文本内容'.encode('utf-8')).decode('ascii')
        message = {
            'id': 'message-1', 'threadId': 'thread-1', 'labelIds': ['INBOX', 'UNREAD'],
            'snippet': '摘要',
            'payload': {
                'headers': [
                    {'name': 'Subject', 'value': '主题'},
                    {'name': 'From', 'value': 'sender@example.com'},
                ],
                'body': {'data': encoded},
            },
        }
        result = GmailService._message_dict(message)
        self.assertEqual(result['subject'], '主题')
        self.assertEqual(result['from'], 'sender@example.com')
        self.assertEqual(result['body'], '纯文本内容')

    def test_google_api_transport_uses_bounded_timeout(self):
        credentials = MagicMock()
        with patch('httplib2.Http') as http_class, patch(
            'google_auth_httplib2.AuthorizedHttp'
        ) as authorized_http_class, patch(
            'googleapiclient.discovery.build', return_value='service'
        ) as build:
            result = GmailService._service_for_credentials(credentials)

        self.assertEqual(result, 'service')
        http_class.assert_called_once_with(timeout=30.0)
        authorized_http_class.assert_called_once_with(
            credentials, http=http_class.return_value
        )
        build.assert_called_once_with(
            'gmail', 'v1', http=authorized_http_class.return_value,
            cache_discovery=False,
        )

    def test_oauth_token_exchange_uses_bounded_timeout(self):
        flow = MagicMock()
        flow.credentials.scopes = ['scope']
        flow.credentials.to_json.return_value = (
            '{"refresh_token":"refresh","token":"access",'
            '"token_uri":"uri","client_id":"id","client_secret":"secret"}'
        )
        profile_service = MagicMock()
        profile_service.users.return_value.getProfile.return_value.execute.return_value = {
            'emailAddress': 'USER@EXAMPLE.TEST'
        }
        with patch.object(GmailService, '_flow', return_value=flow), patch.object(
            GmailService, '_service_for_credentials', return_value=profile_service
        ):
            connection = GmailService.complete_authorization('code', 'state')

        flow.fetch_token.assert_called_once_with(code='code', timeout=30.0)
        self.assertEqual(connection.email, 'user@example.test')

    def test_expired_credentials_refresh_uses_bounded_timeout(self):
        connection = GmailConnection(
            email='refresh@example.test',
            token_data=GmailService._encrypt({'refresh_token': 'refresh'}),
            scopes='scope',
        )
        db.session.add(connection)
        db.session.commit()

        credentials = MagicMock(expired=True, refresh_token='refresh')
        credentials.to_json.return_value = (
            '{"refresh_token":"refresh","token":"access",'
            '"token_uri":"uri","client_id":"id","client_secret":"secret"}'
        )
        with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=credentials), patch.object(
            GmailService, '_service_for_credentials', return_value='service'
        ):
            self.assertEqual(GmailService._service(connection), 'service')

        refresh_request = credentials.refresh.call_args.args[0]
        self.assertEqual(refresh_request.timeout, 30.0)

    def test_gmail_timeout_rejects_non_finite_values(self):
        for value in ('nan', 'inf', '-inf'):
            with self.subTest(value=value):
                self.app.config['GMAIL_HTTP_TIMEOUT_SECONDS'] = value
                with self.assertRaises(GmailServiceError):
                    GmailService._http_timeout()


if __name__ == '__main__':
    unittest.main()
