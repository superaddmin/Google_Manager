import argparse
import base64
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import tempfile

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b'GMBACKUP\x01'
NONCE_SIZE = 12
TAG_SIZE = 16
DIGEST_SIZE = 32
SIZE_FORMAT = '>Q'
SIZE_BYTES = struct.calcsize(SIZE_FORMAT)
HEADER_SIZE = len(MAGIC) + NONCE_SIZE + SIZE_BYTES + DIGEST_SIZE
CHUNK_SIZE = 1024 * 1024
MAX_KEY_FILE_BYTES = 4 * 1024
KEY_FILE_ERROR = '备份密钥文件必须是当前用户所有的 0600 普通文件'


def _normalize_key(encryption_key):
    if isinstance(encryption_key, str):
        encryption_key = encryption_key.strip().encode('ascii')
    try:
        Fernet(encryption_key)
        raw_key = base64.urlsafe_b64decode(encryption_key)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('备份加密密钥必须是有效的 Fernet 密钥') from error
    if len(raw_key) != 32:
        raise ValueError('备份加密密钥长度无效')
    return raw_key


def _secure_tempfile(directory, suffix):
    descriptor, name = tempfile.mkstemp(prefix='.google-manager-', suffix=suffix, dir=directory)
    os.chmod(name, 0o600)
    os.close(descriptor)
    return Path(name)


def _sqlite_integrity_check(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as database:
        result = database.execute('PRAGMA integrity_check').fetchone()
    if not result or result[0] != 'ok':
        raise RuntimeError('SQLite 完整性检查失败')


def _file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b''):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.digest()


def _encrypt_file(source, destination, encryption_key, overwrite_staging=False):
    raw_key = _normalize_key(encryption_key)
    size, digest = _file_digest(source)
    nonce = os.urandom(NONCE_SIZE)
    header = MAGIC + nonce + struct.pack(SIZE_FORMAT, size) + digest
    encryptor = Cipher(algorithms.AES(raw_key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    created = False
    try:
        mode = 'wb' if overwrite_staging else 'xb'
        with destination.open(mode) as output:
            created = True
            os.chmod(destination, 0o600)
            with source.open('rb') as original:
                output.write(header)
                for chunk in iter(lambda: original.read(CHUNK_SIZE), b''):
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _decrypt_file(source, destination, encryption_key):
    raw_key = _normalize_key(encryption_key)
    total_size = source.stat().st_size
    if total_size < HEADER_SIZE + TAG_SIZE:
        raise ValueError('备份文件格式无效或已截断')
    with source.open('rb') as encrypted:
        header = encrypted.read(HEADER_SIZE)
        if not header.startswith(MAGIC):
            raise ValueError('备份文件格式或版本不受支持')
        nonce_start = len(MAGIC)
        nonce = header[nonce_start:nonce_start + NONCE_SIZE]
        size_start = nonce_start + NONCE_SIZE
        expected_size = struct.unpack(SIZE_FORMAT, header[size_start:size_start + SIZE_BYTES])[0]
        expected_digest = header[-DIGEST_SIZE:]
        encrypted.seek(-TAG_SIZE, os.SEEK_END)
        tag = encrypted.read(TAG_SIZE)
        encrypted.seek(HEADER_SIZE)
        remaining = total_size - HEADER_SIZE - TAG_SIZE
        decryptor = Cipher(algorithms.AES(raw_key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open('wb') as output:
                os.chmod(destination, 0o600)
                while remaining:
                    chunk = encrypted.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError('备份文件已截断')
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    output.write(plaintext)
                    digest.update(plaintext)
                    size += len(plaintext)
                final = decryptor.finalize()
                output.write(final)
                digest.update(final)
                size += len(final)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    if size != expected_size or digest.digest() != expected_digest:
        destination.unlink(missing_ok=True)
        raise ValueError('备份文件摘要校验失败')


def _fsync_directory(directory):
    if os.name == 'nt':
        return
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_without_overwrite(source, destination):
    os.chmod(source, 0o600)
    try:
        os.link(source, destination)
    except FileExistsError as error:
        raise ValueError('发布目标必须为不存在的新文件') from error
    try:
        _fsync_directory(destination.parent)
    except Exception:
        destination.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
        raise


def backup_database(source, destination, encryption_key):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('备份目标必须为不存在的新文件')
    if not destination.parent.is_dir():
        raise ValueError('备份目标目录不存在')
    plaintext = _secure_tempfile(destination.parent, '.db')
    encrypted = _secure_tempfile(destination.parent, '.gmbak')
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(plaintext)) as backup:
                original.backup(backup)
                backup.commit()
        _sqlite_integrity_check(plaintext)
        _encrypt_file(plaintext, encrypted, encryption_key, overwrite_staging=True)
        verify_backup(encrypted, encryption_key)
        _publish_without_overwrite(encrypted, destination)
    finally:
        plaintext.unlink(missing_ok=True)
        encrypted.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
    return destination


def verify_backup(source, encryption_key):
    source = Path(source).resolve(strict=True)
    plaintext = _secure_tempfile(source.parent, '.verify.db')
    try:
        _decrypt_file(source, plaintext, encryption_key)
        _sqlite_integrity_check(plaintext)
        size, digest = _file_digest(plaintext)
        return {'size': size, 'sha256': digest.hex()}
    finally:
        plaintext.unlink(missing_ok=True)
        _fsync_directory(source.parent)


def restore_database(source, destination, encryption_key):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('恢复目标必须为不存在的新文件')
    if not destination.parent.is_dir():
        raise ValueError('恢复目标目录不存在')
    plaintext = _secure_tempfile(destination.parent, '.restore.db')
    published = False
    try:
        _decrypt_file(source, plaintext, encryption_key)
        _sqlite_integrity_check(plaintext)
        _publish_without_overwrite(plaintext, destination)
        published = True
        _sqlite_integrity_check(destination)
    except Exception:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        plaintext.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
    return destination


def _load_key(key_file):
    if key_file:
        path = Path(key_file)
        descriptor = None
        try:
            path_stat = path.lstat()
            reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or getattr(path_stat, 'st_file_attributes', 0) & reparse_flag
                or path_stat.st_size <= 0
                or path_stat.st_size > MAX_KEY_FILE_BYTES
            ):
                raise ValueError(KEY_FILE_ERROR)
            flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
            flags |= getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
            descriptor = os.open(path, flags)
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_size <= 0
                or opened_stat.st_size > MAX_KEY_FILE_BYTES
                or (path_stat.st_dev, path_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino)
            ):
                raise ValueError(KEY_FILE_ERROR)
            if sys.platform.startswith('linux') and (
                stat.S_IMODE(opened_stat.st_mode) != 0o600
                or opened_stat.st_uid != os.geteuid()
            ):
                raise ValueError(KEY_FILE_ERROR)
            chunks = []
            remaining = MAX_KEY_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b''.join(chunks)
            if len(value) > MAX_KEY_FILE_BYTES:
                raise ValueError(KEY_FILE_ERROR)
            key = value.decode('ascii').strip()
            if not key:
                raise ValueError(KEY_FILE_ERROR)
            return key
        except (OSError, UnicodeError, ValueError):
            raise ValueError(KEY_FILE_ERROR) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
    key = os.environ.get('DATABASE_BACKUP_ENCRYPTION_KEY', '').strip()
    if not key:
        raise ValueError(
            '必须通过 DATABASE_BACKUP_ENCRYPTION_KEY 或 --key-file 提供独立备份密钥'
        )
    return key


def main():
    parser = argparse.ArgumentParser(description='加密备份、校验或安全恢复 SQLite 数据库')
    parser.add_argument('--key-file', help='只含 Fernet 密钥的受限权限文件')
    commands = parser.add_subparsers(dest='command', required=True)
    backup_parser = commands.add_parser('backup')
    backup_parser.add_argument('source')
    backup_parser.add_argument('destination')
    verify_parser = commands.add_parser('verify')
    verify_parser.add_argument('source')
    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('source')
    restore_parser.add_argument('destination')
    arguments = parser.parse_args()
    key = _load_key(arguments.key_file)
    if arguments.command == 'backup':
        backup_database(arguments.source, arguments.destination, key)
        print('加密备份完成，认证与数据库完整性检查通过')
    elif arguments.command == 'verify':
        result = verify_backup(arguments.source, key)
        print(f'备份校验通过: size={result["size"]}, sha256={result["sha256"]}')
    else:
        restore_database(arguments.source, arguments.destination, key)
        print('数据库恢复完成，认证与数据库完整性检查通过')


if __name__ == '__main__':
    main()
