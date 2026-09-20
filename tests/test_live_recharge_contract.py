from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch

from app import create_app, db


class LiveRechargeContractTestCase(unittest.TestCase):
    def test_anonymous_flow_uses_real_http_transport_with_isolated_upstream(self):
        remote = {}
        class UpstreamHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload):
                encoded = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/user/redeem-codes/validate':
                    self.respond({'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'}})
                elif self.path == '/user/tasks':
                    remote['submission'] = payload
                    remote['task'] = {**payload, 'task_no': 'SYNTHETIC-REMOTE-TASK', 'status': 'processing'}
                    self.respond({'ok': True, 'task': remote['task']})
                elif self.path == '/user/tasks/recall':
                    remote['task']['status'] = 'recalled'
                    self.respond({'ok': True, 'task': {
                        'client_task_no': payload['client_task_no'],
                        'redeem_code': payload['redeem_code'],
                        'status': 'recalled',
                    }})
                else:
                    self.respond({'ok': False})

            def do_GET(self):
                self.respond({'ok': True, 'task': remote['task']})

        server = ThreadingHTTPServer(('127.0.0.1', 0), UpstreamHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        application = create_app('testing')
        application.config.update(RECHARGE_MODE='live', RECHARGE_UPSTREAM_URL=f'http://127.0.0.1:{server.server_port}')
        try:
            with application.app_context(), patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                client = application.test_client()
                client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
                payload = {'redeem_code': 'PLUS-SYNTHETIC-HTTP', 'token_input': 'synthetic-credential',
                           'plan_type': 'PLUS', 'account_email': 'synthetic@example.test',
                           'agreement_accepted': True, 'email_verified': True,
                           'acknowledge_non_free': True, 'is_renewal': False}
                self.assertEqual(client.post('/api/recharge/redeem-codes/validate', json=payload).status_code, 200)
                challenge = client.post('/api/recharge/submission-challenges', json=payload)
                self.assertEqual(challenge.status_code, 200)
                payload['challenge_token'] = challenge.get_json()['data']['challenge_token']
                created = client.post('/api/recharge/tasks', json=payload)
                self.assertEqual(created.status_code, 201, created.get_json())
                identifier = created.get_json()['data']['task_no']
                self.assertEqual(remote['submission']['idempotency_key'], identifier)
                self.assertNotIn('challenge_token', remote['submission'])
                queried = client.get('/api/recharge/tasks/' + identifier)
                self.assertEqual(queried.get_json()['data']['status'], 'processing')
                self.assertNotIn('redeem_code', queried.get_json()['data'])
                recalled = client.post('/api/recharge/tasks/recall', json={
                    'task_no': identifier,
                    'redeem_code': payload['redeem_code'], 'email': payload['account_email'], 'confirmed': True,
                })
                self.assertEqual(recalled.status_code, 200)
                self.assertEqual(recalled.get_json()['data']['status'], 'recalled')
                self.assertEqual(client.get('/api/accounts').status_code, 401)
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
