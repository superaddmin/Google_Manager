import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from deploy.preflight import (
    EXPECTED_RELEASE_BUNDLE_FILES,
    EXPECTED_RELEASE_LOCKFILES,
    EXPECTED_RELEASE_PUBLIC_FILES,
    _restricted_file_permissions,
    evaluate_preflight,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = PROJECT_ROOT / 'deploy' / 'preflight.py'


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / 'instance').mkdir()
        (self.root / 'googlemail' / 'runtime').mkdir(parents=True)
        (self.root / 'googlemail' / 'output').mkdir()
        for directory in (
            self.root / 'instance',
            self.root / 'googlemail' / 'runtime',
            self.root / 'googlemail' / 'output',
        ):
            directory.chmod(0o700)
        self.credentials = self.root / 'credentials.json'
        self.credentials.write_text(json.dumps({
            'web': {
                'client_id': 'synthetic-client-id',
                'client_secret': 'synthetic-client-secret',
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        }), encoding='utf-8')
        self.credentials.chmod(0o600)
        self.env_file = self.root / 'candidate.env'
        self.manifest = self.root / 'manifest.json'
        self._write_valid_release_bundle()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _valid_values(self):
        fernet_key = base64.urlsafe_b64encode(b'f' * 32).decode('ascii')
        return {
            'FLASK_ENV': 'production',
            'ADMIN_PASSWORD': 'StrongSyntheticPassword_123!',
            'SECRET_KEY': '0123456789abcdef' * 4,
            'GMAIL_TOKEN_ENCRYPTION_KEY': fernet_key,
            'GOOGLE_MANAGER_IMAGE': (
                'registry.production.example.org/google-manager@sha256:' + 'a' * 64
            ),
            'PUBLIC_DOMAIN': 'app.production.example.org',
            'RECHARGE_MODE': 'disabled',
            'RECHARGE_UPSTREAM_URL': '',
            'RECHARGE_UPSTREAM_ALLOWED_HOSTS': 'upstream.production.example.org',
            'DATABASE_URL': '',
            'GMAIL_CLIENT_SECRET_FILE': '/app/credentials.json',
            'GMAIL_REDIRECT_URI': (
                'https://app.production.example.org/api/gmail/oauth/callback'
            ),
            'GMAIL_PUBSUB_TOPIC': '',
            'GMAIL_PUBSUB_VERIFICATION_TOKEN': '',
            'PROXY': '',
            'HEADLESS': 'true',
            'TRUSTED_PROXY_CIDRS': '172.30.8.1/32',
            'GUNICORN_BIND': '0.0.0.0:8002',
            'GUNICORN_WORKERS': '2',
            'GUNICORN_THREADS': '4',
            'GUNICORN_TIMEOUT': '120',
            'GUNICORN_LOG_LEVEL': 'info',
        }

    def _valid_manifest(self):
        digest = 'b' * 64
        release_digest = lambda relative_path: (
            self._sha256(self.root / relative_path)
            if hasattr(self, 'root') and (self.root / relative_path).is_file()
            else digest
        )
        return {
            'schema_version': 1,
            'release_status': 'AWAITING_MANUAL_SIGNOFF',
            'production_approved': False,
            'signoff_decision': 'NO-GO',
            'generated_at': '2026-09-20T00:00:00+00:00',
            'scope': {
                'included': 'CDK workbench',
                'excluded': ['cash payments', 'payment channels', 'payment callbacks'],
            },
            'git': {'sha': 'c' * 40, 'dirty': False},
            'platform': 'linux/amd64',
            'image': self._valid_values()['GOOGLE_MANAGER_IMAGE'],
            'ci_run_url': 'https://ci.production.example.org/runs/123',
            'scan': {
                'severity_counts': {
                    'UNKNOWN': 0, 'LOW': 1, 'MEDIUM': 1, 'HIGH': 0, 'CRITICAL': 0,
                },
                'high_critical_count': 0,
                'suppressed_findings_count': 0,
                'result_count': 1,
                'result_types': {'debian': 1},
                'evidence_path': 'evidence/trivy.json',
                'sha256': release_digest('evidence/trivy.json'),
            },
            'sbom': {
                'component_count': 2,
                'missing_license_metadata_count': 0,
                'evidence_path': 'evidence/sbom.cdx.json',
                'sha256': release_digest('evidence/sbom.cdx.json'),
            },
            'license_inventory': {
                'path': 'LICENSE-INVENTORY.json',
                'sha256': release_digest('LICENSE-INVENTORY.json'),
            },
            'lockfile_sha256': {path: digest for path in EXPECTED_RELEASE_LOCKFILES},
            'public_file_sha256': {
                path: release_digest(path)
                for path in EXPECTED_RELEASE_PUBLIC_FILES
            },
            'blockers': [],
        }

    @staticmethod
    def _sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_valid_release_bundle(self):
        for relative_path in EXPECTED_RELEASE_PUBLIC_FILES:
            path = self.root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'synthetic release file: {relative_path}\n', encoding='utf-8')
        evidence = {
            'evidence/trivy.json': '{"synthetic":"trivy"}\n',
            'evidence/sbom.cdx.json': '{"synthetic":"sbom"}\n',
            'LICENSE-INVENTORY.json': '{"synthetic":"licenses"}\n',
            'README.md': '# Synthetic release bundle\n',
            'RELEASE-SIGNOFF.md': 'Status: NO-GO\n',
        }
        for relative_path, content in evidence.items():
            path = self.root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8', newline='\n')
        self.manifest.write_text(
            json.dumps(self._valid_manifest()), encoding='utf-8', newline='\n'
        )
        checksum_lines = [
            f'{self._sha256(self.root / relative_path)}  {relative_path}'
            for relative_path in sorted(EXPECTED_RELEASE_BUNDLE_FILES)
        ]
        (self.root / 'SHA256SUMS').write_text(
            '\n'.join(checksum_lines) + '\n', encoding='ascii', newline='\n'
        )

    def _write_env(self, values=None, raw_text=None):
        if raw_text is None:
            values = values or self._valid_values()
            raw_text = ''.join(f'{key}={value}\n' for key, value in values.items())
        self.env_file.write_text(raw_text, encoding='utf-8', newline='\n')
        self.env_file.chmod(0o600)

    def _evaluate(self, **overrides):
        options = {
            'env_file': self.env_file,
            'project_root': self.root,
            'credentials_file': self.credentials,
            'expected_runtime_uid': self.credentials.stat().st_uid,
            'platform_name': 'Linux',
        }
        options.update(overrides)
        with ExitStack() as stack:
            if sys.platform == 'win32' and options['platform_name'] == 'Linux':
                stack.enter_context(mock.patch(
                    'deploy.preflight._restricted_file_permissions', return_value=True
                ))
                stack.enter_context(mock.patch(
                    'deploy.preflight._runtime_permissions', return_value=True
                ))
            return evaluate_preflight(**options)

    @staticmethod
    def _codes(report):
        return {check['code'] for check in report['checks']}

    def test_valid_linux_candidate_awaits_manual_signoff(self):
        self._write_env()

        report = self._evaluate()

        self.assertEqual('pending', report['overall_status'])
        self.assertEqual(2, report['exit_code'])
        self.assertEqual(0, report['summary']['failed'])
        self.assertEqual(1, report['summary']['pending'])
        self.assertIn('RELEASE_MANUAL_SIGNOFF_PENDING', self._codes(report))

    def test_missing_image_digest_is_pending_not_passed(self):
        values = self._valid_values()
        values.pop('GOOGLE_MANAGER_IMAGE')
        self._write_env(values)
        self.manifest.unlink()

        report = self._evaluate()

        self.assertEqual('pending', report['overall_status'])
        self.assertIn('IMAGE_DIGEST_PENDING', self._codes(report))

    def test_template_placeholders_and_weak_secrets_fail_without_leaking_values(self):
        values = self._valid_values()
        leaked_password = 'do-not-print-this-password'
        values.update({
            'ADMIN_PASSWORD': leaked_password,
            'SECRET_KEY': 'CHANGE_ME',
            'GMAIL_TOKEN_ENCRYPTION_KEY': 'CHANGE_ME',
            'GOOGLE_MANAGER_IMAGE': 'registry.example.invalid/app@sha256:CHANGE_ME',
            'PUBLIC_DOMAIN': 'app.example.invalid',
        })
        self._write_env(values)

        report = self._evaluate()
        serialized = json.dumps(report)

        self.assertEqual('failed', report['overall_status'])
        self.assertIn('ADMIN_PASSWORD_INVALID', self._codes(report))
        self.assertIn('SECRET_KEY_INVALID', self._codes(report))
        self.assertIn('FERNET_KEY_INVALID', self._codes(report))
        self.assertIn('IMAGE_DIGEST_INVALID', self._codes(report))
        self.assertIn('PUBLIC_DOMAIN_INVALID', self._codes(report))
        self.assertNotIn(leaked_password, serialized)
        self.assertNotIn('CHANGE_ME', serialized)

    def test_strict_dotenv_rejects_unknown_duplicate_and_shell_syntax(self):
        cases = (
            ('UNKNOWN_SECRET=hidden\n', 'ENV_UNKNOWN_FIELD'),
            ('FLASK_ENV=production\nFLASK_ENV=production\n', 'ENV_DUPLICATE_FIELD'),
            ('SECRET_KEY=${INHERITED_SECRET}\n', 'ENV_INTERPOLATION_FORBIDDEN'),
            ('SECRET_KEY=value;touch /tmp/preflight-pwned\n', 'ENV_INTERPOLATION_FORBIDDEN'),
            ('export FLASK_ENV=production\n', 'ENV_SYNTAX_INVALID'),
            ('SECRET_KEY="value" # "strong-suffix"\n', 'ENV_SYNTAX_INVALID'),
        )
        for raw_text, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self._write_env(raw_text=raw_text)
                report = self._evaluate()
                self.assertEqual('failed', report['overall_status'])
                self.assertIn(expected_code, self._codes(report))
                self.assertNotIn('hidden', json.dumps(report))
                self.assertNotIn('preflight-pwned', json.dumps(report))

    def test_invalid_fernet_key_fails(self):
        values = self._valid_values()
        values['GMAIL_TOKEN_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(b'short').decode('ascii')
        self._write_env(values)

        report = self._evaluate()

        self.assertEqual('failed', report['overall_status'])
        self.assertIn('FERNET_KEY_INVALID', self._codes(report))

    def test_inline_comment_cannot_change_compose_secret(self):
        values = self._valid_values()
        values['ADMIN_PASSWORD'] = 'a' * 16 + ' #StrongSynthetic_123!'
        self._write_env(values)
        report = self._evaluate()
        self.assertIn('ENV_SYNTAX_INVALID', self._codes(report))
        self.assertNotIn(values['ADMIN_PASSWORD'], json.dumps(report))

    def test_blocked_missing_and_mismatched_release_manifest_never_pass(self):
        self._write_env()
        for update in ({'release_status': 'BLOCKED'}, {'image': 'unrelated'}, {'platform': 'unknown'}):
            metadata = {**self._valid_manifest(), **update}
            self.manifest.write_text(json.dumps(metadata), encoding='utf-8')
            self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(self._evaluate()))
        self.manifest.unlink()
        self.assertEqual(self._evaluate()['overall_status'], 'pending')

    def test_manifest_release_evidence_schema_fails_closed(self):
        self._write_env()
        mutations = (
            ('approved', lambda value: value.update(production_approved=True)),
            ('decision', lambda value: value.update(signoff_decision='GO')),
            ('blockers', lambda value: value.update(blockers=['open_gate'])),
            ('dirty', lambda value: value['git'].update(dirty=True)),
            ('critical', lambda value: value['scan']['severity_counts'].update(CRITICAL=1)),
            ('high_count', lambda value: value['scan'].update(high_critical_count=1)),
            ('suppressed', lambda value: value['scan'].update(suppressed_findings_count=1)),
            ('license', lambda value: value['sbom'].update(missing_license_metadata_count=1)),
            ('ci_url', lambda value: value.update(ci_run_url='https://user:secret@ci.example.org/run')),
            ('hash', lambda value: value['scan'].update(sha256='not-a-digest')),
            ('extra', lambda value: value.update(unreviewed=True)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                metadata = self._valid_manifest()
                mutate(metadata)
                self.manifest.write_text(json.dumps(metadata), encoding='utf-8')
                report = self._evaluate()
                self.assertEqual('failed', report['overall_status'])
                self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(report))
                self.assertNotIn('secret', json.dumps(report))

    def test_manifest_requires_untampered_referenced_bundle_files(self):
        self._write_env()
        target = self.root / 'evidence' / 'trivy.json'
        target.write_text('{"tampered":true}\n', encoding='utf-8')

        report = self._evaluate()

        self.assertEqual('failed', report['overall_status'])
        self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(report))

        self._write_valid_release_bundle()
        (self.root / 'SHA256SUMS').unlink()
        report = self._evaluate()
        self.assertEqual('failed', report['overall_status'])
        self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(report))

        self._write_valid_release_bundle()
        self.manifest.unlink()
        try:
            self.manifest.symlink_to('missing-manifest.json')
        except OSError:
            return
        report = self._evaluate()
        self.assertEqual('failed', report['overall_status'])
        self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(report))

    def test_source_lockfiles_are_recorded_but_not_required_in_bundle(self):
        self._write_env()

        self.assertTrue(all(not (self.root / path).exists() for path in EXPECTED_RELEASE_LOCKFILES))
        report = self._evaluate()

        self.assertEqual('pending', report['overall_status'])
        self.assertEqual({'RELEASE_MANUAL_SIGNOFF_PENDING'}, {
            check['code'] for check in report['checks'] if check['status'] != 'passed'
        })

    def test_manifest_platform_must_match_linux_host(self):
        self._write_env()
        for machine_name in ('aarch64', 'ppc64le'):
            with self.subTest(machine_name=machine_name):
                report = self._evaluate(machine_name=machine_name)
                self.assertEqual('failed', report['overall_status'])
                self.assertIn('RELEASE_PLATFORM_INVALID', self._codes(report))
                self.assertIn('RELEASE_MANIFEST_INVALID', self._codes(report))

    def test_database_and_credentials_match_actual_compose_mounts(self):
        for value in ('sqlite:///:memory:', 'sqlite:////tmp/lost.db', 'postgresql://private:secret@db/data'):
            values = self._valid_values()
            values['DATABASE_URL'] = value
            self._write_env(values)
            report = self._evaluate()
            self.assertIn('DATABASE_PROFILE_INVALID', self._codes(report))
            self.assertNotIn(value, json.dumps(report))
        values = self._valid_values()
        values['GMAIL_CLIENT_SECRET_FILE'] = '/different/file.json'
        self._write_env(values)
        self.assertIn('OAUTH_MOUNT_INVALID', self._codes(self._evaluate()))

    def test_trusted_proxies_are_canonical_private_host_addresses(self):
        for value in ('172.30.8.1/24', '0.0.0.0/1', '8.8.8.8/32', '0.0.0.0/32', '::/128'):
            with self.subTest(value=value):
                values = self._valid_values()
                values['TRUSTED_PROXY_CIDRS'] = value
                self._write_env(values)
                self.assertIn('TRUSTED_PROXY_INVALID', self._codes(self._evaluate()))

    def test_live_mode_requires_safe_https_exact_allowed_host(self):
        invalid_pairs = (
            ('http://upstream.production.example.org/api', 'upstream.production.example.org'),
            ('https://other.production.example.org/api', 'upstream.production.example.org'),
            ('https://127.0.0.1/api', '127.0.0.1'),
            ('https://user:password@upstream.production.example.org/api', 'upstream.production.example.org'),
            ('https://upstream.production.example.org/api?token=secret', 'upstream.production.example.org'),
        )
        for upstream_url, allowed_hosts in invalid_pairs:
            with self.subTest(upstream_url=upstream_url):
                values = self._valid_values()
                values.update({
                    'RECHARGE_MODE': 'live',
                    'RECHARGE_UPSTREAM_URL': upstream_url,
                    'RECHARGE_UPSTREAM_ALLOWED_HOSTS': allowed_hosts,
                })
                self._write_env(values)
                report = self._evaluate()
                self.assertEqual('failed', report['overall_status'])
                self.assertIn('RECHARGE_LIVE_URL_INVALID', self._codes(report))
                self.assertNotIn(upstream_url, json.dumps(report))

        values = self._valid_values()
        values.update({
            'RECHARGE_MODE': 'live',
            'RECHARGE_UPSTREAM_URL': 'https://upstream.production.example.org/api',
            'RECHARGE_UPSTREAM_ALLOWED_HOSTS': 'upstream.production.example.org',
        })
        self._write_env(values)
        report = self._evaluate()
        self.assertEqual('pending', report['overall_status'])
        self.assertIn('RELEASE_MANUAL_SIGNOFF_PENDING', self._codes(report))

    def test_bad_oauth_file_and_missing_runtime_directories_fail(self):
        self._write_env()
        self.credentials.write_text('{invalid', encoding='utf-8')
        (self.root / 'googlemail' / 'output').rmdir()

        report = self._evaluate()

        self.assertEqual('failed', report['overall_status'])
        self.assertIn('OAUTH_FILE_INVALID', self._codes(report))
        self.assertIn('RUNTIME_DIRECTORY_INVALID', self._codes(report))

    def test_oauth_file_requires_official_google_endpoints(self):
        self._write_env()
        for auth_uri in (
            'https://accounts.google.com/o/oauth2/auth',
            'https://accounts.google.com/o/oauth2/v2/auth',
        ):
            with self.subTest(auth_uri=auth_uri):
                document = {
                    'web': {
                        'client_id': 'synthetic-client-id',
                        'client_secret': 'synthetic-client-secret',
                        'auth_uri': auth_uri,
                        'token_uri': 'https://oauth2.googleapis.com/token',
                    }
                }
                self.credentials.write_text(json.dumps(document), encoding='utf-8')
                self.credentials.chmod(0o600)
                self.assertNotIn('OAUTH_FILE_INVALID', self._codes(self._evaluate()))

        document['web']['auth_uri'] = 'https://accounts.example.org/auth'
        self.credentials.write_text(json.dumps(document), encoding='utf-8')
        self.assertIn('OAUTH_FILE_INVALID', self._codes(self._evaluate()))

        document['web']['auth_uri'] = 'https://accounts.google.com/o/oauth2/auth'
        document['web']['token_uri'] = 'https://oauth.example.org/token'
        self.credentials.write_text(json.dumps(document), encoding='utf-8')
        self.assertIn('OAUTH_FILE_INVALID', self._codes(self._evaluate()))

    def test_pubsub_topic_and_strong_token_must_be_configured_together(self):
        invalid_pairs = (
            ('projects/synthetic-project/topics/gmail-events', ''),
            ('', 'StrongSyntheticPubSubToken_1234567890'),
            ('projects/synthetic-project/topics/gmail-events', 'x'),
            ('projects/INVALID/topics/gmail-events', 'StrongSyntheticPubSubToken_1234567890'),
            ('projects/synthetic-project/topics/gmail-events', 'a' * 32),
        )
        for topic, token in invalid_pairs:
            with self.subTest(topic=topic, token_length=len(token)):
                values = self._valid_values()
                values['GMAIL_PUBSUB_TOPIC'] = topic
                values['GMAIL_PUBSUB_VERIFICATION_TOKEN'] = token
                self._write_env(values)
                report = self._evaluate()
                self.assertEqual('failed', report['overall_status'])
                self.assertIn('PUBSUB_CONFIG_INVALID', self._codes(report))
                if len(token) >= 16:
                    self.assertNotIn(token, json.dumps(report))

        values = self._valid_values()
        values['GMAIL_PUBSUB_TOPIC'] = 'projects/synthetic-project/topics/gmail-events'
        values['GMAIL_PUBSUB_VERIFICATION_TOKEN'] = 'StrongSyntheticPubSubToken_1234567890'
        self._write_env(values)
        report = self._evaluate()
        self.assertEqual('pending', report['overall_status'])
        self.assertNotIn('PUBSUB_CONFIG_INVALID', self._codes(report))

    @unittest.skipIf(os.name == 'nt', 'POSIX file types and ownership are verified in Linux CI')
    def test_runtime_tree_rejects_insecure_files_links_and_special_nodes(self):
        self._write_env()
        instance = self.root / 'instance'
        insecure = instance / 'accounts.db'
        insecure.write_bytes(b'synthetic')
        insecure.chmod(0o644)
        self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(self._evaluate()))

        insecure.unlink()
        link = instance / 'linked.db'
        link.symlink_to(self.credentials)
        self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(self._evaluate()))

        link.unlink()
        pipe = instance / 'unexpected.pipe'
        os.mkfifo(pipe, 0o600)
        self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(self._evaluate()))

    def test_environment_file_owner_must_be_root_or_deploy_user(self):
        safe_mode = stat.S_IFREG | 0o600
        with mock.patch.object(
            Path, 'lstat', return_value=SimpleNamespace(st_mode=safe_mode, st_uid=2000)
        ):
            self.assertFalse(_restricted_file_permissions(Path('candidate.env'), {0, 10001}))
            self.assertTrue(_restricted_file_permissions(Path('candidate.env'), {0, 2000}))

    @unittest.skipIf(os.name == 'nt', 'bounded POSIX tree walk is verified in Linux CI')
    def test_runtime_tree_walk_is_bounded(self):
        self._write_env()
        (self.root / 'instance' / 'one.db').write_bytes(b'one')
        (self.root / 'instance' / 'one.db').chmod(0o600)
        with mock.patch('deploy.preflight.MAX_RUNTIME_ENTRIES', 0):
            self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(self._evaluate()))

    def test_non_linux_permissions_are_pending(self):
        self._write_env()

        report = self._evaluate(platform_name='Windows')

        self.assertEqual('pending', report['overall_status'])
        self.assertIn('ENV_FILE_PERMISSION_PENDING', self._codes(report))
        self.assertIn('OAUTH_FILE_PERMISSION_PENDING', self._codes(report))
        self.assertIn('RUNTIME_PERMISSION_PENDING', self._codes(report))

    def test_preflight_is_read_only(self):
        self._write_env()
        tracked_paths = [
            self.env_file,
            self.credentials,
            self.root / 'instance',
            self.root / 'googlemail' / 'runtime',
            self.root / 'googlemail' / 'output',
        ]
        before = {
            path: (path.stat().st_mode, path.stat().st_size, path.stat().st_mtime_ns)
            for path in tracked_paths
        }

        report = self._evaluate()

        after = {
            path: (path.stat().st_mode, path.stat().st_size, path.stat().st_mtime_ns)
            for path in tracked_paths
        }
        self.assertEqual('pending', report['overall_status'])
        self.assertIn('RELEASE_MANUAL_SIGNOFF_PENDING', self._codes(report))
        self.assertEqual(before, after)

    def test_cli_help_and_missing_argument_are_safe(self):
        help_result = subprocess.run(
            [sys.executable, str(PREFLIGHT_SCRIPT), '--help'],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, help_result.returncode)
        self.assertIn('--env-file', help_result.stdout)

        missing_result = subprocess.run(
            [sys.executable, str(PREFLIGHT_SCRIPT)],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(1, missing_result.returncode)
        report = json.loads(missing_result.stdout)
        self.assertEqual('failed', report['overall_status'])
        self.assertIn('ENV_FILE_REQUIRED', self._codes(report))
        self.assertEqual('', missing_result.stderr)

    def test_cli_rejects_runtime_uid_override(self):
        self._write_env()
        result = subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT_SCRIPT),
                '--env-file',
                str(self.env_file),
                '--project-root',
                str(self.root),
                '--credentials-file',
                str(self.credentials),
                '--expected-runtime-uid',
                '0',
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(1, result.returncode)
        self.assertEqual('', result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual('failed', report['overall_status'])
        self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(report))

    def test_cli_does_not_inherit_or_read_local_dotenv(self):
        leaked_value = 'local-dotenv-secret-must-not-appear'
        (self.root / '.env').write_text(
            f'ADMIN_PASSWORD={leaked_value}\n', encoding='utf-8'
        )
        self._write_env()
        command = [
            sys.executable,
            str(PREFLIGHT_SCRIPT),
            '--env-file',
            str(self.env_file),
            '--project-root',
            str(self.root),
            '--credentials-file',
            str(self.credentials),
        ]

        result = subprocess.run(
            command,
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        linux_uid_mismatch = sys.platform != 'win32' and self.credentials.stat().st_uid != 10001
        self.assertEqual(1 if linux_uid_mismatch else 2, result.returncode)
        self.assertNotIn(leaked_value, result.stdout)
        self.assertEqual('', result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual('failed' if linux_uid_mismatch else 'pending', report['overall_status'])
        if linux_uid_mismatch:
            self.assertIn('RUNTIME_PERMISSION_INVALID', self._codes(report))


if __name__ == '__main__':
    unittest.main()
