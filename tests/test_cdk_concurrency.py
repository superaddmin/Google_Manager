import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app import db
from app.config import TestingConfig
from app.models.cdk import CdkCard, CdkRedemption, CdkStock
from app.models.recharge_task import RechargeTask
from tests.test_cdk import CdkFixture


class CdkConcurrencyTestCase(CdkFixture):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='google-manager-cdk-concurrency-')
        self.database = 'sqlite:///' + (Path(self.directory.name) / 'concurrency.sqlite').as_posix()
        self.configuration = patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', self.database)
        self.configuration.start()
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.configuration.stop()
            self.directory.cleanup()

    def worker(self, data):
        environment = os.environ.copy()
        environment['CDK_TEST_DATABASE'] = self.database
        environment['CDK_TEST_SETTINGS'] = json.dumps({name: self.application.config[name] for name in
            ('SECRET_KEY', 'GMAIL_TOKEN_ENCRYPTION_KEY', 'CDK_ENCRYPTION_KEYS', 'CDK_LOOKUP_KEYS')})
        environment['CDK_TEST_REQUEST'] = json.dumps(data)
        source = """
import json, os
from app import create_app, db
from app.config import TestingConfig
TestingConfig.SQLALCHEMY_DATABASE_URI = os.environ['CDK_TEST_DATABASE']
TestingConfig.AUTO_CREATE_DB = False
application = create_app('testing')
application.config.update(json.loads(os.environ['CDK_TEST_SETTINGS']))
application.config['CDK_ENABLED'] = True
data = json.loads(os.environ['CDK_TEST_REQUEST'])
client = application.test_client()
client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
with client.session_transaction() as session:
    session['cdk_context'] = data['context']
response = client.post('/api/cdk/redemptions', json=data['payload'], headers={'Idempotency-Key': data['key']})
body = response.get_json()
print(json.dumps({'status': response.status_code, 'id': (body.get('data') or {}).get('id')}))
with application.app_context():
    db.session.remove()
    db.engine.dispose()
"""
        result = subprocess.run([sys.executable, '-c', source], env=environment, capture_output=True,
                                encoding='utf-8', timeout=40, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def racing_requests(self, same_key):
        self.create_catalog()
        payload = self.intent()
        challenge = self.post('/challenges', payload, self.customer)
        payload['challenge_token'] = challenge['challenge_token']
        with self.customer.session_transaction() as session:
            context = session['cdk_context']
        key = uuid.uuid4().hex
        requests = [{'payload': payload, 'context': context, 'key': key if same_key else uuid.uuid4().hex} for _ in range(4)]
        db.session.remove()
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(self.worker, requests))
        self.assertEqual(CdkRedemption.query.count(), 1)
        self.assertEqual(RechargeTask.query.count(), 1)
        self.assertEqual(CdkCard.query.first().usage, 'reserved')
        self.assertEqual(CdkStock.query.first().state, 'reserved')
        return results

    def test_same_key_four_processes_replay_one_intent(self):
        results = self.racing_requests(True)
        self.assertEqual([record['status'] for record in results], [202] * 4)
        self.assertEqual(len({record['id'] for record in results}), 1)

    def test_different_keys_four_processes_only_one_reservation(self):
        results = self.racing_requests(False)
        self.assertEqual(sorted(record['status'] for record in results), [202, 409, 409, 409])
