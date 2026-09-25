from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import pkgutil

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Column, MetaData, String, Table, inspect, select, text

from app.services.field_encryption import (
    DETERMINISTIC_PREFIX,
    ENCRYPTED_PREFIX,
    decrypt_value,
    deterministic_decrypt_value,
    deterministic_encrypt_value,
    encrypt_value,
    is_deterministically_encrypted,
    is_encrypted,
)
from app.utils.email import canonicalize_email


MIGRATION_TABLE = 'schema_migrations'
SQLITE_BUSY_TIMEOUT_MS = 30_000
POSTGRES_MIGRATION_LOCK_ID = 774_662_091
MYSQL_MIGRATION_LOCK_NAME = 'google_manager_schema_migrations'

MIGRATION_REQUIRED_COLUMNS = {
    'gmail_executions': {'key', 'log_id', 'lease_until', 'lease_token'},
    'recharge_billing_mutations': {
        'credential_hash', 'action', 'operation_id', 'state', 'started_at',
        'lease_until', 'lease_token', 'updated_at', 'confirmed_auto_renew',
        'result_status',
    },
}

GMAIL_REQUIRED_COLUMNS = {
    'gmail_connections': {'id', 'email', 'token_data', 'scopes', 'created_at', 'updated_at'},
    'gmail_rules': {
        'id', 'connection_id', 'name', 'query', 'actions', 'enabled', 'priority',
        'requires_confirmation', 'created_at', 'updated_at',
    },
    'gmail_watches': {
        'id', 'connection_id', 'topic_name', 'history_id', 'expiration_at', 'active',
        'created_at', 'updated_at',
    },
    'gmail_task_logs': {
        'id', 'connection_id', 'rule_id', 'message_id', 'action', 'status',
        'request_data', 'result_data', 'error_message', 'created_at', 'completed_at',
    },
    'gmail_action_confirmations': {
        'id', 'task_log_id', 'status', 'reviewer', 'note', 'requested_at', 'reviewed_at',
    },
    'gmail_executions': MIGRATION_REQUIRED_COLUMNS['gmail_executions'],
}

GMAIL_REQUIRED_UNIQUE_KEYS = {
    'gmail_connections': ({'id'}, {'email'}),
    'gmail_rules': ({'id'},),
    'gmail_watches': ({'id'}, {'connection_id'}),
    'gmail_task_logs': ({'id'},),
    'gmail_action_confirmations': ({'id'}, {'task_log_id'}),
    'gmail_executions': ({'key'}, {'log_id'}),
}

FULL_REQUIRED_COLUMNS = {
    **MIGRATION_REQUIRED_COLUMNS,
    **GMAIL_REQUIRED_COLUMNS,
    MIGRATION_TABLE: {'version', 'applied_at'},
    'accounts': {
        'id', 'email', 'password', 'recovery', 'secret', 'remark', 'status',
        'pre_lock_status', 'sold_status', 'created_at', 'updated_at',
    },
    'account_history': {
        'id', 'account_id', 'field_name', 'old_value', 'new_value', 'changed_at',
    },
    'admin_sessions': {
        'token_hash', 'credential_version', 'created_at', 'expires_at', 'revoked_at',
    },
    'recharge_reconciliations': {
        'id', 'task_no', 'resolution', 'request_fingerprint', 'evidence_source',
        'evidence_reference', 'evidence_sha256', 'evidence_fingerprint',
        'evidence_observed_at',
        'upstream_task_no', 'upstream_status', 'previous_status', 'final_status',
        'actor_id', 'created_at',
    },
    'recharge_tasks': {
        'id', 'task_no', 'redeem_code', 'plan_type', 'account_email', 'status',
        'status_text', 'card_last4', 'is_renewal', 'is_mock', 'challenge_token',
        'notify_email', 'notice', 'created_at', 'updated_at',
    },
    'recharge_task_access': {'task_no', 'owner_digest'},
    'recharge_mutation_reconciliations': {
        'operation_id', 'task_no', 'action', 'resolution', 'basis',
        'request_fingerprint', 'evidence_fingerprint', 'evidence_source',
        'evidence_reference', 'evidence_sha256', 'evidence_observed_at',
        'mutation_started_at', 'previous_state', 'final_state',
        'upstream_task_no', 'previous_status', 'final_status', 'actor_id', 'created_at',
    },
    'recharge_operations': {'task_no', 'active_key', 'upstream_task_no'},
    'recharge_mutations': {'task_no', 'action', 'operation_id', 'state', 'started_at'},
    'runtime_jobs': {
        'id', 'kind', 'status', 'active_key', 'request_key', 'payload', 'account_ids',
        'snapshot', 'attempts', 'available_at', 'created_at', 'updated_at',
        'cancel_requested',
    },
    'runtime_states': {'key', 'value'},
    'request_limits': {'key', 'count', 'expires_at'},
    'login_attempts': {'ip', 'attempts', 'last_attempt', 'banned_until'},
    'one_time_tokens': {
        'namespace', 'token_hash', 'binding', 'payload', 'expires_at', 'consumed_at',
    },
}

FULL_REQUIRED_UNIQUE_KEYS = {
    **GMAIL_REQUIRED_UNIQUE_KEYS,
    MIGRATION_TABLE: ({'version'},),
    'accounts': ({'id'}, {'email'}),
    'admin_sessions': ({'token_hash'},),
    'recharge_billing_mutations': ({'credential_hash'},),
    'recharge_reconciliations': ({'id'}, {'task_no'}, {'evidence_fingerprint'}),
    'recharge_tasks': ({'id'}, {'task_no'}),
    'recharge_task_access': ({'task_no'},),
    'recharge_operations': ({'task_no'}, {'active_key'}, {'upstream_task_no'}),
    'recharge_mutations': ({'task_no'},),
    'recharge_mutation_reconciliations': ({'operation_id'}, {'evidence_fingerprint'}),
    'runtime_jobs': ({'id'}, {'active_key'}, {'request_key'}),
    'runtime_states': ({'key'},),
    'request_limits': ({'key'},),
    'login_attempts': ({'ip'},),
    'one_time_tokens': ({'namespace', 'token_hash'},),
}


def import_all_models():
    """Load every model module before SQLAlchemy creates missing tables."""
    import app.models as model_package

    for module in pkgutil.iter_modules(model_package.__path__, model_package.__name__ + '.'):
        importlib.import_module(module.name)


def _quoted(connection, identifier):
    return connection.dialect.identifier_preparer.quote(identifier)


def _columns(connection, table_name):
    return {column['name'] for column in inspect(connection).get_columns(table_name)}


def _add_missing_columns(connection, table_name, definitions):
    tables = set(inspect(connection).get_table_names())
    if table_name not in tables:
        raise RuntimeError(f'数据库缺少迁移目标表: {table_name}')
    existing = _columns(connection, table_name)
    table_sql = _quoted(connection, table_name)
    for column_name, column_type in definitions:
        if column_name in existing:
            continue
        column_sql = _quoted(connection, column_name)
        connection.execute(text(
            f'ALTER TABLE {table_sql} ADD COLUMN {column_sql} {column_type}'
        ))
        existing.add(column_name)


def _migrate_gmail_execution_lease_token(connection):
    _add_missing_columns(connection, 'gmail_executions', (
        ('lease_token', 'VARCHAR(32)'),
    ))


def _migrate_recharge_billing_mutation_lease(connection):
    _add_missing_columns(connection, 'recharge_billing_mutations', (
        ('lease_until', 'FLOAT'),
        ('lease_token', 'VARCHAR(32)'),
        ('updated_at', 'FLOAT'),
        ('confirmed_auto_renew', 'BOOLEAN'),
        ('result_status', 'VARCHAR(128)'),
    ))
    table_sql = _quoted(connection, 'recharge_billing_mutations')
    connection.execute(text(
        f'UPDATE {table_sql} SET updated_at = started_at WHERE updated_at IS NULL'
    ))
    # A pre-lease pending operation must be reconciled before a new intent is accepted.
    connection.execute(text(
        f"UPDATE {table_sql} SET lease_until = 0 "
        "WHERE state = 'pending' AND lease_until IS NULL"
    ))


def _expand_account_sensitive_columns(connection):
    dialect = connection.dialect.name
    if dialect == 'postgresql':
        table_sql = _quoted(connection, 'accounts')
        for column_name in ('password', 'recovery', 'secret'):
            connection.execute(text(
                f'ALTER TABLE {table_sql} ALTER COLUMN '
                f'{_quoted(connection, column_name)} TYPE TEXT'
            ))
    elif dialect in {'mysql', 'mariadb'}:
        table_sql = _quoted(connection, 'accounts')
        connection.execute(text(
            f'ALTER TABLE {table_sql} MODIFY COLUMN '
            f'{_quoted(connection, "password")} TEXT NOT NULL'
        ))
        for column_name in ('recovery', 'secret'):
            connection.execute(text(
                f'ALTER TABLE {table_sql} MODIFY COLUMN '
                f'{_quoted(connection, column_name)} TEXT NULL'
            ))
    elif dialect != 'sqlite':
        raise RuntimeError(f'不支持为 {dialect} 数据库扩展敏感字段列类型')


def _evidence_fingerprint(source, reference, evidence_sha256):
    normalized = json.dumps(
        [source.strip(), reference.strip(), evidence_sha256.strip().lower()],
        ensure_ascii=False,
        separators=(',', ':'),
    )
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def _has_noncanonical_upstream_task_no(connection):
    result = connection.execute(text(
        'SELECT upstream_task_no FROM recharge_operations '
        'WHERE upstream_task_no IS NOT NULL'
    ))
    try:
        return any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            for value in result.scalars()
        )
    finally:
        result.close()


def _create_unique_index(connection, table_name, column_name, index_name):
    if frozenset({column_name}) in _unique_keys(connection, table_name):
        return
    connection.execute(text(
        f'CREATE UNIQUE INDEX {_quoted(connection, index_name)} '
        f'ON {_quoted(connection, table_name)} ({_quoted(connection, column_name)})'
    ))


def _migrate_recharge_cross_process_uniques(connection):
    tables = set(inspect(connection).get_table_names())
    if 'recharge_operations' in tables:
        missing = FULL_REQUIRED_COLUMNS['recharge_operations'] - _columns(connection, 'recharge_operations')
        if missing:
            raise RuntimeError(
                'recharge_operations 缺少列: ' + ', '.join(sorted(missing))
            )
        if _has_noncanonical_upstream_task_no(connection):
            raise RuntimeError(
                'recharge_operations 存在空白上游任务号或其他非规范值，必须先人工核对'
            )
        duplicate_upstream = connection.execute(text(
            'SELECT 1 FROM recharge_operations '
            'WHERE upstream_task_no IS NOT NULL '
            'GROUP BY upstream_task_no HAVING COUNT(*) > 1 LIMIT 1'
        )).first()
        if duplicate_upstream is not None:
            raise RuntimeError('recharge_operations 存在重复上游任务号，必须先人工核对')
        _create_unique_index(
            connection,
            'recharge_operations',
            'upstream_task_no',
            'uq_recharge_operations_upstream_task_no',
        )

    if 'recharge_reconciliations' in tables:
        columns = _columns(connection, 'recharge_reconciliations')
        if 'evidence_fingerprint' not in columns:
            connection.execute(text(
                'ALTER TABLE recharge_reconciliations ADD COLUMN '
                "evidence_fingerprint VARCHAR(64) NOT NULL DEFAULT ''"
            ))
        rows = connection.execute(text(
            'SELECT id, evidence_source, evidence_reference, evidence_sha256, '
            'evidence_fingerprint FROM recharge_reconciliations ORDER BY id'
        )).mappings().all()
        fingerprints = set()
        for row in rows:
            values = (row['evidence_source'], row['evidence_reference'], row['evidence_sha256'])
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise RuntimeError('recharge_reconciliations 存在无效证据字段，无法迁移')
            fingerprint = _evidence_fingerprint(*values)
            if fingerprint in fingerprints:
                raise RuntimeError('recharge_reconciliations 存在重复证据，必须先人工核对')
            fingerprints.add(fingerprint)
            existing = row['evidence_fingerprint']
            if existing not in (None, '', fingerprint):
                raise RuntimeError('recharge_reconciliations 证据指纹与证据字段不一致')
            if existing != fingerprint:
                connection.execute(text(
                    'UPDATE recharge_reconciliations SET evidence_fingerprint = :fingerprint '
                    'WHERE id = :id'
                ), {'fingerprint': fingerprint, 'id': row['id']})
        _create_unique_index(
            connection,
            'recharge_reconciliations',
            'evidence_fingerprint',
            'uq_recharge_reconciliations_evidence_fingerprint',
        )


def _expand_recharge_sensitive_columns(connection):
    if 'recharge_tasks' not in set(inspect(connection).get_table_names()):
        return
    dialect = connection.dialect.name
    if dialect == 'postgresql':
        table_sql = _quoted(connection, 'recharge_tasks')
        for column_name in ('redeem_code', 'account_email', 'notify_email'):
            connection.execute(text(
                f'ALTER TABLE {table_sql} ALTER COLUMN '
                f'{_quoted(connection, column_name)} TYPE TEXT'
            ))
    elif dialect in {'mysql', 'mariadb'}:
        raise RuntimeError(
            'MySQL/MariaDB 的充值敏感字段索引迁移需要单独完成容量与索引长度验收'
        )
    elif dialect != 'sqlite':
        raise RuntimeError(f'不支持为 {dialect} 数据库扩展充值敏感字段列类型')


def _migrate_email_canonicalization(connection):
    for table_name in ('accounts', 'gmail_connections'):
        if table_name not in set(inspect(connection).get_table_names()):
            continue
        if 'email' not in _columns(connection, table_name):
            continue
        table_sql = _quoted(connection, table_name)
        rows = connection.execute(text(
            f'SELECT id, email FROM {table_sql} ORDER BY id'
        )).mappings().all()
        seen = {}
        updates = []
        for row in rows:
            try:
                canonical = canonicalize_email(row['email'])
            except ValueError as error:
                raise RuntimeError(f'{table_name}.email 存在无效邮箱值，无法迁移') from error
            if not canonical:
                raise RuntimeError(f'{table_name}.email 存在空值，无法迁移')
            previous_id = seen.get(canonical)
            if previous_id is not None and previous_id != row['id']:
                raise RuntimeError(f'{table_name}.email 规范化后存在重复值，必须先人工核对')
            seen[canonical] = row['id']
            if row['email'] != canonical:
                updates.append((row['id'], canonical))
        for row_id, canonical in updates:
            connection.execute(text(
                f'UPDATE {table_sql} SET email = :email WHERE id = :id'
            ), {'email': canonical, 'id': row_id})


def _migrate_account_pre_lock_status(connection):
    if 'accounts' not in set(inspect(connection).get_table_names()):
        return
    _add_missing_columns(connection, 'accounts', (
        ('pre_lock_status', 'VARCHAR(20)'),
    ))


def _migrate_cdk_catalog(connection):
    from app import db
    import app.models.cdk
    tables = [table for name, table in db.metadata.tables.items() if name.startswith('cdk_')]
    db.metadata.create_all(bind=connection, tables=tables)


MIGRATIONS = (
    ('20260920_01_gmail_execution_lease_token', _migrate_gmail_execution_lease_token),
    ('20260920_02_recharge_billing_mutation_lease', _migrate_recharge_billing_mutation_lease),
    ('20260920_03_account_sensitive_columns', _expand_account_sensitive_columns),
    ('20260920_04_recharge_cross_process_uniques', _migrate_recharge_cross_process_uniques),
    ('20260920_05_recharge_sensitive_columns', _expand_recharge_sensitive_columns),
    ('20260924_06_email_canonicalization', _migrate_email_canonicalization),
    ('20260924_07_account_pre_lock_status', _migrate_account_pre_lock_status),
    ('20260925_08_cdk_catalog', _migrate_cdk_catalog),
)


def _migration_table(metadata):
    return Table(
        MIGRATION_TABLE,
        metadata,
        Column('version', String(80), primary_key=True),
        Column('applied_at', String(40), nullable=False),
    )


@contextmanager
def _locked_schema_connection(engine):
    connection = engine.connect()
    dialect = connection.dialect.name
    mysql_lock_acquired = False
    try:
        if dialect == 'sqlite':
            connection.exec_driver_sql(f'PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}')
            connection.commit()
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        elif dialect == 'postgresql':
            connection.begin()
            connection.execute(
                text('SELECT pg_advisory_xact_lock(:lock_id)'),
                {'lock_id': POSTGRES_MIGRATION_LOCK_ID},
            )
        elif dialect in {'mysql', 'mariadb'}:
            result = connection.execute(
                text('SELECT GET_LOCK(:lock_name, 30)'),
                {'lock_name': MYSQL_MIGRATION_LOCK_NAME},
            ).scalar_one()
            if result != 1:
                raise RuntimeError('等待数据库迁移锁超时')
            mysql_lock_acquired = True
            connection.commit()
            connection.begin()
        else:
            connection.begin()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if mysql_lock_acquired:
            try:
                connection.execute(
                    text('SELECT RELEASE_LOCK(:lock_name)'),
                    {'lock_name': MYSQL_MIGRATION_LOCK_NAME},
                )
                connection.commit()
            except Exception:
                connection.rollback()
        connection.close()


def _apply_schema_migrations(connection):
    metadata = MetaData()
    migration_table = _migration_table(metadata)
    applied_now = []
    migration_table.create(bind=connection, checkfirst=True)
    applied = set(connection.execute(select(migration_table.c.version)).scalars())
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        migration(connection)
        connection.execute(migration_table.insert().values(
            version=version,
            applied_at=datetime.now(timezone.utc).isoformat(),
        ))
        applied_now.append(version)
    return applied_now


def apply_schema_migrations(engine):
    """Apply migrations under a database-level lock and validate their scope."""
    with _locked_schema_connection(engine) as connection:
        applied_now = _apply_schema_migrations(connection)
        validate_schema(connection, scope='migration')
    return applied_now


def _unique_keys(connection, table_name):
    inspector = inspect(connection)
    keys = set()
    primary_key = inspector.get_pk_constraint(table_name).get('constrained_columns') or []
    if primary_key:
        keys.add(frozenset(primary_key))
    for constraint in inspector.get_unique_constraints(table_name):
        columns = constraint.get('column_names') or []
        if columns:
            keys.add(frozenset(columns))
    for index in inspector.get_indexes(table_name):
        columns = index.get('column_names') or []
        dialect_options = index.get('dialect_options') or {}
        has_predicate = any(
            option.endswith('_where') and value is not None
            for option, value in dialect_options.items()
        )
        if index.get('unique') and columns and not has_predicate:
            keys.add(frozenset(columns))
    return keys


def validate_schema(bind, scope='full'):
    """Validate either migration targets or the complete production schema."""
    if scope not in {'migration', 'gmail', 'full'}:
        raise ValueError('未知数据库结构校验范围')
    required = {
        'migration': MIGRATION_REQUIRED_COLUMNS,
        'gmail': GMAIL_REQUIRED_COLUMNS,
        'full': FULL_REQUIRED_COLUMNS,
    }[scope]
    unique_keys = {
        'migration': {},
        'gmail': GMAIL_REQUIRED_UNIQUE_KEYS,
        'full': FULL_REQUIRED_UNIQUE_KEYS,
    }[scope]
    if scope == 'full':
        from app import db
        import app.models.cdk
        required = dict(required)
        unique_keys = dict(unique_keys)
        from sqlalchemy import UniqueConstraint
        for name, table in db.metadata.tables.items():
            if name.startswith('cdk_'):
                required[name] = {column.name for column in table.columns}
                unique_keys[name] = tuple(
                    {column.name for column in constraint.columns}
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint) or constraint is table.primary_key
                )
    close_connection = not hasattr(bind, 'exec_driver_sql')
    connection = bind.connect() if close_connection else bind
    try:
        tables = set(inspect(connection).get_table_names())
        errors = []
        for table_name, required_columns in required.items():
            if table_name not in tables:
                errors.append(f'{table_name}: 表不存在')
                continue
            missing = sorted(required_columns - _columns(connection, table_name))
            if missing:
                errors.append(f'{table_name}: 缺少列 {", ".join(missing)}')
        for table_name, expected_keys in unique_keys.items():
            if table_name not in tables:
                continue
            actual_keys = _unique_keys(connection, table_name)
            for expected_key in expected_keys:
                if frozenset(expected_key) not in actual_keys:
                    errors.append(
                        f'{table_name}: 缺少唯一键 ({", ".join(sorted(expected_key))})'
                    )
        if scope == 'full':
            operation_columns = (
                _columns(connection, 'recharge_operations')
                if 'recharge_operations' in tables else set()
            )
            if (
                FULL_REQUIRED_COLUMNS['recharge_operations'].issubset(operation_columns)
                and _has_noncanonical_upstream_task_no(connection)
            ):
                errors.append('recharge_operations: upstream_task_no 存在非规范值')
        if errors:
            raise RuntimeError('数据库结构校验失败: ' + '; '.join(errors))
    finally:
        if close_connection:
            connection.close()


def initialize_database(engine):
    """Create and migrate the full schema in one serialized transaction."""
    from app import db

    import_all_models()
    with _locked_schema_connection(engine) as connection:
        db.metadata.create_all(bind=connection)
        applied_now = _apply_schema_migrations(connection)
        validate_schema(connection, scope='full')
    return applied_now


def probe_gmail_schema(engine):
    with engine.connect() as connection:
        for table_name, columns in GMAIL_REQUIRED_COLUMNS.items():
            table_sql = _quoted(connection, table_name)
            selected = ', '.join(f'{table_sql}.{_quoted(connection, column)}' for column in sorted(columns))
            connection.execute(text(
                f'SELECT {selected} FROM {table_sql} WHERE 1 = 0'
            )).close()


def encrypt_existing_sensitive_data(engine, encryption_key, apply=False, batch_size=500):
    """Inventory or encrypt existing plaintext without running during init-db."""
    if not encryption_key:
        raise RuntimeError('存量敏感数据加密需要长期 Fernet 密钥')
    metadata = MetaData()
    accounts = Table('accounts', metadata, autoload_with=engine)
    history = Table('account_history', metadata, autoload_with=engine)
    available_tables = set(inspect(engine).get_table_names())
    recharge_tasks = (
        Table('recharge_tasks', metadata, autoload_with=engine)
        if 'recharge_tasks' in available_tables else None
    )
    counts = {
        'account_values': 0,
        'history_values': 0,
        'recharge_task_values': 0,
    }

    def protect_table(connection, table, fields, predicate=None, counter='account_values'):
        last_id = None
        while True:
            statement = select(table.c.id, *[table.c[name] for name in fields])
            if last_id is not None:
                statement = statement.where(table.c.id > last_id)
            statement = statement.order_by(table.c.id).limit(batch_size)
            if predicate is not None:
                statement = statement.where(predicate)
            rows = connection.execute(statement).mappings().all()
            if not rows:
                break
            for row in rows:
                last_id = row['id']
                updates = {}
                for name in fields:
                    value = row[name]
                    if value in (None, ''):
                        continue
                    if is_encrypted(value):
                        decrypt_value(value, encryption_key, require_encrypted=False)
                        continue
                    updates[name] = encrypt_value(value, encryption_key)
                counts[counter] += len(updates)
                if apply and updates:
                    connection.execute(
                        table.update().where(table.c.id == row['id']).values(**updates)
                    )

    with engine.begin() as connection:
        protect_table(connection, accounts, ('password', 'recovery', 'secret'))
        protect_table(
            connection,
            history,
            ('old_value', 'new_value'),
            history.c.field_name.in_(('password', 'recovery', 'secret')),
            'history_values',
        )
        if recharge_tasks is not None:
            last_id = None
            while True:
                statement = select(
                    recharge_tasks.c.id,
                    recharge_tasks.c.redeem_code,
                    recharge_tasks.c.account_email,
                    recharge_tasks.c.notify_email,
                )
                if last_id is not None:
                    statement = statement.where(recharge_tasks.c.id > last_id)
                rows = connection.execute(
                    statement.order_by(recharge_tasks.c.id).limit(batch_size)
                ).mappings().all()
                if not rows:
                    break
                for row in rows:
                    last_id = row['id']
                    updates = {}
                    for field_name, domain in (
                        ('redeem_code', 'recharge-task:redeem-code'),
                        ('account_email', 'recharge-task:account-email'),
                    ):
                        value = row[field_name]
                        if value in (None, ''):
                            continue
                        if is_deterministically_encrypted(value):
                            deterministic_decrypt_value(
                                value, domain, encryption_key, require_encrypted=False,
                            )
                        elif is_encrypted(value):
                            raise RuntimeError(
                                f'recharge_tasks.{field_name} 使用了错误的密文格式'
                            )
                        else:
                            updates[field_name] = deterministic_encrypt_value(
                                value, domain, encryption_key,
                            )
                    notify_email = row['notify_email']
                    if notify_email not in (None, ''):
                        if is_encrypted(notify_email):
                            decrypt_value(
                                notify_email, encryption_key, require_encrypted=False,
                            )
                        elif is_deterministically_encrypted(notify_email):
                            raise RuntimeError(
                                'recharge_tasks.notify_email 使用了错误的密文格式'
                            )
                        else:
                            updates['notify_email'] = encrypt_value(
                                notify_email, encryption_key,
                            )
                    counts['recharge_task_values'] += len(updates)
                    if apply and updates:
                        connection.execute(
                            recharge_tasks.update()
                            .where(recharge_tasks.c.id == row['id'])
                            .values(**updates)
                        )
    return counts


def has_unencrypted_sensitive_data(engine):
    """Return a boolean plaintext signal without loading or returning field values."""
    random_prefix_pattern = ENCRYPTED_PREFIX + '%'
    deterministic_prefix_pattern = DETERMINISTIC_PREFIX + '%'
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        if not {'accounts', 'account_history', 'recharge_tasks'}.issubset(tables):
            raise RuntimeError('敏感数据明文检查所需表不存在')
        account_plaintext = connection.execute(text(
            'SELECT 1 FROM accounts WHERE '
            "(password IS NOT NULL AND password <> '' AND password NOT LIKE :prefix) OR "
            "(recovery IS NOT NULL AND recovery <> '' AND recovery NOT LIKE :prefix) OR "
            "(secret IS NOT NULL AND secret <> '' AND secret NOT LIKE :prefix) "
            'LIMIT 1'
        ), {'prefix': random_prefix_pattern}).first()
        if account_plaintext is not None:
            return True
        history_plaintext = connection.execute(text(
            'SELECT 1 FROM account_history WHERE '
            "field_name IN ('password', 'recovery', 'secret') AND ("
            "(old_value IS NOT NULL AND old_value <> '' AND old_value NOT LIKE :prefix) OR "
            "(new_value IS NOT NULL AND new_value <> '' AND new_value NOT LIKE :prefix)) "
            'LIMIT 1'
        ), {'prefix': random_prefix_pattern}).first()
        if history_plaintext is not None:
            return True
        recharge_plaintext = connection.execute(text(
            'SELECT 1 FROM recharge_tasks WHERE '
            '(redeem_code IS NOT NULL AND redeem_code <> \'\' '
            'AND redeem_code NOT LIKE :deterministic_prefix) OR '
            '(account_email IS NOT NULL AND account_email <> \'\' '
            'AND account_email NOT LIKE :deterministic_prefix) OR '
            '(notify_email IS NOT NULL AND notify_email <> \'\' '
            'AND notify_email NOT LIKE :random_prefix) LIMIT 1'
        ), {
            'deterministic_prefix': deterministic_prefix_pattern,
            'random_prefix': random_prefix_pattern,
        }).first()
        return recharge_plaintext is not None


def validate_sensitive_data(engine, encryption_key, batch_size=500, max_rows=None):
    """Validate all sensitive values, or a bounded sample per table when requested."""
    if not encryption_key:
        raise RuntimeError('敏感数据校验需要长期加密密钥')
    if max_rows is not None and (isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1):
        raise ValueError('敏感数据抽样上限必须是正整数')
    metadata = MetaData()
    available_tables = set(inspect(engine).get_table_names())
    required_tables = {'accounts', 'account_history', 'recharge_tasks'}
    if not required_tables.issubset(available_tables):
        raise RuntimeError('敏感数据校验所需表不存在')
    accounts = Table('accounts', metadata, autoload_with=engine)
    history = Table('account_history', metadata, autoload_with=engine)
    recharge_tasks = Table('recharge_tasks', metadata, autoload_with=engine)
    token_cipher = Fernet(encryption_key.encode('ascii') if isinstance(encryption_key, str) else encryption_key)

    def validate_tokens(connection, table_name, field_name):
        table = Table(table_name, metadata, autoload_with=connection)
        last_id = None
        checked = 0
        while max_rows is None or checked < max_rows:
            statement = select(table.c.id, table.c[field_name])
            if last_id is not None:
                statement = statement.where(table.c.id > last_id)
            limit = batch_size if max_rows is None else min(batch_size, max_rows - checked)
            rows = connection.execute(statement.order_by(table.c.id).limit(limit)).mappings().all()
            if not rows:
                return
            checked += len(rows)
            for row in rows:
                last_id = row['id']
                try:
                    payload = json.loads(token_cipher.decrypt(row[field_name].encode('ascii')))
                    if not isinstance(payload, dict):
                        raise ValueError('invalid payload')
                except (InvalidToken, ValueError, TypeError, AttributeError) as error:
                    raise RuntimeError(f'{table_name}.{field_name} 无法使用当前业务密钥读取') from error

    def validate_random(connection, table, fields):
        last_id = None
        checked = 0
        while max_rows is None or checked < max_rows:
            statement = select(table.c.id, *[table.c[name] for name in fields])
            if last_id is not None:
                statement = statement.where(table.c.id > last_id)
            limit = batch_size if max_rows is None else min(batch_size, max_rows - checked)
            rows = connection.execute(
                statement.order_by(table.c.id).limit(limit)
            ).mappings().all()
            if not rows:
                return
            checked += len(rows)
            for row in rows:
                last_id = row['id']
                for field_name in fields:
                    value = row[field_name]
                    if value in (None, ''):
                        continue
                    if not is_encrypted(value):
                        raise RuntimeError(f'{table.name}.{field_name} 存在未加密值')
                    decrypt_value(value, encryption_key, require_encrypted=True)

    def validate_history(connection):
        last_id = None
        checked = 0
        while max_rows is None or checked < max_rows:
            statement = select(
                history.c.id,
                history.c.field_name,
                history.c.old_value,
                history.c.new_value,
            )
            if last_id is not None:
                statement = statement.where(history.c.id > last_id)
            limit = batch_size if max_rows is None else min(batch_size, max_rows - checked)
            rows = connection.execute(
                statement.order_by(history.c.id).limit(limit)
            ).mappings().all()
            if not rows:
                return
            checked += len(rows)
            for row in rows:
                last_id = row['id']
                if row['field_name'] not in ('password', 'recovery', 'secret'):
                    continue
                for field_name in ('old_value', 'new_value'):
                    value = row[field_name]
                    if value in (None, ''):
                        continue
                    if not is_encrypted(value):
                        raise RuntimeError(f'account_history.{field_name} 存在未加密值')
                    decrypt_value(value, encryption_key, require_encrypted=True)

    def validate_recharge(connection):
        last_id = None
        deterministic_fields = (
            ('redeem_code', 'recharge-task:redeem-code'),
            ('account_email', 'recharge-task:account-email'),
        )
        checked = 0
        while max_rows is None or checked < max_rows:
            statement = select(
                recharge_tasks.c.id,
                recharge_tasks.c.redeem_code,
                recharge_tasks.c.account_email,
                recharge_tasks.c.notify_email,
            )
            if last_id is not None:
                statement = statement.where(recharge_tasks.c.id > last_id)
            limit = batch_size if max_rows is None else min(batch_size, max_rows - checked)
            rows = connection.execute(
                statement.order_by(recharge_tasks.c.id).limit(limit)
            ).mappings().all()
            if not rows:
                return
            checked += len(rows)
            for row in rows:
                last_id = row['id']
                for field_name, domain in deterministic_fields:
                    value = row[field_name]
                    if value in (None, ''):
                        continue
                    if not is_deterministically_encrypted(value):
                        raise RuntimeError(f'recharge_tasks.{field_name} 存在未加密值')
                    deterministic_decrypt_value(value, domain, encryption_key, require_encrypted=True)
                value = row['notify_email']
                if value in (None, ''):
                    continue
                if not is_encrypted(value):
                    raise RuntimeError('recharge_tasks.notify_email 存在未加密值')
                decrypt_value(value, encryption_key, require_encrypted=True)

    with engine.connect() as connection:
        validate_random(connection, accounts, ('password', 'recovery', 'secret'))
        validate_history(connection)
        validate_recharge(connection)
        for table_name, field_name in (('gmail_connections', 'token_data'), ('runtime_jobs', 'payload')):
            if table_name in available_tables:
                validate_tokens(connection, table_name, field_name)
    from app.services.cdk_maintenance import validate_cdk_ciphertexts
    validate_cdk_ciphertexts(engine, max_rows=max_rows)
