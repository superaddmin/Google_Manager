from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.services.schema_migration import MIGRATIONS, initialize_database, validate_schema


def create_legacy_recharge_operations(connection):
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


def dispose_application_database(application):
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


class MigrationConstraintTestCase(unittest.TestCase):
    def test_full_schema_rejects_missing_gmail_tables(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        for table_name in ('gmail_connections', 'gmail_rules', 'gmail_watches',
                           'gmail_task_logs', 'gmail_action_confirmations'):
            with self.subTest(table=table_name):
                engine = create_engine('sqlite:///:memory:')
                try:
                    initialize_database(engine)
                    with engine.begin() as connection:
                        connection.exec_driver_sql(f'DROP TABLE {table_name}')
                    with self.assertRaisesRegex(RuntimeError, table_name):
                        validate_schema(engine)
                finally:
                    engine.dispose()

    def test_initialization_rejects_incomplete_existing_gmail_schema(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        for table_name, column_name in (
            ('gmail_connections', 'scopes'), ('gmail_watches', 'active'),
            ('gmail_action_confirmations', 'reviewed_at'),
        ):
            with self.subTest(table=table_name, column=column_name):
                engine = create_engine('sqlite:///:memory:')
                try:
                    initialize_database(engine)
                    with engine.begin() as connection:
                        connection.exec_driver_sql(
                            f'ALTER TABLE {table_name} RENAME COLUMN {column_name} TO legacy_column'
                        )
                    with self.assertRaisesRegex(RuntimeError, column_name):
                        initialize_database(engine)
                finally:
                    engine.dispose()

    @staticmethod
    def create_legacy_identity_tables(connection, account_rows):
        connection.exec_driver_sql(
            'CREATE TABLE accounts ('
            'id INTEGER PRIMARY KEY, '
            'email VARCHAR(255) NOT NULL UNIQUE, '
            'password TEXT NOT NULL, '
            'recovery TEXT, '
            'secret TEXT, '
            'remark VARCHAR(255), '
            'status VARCHAR(20), '
            'sold_status VARCHAR(20), '
            'created_at DATETIME, '
            'updated_at DATETIME)'
        )
        connection.exec_driver_sql(
            'CREATE TABLE gmail_connections ('
            'id INTEGER PRIMARY KEY, '
            'email VARCHAR(255) NOT NULL UNIQUE, '
            'token_data TEXT NOT NULL, '
            'scopes TEXT NOT NULL, '
            'created_at DATETIME NOT NULL, '
            'updated_at DATETIME NOT NULL)'
        )
        connection.exec_driver_sql(
            'CREATE UNIQUE INDEX uq_legacy_accounts_email ON accounts (email)'
        )
        connection.exec_driver_sql(
            'CREATE UNIQUE INDEX uq_legacy_gmail_connections_email '
            'ON gmail_connections (email)'
        )
        connection.exec_driver_sql(
            'INSERT INTO accounts '
            '(id, email, password, status, sold_status) VALUES (?, ?, ?, ?, ?)',
            account_rows,
        )
        connection.exec_driver_sql(
            'INSERT INTO gmail_connections '
            '(id, email, token_data, scopes, created_at, updated_at) '
            'VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)',
            ('MAILBOX@EXAMPLE.TEST', 'token', 'scope'),
        )

    def test_migration_canonicalizes_identity_emails_and_adds_lock_state(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy-identities.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            try:
                with engine.begin() as connection:
                    self.create_legacy_identity_tables(
                        connection,
                        [(1, 'USER@EXAMPLE.TEST', 'encrypted', 'pro', 'unsold')],
                    )
                with application.app_context():
                    self.assertEqual(len(initialize_database(engine)), len(MIGRATIONS))
                with engine.connect() as connection:
                    account = connection.exec_driver_sql(
                        'SELECT email, pre_lock_status FROM accounts WHERE id = 1'
                    ).one()
                    gmail_email = connection.exec_driver_sql(
                        'SELECT email FROM gmail_connections WHERE id = 1'
                    ).scalar_one()
                self.assertEqual(tuple(account), ('user@example.test', None))
                self.assertEqual(gmail_email, 'mailbox@example.test')
            finally:
                engine.dispose()

    def test_email_canonicalization_collision_rolls_back_without_merging(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'legacy-identity-collision.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            try:
                with engine.begin() as connection:
                    self.create_legacy_identity_tables(
                        connection,
                        [
                            (1, 'USER@EXAMPLE.TEST', 'encrypted-1', 'inactive', 'unsold'),
                            (2, 'user@example.test', 'encrypted-2', 'pro', 'unsold'),
                        ],
                    )
                with application.app_context(), self.assertRaisesRegex(
                    RuntimeError, '规范化后存在重复值'
                ):
                    initialize_database(engine)
                with engine.connect() as connection:
                    emails = connection.exec_driver_sql(
                        'SELECT id, email FROM accounts ORDER BY id'
                    ).all()
                    tables = set(inspect(connection).get_table_names())
                self.assertEqual(
                    [tuple(row) for row in emails],
                    [(1, 'USER@EXAMPLE.TEST'), (2, 'user@example.test')],
                )
                self.assertNotIn('schema_migrations', tables)
            finally:
                engine.dispose()

    def test_partial_unique_index_never_satisfies_full_unique_requirement(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'partial-unique.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                create_legacy_recharge_operations(connection)
                connection.exec_driver_sql(
                    'CREATE UNIQUE INDEX fake_partial_unique '
                    'ON recharge_operations (upstream_task_no) WHERE 0'
                )
                valid_upstream_task_no = 'R' * 128
                connection.exec_driver_sql(
                    'INSERT INTO recharge_operations '
                    '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                    ('LOCAL-1', 'ACTIVE-1', valid_upstream_task_no),
                )
            try:
                with application.app_context():
                    applied = initialize_database(engine)
                self.assertEqual(len(applied), len(MIGRATIONS))

                indexes = {
                    index['name']: index
                    for index in inspect(engine).get_indexes('recharge_operations')
                }
                self.assertIn('fake_partial_unique', indexes)
                self.assertIn('uq_recharge_operations_upstream_task_no', indexes)

                with self.assertRaises(IntegrityError):
                    with engine.begin() as connection:
                        connection.exec_driver_sql(
                            'INSERT INTO recharge_operations '
                            '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                            ('LOCAL-2', 'ACTIVE-2', valid_upstream_task_no),
                        )

                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        'DROP INDEX uq_recharge_operations_upstream_task_no'
                    )
                with self.assertRaises(RuntimeError) as raised:
                    validate_schema(engine, scope='full')
                self.assertIn('recharge_operations', str(raised.exception))
                self.assertIn('upstream_task_no', str(raised.exception))
            finally:
                engine.dispose()

    def test_migration_rejects_noncanonical_upstream_values_without_mutation(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        invalid_values = {
            'padded': ' REMOTE ',
            'c0_control': 'REMOTE\x1fVALUE',
            'del_control': 'REMOTE\x7fVALUE',
            'overlong': 'R' * 129,
        }
        for label, upstream_task_no in invalid_values.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    database_path = Path(directory) / f'{label}.db'
                    engine = create_engine(f'sqlite:///{database_path.as_posix()}')
                    with engine.begin() as connection:
                        create_legacy_recharge_operations(connection)
                        connection.exec_driver_sql(
                            'INSERT INTO recharge_operations '
                            '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                            ('LOCAL-1', 'ACTIVE-1', upstream_task_no),
                        )
                    try:
                        with application.app_context(), self.assertRaisesRegex(
                            RuntimeError, '非规范值'
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
                            ('LOCAL-1', 'ACTIVE-1', upstream_task_no),
                        )
                        self.assertNotIn('schema_migrations', tables)
                    finally:
                        engine.dispose()

    def test_migration_rejects_canonical_collision_without_merging_rows(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'canonical-collision.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            rows = [
                ('LOCAL-1', 'ACTIVE-1', ' REMOTE '),
                ('LOCAL-2', 'ACTIVE-2', 'REMOTE'),
            ]
            with engine.begin() as connection:
                create_legacy_recharge_operations(connection)
                connection.exec_driver_sql(
                    'INSERT INTO recharge_operations '
                    '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                    rows,
                )
            try:
                with application.app_context(), self.assertRaisesRegex(
                    RuntimeError, '非规范值'
                ):
                    initialize_database(engine)
                with engine.connect() as connection:
                    persisted = connection.exec_driver_sql(
                        'SELECT task_no, active_key, upstream_task_no '
                        'FROM recharge_operations ORDER BY task_no'
                    ).all()
                    tables = set(inspect(connection).get_table_names())
                self.assertEqual([tuple(row) for row in persisted], rows)
                self.assertNotIn('schema_migrations', tables)
            finally:
                engine.dispose()

    def test_reinitialization_rejects_noncanonical_value_after_migration_04(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'already-migrated.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            try:
                with application.app_context():
                    self.assertEqual(len(initialize_database(engine)), len(MIGRATIONS))
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        'INSERT INTO recharge_tasks '
                        '(task_no, redeem_code, plan_type, account_email, status) VALUES (?, ?, ?, ?, ?)',
                        ('LOCAL-1', 'PLUS-MIGRATION-FIXTURE', 'PLUS', 'migration@example.test', 'unknown'),
                    )
                    connection.exec_driver_sql(
                        'INSERT INTO recharge_operations '
                        '(task_no, active_key, upstream_task_no) VALUES (?, ?, ?)',
                        ('LOCAL-1', 'ACTIVE-1', ' REMOTE '),
                    )

                with application.app_context(), self.assertRaisesRegex(
                    RuntimeError, 'upstream_task_no 存在非规范值'
                ):
                    initialize_database(engine)

                with engine.connect() as connection:
                    row = connection.exec_driver_sql(
                        'SELECT task_no, active_key, upstream_task_no '
                        'FROM recharge_operations'
                    ).one()
                    migration_count = connection.exec_driver_sql(
                        'SELECT COUNT(*) FROM schema_migrations'
                    ).scalar_one()
                self.assertEqual(tuple(row), ('LOCAL-1', 'ACTIVE-1', ' REMOTE '))
                self.assertEqual(migration_count, len(MIGRATIONS))
            finally:
                engine.dispose()

    def test_full_validation_skips_semantic_query_for_malformed_operation_table(self):
        application = create_app('testing')
        self.addCleanup(dispose_application_database, application)
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / 'malformed-operation.db'
            engine = create_engine(f'sqlite:///{database_path.as_posix()}')
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE recharge_operations (task_no VARCHAR(64) PRIMARY KEY)'
                )
            try:
                with self.assertRaises(RuntimeError) as raised:
                    validate_schema(engine, scope='full')
                message = str(raised.exception)
                self.assertIn('recharge_operations', message)
                self.assertIn('active_key', message)
                self.assertIn('upstream_task_no', message)
                self.assertNotIn('no such column', message)
                with application.app_context(), self.assertRaisesRegex(RuntimeError, 'recharge_operations 缺少列'):
                    initialize_database(engine)
                self.assertEqual(set(inspect(engine).get_table_names()), {'recharge_operations'})
            finally:
                engine.dispose()


if __name__ == '__main__':
    unittest.main()
