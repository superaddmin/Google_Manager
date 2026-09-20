import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet

from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_task import RechargeTask
from app.models.recharge_mutation import RechargeMutation
from app.services.gmail_service import OAuthStateManager
from app.services.runtime_queue import RuntimeQueue


class SharedRechargeStateTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='google-manager-state-test-')
        self.database = 'sqlite:///' + (Path(self.directory.name) / 'isolated.db').as_posix()
        self.config_patch = patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', self.database)
        self.config_patch.start()
        self.app = create_app('testing')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.config_patch.stop()
        self.directory.cleanup()

    def worker(self, body, data):
        environment = os.environ.copy()
        environment['ISOLATED_TEST_DATABASE'] = self.database
        environment['ISOLATED_TEST_SECRET'] = self.app.secret_key
        environment['ISOLATED_TEST_ENCRYPTION_KEY'] = self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY']
        environment['ISOLATED_TEST_DATA'] = json.dumps(data)
        source = """
import json, os, urllib.request
from app import create_app, db
from app.config import TestingConfig
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService, RechargeContractError
from app.services.gmail_service import OAuthStateManager
from app.services.runtime_queue import RuntimeQueue
from app.models.request_limit import RequestLimit
TestingConfig.SQLALCHEMY_DATABASE_URI = os.environ['ISOLATED_TEST_DATABASE']
TestingConfig.SECRET_KEY = os.environ['ISOLATED_TEST_SECRET']
TestingConfig.GMAIL_TOKEN_ENCRYPTION_KEY = os.environ['ISOLATED_TEST_ENCRYPTION_KEY']
application = create_app('testing')
data = json.loads(os.environ['ISOLATED_TEST_DATA'])
def deny_network(*args, **kwargs):
    raise AssertionError('External HTTP forbidden')
urllib.request.urlopen = deny_network
with application.app_context():
""" + '\n'.join('    ' + line for line in body.splitlines()) + "\n    db.session.remove()\n    db.engine.dispose()\n"
        result = subprocess.run(
            [sys.executable, '-c', source],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def test_oauth_state_is_consumed_once_across_processes(self):
        state = OAuthStateManager.register('synthetic-shared-state', account_id=123)
        db.session.remove()
        body = "print(json.dumps({'accepted': OAuthStateManager.consume(data['state']) is not None}))"
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda unused: self.worker(body, {'state': state}), range(2)))
        self.assertEqual(sorted(result['accepted'] for result in results), [False, True])
        self.assertFalse(OAuthStateManager.validate(state))

    def test_challenge_survives_worker_change_and_cannot_replay(self):
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        payload = {
            'redeem_code': 'PLUS-SHARED-SYNTHETIC', 'token_input': 'synthetic-credential',
            'plan_type': 'PLUS', 'account_email': 'fixture@example.test',
            'agreement_accepted': True, 'email_verified': True, 'acknowledge_non_free': True,
        }
        response = client.post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(response.status_code, 200)
        payload['challenge_token'] = response.get_json()['data']['challenge_token']
        with client.session_transaction() as session:
            context = session['recharge_context']
        body = """client = application.test_client()
client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
with client.session_transaction() as session:
    session['recharge_context'] = data['context']
response = client.post('/api/recharge/tasks', json=data['payload'])
print(json.dumps({'http': response.status_code}))"""
        first = self.worker(body, {'context': context, 'payload': payload})
        replay = self.worker(body, {'context': context, 'payload': payload})
        self.assertEqual(first['http'], 201)
        self.assertEqual(replay['http'], 400)
        self.assertEqual(RechargeTask.query.count(), 1)

    def test_business_operation_is_unique_across_processes(self):
        body = """task = RechargeTask(task_no=data['task_no'], redeem_code='PLUS-SHARED-OPERATION',
    plan_type='PLUS', account_email='fixture@example.test', status='processing', is_mock=True)
try:
    RechargeService._persist_new_task(task, 'mock')
    result = 'created'
except RechargeContractError:
    result = 'blocked'
print(json.dumps({'result': result}))"""
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda task_no: self.worker(body, {'task_no': task_no}), ['TK-SHARED-1', 'TK-SHARED-2']))
        self.assertEqual(sorted(result['result'] for result in results), ['blocked', 'created'])
        self.assertEqual(RechargeTask.query.count(), 1)

    def test_public_rate_limit_is_shared_between_processes(self):
        body = "print(json.dumps({'accepted': RequestLimit.consume('synthetic-shared-limit', 2, 60)}))"
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda unused: self.worker(body, {}), range(4)))
        self.assertEqual(sum(result['accepted'] for result in results), 2)

    def test_job_can_be_claimed_only_once_across_processes(self):
        job = RuntimeQueue.enqueue('gmail_notification', {})
        identifier = job.id
        db.session.remove()
        body = "job = RuntimeQueue.claim()\nprint(json.dumps({'job': job.id if job else None}))"
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda unused: self.worker(body, {}), range(2)))
        self.assertEqual([result['job'] for result in results].count(identifier), 1)

    def test_mutation_is_reserved_once_before_calling_upstream_across_processes(self):
        task = RechargeTask(task_no='TK-SHARED-MUTATION', redeem_code='PLUS-SHARED-MUTATION',
                            plan_type='PLUS', account_email='fixture@example.test', status='processing', is_mock=False)
        db.session.add(task)
        db.session.commit()
        db.session.remove()
        body = """from app.models.recharge_mutation import RechargeMutation
task = RechargeTask.query.filter_by(task_no='TK-SHARED-MUTATION').one()
try:
    RechargeMutation.claim(task, 'recall')
    result = 'reserved'
except RechargeContractError:
    result = 'blocked'
print(json.dumps({'result': result}))"""
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda unused: self.worker(body, {}), range(2)))
        self.assertEqual(sorted(result['result'] for result in results), ['blocked', 'reserved'])

    def test_completed_mutation_can_only_be_reopened_once_across_processes(self):
        """done -> pending 必须是条件更新，不能让两个进程同时领取下一次操作。"""
        task = RechargeTask(
            task_no='TK-SHARED-DONE-MUTATION', redeem_code='PLUS-SHARED-DONE',
            plan_type='PLUS', account_email='fixture@example.test',
            status='processing', is_mock=False,
        )
        db.session.add(task)
        db.session.commit()
        operation_id = RechargeMutation.claim(task, 'recall')
        RechargeMutation.finish(task.task_no, operation_id, 'done')
        db.session.commit()
        body = """from app.models.recharge_mutation import RechargeMutation
task = RechargeTask.query.filter_by(task_no='TK-SHARED-DONE-MUTATION').one()
try:
    RechargeMutation.claim(task, 'recall')
    result = 'reserved'
except RechargeContractError:
    result = 'blocked'
print(json.dumps({'result': result}))"""
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda unused: self.worker(body, {}), range(2)))
        self.assertEqual(sorted(result['result'] for result in results), ['blocked', 'reserved'])


if __name__ == '__main__':
    unittest.main()
