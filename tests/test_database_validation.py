import contextlib
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from app.manage import main, validate_database
from app.services.field_encryption import encrypt_value
from app.services.schema_migration import initialize_database


class DatabaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'restored.db'
        self.url = 'sqlite:///' + self.path.as_posix()
        self.key = Fernet.generate_key().decode('ascii')
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        initialize_database(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text(
                'INSERT INTO accounts (id, email, password) VALUES (:id, :email, :password)'
            ), [
                {'id': number, 'email': f'validation-{number}@example.test',
                 'password': encrypt_value('synthetic-value', self.key)}
                for number in range(1, 23)
            ])

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_valid_database_is_verified_without_writes_or_network(self):
        original = self.digest()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            validate_database(self.url, self.key)
        self.assertEqual(original, self.digest())

    def test_corruption_after_readiness_sample_is_rejected(self):
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE accounts SET password='plaintext-fixture' WHERE id=22"))
        original = self.digest()
        with self.assertRaises(RuntimeError):
            validate_database(self.url, self.key)
        self.assertEqual(original, self.digest())

    def test_wrong_business_key_is_rejected_without_writes(self):
        original = self.digest()
        with self.assertRaises(RuntimeError):
            validate_database(self.url, Fernet.generate_key().decode('ascii'))
        self.assertEqual(original, self.digest())

    def test_missing_schema_is_not_created(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE gmail_watches')
        original = self.digest()
        with self.assertRaises(RuntimeError):
            validate_database(self.url, self.key)
        self.assertEqual(original, self.digest())

    def test_missing_database_is_not_created(self):
        missing = self.path.with_name('missing.db')
        with self.assertRaises(FileNotFoundError):
            validate_database('sqlite:///' + missing.as_posix(), self.key)
        self.assertFalse(missing.exists())

    def test_cli_succeeds_without_flask_startup_or_migration(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'DATABASE_URL': self.url, 'GMAIL_TOKEN_ENCRYPTION_KEY': self.key}), \
                patch('sys.argv', ['app.manage', 'validate-db']), \
                patch('dotenv.load_dotenv'), \
                patch('app.manage.create_app', side_effect=AssertionError('must not start app')), \
                contextlib.redirect_stdout(output):
            main()
        self.assertIn('全部业务密文校验通过', output.getvalue())
        self.assertNotIn(self.key, output.getvalue())

    def test_cli_failure_has_nonzero_status_and_no_sensitive_values(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'DATABASE_URL': self.url, 'GMAIL_TOKEN_ENCRYPTION_KEY': 'synthetic-bad-key'}), \
                patch('sys.argv', ['app.manage', 'validate-db']), \
                patch('dotenv.load_dotenv'), contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(1, raised.exception.code)
        self.assertIn('数据库全量校验失败', output.getvalue())
        self.assertNotIn('synthetic-bad-key', output.getvalue())
        self.assertNotIn(str(self.path), output.getvalue())
