#!/usr/bin/env python3
"""Run fixed Compose operations with the explicitly checked configuration.

No shell variables or implicit dotenv files may override application settings.
This tool never supplies production approval: mutating operations require the
manifest hash obtained separately from the authorized release owner's GO record.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    from .preflight import (evaluate_preflight, load_candidate_environment,
                            _validate_release_manifest, _linux_platform)
except ImportError:
    from preflight import (evaluate_preflight, load_candidate_environment,
                           _validate_release_manifest, _linux_platform)


COMMANDS = {
    'config': ['config', '--quiet'],
    'pull': ['pull'],
    'up': ['up', '-d', '--no-build', '--force-recreate', '--wait', '--wait-timeout', '180'],
    'init-db': ['run', '--rm', '--no-deps', 'initialize'],
    'inventory-encryption': ['run', '--rm', '--no-deps', 'initialize', 'python', '-m',
                             'app.manage', 'encrypt-sensitive-data'],
    'apply-encryption': ['run', '--rm', '--no-deps', 'initialize', 'python', '-m',
                         'app.manage', 'encrypt-sensitive-data', '--apply'],
    'status': ['ps', '-a', '--format', 'json'],
    'monitor': ['exec', '-T', 'google-manager', 'python', '-m', 'app.monitor'],
    'worker-check': ['exec', '-T', 'worker', 'python', '-m', 'app.worker', '--check'],
}
REQUIRES_GO = {'up', 'init-db', 'inventory-encryption', 'apply-encryption'}
APPLICATION_FIELDS = {
    'FLASK_ENV', 'ADMIN_PASSWORD', 'SECRET_KEY', 'GMAIL_TOKEN_ENCRYPTION_KEY',
    'GMAIL_HTTP_TIMEOUT_SECONDS',
    'RECHARGE_MODE', 'RECHARGE_UPSTREAM_URL', 'RECHARGE_UPSTREAM_ALLOWED_HOSTS',
    'DATABASE_URL', 'GMAIL_CLIENT_SECRET_FILE', 'GMAIL_REDIRECT_URI',
    'GMAIL_PUBSUB_TOPIC', 'GMAIL_PUBSUB_VERIFICATION_TOKEN', 'PROXY', 'HEADLESS',
    'TRUSTED_PROXY_CIDRS', 'GUNICORN_BIND', 'GUNICORN_WORKERS', 'GUNICORN_THREADS',
    'GUNICORN_TIMEOUT', 'GUNICORN_LOG_LEVEL',
    'CDK_ENABLED', 'CDK_ACTIVE_KEY_ID', 'CDK_ENCRYPTION_KEYS', 'CDK_LOOKUP_KEYS',
    'CDK_SMTP_HOST', 'CDK_SMTP_PORT', 'CDK_SMTP_FROM', 'CDK_SMTP_USERNAME', 'CDK_SMTP_PASSWORD',
}
DEFAULTS = {
    'FLASK_ENV': 'production', 'RECHARGE_MODE': 'disabled',
    'RECHARGE_UPSTREAM_ALLOWED_HOSTS': 'aichong666.com',
    'GMAIL_CLIENT_SECRET_FILE': '/app/credentials.json', 'HEADLESS': 'true',
    'GMAIL_HTTP_TIMEOUT_SECONDS': '30',
    'TRUSTED_PROXY_CIDRS': '172.30.8.1/32', 'GUNICORN_BIND': '0.0.0.0:8002',
    'GUNICORN_WORKERS': '2', 'GUNICORN_THREADS': '4', 'GUNICORN_TIMEOUT': '120',
    'GUNICORN_LOG_LEVEL': 'info',
    'CDK_ENABLED': '0', 'CDK_ACTIVE_KEY_ID': 'v1', 'CDK_SMTP_PORT': '465',
}
TRUSTED_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
REQUIRED_HASHED_FILES = {'manifest.json', 'docker-compose.yml',
                         'deploy/chromium-seccomp.json',
                         'deploy/compose_release.py', 'deploy/preflight.py',
                         'deploy/env.production.example'}


class ComposeReleaseError(Exception):
    """A stable, non-sensitive failure code."""


def controlled_environment(values, inherited=None):
    environment = {'PATH': TRUSTED_PATH, 'LANG': 'C.UTF-8'}
    environment.update(values)
    environment['COMPOSE_DISABLE_ENV_FILE'] = '1'
    return environment


def _trusted_executable(path):
    if not path or not Path(path).is_absolute():
        raise ComposeReleaseError('COMPOSE_EXECUTABLE_UNTRUSTED')
    candidate = Path(path).resolve(strict=True)
    for node in (candidate, *candidate.parents):
        metadata = node.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ComposeReleaseError('COMPOSE_EXECUTABLE_UNTRUSTED')
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ComposeReleaseError('COMPOSE_EXECUTABLE_UNTRUSTED')
    return str(candidate)


def _resolve_compose_binary(explicit):
    if platform.system() != 'Linux':
        raise ComposeReleaseError('LINUX_REQUIRED')
    candidates = [explicit] if explicit else [
        '/usr/libexec/docker/cli-plugins/docker-compose',
        '/usr/lib/docker/cli-plugins/docker-compose',
        '/usr/local/lib/docker/cli-plugins/docker-compose',
    ]
    for path in candidates:
        if path and Path(path).exists():
            return _trusted_executable(path)
    raise ComposeReleaseError('COMPOSE_EXECUTABLE_UNAVAILABLE')


def _temporary_parent():
    # Do not honor inherited TMPDIR/TMP/TEMP: another owner of the parent could
    # rename a private child directory and substitute a different Compose file.
    parent = Path('/tmp')
    metadata = parent.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o1777):
        raise ComposeReleaseError('TEMPORARY_PARENT_UNTRUSTED')
    return parent


def _docker_auth_snapshot(directory):
    if directory is None:
        return b'{}'
    root = Path(directory).resolve(strict=True)
    for node in (root, *root.parents):
        metadata = node.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ComposeReleaseError('DOCKER_AUTH_UNTRUSTED')
    config_path = root / 'config.json'
    metadata = config_path.lstat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise ComposeReleaseError('DOCKER_AUTH_UNTRUSTED')
    content = _read_regular(root, 'config.json', 1024 * 1024)
    config = json.loads(content)
    if not isinstance(config, dict) or not set(config).issubset({'auths', 'credsStore', 'credHelpers'}):
        raise ComposeReleaseError('DOCKER_AUTH_UNTRUSTED')
    helpers = list(config.get('credHelpers', {}).values())
    if config.get('credsStore'):
        helpers.append(config['credsStore'])
    for helper in helpers:
        if not isinstance(helper, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', helper):
            raise ComposeReleaseError('DOCKER_AUTH_UNTRUSTED')
        _trusted_executable(shutil.which('docker-credential-' + helper, path=TRUSTED_PATH))
    return content


def _read_regular(root, relative, max_bytes=128 * 1024 * 1024):
    path_parts = PurePosixPath(relative)
    if (not relative or path_parts.is_absolute() or '\\' in relative
            or ':' in relative or any(p in {'', '.', '..'} for p in relative.split('/'))):
        raise ComposeReleaseError('BUNDLE_PATH_INVALID')
    current = root
    for part in path_parts.parts:
        current = current / part
        metadata = current.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & 0x400):
            raise ComposeReleaseError('BUNDLE_PATH_INVALID')
    with current.open('rb') as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ComposeReleaseError('BUNDLE_FILE_INVALID')
        content = source.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ComposeReleaseError('BUNDLE_FILE_INVALID')
    return content


def verify_approved_bundle(root, approved_hash, expected_image, expected_platform):
    if not re.fullmatch(r'[a-f0-9]{64}', approved_hash or ''):
        raise ComposeReleaseError('MANUAL_GO_HASH_REQUIRED')
    manifest_bytes = _read_regular(root, 'manifest.json', 2 * 1024 * 1024)
    if hashlib.sha256(manifest_bytes).hexdigest() != approved_hash:
        raise ComposeReleaseError('APPROVED_MANIFEST_MISMATCH')
    manifest = json.loads(manifest_bytes)
    if not _validate_release_manifest(manifest, expected_image, expected_platform):
        raise ComposeReleaseError('BUNDLE_BLOCKED')
    expected = {}
    for line in _read_regular(root, 'SHA256SUMS', 2 * 1024 * 1024).decode('utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        if not match or match[2] in expected or len(expected) >= 10000:
            raise ComposeReleaseError('CHECKSUM_LIST_INVALID')
        expected[match[2]] = match[1]
    if not REQUIRED_HASHED_FILES.issubset(expected):
        raise ComposeReleaseError('CHECKSUM_LIST_INCOMPLETE')
    # The trusted manifest binds every shipped file and evidence hash. A modified
    # checksum list alone cannot bless altered Compose or deployment code.
    bound = dict(manifest.get('public_file_sha256', {}))
    for field in ('scan', 'sbom'):
        item = manifest[field]
        bound[item['evidence_path']] = item['sha256']
    item = manifest['license_inventory']
    bound[item['path']] = item['sha256']
    for relative, digest in bound.items():
        if expected.get(relative) != digest:
            raise ComposeReleaseError('MANIFEST_CHECKSUM_MISMATCH')
    if not (REQUIRED_HASHED_FILES - {'manifest.json'}).issubset(bound):
        raise ComposeReleaseError('MANIFEST_CHECKSUM_INCOMPLETE')
    if expected['manifest.json'] != approved_hash:
        raise ComposeReleaseError('MANIFEST_CHECKSUM_MISMATCH')
    compose_snapshot = None
    for relative, digest in expected.items():
        content = _read_regular(root, relative)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ComposeReleaseError('BUNDLE_CHECKSUM_MISMATCH')
        if relative == 'docker-compose.yml':
            compose_snapshot = content
    return compose_snapshot


def _validate_rendered(rendered, values):
    services = rendered.get('services')
    if not isinstance(services, dict) or set(services) != {'google-manager', 'initialize', 'worker'}:
        raise ComposeReleaseError('COMPOSE_SERVICES_MISMATCH')
    expected = {field: values.get(field) or DEFAULTS.get(field, '')
                for field in APPLICATION_FIELDS}
    for service in services.values():
        if (not isinstance(service, dict) or 'build' in service
                or service.get('image') != values['GOOGLE_MANAGER_IMAGE']
                or service.get('environment') != expected):
            raise ComposeReleaseError('COMPOSE_CONFIG_MISMATCH')


def run_operation(*, operation, env_file, project_root, approved_manifest_sha256=None,
                  compose_binary=None, docker_config=None):
    if operation not in COMMANDS:
        raise ComposeReleaseError('OPERATION_INVALID')
    root = Path(project_root).resolve(strict=True)
    values = load_candidate_environment(env_file)
    report = evaluate_preflight(env_file=env_file, project_root=root)
    unresolved = [check for check in report['checks']
                  if check['status'] != 'passed'
                  and check['code'] != 'RELEASE_MANUAL_SIGNOFF_PENDING']
    if unresolved:
        raise ComposeReleaseError('PREFLIGHT_NOT_READY')
    if load_candidate_environment(env_file) != values:
        raise ComposeReleaseError('ENVIRONMENT_CHANGED')
    compose_snapshot = _read_regular(root, 'docker-compose.yml', 1024 * 1024)
    if operation in REQUIRES_GO:
        compose_snapshot = verify_approved_bundle(root, approved_manifest_sha256,
                                                  values['GOOGLE_MANAGER_IMAGE'],
                                                  _linux_platform(platform.machine()))
    executable = _resolve_compose_binary(compose_binary)
    docker_auth = _docker_auth_snapshot(docker_config)
    environment = controlled_environment(values)
    # Snapshot the Compose source in a private temporary directory. Configuration
    # validation and execution cannot race a replacement of the source YAML.
    with tempfile.TemporaryDirectory(prefix='google-manager-compose-', dir=_temporary_parent()) as directory:
        environment['HOME'] = directory
        environment['DOCKER_CONFIG'] = directory
        auth_file = Path(directory) / 'config.json'
        with os.fdopen(os.open(auth_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(docker_auth)
        compose_file = Path(directory) / 'compose.yml'
        with os.fdopen(os.open(compose_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(compose_snapshot)
        command = [executable, '--project-directory', str(root),
                   '--project-name', 'google-manager', '--env-file', os.devnull,
                   '--file', str(compose_file)]
        # The config output contains secrets; it is parsed only in memory and
        # never forwarded to stdout/stderr, including on errors.
        result = subprocess.run(command + ['config', '--format', 'json'], cwd=root,
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', check=False, timeout=60)
        if result.returncode:
            raise ComposeReleaseError('COMPOSE_CONFIG_FAILED')
        _validate_rendered(json.loads(result.stdout), values)
        if operation != 'config':
            result = subprocess.run(command + COMMANDS[operation], cwd=root,
                                    env=environment, capture_output=True, text=True,
                                    encoding='utf-8', check=False, timeout=900)
            if result.returncode:
                raise ComposeReleaseError('COMPOSE_OPERATION_FAILED')
    report = {'operation': operation, 'status': 'completed',
              'production_approved_by_tool': False}
    if operation == 'status':
        output = result.stdout.strip()
        entries = json.loads(output) if output.startswith('[') else [json.loads(line) for line in output.splitlines()]
        allowed_states = {'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'}
        statuses = []
        for item in entries:
            if (item.get('Service') not in {'google-manager', 'initialize', 'worker'}
                    or item.get('State') not in allowed_states
                    or item.get('Health', '') not in {'', 'starting', 'healthy', 'unhealthy'}):
                raise ComposeReleaseError('COMPOSE_STATUS_INVALID')
            statuses.append({field: item.get(field) for field in ('Service', 'State', 'Health', 'ExitCode')})
        report['services'] = statuses
    if operation in {'inventory-encryption', 'apply-encryption'}:
        match = re.search(r'account_values=(\d+), history_values=(\d+), recharge_task_values=(\d+)', result.stdout)
        if not match:
            raise ComposeReleaseError('ENCRYPTION_RESULT_INVALID')
        report['counts'] = dict(zip(('account_values', 'history_values', 'recharge_task_values'),
                                   map(int, match.groups())))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=COMMANDS)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--compose-binary', help='Absolute, root-owned Compose executable; system plugin paths are the default.')
    parser.add_argument('--docker-config', help='Optional root-owned registry auth directory (config.json must be 0600).')
    parser.add_argument('--approved-manifest-sha256',
                        help='Hash from the independently approved GO record; never auto-read from the bundle.')
    args = parser.parse_args(argv)
    try:
        report = run_operation(**vars(args))
    except ComposeReleaseError as error:
        report = {'status': 'failed', 'code': str(error)}
    except Exception:
        report = {'status': 'failed', 'code': 'CONTROLLED_COMPOSE_FAILED'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
