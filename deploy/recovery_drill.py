"""Exercise the shipped backup CLI using only generated, disposable data."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import closing

from cryptography.fernet import Fernet


def require(condition):
    if not condition:
        raise RuntimeError('recovery_drill_check_failed')


def run_drill():
    # Resolve imports from the installed application, never load a dotenv file.
    from sqlalchemy import create_engine
    from app.services.schema_migration import initialize_database, validate_schema
    from app.services.field_encryption import (
        encrypt_value, decrypt_value,
        deterministic_encrypt_value, deterministic_decrypt_value,
    )

    started = time.monotonic()
    backup_cli = Path(__file__).resolve().with_name('backup_database.py')
    with tempfile.TemporaryDirectory(prefix='google-manager-recovery-drill-') as directory:
        root = Path(directory)
        source, restored = root / 'source.db', root / 'restored.db'
        backup, bad_restore = root / 'backup.gmbak', root / 'invalid.db'
        key_file, wrong_key_file = root / 'backup.key', root / 'wrong.key'
        for path in (key_file, wrong_key_file):
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(Fernet.generate_key())
        business_key = Fernet.generate_key()
        engine = create_engine('sqlite:///' + source.as_posix())
        try:
            migrations = initialize_database(engine)
            validate_schema(engine)
        finally:
            engine.dispose()
        expected = ('synthetic-password', 'PLUS-SYNTHETIC-RECOVERY')
        with closing(sqlite3.connect(source)) as connection:
            connection.execute('CREATE TABLE recovery_fixture (id INTEGER PRIMARY KEY, secret TEXT, code TEXT)')
            connection.execute('INSERT INTO recovery_fixture VALUES (1, ?, ?)', (
                encrypt_value(expected[0], business_key),
                deterministic_encrypt_value(expected[1], 'recharge_tasks.redeem_code', business_key),
            ))
            connection.commit()
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()

        def invoke(command, *paths, key=key_file, succeeds=True):
            result = subprocess.run(
                [sys.executable, str(backup_cli), '--key-file', str(key), command,
                 *(str(path) for path in paths)],
                cwd=root, capture_output=True, timeout=60, check=False,
            )
            # Child tracebacks can contain temporary paths; do not relay them.
            require((result.returncode == 0) == succeeds)

        invoke('backup', source, backup)
        invoke('verify', backup)
        require(backup.read_bytes()[:16] != source.read_bytes()[:16])
        invoke('restore', backup, bad_restore, key=wrong_key_file, succeeds=False)
        require(not bad_restore.exists())
        invoke('restore', backup, restored)
        restored_digest = hashlib.sha256(restored.read_bytes()).hexdigest()
        invoke('restore', backup, restored, succeeds=False)
        require(hashlib.sha256(restored.read_bytes()).hexdigest() == restored_digest)
        require(hashlib.sha256(source.read_bytes()).hexdigest() == source_digest)
        with closing(sqlite3.connect(restored.as_uri() + '?mode=ro', uri=True)) as connection:
            require(connection.execute('PRAGMA integrity_check').fetchone() == ('ok',))
            stored = connection.execute('SELECT secret, code FROM recovery_fixture WHERE id=1').fetchone()
            require(decrypt_value(stored[0], business_key, require_encrypted=True) == expected[0])
            require(deterministic_decrypt_value(
                stored[1], 'recharge_tasks.redeem_code', business_key, require_encrypted=True,
            ) == expected[1])
        engine = create_engine('sqlite:///' + restored.as_posix())
        try:
            validate_schema(engine)
        finally:
            engine.dispose()
        if os.name != 'nt':
            require(stat.S_IMODE(restored.stat().st_mode) == 0o600)
            require(stat.S_IMODE(backup.stat().st_mode) == 0o600)
        return {
            'status': 'passed', 'synthetic_only': True,
            'migrations_applied': len(migrations), 'schema_valid': True,
            'ciphertext_readable': True, 'wrong_key_rejected': True,
            'overwrite_rejected': True, 'source_unchanged': True,
            'permissions': 'pending_on_windows' if os.name == 'nt' else '0600',
            'restored_sha256': restored_digest,
            'elapsed_ms': round((time.monotonic() - started) * 1000),
            'production_rpo_rto_validated': False,
        }


def main():
    try:
        report = run_drill()
    except Exception:
        report = {'status': 'failed', 'synthetic_only': True, 'error': 'recovery_drill_failed'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    # ``python -m deploy.recovery_drill`` keeps the installed app on sys.path.
    raise SystemExit(main())
