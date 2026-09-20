import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy import compose_release as release
from tests import test_deployment_preflight as preflight_fixtures


class ControlledComposeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'docker-compose.yml').write_text('services: {}\n', encoding='utf-8')
        self.values = {field: release.DEFAULTS.get(field, '') for field in release.APPLICATION_FIELDS}
        self.values.update({'GOOGLE_MANAGER_IMAGE': 'registry.example.org/app@sha256:' + 'a' * 64,
                            'ADMIN_PASSWORD': 'synthetic-secret-never-in-output',
                            'SECRET_KEY': 'synthetic-key', 'GMAIL_TOKEN_ENCRYPTION_KEY': 'synthetic-fernet'})
        self.rendered = {'services': {name: {'image': self.values['GOOGLE_MANAGER_IMAGE'],
                                           'environment': {field: self.values[field] for field in release.APPLICATION_FIELDS}}
                                      for name in ('google-manager', 'initialize', 'worker')}}

    def _run(self, operation='config', **kwargs):
        with mock.patch.object(release, 'load_candidate_environment', return_value=self.values), \
             mock.patch.object(release, '_resolve_compose_binary', return_value='/usr/libexec/docker/cli-plugins/docker-compose'), \
             mock.patch.object(release, '_temporary_parent', return_value=self.root), \
             mock.patch.object(release, 'evaluate_preflight', return_value={'checks': [
                 {'status': 'pending', 'code': 'RELEASE_MANUAL_SIGNOFF_PENDING'}]}):
            return release.run_operation(operation=operation, env_file=self.root / 'candidate.env',
                                         project_root=self.root, **kwargs)

    def test_inherited_compose_docker_and_secret_overrides_are_removed(self):
        inherited = {'PATH': '/usr/bin', 'HOME': '/home/operator',
                     'GOOGLE_MANAGER_IMAGE': 'unreviewed:latest', 'SECRET_KEY': 'wrong',
                     'COMPOSE_FILE': 'attacker.yml', 'COMPOSE_PROFILES': 'hidden',
                     'DOCKER_HOST': 'tcp://untrusted:2375', 'DOCKER_CONTEXT': 'production-other',
                     'PYTHONPATH': '/untrusted'}
        environment = release.controlled_environment(self.values, inherited)
        self.assertEqual(environment['SECRET_KEY'], 'synthetic-key')
        self.assertEqual(environment['GOOGLE_MANAGER_IMAGE'], self.values['GOOGLE_MANAGER_IMAGE'])
        self.assertEqual(environment['COMPOSE_DISABLE_ENV_FILE'], '1')
        for field in ('COMPOSE_FILE', 'COMPOSE_PROFILES', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'PYTHONPATH'):
            self.assertNotIn(field, environment)

    def test_pull_reuses_validated_snapshot_and_never_prints_expanded_secrets(self):
        seen = []

        def execute(command, **options):
            seen.append((command, options))
            snapshot = Path(command[command.index('--file') + 1])
            self.assertEqual(snapshot.read_text(encoding='utf-8'), 'services: {}\n')
            if len(seen) == 1:
                (self.root / 'docker-compose.yml').write_text('services: modified\n', encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, json.dumps(self.rendered), '')
            return subprocess.CompletedProcess(command, 0, self.values['ADMIN_PASSWORD'], '')

        with mock.patch.object(release.subprocess, 'run', side_effect=execute):
            result = self._run('pull')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen[0][1]['env'], seen[1][1]['env'])
        self.assertEqual(seen[1][0][-1], 'pull')
        self.assertNotIn(self.values['ADMIN_PASSWORD'], json.dumps(result))
        self.assertFalse(Path(seen[0][0][seen[0][0].index('--file') + 1]).exists())

    def test_rendered_image_secret_or_build_mismatch_prevents_operation(self):
        for key, value in (('image', 'unreviewed:latest'), ('environment', {}), ('build', '.')):
            with self.subTest(key=key):
                rendered = json.loads(json.dumps(self.rendered))
                rendered['services']['worker'][key] = value
                with mock.patch.object(release.subprocess, 'run', return_value=
                                       subprocess.CompletedProcess([], 0, json.dumps(rendered), '')) as run:
                    with self.assertRaisesRegex(release.ComposeReleaseError, 'COMPOSE_CONFIG_MISMATCH'):
                        self._run('pull')
                self.assertEqual(run.call_count, 1)

    def test_pending_or_failed_preflight_cannot_deploy(self):
        with mock.patch.object(release, 'load_candidate_environment', return_value=self.values), \
             mock.patch.object(release, 'evaluate_preflight', return_value={'checks': [
                 {'status': 'pending', 'code': 'RELEASE_MANIFEST_PENDING'}]}), \
             mock.patch.object(release.subprocess, 'run') as run:
            with self.assertRaisesRegex(release.ComposeReleaseError, 'PREFLIGHT_NOT_READY'):
                release.run_operation(operation='up', env_file='candidate.env', project_root=self.root)
            run.assert_not_called()

    def test_manual_go_hash_is_required_before_any_deploy_command(self):
        with mock.patch.object(release.subprocess, 'run') as run:
            for operation in release.REQUIRES_GO:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(release.ComposeReleaseError, 'MANUAL_GO_HASH_REQUIRED'):
                        self._run(operation)
            run.assert_not_called()

    def _bundle(self):
        manifest = preflight_fixtures.DeploymentPreflightTests()._valid_manifest()
        manifest['image'] = self.values['GOOGLE_MANAGER_IMAGE']
        files = {relative: b'synthetic\n' for relative in manifest['public_file_sha256']}
        files.update({'evidence/trivy.json': b'{}', 'evidence/sbom.cdx.json': b'{}',
                      'LICENSE-INVENTORY.json': b'{}'})
        digest = lambda data: hashlib.sha256(data).hexdigest()
        manifest['public_file_sha256'] = {relative: digest(data) for relative, data in files.items()
                                           if relative in manifest['public_file_sha256']}
        for field in ('scan', 'sbom', 'license_inventory'):
            manifest[field]['sha256'] = digest(b'{}')
        files['manifest.json'] = json.dumps(manifest).encode()
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.root / 'SHA256SUMS').write_text(''.join(f'{digest(content)}  {relative}\n'
                                                   for relative, content in sorted(files.items())), encoding='utf-8')
        return digest(files['manifest.json'])

    def _verify(self, approved_hash):
        return release.verify_approved_bundle(self.root, approved_hash,
                                               self.values['GOOGLE_MANAGER_IMAGE'], 'linux/amd64')

    def test_checksums_are_bound_to_independently_approved_manifest(self):
        approved_hash = self._bundle()
        self.assertEqual(self._verify(approved_hash), b'synthetic\n')
        (self.root / 'docker-compose.yml').write_bytes(b'tampered')
        with self.assertRaisesRegex(release.ComposeReleaseError, 'BUNDLE_CHECKSUM_MISMATCH'):
            self._verify(approved_hash)
        checksums = self.root / 'SHA256SUMS'
        content = checksums.read_text(encoding='utf-8')
        content = content.replace(hashlib.sha256(b'synthetic\n').hexdigest() + '  docker-compose.yml',
                                  hashlib.sha256(b'tampered').hexdigest() + '  docker-compose.yml')
        checksums.write_text(content, encoding='utf-8')
        with self.assertRaisesRegex(release.ComposeReleaseError, 'MANIFEST_CHECKSUM_MISMATCH'):
            self._verify(approved_hash)

    def test_checksums_reject_traversal(self):
        for malformed in ('../outside', '/absolute', 'C:/absolute', 'deploy/../outside'):
            approved_hash = self._bundle()
            with (self.root / 'SHA256SUMS').open('a', encoding='utf-8') as stream:
                stream.write('a' * 64 + '  ' + malformed + '\n')
            with self.assertRaises(release.ComposeReleaseError):
                self._verify(approved_hash)

    def test_approved_manifest_must_match_actual_image_and_schema(self):
        for change in ({'image': 'other@sha256:' + 'f' * 64}, {'schema_version': 99}, {'platform': 'linux/arm64'}):
            self._bundle()
            manifest_path = self.root / 'manifest.json'
            metadata = json.loads(manifest_path.read_bytes())
            metadata.update(change)
            content = json.dumps(metadata).encode()
            manifest_path.write_bytes(content)
            with self.assertRaisesRegex(release.ComposeReleaseError, 'BUNDLE_BLOCKED'):
                self._verify(hashlib.sha256(content).hexdigest())

    def test_start_waits_for_health_and_mutating_commands_require_go(self):
        self.assertIn('--wait', release.COMMANDS['up'])
        self.assertIn('--wait-timeout', release.COMMANDS['up'])
        self.assertEqual(release.REQUIRES_GO, {'up', 'init-db', 'inventory-encryption', 'apply-encryption'})
        self.assertNotIn('stop', release.COMMANDS)

    def test_untrusted_executable_is_rejected_before_secrets_are_sent(self):
        with self.assertRaisesRegex(release.ComposeReleaseError, 'COMPOSE_EXECUTABLE_UNTRUSTED'):
            release._trusted_executable('docker-compose')

    def test_temporary_parent_ignores_environment_and_requires_root_sticky_directory(self):
        from types import SimpleNamespace
        import stat
        for owner, mode, valid in ((0, 0o1777, True), (1000, 0o1777, False), (0, 0o777, False)):
            with mock.patch.dict(release.os.environ, {'TMPDIR': '/attacker', 'TMP': '/attacker', 'TEMP': '/attacker'}), \
                 mock.patch.object(Path, 'lstat', return_value=SimpleNamespace(st_uid=owner, st_mode=stat.S_IFDIR | mode)):
                if valid:
                    self.assertEqual(release._temporary_parent(), Path('/tmp'))
                else:
                    with self.assertRaisesRegex(release.ComposeReleaseError, 'TEMPORARY_PARENT_UNTRUSTED'):
                        release._temporary_parent()

    def test_cli_unexpected_error_redacts_paths_and_secrets(self):
        with mock.patch.object(release, 'run_operation', side_effect=RuntimeError('private-path synthetic-secret')), \
             mock.patch('builtins.print') as output:
            code = release.main(['config', '--env-file', 'private.env', '--project-root', '.'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.call_args.args[0]),
                         {'status': 'failed', 'code': 'CONTROLLED_COMPOSE_FAILED'})


if __name__ == '__main__':
    unittest.main()
