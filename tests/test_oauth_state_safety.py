import unittest
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.services.gmail_service import OAuthStateManager


class OAuthStateSafetyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.connection = MagicMock()
        self.connection.to_dict.return_value = {'id': 42, 'email': 'fixture@example.test'}

    def tearDown(self):
        OAuthStateManager.clear()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_session_callback_consumes_registered_state(self):
        state = OAuthStateManager.register('synthetic-session-state')
        with self.client.session_transaction() as session:
            session['gmail_oauth_state'] = state
        with patch('app.routes.api.GmailService.complete_authorization', return_value=self.connection) as exchange:
            first = self.client.get(f'/api/gmail/oauth/callback?state={state}&code=synthetic-code')
            replay = self.app.test_client().get(f'/api/gmail/oauth/callback?state={state}&code=other-code')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 400)
        self.assertFalse(OAuthStateManager.validate(state))
        exchange.assert_called_once()

    def test_session_alone_cannot_authorize_missing_registry_state(self):
        with self.client.session_transaction() as session:
            session['gmail_oauth_state'] = 'unregistered-session-state'
        with patch('app.routes.api.GmailService.complete_authorization', return_value=self.connection) as exchange:
            response = self.client.get('/api/gmail/oauth/callback?state=unregistered-session-state&code=synthetic-code')
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()

    def test_mismatched_session_does_not_consume_another_state(self):
        state = OAuthStateManager.register('synthetic-registered-state')
        with self.client.session_transaction() as session:
            session['gmail_oauth_state'] = 'different-session-state'
        with patch('app.routes.api.GmailService.complete_authorization', return_value=self.connection) as exchange:
            response = self.client.get(f'/api/gmail/oauth/callback?state={state}&code=synthetic-code')
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()
        self.assertTrue(OAuthStateManager.validate(state))

    def test_error_callback_also_consumes_state(self):
        state = OAuthStateManager.register('synthetic-canceled-state')
        with self.client.session_transaction() as session:
            session['gmail_oauth_state'] = state
        response = self.client.get(f'/api/gmail/oauth/callback?state={state}&error=access_denied')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OAuthStateManager.validate(state))


if __name__ == '__main__':
    unittest.main()
