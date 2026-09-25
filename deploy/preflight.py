#!/usr/bin/env python3
"""Read-only production deployment preflight checks.

The command deliberately accepts only an explicitly named candidate environment
file. It never loads ``.env`` implicitly, connects to external services, mutates
the database, or starts deployment services.
"""

import argparse
import base64
import binascii
from datetime import datetime
import hashlib
import ipaddress
import json
import math
import os
import platform
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit


EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_PENDING = 2
MAX_ENV_BYTES = 128 * 1024
MAX_CREDENTIAL_BYTES = 1024 * 1024
MAX_RUNTIME_ENTRIES = 10_000
MAX_RUNTIME_DEPTH = 32
MAX_RELEASE_FILE_BYTES = 64 * 1024 * 1024
MAX_CHECKSUM_BYTES = 2 * 1024 * 1024
CONTAINER_RUNTIME_UID = 10001
SUPPORTED_RELEASE_PLATFORMS = {'linux/amd64', 'linux/arm64'}
EXPECTED_RELEASE_LOCKFILES = {
    'requirements.txt', 'package-lock.json', 'frontend/package-lock.json',
    'googlemail/package-lock.json',
}
EXPECTED_RELEASE_PUBLIC_FILES = {
    'docker-compose.yml', 'deploy/env.production.example', 'deploy/preflight.py',
    'deploy/prepare-compose-host.sh',
    'deploy/chromium-seccomp.json',
    'deploy/online_smoke.py',
    'deploy/systemd/google-manager-compose-monitor@.service',
    'deploy/systemd/google-manager-compose-monitor@.timer',
    'deploy/backup_database.py', 'deploy/compose_release.py', 'deploy/browser-artifacts.json',
    'deploy/nginx/google-manager.conf',
    'docs/deployment-technical-guide.md', 'docs/server-deployment-guide.md',
    'docs/release-signoff-template.md', 'docs/deployment-preparation.md',
    'docs/production-manual-configuration.md',
    'docs/cdk-implementation-and-operations-2026-09-25.md',
    'docs/browser-security-baseline-2026-09-20.md', 'LICENSE',
}
EXPECTED_RELEASE_BUNDLE_FILES = EXPECTED_RELEASE_PUBLIC_FILES | {
    'evidence/trivy.json', 'evidence/sbom.cdx.json', 'LICENSE-INVENTORY.json',
    'manifest.json', 'README.md', 'RELEASE-SIGNOFF.md',
}

KNOWN_FIELDS = {
    'FLASK_ENV',
    'ADMIN_PASSWORD',
    'ALLOW_TEST_ADMIN_PASSWORD',
    'SECRET_KEY',
    'GMAIL_TOKEN_ENCRYPTION_KEY',
    'GMAIL_HTTP_TIMEOUT_SECONDS',
    'GOOGLE_MANAGER_IMAGE',
    'PUBLIC_DOMAIN',
    'RECHARGE_MODE',
    'RECHARGE_UPSTREAM_URL',
    'RECHARGE_UPSTREAM_ALLOWED_HOSTS',
    'DATABASE_URL',
    'GMAIL_CLIENT_SECRET_FILE',
    'GMAIL_REDIRECT_URI',
    'GMAIL_PUBSUB_TOPIC',
    'GMAIL_PUBSUB_VERIFICATION_TOKEN',
    'PROXY',
    'HEADLESS',
    'TRUSTED_PROXY_CIDRS',
    'GUNICORN_BIND',
    'GUNICORN_WORKERS',
    'GUNICORN_THREADS',
    'GUNICORN_TIMEOUT',
    'GUNICORN_LOG_LEVEL',
    'CDK_ENABLED', 'CDK_ACTIVE_KEY_ID', 'CDK_ENCRYPTION_KEYS', 'CDK_LOOKUP_KEYS',
    'CDK_SMTP_HOST', 'CDK_SMTP_PORT', 'CDK_SMTP_FROM', 'CDK_SMTP_USERNAME', 'CDK_SMTP_PASSWORD',
}

REQUIRED_FIELDS = {
    'FLASK_ENV',
    'ADMIN_PASSWORD',
    'SECRET_KEY',
    'GMAIL_TOKEN_ENCRYPTION_KEY',
    'PUBLIC_DOMAIN',
    'RECHARGE_MODE',
    'GMAIL_CLIENT_SECRET_FILE',
    'HEADLESS',
    'TRUSTED_PROXY_CIDRS',
}

PLACEHOLDER_MARKERS = (
    'change_me',
    'changeme',
    'replace_me',
    'replace-me',
    'example.invalid',
    'your-domain',
    'your_domain',
    '<replace',
    '<change',
)

INSECURE_ADMIN_PASSWORDS = {
    'admin',
    'admin123',
    'changemestrongpassword123!',
    'yourcomplexpassword_2026!',
    'password',
    'password123',
}

INSECURE_SECRET_KEYS = {
    'your-production-secret-key-at-least-32-chars!',
    'changemestrongpassword123!',
    'dev-secret-key-32-chars-minimum-needed!!',
}

MESSAGES = {
    'ENV_FILE_REQUIRED': 'An explicit candidate environment file is required.',
    'ENV_FILE_INVALID': 'The candidate environment file is not a readable regular file.',
    'ENV_FILE_TOO_LARGE': 'The candidate environment file exceeds the supported size limit.',
    'ENV_ENCODING_INVALID': 'The candidate environment file is not valid UTF-8.',
    'ENV_SYNTAX_INVALID': 'The candidate environment file uses unsupported dotenv syntax.',
    'ENV_UNKNOWN_FIELD': 'The candidate environment file contains an unsupported field.',
    'ENV_DUPLICATE_FIELD': 'A candidate environment field is defined more than once.',
    'ENV_INTERPOLATION_FORBIDDEN': 'Interpolation and shell command syntax are forbidden.',
    'ENV_FIELD_MISSING': 'A required production field is missing or empty.',
    'ENV_FILE_PERMISSION_PENDING': 'Environment file permissions require verification on Linux.',
    'ENV_FILE_PERMISSION_INVALID': 'Environment file permissions expose production secrets.',
    'PRODUCTION_MODE_REQUIRED': 'FLASK_ENV must be production.',
    'ADMIN_PASSWORD_INVALID': 'ADMIN_PASSWORD does not meet the production strength policy.',
    'SECRET_KEY_INVALID': 'SECRET_KEY does not meet the production strength policy.',
    'FERNET_KEY_INVALID': 'GMAIL_TOKEN_ENCRYPTION_KEY is not a canonical 32-byte Fernet key.',
    'IMAGE_DIGEST_PENDING': 'A candidate image pinned by sha256 digest is still required.',
    'IMAGE_DIGEST_INVALID': 'GOOGLE_MANAGER_IMAGE must be a non-placeholder registry reference pinned by sha256 digest.',
    'PUBLIC_DOMAIN_INVALID': 'PUBLIC_DOMAIN must be a non-placeholder public DNS hostname.',
    'RECHARGE_MODE_INVALID': 'RECHARGE_MODE must be disabled or live in production.',
    'CDK_CONFIGURATION_INVALID': 'CDK requires independent versioned keys and a valid enable flag.',
    'RECHARGE_LIVE_URL_REQUIRED': 'Live recharge requires an explicit upstream URL and host allowlist.',
    'RECHARGE_LIVE_URL_INVALID': 'The live recharge upstream must be a safe HTTPS URL on the exact allowlist.',
    'HEADLESS_INVALID': 'HEADLESS must be true for this production deployment profile.',
    'TRUSTED_PROXY_INVALID': 'TRUSTED_PROXY_CIDRS must contain scoped valid CIDR networks.',
    'GUNICORN_SETTING_INVALID': 'A Gunicorn setting is outside the supported production range.',
    'GMAIL_HTTP_TIMEOUT_INVALID': 'GMAIL_HTTP_TIMEOUT_SECONDS must be a finite positive number.',
    'OAUTH_REDIRECT_PENDING': 'The explicit public OAuth callback remains to be confirmed.',
    'OAUTH_REDIRECT_INVALID': 'GMAIL_REDIRECT_URI must match the public HTTPS callback exactly.',
    'OAUTH_FILE_INVALID': 'The OAuth client credential is missing, unsafe, unreadable, or structurally invalid.',
    'OAUTH_FILE_PERMISSION_PENDING': 'OAuth credential permissions require verification on Linux.',
    'OAUTH_FILE_PERMISSION_INVALID': 'OAuth credential permissions or ownership are unsafe.',
    'RUNTIME_DIRECTORY_INVALID': 'A required persistent runtime directory is missing or unsafe.',
    'RUNTIME_PERMISSION_PENDING': 'Runtime directory ownership and permissions require verification on Linux.',
    'RUNTIME_PERMISSION_INVALID': 'Runtime directory ownership or permissions do not match the container runtime user.',
    'CONFIG_VALID': 'The candidate production configuration is valid.',
    'ENV_FILE_PERMISSION_VALID': 'Environment file permissions are restricted.',
    'IMAGE_DIGEST_VALID': 'The candidate image identity is pinned by sha256 digest.',
    'OAUTH_FILE_VALID': 'The OAuth client credential has the required structure.',
    'OAUTH_FILE_PERMISSION_VALID': 'OAuth credential permissions are restricted.',
    'RUNTIME_DIRECTORY_VALID': 'Required persistent runtime directories exist.',
    'RUNTIME_PERMISSION_VALID': 'Runtime directory ownership and permissions match the container runtime user.',
    'INTERNAL_ERROR': 'The preflight could not complete due to an internal error.',
    'RELEASE_MANIFEST_PENDING': 'A verified release manifest is required before deployment.',
    'RELEASE_MANIFEST_INVALID': 'The release manifest is blocked, invalid, or mismatched.',
    'RELEASE_MANUAL_SIGNOFF_PENDING': 'The verified release bundle is awaiting an authorized manual signoff.',
    'RELEASE_PLATFORM_INVALID': 'The release platform does not match the Linux deployment host.',
    'DATABASE_PROFILE_INVALID': 'This deployment profile requires the persistent default SQLite database.',
    'OAUTH_MOUNT_INVALID': 'This Compose profile mounts the release credentials.json at /app/credentials.json.',
    'PUBSUB_CONFIG_INVALID': 'Gmail Pub/Sub topic and verification token must be a valid, strong pair.',
}

_ASSIGNMENT = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
_HOST_LABEL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
_IMAGE_REFERENCE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/[a-z0-9._/-]+$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_GIT_SHA = re.compile(r'^[0-9a-f]{40,64}$')
_PUBSUB_TOPIC = re.compile(
    r'^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/topics/'
    r'[A-Za-z][A-Za-z0-9._~+%-]{2,254}$'
)
_PUBSUB_TOKEN = re.compile(r'^[A-Za-z0-9_-]{32,512}$')
_OAUTH_AUTH_URIS = {
    'https://accounts.google.com/o/oauth2/auth',
    'https://accounts.google.com/o/oauth2/v2/auth',
}
_OAUTH_TOKEN_URI = 'https://oauth2.googleapis.com/token'


class CandidateEnvironmentError(Exception):
    def __init__(self, code, field='ENV_FILE'):
        super().__init__(code)
        self.code = code
        self.field = field if field in KNOWN_FIELDS else 'ENV_FILE'


def _check(check_id, field, status, code):
    return {
        'id': check_id,
        'field': field,
        'status': status,
        'code': code,
        'message': MESSAGES[code],
    }


def _decode_value(raw_value):
    if raw_value.startswith("'"):
        if len(raw_value) < 2 or not raw_value.endswith("'"):
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
        value = raw_value[1:-1]
        if "'" in value:
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
    elif raw_value.startswith('"'):
        if len(raw_value) < 2 or not raw_value.endswith('"'):
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
        encoded = raw_value[1:-1]
        value_parts = []
        index = 0
        while index < len(encoded):
            character = encoded[index]
            if character == '"':
                raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
            if character != '\\':
                value_parts.append(character)
                index += 1
                continue
            if index + 1 >= len(encoded) or encoded[index + 1] not in {'\\', '"'}:
                raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
            value_parts.append(encoded[index + 1])
            index += 2
        value = ''.join(value_parts)
    else:
        if any(character in raw_value for character in '#\'"\\'):
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
        value = raw_value

    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
    if '$' in value or '`' in value or any(token in value for token in (';', '&&', '||', '|', '<', '>')):
        raise CandidateEnvironmentError('ENV_INTERPOLATION_FORBIDDEN')
    return value


def load_candidate_environment(path):
    """Parse the documented strict dotenv subset without consulting os.environ."""
    candidate = Path(path)
    try:
        file_stat = candidate.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or candidate.is_symlink():
            raise CandidateEnvironmentError('ENV_FILE_INVALID')
        if file_stat.st_size > MAX_ENV_BYTES:
            raise CandidateEnvironmentError('ENV_FILE_TOO_LARGE')
        raw = candidate.read_bytes()
    except CandidateEnvironmentError:
        raise
    except (OSError, ValueError):
        raise CandidateEnvironmentError('ENV_FILE_INVALID')

    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise CandidateEnvironmentError('ENV_ENCODING_INVALID')

    values = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line != line.strip() or len(line) > 8192:
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
        match = _ASSIGNMENT.fullmatch(line)
        if not match:
            raise CandidateEnvironmentError('ENV_SYNTAX_INVALID')
        field, raw_value = match.groups()
        if field not in KNOWN_FIELDS:
            raise CandidateEnvironmentError('ENV_UNKNOWN_FIELD')
        if field in values:
            raise CandidateEnvironmentError('ENV_DUPLICATE_FIELD', field)
        try:
            values[field] = _decode_value(raw_value)
        except CandidateEnvironmentError as error:
            error.field = field
            raise
    return values


def _is_placeholder(value):
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _is_public_hostname(value):
    if not value or value != value.strip() or value.endswith('.'):
        return False
    lowered = value.lower()
    if _is_placeholder(lowered) or '://' in lowered or ':' in lowered:
        return False
    try:
        ipaddress.ip_address(lowered)
        return False
    except ValueError:
        pass
    labels = lowered.split('.')
    if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        return False
    if labels[-1] in {'invalid', 'example', 'test', 'local', 'localhost'}:
        return False
    return True


def _validate_password(value):
    if (
        value != value.strip()
        or len(value) < 16
        or len(value.encode('utf-8')) > 4096
        or value.lower() in INSECURE_ADMIN_PASSWORDS
        or _is_placeholder(value)
        or len(set(value)) < 8
    ):
        return False
    character_classes = (
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    )
    return sum(character_classes) >= 3


def _validate_secret_key(value):
    encoded = value.encode('utf-8')
    return (
        value == value.strip()
        and 32 <= len(encoded) <= 4096
        and value.lower() not in INSECURE_SECRET_KEYS
        and not _is_placeholder(value)
        and len(set(value)) >= 8
    )


def _validate_fernet_key(value):
    try:
        encoded = value.encode('ascii')
        decoded = base64.b64decode(encoded, altchars=b'-_', validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode('ascii') == value


def _validate_image_reference(value):
    if not value:
        return 'pending'
    if _is_placeholder(value) or value.count('@sha256:') != 1:
        return 'failed'
    reference, digest = value.rsplit('@sha256:', 1)
    if not _IMAGE_REFERENCE.fullmatch(reference) or len(digest) != 64:
        return 'failed'
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        return 'failed'
    if '..' in reference or reference.endswith('/'):
        return 'failed'
    return 'passed'


def _is_nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256_map(value, expected_paths):
    return (
        isinstance(value, dict)
        and set(value) == expected_paths
        and all(
            isinstance(path, str)
            and bool(path)
            and not Path(path).is_absolute()
            and '..' not in Path(path).parts
            and isinstance(digest, str)
            and bool(_SHA256.fullmatch(digest))
            for path, digest in value.items()
        )
    )


def _is_https_evidence_url(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == 'https'
        and bool(parsed.hostname)
        and _is_public_hostname(parsed.hostname.lower().rstrip('.'))
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.query
        and not parsed.fragment
    )


def _linux_platform(machine_name):
    normalized = str(machine_name or '').strip().lower()
    if normalized in {'x86_64', 'amd64'}:
        return 'linux/amd64'
    if normalized in {'aarch64', 'arm64'}:
        return 'linux/arm64'
    return None


def _validate_release_manifest(metadata, image, expected_platform):
    required_fields = {
        'schema_version', 'release_status', 'production_approved',
        'signoff_decision', 'generated_at', 'scope', 'git', 'platform',
        'image', 'ci_run_url', 'scan', 'sbom', 'license_inventory',
        'lockfile_sha256', 'public_file_sha256', 'blockers',
    }
    if not isinstance(metadata, dict) or set(metadata) != required_fields:
        return False
    if (
        metadata.get('schema_version') != 1
        or metadata.get('release_status') != 'AWAITING_MANUAL_SIGNOFF'
        or metadata.get('production_approved') is not False
        or metadata.get('signoff_decision') != 'NO-GO'
        or metadata.get('image') != image
        or metadata.get('platform') != expected_platform
        or metadata.get('blockers') != []
    ):
        return False

    try:
        generated_at = datetime.fromisoformat(metadata['generated_at'])
    except (TypeError, ValueError):
        return False
    if generated_at.tzinfo is None or not _is_https_evidence_url(metadata.get('ci_run_url')):
        return False

    scope = metadata.get('scope')
    if (
        not isinstance(scope, dict)
        or scope.get('included') != 'CDK workbench'
        or scope.get('excluded') != ['cash payments', 'payment channels', 'payment callbacks']
    ):
        return False
    git = metadata.get('git')
    if (
        not isinstance(git, dict)
        or set(git) != {'sha', 'dirty'}
        or not isinstance(git.get('sha'), str)
        or not _GIT_SHA.fullmatch(git['sha'])
        or git.get('dirty') is not False
    ):
        return False

    scan = metadata.get('scan')
    severities = scan.get('severity_counts') if isinstance(scan, dict) else None
    result_types = scan.get('result_types') if isinstance(scan, dict) else None
    if (
        not isinstance(scan, dict)
        or scan.get('evidence_path') != 'evidence/trivy.json'
        or not isinstance(scan.get('sha256'), str)
        or not _SHA256.fullmatch(scan['sha256'])
        or scan.get('high_critical_count') != 0
        or scan.get('suppressed_findings_count') != 0
        or not _is_nonnegative_integer(scan.get('result_count'))
        or scan['result_count'] <= 0
        or not isinstance(severities, dict)
        or set(severities) != {'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
        or any(not _is_nonnegative_integer(count) for count in severities.values())
        or severities['HIGH'] != 0
        or severities['CRITICAL'] != 0
        or not isinstance(result_types, dict)
        or not result_types
        or any(
            not isinstance(name, str) or not name
            or not _is_nonnegative_integer(count) or count <= 0
            for name, count in result_types.items()
        )
        or sum(result_types.values()) != scan['result_count']
    ):
        return False

    sbom = metadata.get('sbom')
    if (
        not isinstance(sbom, dict)
        or sbom.get('evidence_path') != 'evidence/sbom.cdx.json'
        or not isinstance(sbom.get('sha256'), str)
        or not _SHA256.fullmatch(sbom['sha256'])
        or not _is_nonnegative_integer(sbom.get('component_count'))
        or sbom.get('missing_license_metadata_count') != 0
    ):
        return False
    inventory = metadata.get('license_inventory')
    if (
        not isinstance(inventory, dict)
        or inventory.get('path') != 'LICENSE-INVENTORY.json'
        or not isinstance(inventory.get('sha256'), str)
        or not _SHA256.fullmatch(inventory['sha256'])
    ):
        return False
    return (
        _is_sha256_map(metadata.get('lockfile_sha256'), EXPECTED_RELEASE_LOCKFILES)
        and _is_sha256_map(metadata.get('public_file_sha256'), EXPECTED_RELEASE_PUBLIC_FILES)
    )


def _read_release_file(root, relative_path, maximum_bytes=MAX_RELEASE_FILE_BYTES):
    if relative_path not in EXPECTED_RELEASE_BUNDLE_FILES | {'SHA256SUMS'}:
        raise ValueError('unsupported release path')
    path = root
    for component in Path(relative_path).parts:
        path = path / component
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or (
            hasattr(path, 'is_junction') and path.is_junction()
        ):
            raise ValueError('release path is link-like')
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > maximum_bytes:
        raise ValueError('release file is invalid')
    with path.open('rb') as source:
        opened_stat = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_size > maximum_bytes
            or (file_stat.st_dev, file_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino)
        ):
            raise ValueError('release file changed')
        value = source.read(maximum_bytes + 1)
    if len(value) > maximum_bytes:
        raise ValueError('release file is too large')
    return value


def _validate_release_bundle_files(root, metadata, manifest_bytes):
    bound_hashes = dict(metadata['public_file_sha256'])
    bound_hashes[metadata['scan']['evidence_path']] = metadata['scan']['sha256']
    bound_hashes[metadata['sbom']['evidence_path']] = metadata['sbom']['sha256']
    bound_hashes[metadata['license_inventory']['path']] = metadata['license_inventory']['sha256']
    for relative_path, expected_digest in bound_hashes.items():
        content = _read_release_file(root, relative_path)
        if hashlib.sha256(content).hexdigest() != expected_digest:
            return False

    checksum_bytes = _read_release_file(root, 'SHA256SUMS', MAX_CHECKSUM_BYTES)
    try:
        checksum_text = checksum_bytes.decode('ascii')
    except UnicodeDecodeError:
        return False
    checksums = {}
    for line in checksum_text.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9._/@-]+)', line)
        if not match or match.group(2) in checksums:
            return False
        checksums[match.group(2)] = match.group(1)
    if set(checksums) != EXPECTED_RELEASE_BUNDLE_FILES:
        return False
    for relative_path, expected_digest in checksums.items():
        content = (
            manifest_bytes
            if relative_path == 'manifest.json'
            else _read_release_file(root, relative_path)
        )
        if hashlib.sha256(content).hexdigest() != expected_digest:
            return False
    return True


def _allowed_hosts(raw_value):
    hosts = []
    for item in raw_value.split(','):
        host = item.strip().lower().rstrip('.')
        if not _is_public_hostname(host):
            return None
        hosts.append(host)
    return set(hosts) if hosts else None


def _validate_live_upstream(raw_url, raw_allowed_hosts):
    allowed = _allowed_hosts(raw_allowed_hosts)
    if not raw_url or not allowed:
        return False
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != 'https'
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or hostname.lower().rstrip('.') not in allowed
        or port not in (None, 443)
        or not _is_public_hostname(hostname.lower().rstrip('.'))
    ):
        return False
    return True


def _validate_trusted_proxies(raw_value):
    if not raw_value:
        return False
    for item in raw_value.split(','):
        try:
            network = ipaddress.ip_network(item.strip(), strict=True)
        except ValueError:
            return False
        if (
            network.prefixlen != network.max_prefixlen
            or not (network.network_address.is_private or network.network_address.is_loopback)
            or network.network_address.is_unspecified
            or network.network_address.is_multicast
        ):
            return False
    return True


def _validate_integer(value, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return False
    return str(parsed) == value and minimum <= parsed <= maximum


def _validate_positive_number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and parsed > 0


def _validate_oauth_redirect(raw_value, public_domain):
    try:
        parsed = urlsplit(raw_value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == 'https'
        and parsed.hostname == public_domain.lower()
        and parsed.path == '/api/gmail/oauth/callback'
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and not parsed.query
        and not parsed.fragment
    )


def _validate_pubsub_configuration(topic, token):
    if not topic and not token:
        return True
    return (
        bool(topic)
        and bool(token)
        and bool(_PUBSUB_TOPIC.fullmatch(topic))
        and bool(_PUBSUB_TOKEN.fullmatch(token))
        and not _is_placeholder(token)
        and len(set(token)) >= 12
        and any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
    )


def _credential_host_path(env_file, project_root, configured_path, override):
    if override is not None:
        return Path(override)
    normalized = configured_path.replace('\\', '/')
    if normalized == '/app/credentials.json':
        return project_root / 'credentials.json'
    configured = Path(configured_path)
    if configured.is_absolute():
        return configured
    return Path(env_file).parent / configured


def _validate_oauth_file(path):
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
            return False
        if file_stat.st_nlink != 1 or file_stat.st_size <= 0 or file_stat.st_size > MAX_CREDENTIAL_BYTES:
            return False
        document = json.loads(path.read_bytes().decode('utf-8-sig'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    client = document.get('web') or document.get('installed')
    if not isinstance(client, dict):
        return False
    return all(
        isinstance(client.get(field), str) and bool(client[field].strip())
        for field in ('client_id', 'client_secret', 'auth_uri', 'token_uri')
    ) and (
        client['auth_uri'] in _OAUTH_AUTH_URIS
        and client['token_uri'] == _OAUTH_TOKEN_URI
    )


def _validate_runtime_directories(project_root):
    paths = (
        project_root / 'instance',
        project_root / 'googlemail' / 'runtime',
        project_root / 'googlemail' / 'output',
    )
    for path in paths:
        try:
            path_stat = path.lstat()
        except OSError:
            return paths, False
        if not stat.S_ISDIR(path_stat.st_mode) or path.is_symlink():
            return paths, False
    return paths, True


def _restricted_file_permissions(path, expected_uids=None):
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or getattr(file_stat, 'st_nlink', 1) != 1
        or file_stat.st_mode & 0o7777 != 0o600
    ):
        return False
    return expected_uids is None or file_stat.st_uid in expected_uids


def _runtime_permissions(paths, expected_uid):
    entries_seen = 0
    stack = [(path, 0, None) for path in paths]
    while stack:
        path, depth, root_device = stack.pop()
        if depth > MAX_RUNTIME_DEPTH:
            return False
        try:
            path_stat = path.lstat()
        except OSError:
            return False
        if root_device is None:
            root_device = path_stat.st_dev
        if (
            stat.S_ISLNK(path_stat.st_mode)
            or not stat.S_ISDIR(path_stat.st_mode)
            or path_stat.st_dev != root_device
            or path_stat.st_uid != expected_uid
            or path_stat.st_mode & 0o7777 != 0o700
        ):
            return False
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    if entries_seen > MAX_RUNTIME_ENTRIES:
                        return False
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        return False
                    if (
                        stat.S_ISLNK(entry_stat.st_mode)
                        or entry_stat.st_dev != root_device
                        or entry_stat.st_uid != expected_uid
                    ):
                        return False
                    entry_path = Path(entry.path)
                    if stat.S_ISDIR(entry_stat.st_mode):
                        stack.append((entry_path, depth + 1, root_device))
                    elif (
                        not stat.S_ISREG(entry_stat.st_mode)
                        or entry_stat.st_nlink != 1
                        or entry_stat.st_mode & 0o7777 != 0o600
                    ):
                        return False
        except OSError:
            return False
    return True


def _configuration_checks(values):
    checks = []
    for field in sorted(REQUIRED_FIELDS):
        if not values.get(field):
            checks.append(_check('config.required', field, 'failed', 'ENV_FIELD_MISSING'))

    if checks:
        return checks

    valid = True
    if values['FLASK_ENV'] != 'production':
        checks.append(_check('config.production_mode', 'FLASK_ENV', 'failed', 'PRODUCTION_MODE_REQUIRED'))
        valid = False
    if not _validate_password(values['ADMIN_PASSWORD']) or values.get('ALLOW_TEST_ADMIN_PASSWORD', '0') != '0':
        checks.append(_check('config.admin_password', 'ADMIN_PASSWORD', 'failed', 'ADMIN_PASSWORD_INVALID'))
        valid = False
    if not _validate_secret_key(values['SECRET_KEY']):
        checks.append(_check('config.secret_key', 'SECRET_KEY', 'failed', 'SECRET_KEY_INVALID'))
        valid = False
    if not _validate_fernet_key(values['GMAIL_TOKEN_ENCRYPTION_KEY']):
        checks.append(_check('config.fernet_key', 'GMAIL_TOKEN_ENCRYPTION_KEY', 'failed', 'FERNET_KEY_INVALID'))
        valid = False

    image_status = _validate_image_reference(values.get('GOOGLE_MANAGER_IMAGE', ''))
    if image_status == 'pending':
        checks.append(_check('artifact.image_digest', 'GOOGLE_MANAGER_IMAGE', 'pending', 'IMAGE_DIGEST_PENDING'))
    elif image_status == 'failed':
        checks.append(_check('artifact.image_digest', 'GOOGLE_MANAGER_IMAGE', 'failed', 'IMAGE_DIGEST_INVALID'))
        valid = False
    else:
        checks.append(_check('artifact.image_digest', 'GOOGLE_MANAGER_IMAGE', 'passed', 'IMAGE_DIGEST_VALID'))

    domain = values['PUBLIC_DOMAIN'].lower()
    if not _is_public_hostname(domain):
        checks.append(_check('network.public_domain', 'PUBLIC_DOMAIN', 'failed', 'PUBLIC_DOMAIN_INVALID'))
        valid = False

    recharge_mode = values['RECHARGE_MODE'].lower().strip()
    if recharge_mode not in {'disabled', 'live'} or recharge_mode != values['RECHARGE_MODE']:
        checks.append(_check('recharge.mode', 'RECHARGE_MODE', 'failed', 'RECHARGE_MODE_INVALID'))
        valid = False
    elif recharge_mode == 'live':
        if not values.get('RECHARGE_UPSTREAM_URL') or not values.get('RECHARGE_UPSTREAM_ALLOWED_HOSTS'):
            checks.append(_check('recharge.live_upstream', 'RECHARGE_UPSTREAM_URL', 'failed', 'RECHARGE_LIVE_URL_REQUIRED'))
            valid = False
        elif not _validate_live_upstream(
            values['RECHARGE_UPSTREAM_URL'], values['RECHARGE_UPSTREAM_ALLOWED_HOSTS']
        ):
            checks.append(_check('recharge.live_upstream', 'RECHARGE_UPSTREAM_URL', 'failed', 'RECHARGE_LIVE_URL_INVALID'))
            valid = False

    cdk_valid = values.get('CDK_ENABLED', '0') in {'0', '1'}
    if values.get('CDK_ENABLED') == '1':
        try:
            encryption = json.loads(values.get('CDK_ENCRYPTION_KEYS', ''))
            lookup = json.loads(values.get('CDK_LOOKUP_KEYS', ''))
            active = values.get('CDK_ACTIVE_KEY_ID', 'v1')
            cdk_valid = (
                isinstance(encryption, dict) and isinstance(lookup, dict)
                and 1 <= len(encryption) <= 4 and 1 <= len(lookup) <= 4
                and active in encryption and active in lookup
                and all(re.fullmatch(r'[A-Za-z0-9_]{1,32}', key) for key in [*encryption, *lookup])
                and all(_validate_fernet_key(value) for value in [*encryption.values(), *lookup.values()])
                and not set(encryption.values()) & set(lookup.values())
                and values['GMAIL_TOKEN_ENCRYPTION_KEY'] not in [*encryption.values(), *lookup.values()]
            )
        except (ValueError, TypeError, AttributeError):
            cdk_valid = False
    if not cdk_valid:
        checks.append(_check('cdk.configuration', 'CDK_ENABLED', 'failed', 'CDK_CONFIGURATION_INVALID'))
        valid = False

    if values['HEADLESS'].lower() != 'true':
        checks.append(_check('runtime.headless', 'HEADLESS', 'failed', 'HEADLESS_INVALID'))
        valid = False
    if not _validate_trusted_proxies(values['TRUSTED_PROXY_CIDRS']):
        checks.append(_check('network.trusted_proxies', 'TRUSTED_PROXY_CIDRS', 'failed', 'TRUSTED_PROXY_INVALID'))
        valid = False

    numeric_settings = (
        ('GUNICORN_WORKERS', 1, 32),
        ('GUNICORN_THREADS', 1, 64),
        ('GUNICORN_TIMEOUT', 30, 600),
    )
    for field, minimum, maximum in numeric_settings:
        if field in values and values[field] and not _validate_integer(values[field], minimum, maximum):
            checks.append(_check('runtime.gunicorn', field, 'failed', 'GUNICORN_SETTING_INVALID'))
            valid = False
    if 'GMAIL_HTTP_TIMEOUT_SECONDS' in values and values['GMAIL_HTTP_TIMEOUT_SECONDS'] and not _validate_positive_number(
        values['GMAIL_HTTP_TIMEOUT_SECONDS']
    ):
        checks.append(_check(
            'runtime.gmail', 'GMAIL_HTTP_TIMEOUT_SECONDS', 'failed', 'GMAIL_HTTP_TIMEOUT_INVALID'
        ))
        valid = False
    if 'GUNICORN_BIND' in values and values['GUNICORN_BIND'] not in {'', '0.0.0.0:8002'}:
        checks.append(_check('runtime.gunicorn', 'GUNICORN_BIND', 'failed', 'GUNICORN_SETTING_INVALID'))
        valid = False
    if 'GUNICORN_LOG_LEVEL' in values and values['GUNICORN_LOG_LEVEL'] not in {
        '', 'critical', 'error', 'warning', 'info'
    }:
        checks.append(_check('runtime.gunicorn', 'GUNICORN_LOG_LEVEL', 'failed', 'GUNICORN_SETTING_INVALID'))
        valid = False

    redirect_uri = values.get('GMAIL_REDIRECT_URI', '')
    if not redirect_uri:
        checks.append(_check('oauth.public_redirect', 'GMAIL_REDIRECT_URI', 'pending', 'OAUTH_REDIRECT_PENDING'))
    elif not _is_public_hostname(domain) or not _validate_oauth_redirect(redirect_uri, domain):
        checks.append(_check('oauth.public_redirect', 'GMAIL_REDIRECT_URI', 'failed', 'OAUTH_REDIRECT_INVALID'))
        valid = False

    if not _validate_pubsub_configuration(
        values.get('GMAIL_PUBSUB_TOPIC', ''),
        values.get('GMAIL_PUBSUB_VERIFICATION_TOKEN', ''),
    ):
        checks.append(_check(
            'gmail.pubsub', 'GMAIL_PUBSUB_VERIFICATION_TOKEN', 'failed',
            'PUBSUB_CONFIG_INVALID',
        ))
        valid = False

    if valid:
        checks.append(_check('config.production', 'ENV_FILE', 'passed', 'CONFIG_VALID'))
    return checks


def evaluate_preflight(
    env_file,
    project_root=None,
    credentials_file=None,
    expected_runtime_uid=10001,
    platform_name=None,
    machine_name=None,
):
    """Evaluate a candidate deployment and return a redacted JSON-ready report."""
    checks = []
    try:
        values = load_candidate_environment(env_file)
    except CandidateEnvironmentError as error:
        checks.append(_check('environment.parse', error.field, 'failed', error.code))
        return _report(checks)

    checks.extend(_configuration_checks(values))
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    current_platform = platform_name or platform.system()
    expected_platform = None
    if current_platform == 'Linux':
        expected_platform = _linux_platform(machine_name or platform.machine())

    manifest = root / 'manifest.json'
    try:
        manifest.lstat()
        manifest_present = True
    except FileNotFoundError:
        manifest_present = False
    except OSError:
        manifest_present = True
    if not manifest_present:
        checks.append(_check('artifact.manifest', 'RELEASE_MANIFEST', 'pending', 'RELEASE_MANIFEST_PENDING'))
    else:
        valid_manifest = False
        platform_matches = True
        try:
            manifest_bytes = _read_release_file(root, 'manifest.json', MAX_CREDENTIAL_BYTES)
            metadata = json.loads(manifest_bytes.decode('utf-8'))
            manifest_platform = metadata.get('platform') if isinstance(metadata, dict) else None
            validation_platform = (
                expected_platform if current_platform == 'Linux' else manifest_platform
            )
            platform_matches = (
                validation_platform in SUPPORTED_RELEASE_PLATFORMS
                and manifest_platform == validation_platform
            )
            valid_manifest = (
                platform_matches
                and _validate_release_manifest(
                    metadata, values.get('GOOGLE_MANAGER_IMAGE'), validation_platform
                )
                and _validate_release_bundle_files(root, metadata, manifest_bytes)
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError):
            pass
        if current_platform == 'Linux' and not platform_matches:
            checks.append(_check(
                'artifact.platform', 'RELEASE_PLATFORM', 'failed', 'RELEASE_PLATFORM_INVALID'
            ))
        if valid_manifest:
            checks.append(_check(
                'artifact.manifest', 'RELEASE_MANIFEST', 'pending',
                'RELEASE_MANUAL_SIGNOFF_PENDING',
            ))
        else:
            checks.append(_check(
                'artifact.manifest', 'RELEASE_MANIFEST', 'failed', 'RELEASE_MANIFEST_INVALID'
            ))

    if values.get('DATABASE_URL', '') not in {'', 'sqlite:////app/instance/accounts.db'}:
        checks.append(_check('database.persistence', 'DATABASE_URL', 'failed', 'DATABASE_PROFILE_INVALID'))

    configured_credential = values.get('GMAIL_CLIENT_SECRET_FILE', '')
    credential_path = _credential_host_path(
        env_file, root, configured_credential, credentials_file
    )
    if configured_credential != '/app/credentials.json' or credential_path.resolve() != (root / 'credentials.json').resolve():
        checks.append(_check('oauth.mount', 'GMAIL_CLIENT_SECRET_FILE', 'failed', 'OAUTH_MOUNT_INVALID'))
    oauth_valid = _validate_oauth_file(credential_path)
    if oauth_valid:
        checks.append(_check('oauth.client_file', 'GMAIL_CLIENT_SECRET_FILE', 'passed', 'OAUTH_FILE_VALID'))
    else:
        checks.append(_check('oauth.client_file', 'GMAIL_CLIENT_SECRET_FILE', 'failed', 'OAUTH_FILE_INVALID'))

    runtime_paths, runtime_valid = _validate_runtime_directories(root)
    if runtime_valid:
        checks.append(_check('filesystem.runtime_directories', 'RUNTIME_DIRECTORIES', 'passed', 'RUNTIME_DIRECTORY_VALID'))
    else:
        checks.append(_check('filesystem.runtime_directories', 'RUNTIME_DIRECTORIES', 'failed', 'RUNTIME_DIRECTORY_INVALID'))

    if current_platform != 'Linux':
        checks.append(_check('filesystem.env_permissions', 'ENV_FILE', 'pending', 'ENV_FILE_PERMISSION_PENDING'))
        checks.append(_check('filesystem.oauth_permissions', 'GMAIL_CLIENT_SECRET_FILE', 'pending', 'OAUTH_FILE_PERMISSION_PENDING'))
        checks.append(_check('filesystem.runtime_permissions', 'RUNTIME_DIRECTORIES', 'pending', 'RUNTIME_PERMISSION_PENDING'))
    else:
        deploy_uid = os.geteuid() if hasattr(os, 'geteuid') else expected_runtime_uid
        if _restricted_file_permissions(Path(env_file), {0, deploy_uid}):
            checks.append(_check('filesystem.env_permissions', 'ENV_FILE', 'passed', 'ENV_FILE_PERMISSION_VALID'))
        else:
            checks.append(_check('filesystem.env_permissions', 'ENV_FILE', 'failed', 'ENV_FILE_PERMISSION_INVALID'))
        if oauth_valid and _restricted_file_permissions(credential_path, {expected_runtime_uid}):
            checks.append(_check('filesystem.oauth_permissions', 'GMAIL_CLIENT_SECRET_FILE', 'passed', 'OAUTH_FILE_PERMISSION_VALID'))
        else:
            checks.append(_check('filesystem.oauth_permissions', 'GMAIL_CLIENT_SECRET_FILE', 'failed', 'OAUTH_FILE_PERMISSION_INVALID'))
        if runtime_valid and _runtime_permissions(runtime_paths, expected_runtime_uid):
            checks.append(_check('filesystem.runtime_permissions', 'RUNTIME_DIRECTORIES', 'passed', 'RUNTIME_PERMISSION_VALID'))
        else:
            checks.append(_check('filesystem.runtime_permissions', 'RUNTIME_DIRECTORIES', 'failed', 'RUNTIME_PERMISSION_INVALID'))

    return _report(checks)


def _report(checks):
    counts = {
        status: sum(check['status'] == status for check in checks)
        for status in ('passed', 'failed', 'pending')
    }
    if counts['failed']:
        overall_status = 'failed'
        exit_code = EXIT_FAILED
    elif counts['pending']:
        overall_status = 'pending'
        exit_code = EXIT_PENDING
    else:
        overall_status = 'passed'
        exit_code = EXIT_PASSED
    return {
        'schema_version': 1,
        'overall_status': overall_status,
        'exit_code': exit_code,
        'summary': counts,
        'checks': checks,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Read-only production deployment preflight. The strict dotenv subset '
            'supports KEY=VALUE and complete single/double quoted values only; '
            'interpolation and shell syntax are rejected.'
        )
    )
    parser.add_argument(
        '--env-file',
        help='Explicit candidate production environment file (never defaults to .env).',
    )
    parser.add_argument(
        '--project-root',
        help='Candidate release root containing instance/ and googlemail runtime directories.',
    )
    parser.add_argument(
        '--credentials-file',
        help='Host-side OAuth credential file; overrides GMAIL_CLIENT_SECRET_FILE path mapping.',
    )
    parser.add_argument(
        '--expected-runtime-uid',
        type=int,
        default=CONTAINER_RUNTIME_UID,
        help='Fixed Linux owner UID for this container profile (must be 10001).',
    )
    parser.add_argument('--pretty', action='store_true', help='Pretty-print the JSON report.')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.env_file:
        report = _report([
            _check('environment.argument', 'ENV_FILE', 'failed', 'ENV_FILE_REQUIRED')
        ])
    elif args.expected_runtime_uid != CONTAINER_RUNTIME_UID:
        report = _report([
            _check('filesystem.runtime_uid', 'RUNTIME_DIRECTORIES', 'failed', 'RUNTIME_PERMISSION_INVALID')
        ])
    else:
        try:
            report = evaluate_preflight(
                env_file=args.env_file,
                project_root=args.project_root,
                credentials_file=args.credentials_file,
                expected_runtime_uid=args.expected_runtime_uid,
            )
        except Exception:
            report = _report([
                _check('preflight.internal', 'PREFLIGHT', 'failed', 'INTERNAL_ERROR')
            ])
    json.dump(
        report,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    sys.stdout.write('\n')
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
