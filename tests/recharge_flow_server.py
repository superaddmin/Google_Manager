"""Isolated Flask and synthetic-upstream fixture for the recharge browser test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit

from werkzeug.serving import make_server


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


class SyntheticUpstream:
    """Small HTTP upstream that models acceptance followed by completion."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks = {}
        self.validation_count = 0
        self.create_count = 0
        self.status_read_count = 0
        self.challenge_token_forwarded = False
        self.idempotency_matches_client_task = False

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

    def validate(self):
        with self._lock:
            self.validation_count += 1
        return {
            'ok': True,
            'result': {
                'product': 'gpt',
                'plan_type': 'PLUS',
                'plan_name': 'Synthetic ChatGPT Plus',
                'status': 'unused',
                'account_change_locked': False,
                'is_renewal_supported': True,
            },
        }

    def create(self, payload):
        with self._lock:
            self.create_count += 1
            self.challenge_token_forwarded = 'challenge_token' in payload
            self.idempotency_matches_client_task = (
                payload.get('idempotency_key') == payload.get('client_task_no')
            )
            remote_task_no = f'SYNTHETIC-REMOTE-{self.create_count}'
            task = {
                'task_no': remote_task_no,
                'client_task_no': payload.get('client_task_no'),
                'redeem_code': payload.get('redeem_code'),
                'account_email': payload.get('account_email'),
                'plan_type': payload.get('plan_type'),
                'status': 'processing',
                'status_text': 'Synthetic upstream processing',
                'card_last4': '4242',
                'is_renewal': payload.get('is_renewal', False),
                'created_at': self._timestamp(),
                'updated_at': self._timestamp(),
            }
            self._tasks[remote_task_no] = task
            return {'ok': True, 'task': dict(task)}

    def read(self, remote_task_no):
        with self._lock:
            task = self._tasks.get(remote_task_no)
            if task is None:
                return {'ok': False, 'message': 'Synthetic task not found'}
            self.status_read_count += 1
            if self.status_read_count >= 2:
                task['status'] = 'completed'
                task['status_text'] = 'Synthetic upstream completed'
                task['updated_at'] = self._timestamp()
            return {'ok': True, 'task': dict(task)}

    def snapshot(self):
        with self._lock:
            return {
                'validation_count': self.validation_count,
                'create_count': self.create_count,
                'status_read_count': self.status_read_count,
                'challenge_token_forwarded': self.challenge_token_forwarded,
                'idempotency_matches_client_task': self.idempotency_matches_client_task,
            }


def json_response(handler, payload, status=200):
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    raw_length = handler.headers.get('Content-Length', '0')
    try:
        length = int(raw_length)
    except ValueError:
        return None
    if not 0 <= length <= 1024 * 1024:
        return None
    try:
        payload = json.loads(handler.rfile.read(length))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_upstream_handler(upstream):
    class UpstreamHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = read_json_body(self)
            if payload is None:
                json_response(self, {'ok': False, 'message': 'Invalid JSON'}, 400)
                return
            if self.path == '/user/redeem-codes/validate':
                json_response(self, upstream.validate())
                return
            if self.path == '/user/tasks':
                json_response(self, upstream.create(payload))
                return
            json_response(self, {'ok': False, 'message': 'Synthetic route not found'}, 404)

        def do_GET(self):
            path = urlsplit(self.path).path
            prefix = '/user/tasks/'
            if not path.startswith(prefix):
                json_response(self, {'ok': False, 'message': 'Synthetic route not found'}, 404)
                return
            json_response(self, upstream.read(unquote(path[len(prefix):])))

    return UpstreamHandler


def database_uri(database_path):
    return 'sqlite:///' + str(database_path.resolve()).replace('\\', '/')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()

    database_path = Path(args.database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    os.environ['no_proxy'] = '127.0.0.1,localhost'

    upstream = SyntheticUpstream()
    upstream_server = ThreadingHTTPServer(
        ('127.0.0.1', 0),
        build_upstream_handler(upstream),
    )
    upstream_thread = threading.Thread(
        target=upstream_server.serve_forever,
        name='synthetic-recharge-upstream',
        daemon=True,
    )
    upstream_thread.start()

    from app import create_app, db
    from app.config import TestingConfig
    from app.models.recharge_task import RechargeTask

    TestingConfig.SQLALCHEMY_DATABASE_URI = database_uri(database_path)
    application = create_app('testing')
    application.config.update(
        RECHARGE_MODE='live',
        RECHARGE_UPSTREAM_URL=(
            f'http://127.0.0.1:{upstream_server.server_port}'
        ),
        RECHARGE_RATE_LIMIT_ENABLED=False,
    )

    server_holder = {}

    @application.get('/__test__/state')
    def fixture_state():
        tasks = RechargeTask.query.order_by(RechargeTask.id).all()
        return {
            'tasks': [
                {
                    'task_no': task.task_no,
                    'status': task.status,
                    'is_mock': task.is_mock,
                }
                for task in tasks
            ],
            'upstream': upstream.snapshot(),
        }

    @application.post('/__test__/shutdown')
    def fixture_shutdown():
        threading.Thread(
            target=server_holder['server'].shutdown,
            name='recharge-fixture-shutdown',
            daemon=True,
        ).start()
        return {'stopping': True}

    flask_server = make_server(args.host, args.port, application, threaded=True)
    server_holder['server'] = flask_server
    port = flask_server.socket.getsockname()[1]
    print(json.dumps({'base_url': f'http://{args.host}:{port}'}), flush=True)

    try:
        flask_server.serve_forever()
    finally:
        flask_server.server_close()
        upstream_server.shutdown()
        upstream_server.server_close()
        upstream_thread.join(timeout=5)
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


if __name__ == '__main__':
    main()
