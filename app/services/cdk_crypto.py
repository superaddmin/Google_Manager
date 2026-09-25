import base64
import hashlib
import hmac
import json
import re
import secrets

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


class CdkError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def keyring(kind):
    value = current_app.config.get('CDK_' + kind + '_KEYS', {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, dict) or not value or len(value) > 4:
        raise CdkError('CONFIGURATION_REQUIRED', '请配置独立 CDK 密钥及版本', 503)
    for key_id, secret in value.items():
        try:
            valid = bool(re.fullmatch(r'[A-Za-z0-9_]{1,32}', key_id)) and len(base64.urlsafe_b64decode(secret)) == 32
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise CdkError('CONFIGURATION_REQUIRED', 'CDK 密钥格式无效', 503)
    return value


def digest(domain, value, key_id=None):
    keys = keyring('LOOKUP')
    key_id = key_id or current_app.config.get('CDK_ACTIVE_KEY_ID', 'v1')
    if key_id not in keys:
        raise CdkError('CONFIGURATION_REQUIRED', 'CDK 当前查询密钥缺失', 503)
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(base64.urlsafe_b64decode(keys[key_id]), domain.encode() + b'\0' + encoded, hashlib.sha256).hexdigest()


def encrypt(value):
    key_id = current_app.config.get('CDK_ACTIVE_KEY_ID', 'v1')
    keys = keyring('ENCRYPTION')
    if key_id not in keys:
        raise CdkError('CONFIGURATION_REQUIRED', 'CDK 当前加密密钥缺失', 503)
    return key_id + ':' + Fernet(keys[key_id].encode()).encrypt(value.encode()).decode()


def decrypt(value):
    try:
        key_id, ciphertext = value.split(':', 1)
        return Fernet(keyring('ENCRYPTION')[key_id].encode()).decrypt(ciphertext.encode()).decode()
    except (KeyError, InvalidToken, ValueError, UnicodeError) as error:
        raise CdkError('KEY_UNAVAILABLE', '卡密解密失败，请联系运维核对密钥', 503) from error


def normalize_code(value):
    if not isinstance(value, str) or len(value) > 64:
        raise CdkError('INVALID_CODE', '平台卡密格式无效')
    code = value.strip().upper().replace('-', '').replace(' ', '')
    if not re.fullmatch(r'GM1[0-9A-HJKMNP-TV-Z]{27}', code):
        raise CdkError('INVALID_CODE', '请输入 GM1 开头的完整平台卡密')
    body = code[3:-1]
    checksum = ALPHABET[sum((index + 1) * ALPHABET.index(character) for index, character in enumerate(body)) % 32]
    if code[-1] != checksum:
        raise CdkError('INVALID_CODE', '卡密校验位不正确，请检查输入')
    return code


def generate_code():
    body = ''.join(secrets.choice(ALPHABET) for _ in range(26))
    checksum = ALPHABET[sum((index + 1) * ALPHABET.index(character) for index, character in enumerate(body)) % 32]
    return 'GM1' + body + checksum
