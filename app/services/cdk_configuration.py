import os
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash

from app.services.cdk_crypto import keyring


@event.listens_for(Engine, 'connect')
def configure_sqlite(connection, _record):
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.close()


def configure_cdk(application):
    application.config['CDK_ENABLED'] = os.environ.get('CDK_ENABLED') == '1' and not application.testing
    application.config['CDK_ACTIVE_KEY_ID'] = os.environ.get('CDK_ACTIVE_KEY_ID', 'v1')
    for name in ('ENCRYPTION_KEYS', 'LOOKUP_KEYS', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_FROM', 'SMTP_USERNAME', 'SMTP_PASSWORD'):
        application.config['CDK_' + name] = os.environ.get('CDK_' + name, '')
    application.extensions['cdk_dummy_password'] = generate_password_hash('unusable-account-' + os.urandom(16).hex())
    if application.config['CDK_ENABLED']:
        with application.app_context():
            encryption, lookup = keyring('ENCRYPTION'), keyring('LOOKUP')
            active = application.config['CDK_ACTIVE_KEY_ID']
            if (active not in encryption or active not in lookup or set(encryption.values()) & set(lookup.values())
                    or application.config.get('GMAIL_TOKEN_ENCRYPTION_KEY') in [*encryption.values(), *lookup.values()]):
                raise RuntimeError('CDK 加密与查询密钥必须独立且包含当前版本')
