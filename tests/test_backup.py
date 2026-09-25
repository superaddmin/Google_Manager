from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text

from deploy.backup_database import _load_key, backup_database, restore_database, verify_backup
from app import create_app, db
from app.config import TestingConfig
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.models.recharge_task import RechargeTask
from app.services.schema_migration import (
    MIGRATIONS,
    apply_schema_migrations,
    encrypt_existing_sensitive_data,
    has_unencrypted_sensitive_data,
    import_all_models,
    initialize_database,
    validate_sensitive_data,
    validate_schema,
)
from app.services.field_encryption import (
    deterministic_decrypt_value,
    deterministic_encrypt_value,
    encrypt_value,
)
from app.worker import acquire_worker_lock


class DatabaseBackupTestCase(unittest.TestCase):
    def test_backup_key_file_is_bounded_regular_and_not_linked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_file = root / 'backup.key'
            key_file.write_bytes(Fernet.generate_key() + b'\n')
            key_file.chmod(0o600)
            self.assertEqual(_load_key(key_file), key_file.read_text(encoding='ascii').strip())

            oversized = root / 'oversized.key'
            oversized.write_bytes(b'x' * 4097)
            oversized.chmod(0o600)
            with self.assertRaises(ValueError):
                _load_key(oversized)

            invalid_encoding = root / 'invalid-encoding.key'
            invalid_encoding.write_bytes(b'private-value-\xff')
            invalid_encoding.chmod(0o600)
            with self.assertRaises(ValueError) as raised:
                _load_key(invalid_encoding)
            self.assertNotIn('private-value', str(raised.exception))
            self.assertNotIn(str(invalid_encoding), str(raised.exception))

            with self.assertRaises(ValueError):
                _load_key(root)

            link = root / 'linked.key'
            try:
                link.symlink_to(key_file)
            except OSError:
                return
            with self.assertRaises(ValueError):
                _load_key(link)

    def test_backup_key_file_rejects_descriptor_identity_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / 'expected.key'
            replacement = root / 'replacement.key'
            expected.write_bytes(Fernet.generate_key())
            replacement.write_bytes(Fernet.generate_key())
            expected.chmod(0o600)
            replacement.chmod(0o600)
            original_open = os.open

            def replaced_open(unused_path, flags):
                return original_open(replacement, flags)

            with patch('deploy.backup_database.os.open', side_effect=replaced_open):
                with self.assertRaises(ValueError):
                    _load_key(expected)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux owner and mode policy')
    def test_backup_key_file_requires_exact_linux_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'backup.key'
            key_file.write_bytes(Fernet.generate_key())
            key_file.chmod(0o640)
            with self.assertRaises(ValueError):
                _load_key(key_file)

            key_file.chmod(0o600)
            file_uid = key_file.stat().st_uid
            with patch('deploy.backup_database.os.geteuid', return_value=file_uid + 1):
                with self.assertRaises(ValueError):
                    _load_key(key_file)

    def test_worker_lock_excludes_second_process_handle_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            first = acquire_worker_lock(directory)
            try:
                with self.assertRaises(RuntimeError):
                    acquire_worker_lock(directory)
            finally:
                first.close()
            subsequent = acquire_worker_lock(directory)
            subsequent.close()

    def test_encrypted_backup_can_be_restored_and_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            restored_path = Path(directory) / 'restored.db'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('synthetic')")
                database.commit()
            backup_database(source, target, key)
            self.assertNotEqual(target.read_bytes()[:16], source.read_bytes()[:16])
            self.assertEqual(verify_backup(target, key)['size'], source.stat().st_size)
            restore_database(target, restored_path, key)
            with closing(sqlite3.connect(restored_path)) as restored:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute('SELECT value FROM fixture').fetchone()[0], 'synthetic')
            with self.assertRaises(ValueError):
                backup_database(source, target, key)
            with self.assertRaises(ValueError):
                backup_database(source, source, key)
            with self.assertRaises(ValueError):
                restore_database(target, restored_path, key)

    def test_tampered_or_wrong_key_backup_never_publishes_restore_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            restored_path = Path(directory) / 'restored.db'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.commit()
            backup_database(source, target, key)
            with self.assertRaises(Exception):
                restore_database(target, restored_path, Fernet.generate_key())
            self.assertFalse(restored_path.exists())
            contents = bytearray(target.read_bytes())
            contents[len(contents) // 2] ^= 1
            target.write_bytes(contents)
            with self.assertRaises(Exception):
                restore_database(target, restored_path, key)
            self.assertFalse(restored_path.exists())

    def test_backup_target_is_invisible_until_atomic_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('complete')")
                database.commit()
            entered_publish = threading.Event()
            allow_publish = threading.Event()
            original_link = __import__('os').link

            def delayed_link(staging, destination):
                entered_publish.set()
                self.assertTrue(allow_publish.wait(5))
                return original_link(staging, destination)

            with patch('deploy.backup_database.os.link', side_effect=delayed_link):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(backup_database, source, target, key)
                    self.assertTrue(entered_publish.wait(5))
                    self.assertFalse(target.exists())
                    allow_publish.set()
                    self.assertEqual(future.result(), target.resolve())
            self.assertGreater(verify_backup(target, key)['size'], 0)

    def test_concurrent_backup_never_overwrites_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('winner')")
                database.commit()
            barrier = threading.Barrier(2)
            original_link = __import__('os').link

            def synchronized_link(staging, destination):
                barrier.wait(timeout=5)
                return original_link(staging, destination)

            def backup_once():
                try:
                    backup_database(source, target, key)
                    return 'published'
                except ValueError:
                    return 'rejected'

            with patch('deploy.backup_database.os.link', side_effect=synchronized_link):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda unused: backup_once(), range(2)))
            self.assertEqual(sorted(results), ['published', 'rejected'])
            self.assertGreater(verify_backup(target, key)['size'], 0)

    def test_restore_target_is_invisible_until_atomic_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            restored_path = Path(directory) / 'restored.db'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('complete')")
                database.commit()
            backup_database(source, target, key)
            entered_publish = threading.Event()
            allow_publish = threading.Event()
            original_link = __import__('os').link

            def delayed_link(staging, destination):
                entered_publish.set()
                self.assertTrue(allow_publish.wait(5))
                return original_link(staging, destination)

            with patch('deploy.backup_database.os.link', side_effect=delayed_link):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(restore_database, target, restored_path, key)
                    self.assertTrue(entered_publish.wait(5))
                    self.assertFalse(restored_path.exists())
                    allow_publish.set()
                    self.assertEqual(future.result(), restored_path.resolve())
            with closing(sqlite3.connect(restored_path)) as restored:
                self.assertEqual(restored.execute('SELECT value FROM fixture').fetchone()[0], 'complete')

    def test_concurrent_restore_never_overwrites_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            target = Path(directory) / 'backup.gmbak'
            restored_path = Path(directory) / 'restored.db'
            key = Fernet.generate_key()
            with closing(sqlite3.connect(source)) as database:
                database.execute('CREATE TABLE fixture (value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('winner')")
                database.commit()
            backup_database(source, target, key)
            barrier = threading.Barrier(2)

            def restore_once():
                barrier.wait()
                try:
                    restore_database(target, restored_path, key)
                    return 'published'
                except ValueError:
                    return 'rejected'

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda unused: restore_once(), range(2)))
            self.assertEqual(sorted(results), ['published', 'rejected'])
            with closing(sqlite3.connect(restored_path)) as restored:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute('SELECT value FROM fixture').fetchone()[0], 'winner')

    def test_versioned_migration_preserves_old_pending_billing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'old.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE gmail_executions ('
                    'key VARCHAR(64) PRIMARY KEY, log_id INTEGER NOT NULL UNIQUE, '
                    'lease_until FLOAT NOT NULL)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE recharge_billing_mutations ('
                    'credential_hash VARCHAR(64) PRIMARY KEY, action VARCHAR(16) NOT NULL, '
                    'operation_id VARCHAR(32) NOT NULL, state VARCHAR(16) NOT NULL, '
                    'started_at FLOAT NOT NULL)'
                )
                connection.exec_driver_sql(
                    "INSERT INTO recharge_billing_mutations VALUES "
                    "('synthetic-hash', 'cancel', 'synthetic-operation', 'pending', 123.0)"
                )
            first = apply_schema_migrations(engine)
            second = apply_schema_migrations(engine)
            self.assertEqual(len(first), len(MIGRATIONS))
            self.assertEqual(second, [])
            inspector = inspect(engine)
            self.assertIn('lease_token', {column['name'] for column in inspector.get_columns('gmail_executions')})
            billing_columns = {
                column['name'] for column in inspector.get_columns('recharge_billing_mutations')
            }
            self.assertTrue({
                'lease_until', 'lease_token', 'updated_at',
                'confirmed_auto_renew', 'result_status',
            }.issubset(billing_columns))
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    'SELECT operation_id, state, started_at, lease_until, updated_at '
                    'FROM recharge_billing_mutations WHERE credential_hash = ?',
                    ('synthetic-hash',),
                ).one()
                self.assertEqual(tuple(row), (
                    'synthetic-operation', 'pending', 123.0, 0.0, 123.0,
                ))
                self.assertEqual(connection.exec_driver_sql(
                    'SELECT COUNT(*) FROM schema_migrations'
                ).scalar_one(), len(MIGRATIONS))
            engine.dispose()

    def test_recharge_operation_migration_rejects_blank_upstream_without_mutation(self):
        application = create_app('testing')
        for upstream_task_no in ('', ' \t\r\n '):
            with self.subTest(upstream_task_no=repr(upstream_task_no)):
                with tempfile.TemporaryDirectory() as directory:
                    database_path = Path(directory) / 'blank-upstream.db'
                    engine = create_engine(f'sqlite:///{database_path.as_posix()}')
                    with engine.begin() as connection:
                        connection.exec_driver_sql(
                            'CREATE TABLE recharge_operations ('
                            'task_no VARCHAR(64) PRIMARY KEY, '
                            'active_key VARCHAR(64), '
                            'upstream_task_no VARCHAR(128))'
                        )
                        connection.exec_driver_sql(
                            'CREATE UNIQUE INDEX uq_recharge_operations_active_key '
                            'ON recharge_operations (active_key)'
                        )
                        connection.exec_driver_sql(
                            'INSERT INTO recharge_operations '
                            '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                            ('LEGACY-1', 'ACTIVE-1', upstream_task_no),
                        )
                    try:
                        with application.app_context(), self.assertRaisesRegex(
                            RuntimeError, '存在空白上游任务号'
                        ):
                            initialize_database(engine)
                        with engine.connect() as connection:
                            row = connection.exec_driver_sql(
                                'SELECT task_no, active_key, upstream_task_no '
                                'FROM recharge_operations'
                            ).one()
                            tables = set(inspect(connection).get_table_names())
                        self.assertEqual(
                            tuple(row),
                            ('LEGACY-1', 'ACTIVE-1', upstream_task_no),
                        )
                        self.assertNotIn('schema_migrations', tables)
                    finally:
                        engine.dispose()

    def test_recharge_operation_migration_accepts_ordinary_upstream_value(self):
        application = create_app('testing')
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'ordinary-upstream.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE recharge_operations ('
                    'task_no VARCHAR(64) PRIMARY KEY, '
                    'active_key VARCHAR(64), '
                    'upstream_task_no VARCHAR(128))'
                )
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX uq_recharge_operations_active_key '
                    'ON recharge_operations (active_key)'
                )
                connection.exec_driver_sql(
                    'INSERT INTO recharge_operations '
                    '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                    ('LEGACY-1', 'ACTIVE-1', 'UPSTREAM-VALID-1'),
                )
            try:
                with application.app_context():
                    applied = initialize_database(engine)
                self.assertEqual(len(applied), len(MIGRATIONS))
                with engine.connect() as connection:
                    row = connection.exec_driver_sql(
                        'SELECT task_no, active_key, upstream_task_no '
                        'FROM recharge_operations'
                    ).one()
                self.assertEqual(
                    tuple(row),
                    ('LEGACY-1', 'ACTIVE-1', 'UPSTREAM-VALID-1'),
                )
                validate_schema(engine, scope='full')
            finally:
                engine.dispose()

    def test_concurrent_migration_calls_serialize_and_second_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'old.db'
            setup_engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with setup_engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE gmail_executions ('
                    'key VARCHAR(64) PRIMARY KEY, log_id INTEGER NOT NULL UNIQUE, '
                    'lease_until FLOAT NOT NULL)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE recharge_billing_mutations ('
                    'credential_hash VARCHAR(64) PRIMARY KEY, action VARCHAR(16) NOT NULL, '
                    'operation_id VARCHAR(32) NOT NULL, state VARCHAR(16) NOT NULL, '
                    'started_at FLOAT NOT NULL)'
                )
            setup_engine.dispose()
            engines = [
                create_engine(f'sqlite:///{database_path.as_posix()}'),
                create_engine(f'sqlite:///{database_path.as_posix()}'),
            ]
            barrier = threading.Barrier(2)

            def migrate(engine):
                barrier.wait()
                return apply_schema_migrations(engine)

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(migrate, engines))
                self.assertEqual(sorted(len(result) for result in results), [0, len(MIGRATIONS)])
            finally:
                for engine in engines:
                    engine.dispose()

    def test_concurrent_full_initialization_is_serialized(self):
        application = create_app('testing')
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'new.db'
            engines = [
                create_engine(f'sqlite:///{database_path.as_posix()}'),
                create_engine(f'sqlite:///{database_path.as_posix()}'),
            ]
            barrier = threading.Barrier(2)

            def initialize(engine):
                barrier.wait()
                with application.app_context():
                    return initialize_database(engine)

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(initialize, engines))
                self.assertEqual(sorted(len(result) for result in results), [0, len(MIGRATIONS)])
                validate_schema(engines[0], scope='full')
            finally:
                for engine in engines:
                    engine.dispose()

    def test_full_initialization_rejects_existing_fake_shape_table(self):
        application = create_app('testing')
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'fake.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE admin_sessions (token_hash VARCHAR(64) PRIMARY KEY)'
                )
            try:
                with application.app_context(), self.assertRaisesRegex(
                    RuntimeError, 'admin_sessions: 缺少列'
                ):
                    initialize_database(engine)
            finally:
                engine.dispose()

    def test_init_db_model_inventory_includes_security_and_reconciliation_tables(self):
        application = create_app('testing')
        with application.app_context():
            db.drop_all()
            initialize_database(db.engine)
            table_names = set(inspect(db.engine).get_table_names())
            self.assertTrue({
                'admin_sessions',
                'recharge_reconciliations',
                'recharge_billing_mutations',
                'recharge_task_access',
            }.issubset(table_names))
            validate_schema(db.engine, scope='full')
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_old_database_upgrade_encrypt_backup_restore_drill(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy.db'
            backup_path = Path(directory) / 'legacy.gmbak'
            restored_path = Path(directory) / 'restored.db'
            key = Fernet.generate_key()
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE gmail_executions ('
                    'key VARCHAR(64) PRIMARY KEY, log_id INTEGER NOT NULL UNIQUE, '
                    'lease_until FLOAT NOT NULL)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE recharge_billing_mutations ('
                    'credential_hash VARCHAR(64) PRIMARY KEY, action VARCHAR(16) NOT NULL, '
                    'operation_id VARCHAR(32) NOT NULL, state VARCHAR(16) NOT NULL, '
                    'started_at FLOAT NOT NULL)'
                )
                connection.exec_driver_sql(
                    "INSERT INTO recharge_billing_mutations VALUES "
                    "('legacy-hash', 'cancel', 'legacy-operation', 'pending', 456.0)"
                )
                connection.exec_driver_sql(
                    'CREATE TABLE accounts ('
                    'id INTEGER PRIMARY KEY, password TEXT, recovery TEXT, secret TEXT)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE account_history ('
                    'id INTEGER PRIMARY KEY, field_name VARCHAR(50), '
                    'old_value TEXT, new_value TEXT)'
                )
                connection.exec_driver_sql(
                    "INSERT INTO accounts VALUES (1, 'legacy-password', '', 'legacy-secret')"
                )
            apply_schema_migrations(engine)
            encrypt_existing_sensitive_data(engine, key, apply=True)
            engine.dispose()
            backup_database(database_path, backup_path, key)
            restore_database(backup_path, restored_path, key)
            with closing(sqlite3.connect(restored_path)) as restored:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute(
                    'SELECT operation_id, state, lease_until FROM recharge_billing_mutations'
                ).fetchone(), ('legacy-operation', 'pending', 0.0))
                password, secret = restored.execute(
                    'SELECT password, secret FROM accounts'
                ).fetchone()
                self.assertTrue(password.startswith('gmenc:v1:'))
                self.assertTrue(secret.startswith('gmenc:v1:'))
                self.assertEqual(restored.execute(
                    'SELECT COUNT(*) FROM schema_migrations'
                ).fetchone()[0], len(MIGRATIONS))

    def test_sensitive_fields_encrypt_new_and_existing_plaintext(self):
        key = Fernet.generate_key().decode('ascii')
        application = create_app('testing')
        application.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = key
        with application.app_context():
            account = Account(
                email='synthetic@example.test',
                password='gmenc:v1:synthetic-password',
                recovery='recovery@example.test',
                secret='JBSWY3DPEHPK3PXP',
            )
            db.session.add(account)
            db.session.flush()
            db.session.add(AccountHistory(
                account_id=account.id,
                field_name='recovery',
                old_value='old@example.test',
                new_value='recovery@example.test',
            ))
            db.session.commit()
            with db.engine.connect() as connection:
                stored = connection.execute(text(
                    'SELECT password, recovery, secret FROM accounts WHERE id = :id'
                ), {'id': account.id}).one()
                self.assertTrue(all(value.startswith('gmenc:v1:') for value in stored))
                history_value = connection.execute(text(
                    'SELECT new_value FROM account_history WHERE account_id = :id'
                ), {'id': account.id}).scalar_one()
                self.assertTrue(history_value.startswith('gmenc:v1:'))
            db.session.expire_all()
            loaded = db.session.get(Account, account.id)
            self.assertEqual(loaded.password, 'gmenc:v1:synthetic-password')
            self.assertEqual(loaded.recovery, 'recovery@example.test')
            self.assertEqual(loaded.secret, 'JBSWY3DPEHPK3PXP')
            self.assertEqual(loaded.history.one().to_dict()['newValue'], 'recovery@example.test')
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'plaintext.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE accounts ('
                    'id INTEGER PRIMARY KEY, password TEXT, recovery TEXT, secret TEXT)'
                )
                connection.exec_driver_sql(
                    'CREATE TABLE account_history ('
                    'id INTEGER PRIMARY KEY, field_name VARCHAR(50), '
                    'old_value TEXT, new_value TEXT)'
                )
                connection.exec_driver_sql(
                    "INSERT INTO accounts VALUES "
                    "(1, 'old-password', 'old-recovery', 'old-secret')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO account_history VALUES "
                    "(1, 'password', 'old-password', 'new-password'), "
                    "(2, 'sold_status', 'unsold', 'sold')"
                )
            dry_run = encrypt_existing_sensitive_data(engine, key)
            self.assertEqual(dry_run, {
                'account_values': 3,
                'history_values': 2,
                'recharge_task_values': 0,
            })
            applied = encrypt_existing_sensitive_data(engine, key, apply=True)
            self.assertEqual(applied, dry_run)
            with engine.connect() as connection:
                first_ciphertext = connection.exec_driver_sql(
                    'SELECT password FROM accounts WHERE id = 1'
                ).scalar_one()
            self.assertEqual(
                encrypt_existing_sensitive_data(engine, key, apply=True),
                {
                    'account_values': 0,
                    'history_values': 0,
                    'recharge_task_values': 0,
                },
            )
            with self.assertRaises(RuntimeError):
                encrypt_existing_sensitive_data(engine, Fernet.generate_key(), apply=True)
            with engine.connect() as connection:
                values = connection.exec_driver_sql(
                    'SELECT password, recovery, secret FROM accounts'
                ).one()
                self.assertTrue(all(value.startswith('gmenc:v1:') for value in values))
                self.assertEqual(values.password, first_ciphertext)
                history_rows = connection.exec_driver_sql(
                    'SELECT field_name, new_value FROM account_history ORDER BY id'
                ).all()
                self.assertTrue(history_rows[0].new_value.startswith('gmenc:v1:'))
                self.assertEqual(tuple(history_rows[1]), ('sold_status', 'sold'))
            engine.dispose()

    def test_recharge_sensitive_fields_support_cross_app_exact_queries(self):
        key = Fernet.generate_key().decode('ascii')
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'shared.db'
            database_url = f'sqlite:///{database_path.as_posix()}'
            with patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', database_url):
                first_app = create_app('testing')
                first_app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = key
                with first_app.app_context():
                    task = RechargeTask(
                        task_no='TK-ENCRYPTED-1',
                        redeem_code='PLUS-SHARED-SYNTHETIC',
                        plan_type='PLUS',
                        account_email='shared@example.test',
                        notify_email='notify@example.test',
                    )
                    db.session.add(task)
                    db.session.commit()
                    with db.engine.connect() as connection:
                        stored = connection.execute(text(
                            'SELECT redeem_code, account_email, notify_email '
                            'FROM recharge_tasks WHERE task_no = :task_no'
                        ), {'task_no': task.task_no}).one()
                        self.assertTrue(stored.redeem_code.startswith('gmdet:v1:'))
                        self.assertTrue(stored.account_email.startswith('gmdet:v1:'))
                        self.assertTrue(stored.notify_email.startswith('gmenc:v1:'))
                    db.session.remove()
                    db.engine.dispose()

                second_app = create_app('testing')
                second_app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = key
                with second_app.app_context():
                    loaded = RechargeTask.query.filter_by(
                        redeem_code='PLUS-SHARED-SYNTHETIC',
                        account_email='shared@example.test',
                    ).one()
                    self.assertEqual(loaded.task_no, 'TK-ENCRYPTED-1')
                    self.assertEqual(loaded.notify_email, 'notify@example.test')
                    db.session.remove()
                    db.engine.dispose()

                wrong_key_app = create_app('testing')
                wrong_key_app.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = (
                    Fernet.generate_key().decode('ascii')
                )
                with wrong_key_app.app_context():
                    with self.assertRaises(RuntimeError):
                        RechargeTask.query.filter_by(task_no='TK-ENCRYPTED-1').one()
                    db.session.remove()
                    db.engine.dispose()

    def test_recharge_plaintext_migration_is_explicit_idempotent_and_queryable(self):
        key = Fernet.generate_key().decode('ascii')
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy-recharge.db'
            database_url = f'sqlite:///{database_path.as_posix()}'
            with patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', database_url):
                application = create_app('testing')
                application.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = key
                with application.app_context():
                    with db.engine.begin() as connection:
                        connection.execute(text(
                            'INSERT INTO recharge_tasks '
                            '(task_no, redeem_code, plan_type, account_email, status, '
                            'is_renewal, is_mock, notify_email) VALUES '
                            '(:task_no, :redeem_code, :plan_type, :account_email, :status, '
                            ':is_renewal, :is_mock, :notify_email)'
                        ), {
                            'task_no': 'TK-LEGACY-1',
                            'redeem_code': 'PLUS-LEGACY-SYNTHETIC',
                            'plan_type': 'PLUS',
                            'account_email': 'legacy@example.test',
                            'status': 'processing',
                            'is_renewal': False,
                            'is_mock': False,
                            'notify_email': 'notify@example.test',
                        })
                    self.assertTrue(has_unencrypted_sensitive_data(db.engine))
                    with self.assertRaises(RuntimeError):
                        validate_sensitive_data(db.engine, key)
                    dry_run = encrypt_existing_sensitive_data(db.engine, key)
                    self.assertEqual(dry_run['recharge_task_values'], 3)
                    self.assertTrue(has_unencrypted_sensitive_data(db.engine))
                    applied = encrypt_existing_sensitive_data(db.engine, key, apply=True)
                    self.assertEqual(applied, dry_run)
                    self.assertFalse(has_unencrypted_sensitive_data(db.engine))
                    validate_sensitive_data(db.engine, key)
                    with self.assertRaises(RuntimeError):
                        validate_sensitive_data(db.engine, Fernet.generate_key())
                    second = encrypt_existing_sensitive_data(db.engine, key, apply=True)
                    self.assertEqual(second, {
                        'account_values': 0,
                        'history_values': 0,
                        'recharge_task_values': 0,
                    })
                    loaded = RechargeTask.query.filter_by(
                        redeem_code='PLUS-LEGACY-SYNTHETIC'
                    ).one()
                    self.assertEqual(loaded.account_email, 'legacy@example.test')
                    with db.engine.connect() as connection:
                        raw = connection.execute(text(
                            'SELECT redeem_code, account_email, notify_email '
                            'FROM recharge_tasks WHERE task_no = :task_no'
                        ), {'task_no': 'TK-LEGACY-1'}).one()
                        self.assertTrue(raw.redeem_code.startswith('gmdet:v1:'))
                        self.assertTrue(raw.account_email.startswith('gmdet:v1:'))
                        self.assertTrue(raw.notify_email.startswith('gmenc:v1:'))
                    db.session.remove()
                    db.engine.dispose()

    def test_deterministic_encryption_separates_domains_and_rejects_malformed_data(self):
        key = Fernet.generate_key()
        value = 'same-sensitive-value'
        redeem_ciphertext = deterministic_encrypt_value(
            value, 'recharge-task:redeem-code', key,
        )
        repeated = deterministic_encrypt_value(
            value, 'recharge-task:redeem-code', key,
        )
        email_ciphertext = deterministic_encrypt_value(
            value, 'recharge-task:account-email', key,
        )
        self.assertEqual(redeem_ciphertext, repeated)
        self.assertNotEqual(redeem_ciphertext, email_ciphertext)
        self.assertEqual(deterministic_encrypt_value(None, 'domain', key), None)
        self.assertEqual(deterministic_encrypt_value('', 'domain', key), '')
        with self.assertRaises(RuntimeError):
            deterministic_decrypt_value(
                redeem_ciphertext, 'recharge-task:account-email', key,
            )
        with self.assertRaises(RuntimeError):
            deterministic_decrypt_value(
                'gmdet:v1:not-valid-base64!', 'recharge-task:redeem-code', key,
            )

    def test_sensitive_migration_and_validation_include_nonpositive_ids(self):
        key = Fernet.generate_key()
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'nonpositive.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        'CREATE TABLE accounts ('
                        'id INTEGER PRIMARY KEY, password TEXT, recovery TEXT, secret TEXT)'
                    )
                    connection.exec_driver_sql(
                        'CREATE TABLE account_history ('
                        'id INTEGER PRIMARY KEY, field_name TEXT, '
                        'old_value TEXT, new_value TEXT)'
                    )
                    connection.exec_driver_sql(
                        'CREATE TABLE recharge_tasks ('
                        'id INTEGER PRIMARY KEY, task_no TEXT, redeem_code TEXT, '
                        'account_email TEXT, notify_email TEXT)'
                    )
                    for identifier in (-1, 0):
                        connection.exec_driver_sql(
                            'INSERT INTO accounts VALUES (?, ?, ?, ?)',
                            (identifier, f'password-{identifier}', '', ''),
                        )
                        connection.exec_driver_sql(
                            'INSERT INTO account_history VALUES (?, ?, ?, ?)',
                            (identifier, 'password', '', f'new-{identifier}'),
                        )
                        connection.exec_driver_sql(
                            'INSERT INTO recharge_tasks VALUES (?, ?, ?, ?, ?)',
                            (
                                identifier,
                                f'TK-{identifier}',
                                f'CODE-{identifier}',
                                f'user-{identifier}@example.test',
                                f'notify-{identifier}@example.test',
                            ),
                        )
                self.assertTrue(has_unencrypted_sensitive_data(engine))
                with self.assertRaises(RuntimeError):
                    validate_sensitive_data(engine, key, batch_size=1)
                counts = encrypt_existing_sensitive_data(
                    engine, key, apply=True, batch_size=1,
                )
                self.assertEqual(counts, {
                    'account_values': 2,
                    'history_values': 2,
                    'recharge_task_values': 6,
                })
                self.assertFalse(has_unencrypted_sensitive_data(engine))
                validate_sensitive_data(engine, key, batch_size=1)
                with self.assertRaises(RuntimeError):
                    validate_sensitive_data(engine, Fernet.generate_key(), batch_size=1)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "UPDATE recharge_tasks SET redeem_code = 'gmdet:v1:malformed' "
                        'WHERE id = 0'
                    )
                with self.assertRaises(RuntimeError):
                    validate_sensitive_data(engine, key, batch_size=1)
            finally:
                engine.dispose()

    def test_sensitive_field_reads_fail_closed_for_plaintext_and_wrong_key(self):
        first_key = Fernet.generate_key().decode('ascii')
        second_key = Fernet.generate_key().decode('ascii')
        application = create_app('testing')
        application.config.update(
            GMAIL_TOKEN_ENCRYPTION_KEY=first_key,
            SENSITIVE_DATA_REQUIRE_ENCRYPTION=True,
        )
        with application.app_context():
            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    'INSERT INTO accounts '
                    '(email, password, recovery, secret, status, sold_status) VALUES '
                    "('plaintext@example.test', 'plaintext-password', '', '', 'inactive', 'unsold')"
                )
            with self.assertRaises(RuntimeError):
                Account.query.filter_by(email='plaintext@example.test').one()
            db.session.rollback()
            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    'UPDATE accounts SET password = :password WHERE email = :email',
                    {
                        'password': 'gmenc:v1:' + Fernet(first_key.encode('ascii')).encrypt(
                            b'synthetic-password'
                        ).decode('ascii'),
                        'email': 'plaintext@example.test',
                    },
                )
            application.config['GMAIL_TOKEN_ENCRYPTION_KEY'] = second_key
            db.session.expire_all()
            with self.assertRaises(RuntimeError):
                Account.query.filter_by(email='plaintext@example.test').one()
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def test_sensitive_field_write_without_required_key_fails_closed(self):
        with self.assertRaises(RuntimeError):
            encrypt_value('synthetic-password')
        application = create_app('testing')
        application.config.update(
            GMAIL_TOKEN_ENCRYPTION_KEY=None,
            SENSITIVE_DATA_REQUIRE_ENCRYPTION=True,
        )
        with application.app_context(), self.assertRaises(RuntimeError):
            encrypt_value('synthetic-password')
