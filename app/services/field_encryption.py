import base64

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app, has_app_context
from sqlalchemy.types import Text, TypeDecorator


ENCRYPTED_PREFIX = 'gmenc:v1:'
DETERMINISTIC_PREFIX = 'gmdet:v1:'


def _configured_key():
    if not has_app_context():
        return None
    return current_app.config.get('GMAIL_TOKEN_ENCRYPTION_KEY')


def _fernet(encryption_key=None):
    key = encryption_key or _configured_key()
    if not key:
        return None
    if isinstance(key, str):
        try:
            key = key.encode('ascii')
        except UnicodeError as error:
            raise RuntimeError('敏感字段加密密钥格式无效') from error
    try:
        return Fernet(key)
    except (TypeError, ValueError) as error:
        raise RuntimeError('敏感字段加密密钥格式无效') from error


def _master_key(encryption_key=None):
    key = encryption_key or _configured_key()
    if not key:
        return None
    if isinstance(key, str):
        try:
            key = key.encode('ascii')
        except UnicodeError as error:
            raise RuntimeError('敏感字段加密密钥格式无效') from error
    try:
        Fernet(key)
        raw_key = base64.urlsafe_b64decode(key)
    except (TypeError, ValueError) as error:
        raise RuntimeError('敏感字段加密密钥格式无效') from error
    return raw_key


def _missing_key_fails_closed():
    return (
        not has_app_context()
        or current_app.config.get('SENSITIVE_DATA_REQUIRE_ENCRYPTION')
    )


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def encrypt_value(value, encryption_key=None):
    if value is None or value == '':
        return value
    cipher = _fernet(encryption_key)
    if cipher is None:
        if _missing_key_fails_closed():
            raise RuntimeError('缺少敏感字段加密密钥，拒绝写入明文')
        return value
    token = cipher.encrypt(str(value).encode('utf-8')).decode('ascii')
    return ENCRYPTED_PREFIX + token


def decrypt_value(value, encryption_key=None, require_encrypted=None):
    if not is_encrypted(value):
        if require_encrypted is None:
            require_encrypted = bool(
                has_app_context()
                and current_app.config.get('SENSITIVE_DATA_REQUIRE_ENCRYPTION')
            )
        if require_encrypted and value not in (None, ''):
            raise RuntimeError('检测到未加密的敏感字段，必须先完成存量数据迁移')
        return value
    cipher = _fernet(encryption_key)
    if cipher is None:
        raise RuntimeError('缺少敏感字段解密密钥')
    try:
        return cipher.decrypt(value[len(ENCRYPTED_PREFIX):].encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeError) as error:
        raise RuntimeError('敏感字段密文无效或密钥不匹配') from error


def is_deterministically_encrypted(value):
    return isinstance(value, str) and value.startswith(DETERMINISTIC_PREFIX)


def _aessiv(domain, encryption_key=None):
    master_key = _master_key(encryption_key)
    if master_key is None:
        return None
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=b'google-manager:aessiv:v1',
        info=b'deterministic-sensitive-field',
    ).derive(master_key)
    return AESSIV(derived_key), domain.encode('utf-8')


def deterministic_encrypt_value(value, domain, encryption_key=None):
    if value is None or value == '':
        return value
    cipher_context = _aessiv(domain, encryption_key)
    if cipher_context is None:
        if _missing_key_fails_closed():
            raise RuntimeError('缺少敏感字段加密密钥，拒绝写入明文')
        return value
    cipher, associated_data = cipher_context
    ciphertext = cipher.encrypt(str(value).encode('utf-8'), [associated_data])
    return DETERMINISTIC_PREFIX + base64.urlsafe_b64encode(ciphertext).decode('ascii')


def deterministic_decrypt_value(
    value, domain, encryption_key=None, require_encrypted=None,
):
    if not is_deterministically_encrypted(value):
        if require_encrypted is None:
            require_encrypted = bool(
                has_app_context()
                and current_app.config.get('SENSITIVE_DATA_REQUIRE_ENCRYPTION')
            )
        if require_encrypted and value not in (None, ''):
            raise RuntimeError('检测到未加密的敏感字段，必须先完成存量数据迁移')
        return value
    cipher_context = _aessiv(domain, encryption_key)
    if cipher_context is None:
        raise RuntimeError('缺少敏感字段解密密钥')
    cipher, associated_data = cipher_context
    try:
        ciphertext = base64.b64decode(
            value[len(DETERMINISTIC_PREFIX):].encode('ascii'),
            altchars=b'-_',
            validate=True,
        )
        return cipher.decrypt(ciphertext, [associated_data]).decode('utf-8')
    except (InvalidTag, ValueError, UnicodeError) as error:
        raise RuntimeError('敏感字段确定性密文无效、字段不匹配或密钥错误') from error


class EncryptedText(TypeDecorator):
    """Fernet encrypted text with a plaintext-read compatibility window."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


class DeterministicEncryptedText(TypeDecorator):
    """AES-SIV text for equality-only queries; equal plaintext leaks equality."""

    impl = Text
    cache_ok = True

    def __init__(self, domain, *args, **kwargs):
        if not isinstance(domain, str) or not domain:
            raise ValueError('确定性加密字段必须配置 domain')
        self.domain = domain
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        return deterministic_encrypt_value(value, self.domain)

    def process_result_value(self, value, dialect):
        return deterministic_decrypt_value(value, self.domain)
