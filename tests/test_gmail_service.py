import base64
import unittest

from cryptography.fernet import Fernet

from app import create_app, db
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


if __name__ == '__main__':
    unittest.main()