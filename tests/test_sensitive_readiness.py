import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import text

from app import create_app, db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.recharge_task import RechargeTask
from app.models.gmail_connection import GmailConnection
from app.services.gmail_service import GmailService
from app.services.runtime_queue import RuntimeQueue
from app.services.schema_migration import encrypt_existing_sensitive_data, validate_sensitive_data


class SensitiveReadinessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.key = Fernet.generate_key().decode('ascii')
        self.app.config.update(
            GMAIL_TOKEN_ENCRYPTION_KEY=self.key,
            SENSITIVE_DATA_REQUIRE_ENCRYPTION=True,
            GMAIL_CLIENT_SECRET_FILE=None,
        )
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def add_encrypted_records(self):
        account = Account(email='readiness@example.test', password='synthetic-password')
        db.session.add(account)
        db.session.flush()
        db.session.add(AccountHistory(
            account_id=account.id, field_name='password', new_value='synthetic-password',
        ))
        db.session.add(RechargeTask(
            task_no='TK-READINESS', redeem_code='PLUS-READINESS', plan_type='PLUS',
            account_email='readiness@example.test', notify_email='notify@example.test',
        ))
        db.session.commit()
        db.session.remove()

    def test_empty_and_encrypted_databases_are_ready(self):
        self.assertEqual(self.client.get('/health/ready').status_code, 200)
        self.add_encrypted_records()
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.get_json()['sensitiveData'], True)

    def test_plaintext_after_healthy_probe_fails_and_explicit_migration_recovers(self):
        self.add_encrypted_records()
        self.assertEqual(self.client.get('/health/ready').status_code, 200)
        with db.engine.begin() as connection:
            connection.execute(text('UPDATE recharge_tasks SET redeem_code = :code'),
                               {'code': 'PLUS-LEGACY-READINESS'})
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertIs(response.get_json()['sensitiveData'], False)
        self.assertNotIn('PLUS-LEGACY-READINESS', response.get_data(as_text=True))
        encrypt_existing_sensitive_data(db.engine, self.key, apply=True)
        self.assertEqual(self.client.get('/health/ready').status_code, 200)

    def test_key_change_after_healthy_probe_fails_immediately(self):
        self.add_encrypted_records()
        self.assertEqual(self.client.get('/health/ready').status_code, 200)
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = Fernet.generate_key().decode('ascii')
        self.assertEqual(self.client.get('/health/ready').status_code, 503)
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = self.key
        self.assertEqual(self.client.get('/health/ready').status_code, 200)

    def test_malformed_ciphertext_and_history_plaintext_fail_without_echo(self):
        self.add_encrypted_records()
        with db.engine.begin() as connection:
            connection.execute(text("UPDATE account_history SET new_value = 'legacy-secret'"))
        self.assertEqual(self.client.get('/health/ready').status_code, 503)
        encrypt_existing_sensitive_data(db.engine, self.key, apply=True)
        with db.engine.begin() as connection:
            connection.execute(text("UPDATE accounts SET password = 'gmenc:v1:broken-secret'"))
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.get_data(as_text=True))

    def test_database_and_rollback_failure_still_return_json_503(self):
        with patch.object(db.session, 'execute', side_effect=RuntimeError('private-db')), \
                patch.object(db.session, 'rollback', side_effect=RuntimeError('private-rollback')):
            response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'ready': False})

    def test_missing_gmail_connection_table_is_not_ready(self):
        with db.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE gmail_connections')
        self.assertEqual(self.client.get('/health/ready').status_code, 503)

    def test_missing_gmail_columns_are_not_ready(self):
        for table_name, column_name in (
            ('gmail_connections', 'scopes'), ('gmail_rules', 'query'),
            ('gmail_watches', 'active'), ('gmail_task_logs', 'error_message'),
            ('gmail_action_confirmations', 'reviewer'), ('gmail_executions', 'lease_token'),
        ):
            with self.subTest(table=table_name, column=column_name):
                with db.engine.begin() as connection:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "{table_name}" RENAME COLUMN "{column_name}" TO "missing_column"'
                    )
                try:
                    response = self.client.get('/health/ready')
                    self.assertEqual(response.status_code, 503)
                    self.assertFalse(response.get_json()['ready'])
                finally:
                    with db.engine.begin() as connection:
                        connection.exec_driver_sql(
                            f'ALTER TABLE "{table_name}" RENAME COLUMN "missing_column" TO "{column_name}"'
                        )
                self.assertEqual(self.client.get('/health/ready').status_code, 200)

    def test_gmail_only_restore_requires_original_business_key(self):
        db.session.add(GmailConnection(
            email='restore@example.test', scopes='synthetic',
            token_data=GmailService._encrypt({'refresh_token': 'synthetic-refresh'}),
        ))
        db.session.commit()
        self.assert_key_restore_gate()

    def test_queue_only_restore_requires_original_business_key(self):
        RuntimeQueue.enqueue('gmail_notification', {'email': 'restore@example.test', 'historyId': '100'})
        self.assert_key_restore_gate()

    def assert_key_restore_gate(self):
        self.assertEqual(self.client.get('/health/ready').status_code, 200)
        wrong_key = Fernet.generate_key().decode('ascii')
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = wrong_key
        with self.assertRaises(RuntimeError):
            validate_sensitive_data(db.engine, wrong_key)
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['sensitiveData'])
        self.assertNotIn('synthetic-refresh', response.get_data(as_text=True))
        self.app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = self.key
        self.assertEqual(self.client.get('/health/ready').status_code, 200)

    def test_invalid_gmail_and_queue_payloads_fail_validation(self):
        gmail = GmailConnection(email='invalid@example.test', scopes='synthetic', token_data='plaintext-token')
        db.session.add(gmail)
        db.session.commit()
        self.assertEqual(self.client.get('/health/ready').status_code, 503)
        gmail.token_data = GmailService._encrypt({'refresh_token': 'synthetic-refresh'})
        db.session.commit()
        job = RuntimeQueue.enqueue('gmail_notification', {})
        job.payload = GmailService._encrypt(['invalid-payload'])
        db.session.commit()
        self.assertEqual(self.client.get('/health/ready').status_code, 503)

    def test_readiness_samples_are_bounded_and_full_restore_validation_is_not(self):
        for index in range(25):
            db.session.add(GmailConnection(
                email=f'sample-{index}@example.test', scopes='synthetic',
                token_data=GmailService._encrypt({'refresh_token': 'synthetic'}),
            ))
        db.session.commit()
        last = GmailConnection.query.order_by(GmailConnection.id.desc()).first()
        last.token_data = 'broken-beyond-readiness-sample'
        db.session.commit()
        with patch.object(Fernet, 'decrypt', autospec=True, wraps=Fernet.decrypt) as decrypt:
            decrypt.side_effect = lambda cipher, token: b'{"refresh_token":"synthetic"}'
            response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['sensitiveDataSampleLimit'], 20)
        self.assertEqual(decrypt.call_count, 20)
        with self.assertRaisesRegex(RuntimeError, 'gmail_connections.token_data'):
            validate_sensitive_data(db.engine, self.key, batch_size=7)
