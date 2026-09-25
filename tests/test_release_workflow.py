import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'release-gate.yml'
IMAGE_DIGEST = 'sha256:' + 'a' * 64
CONFIG_DIGEST = 'sha256:' + 'b' * 64
GIT_SHA = 'c' * 40
IMAGE = 'ghcr.io/example/google-manager@' + IMAGE_DIGEST


def workflow_python(step_id):
    source = WORKFLOW.read_text(encoding='utf-8')
    step = source.split(f'        id: {step_id}\n', 1)[1].split('\n      - name:', 1)[0]
    script = textwrap.dedent(step.split('        run: |\n', 1)[1])
    return re.search(r"python3 - <<'PY'\n(.*?)\nPY", script, re.DOTALL).group(1)


@unittest.skipUnless(WORKFLOW.is_file(), 'Workflow is not shipped inside the runtime image')
class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = {
            **os.environ,
            'PYTHONPATH': str(ROOT),
            'PYTHONDONTWRITEBYTECODE': '1',
            'PUBLISH_IMAGE': 'true',
            'RELEASE_IMAGE': 'ghcr.io/example/google-manager',
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_SHA': GIT_SHA,
            'GITHUB_RUN_ID': '12345',
            'GITHUB_RUN_ATTEMPT': '2',
            'GITHUB_SERVER_URL': 'https://github.com',
            'GITHUB_REPOSITORY': 'example/google-manager',
            'GITHUB_OUTPUT': str(self.root / 'outputs'),
            'GITHUB_STEP_SUMMARY': str(self.root / 'summary'),
            'IMAGE_REF': IMAGE,
            'IMAGE_DIGEST': IMAGE_DIGEST,
            'CHECK_RESULTS': json.dumps({name: 'success' for name in (
                'runtime', 'compose', 'scan', 'tooling', 'recovery', 'sbom',
            )}),
        }

    def run_step(self, step_id, **environment):
        return subprocess.run(
            [sys.executable, '-B', '-c', workflow_python(step_id)],
            cwd=self.root, env={**self.environment, **environment},
            capture_output=True, text=True, encoding='utf-8', timeout=30,
        )

    def write_json(self, filename, value):
        (self.root / filename).write_text(json.dumps(value), encoding='utf-8')

    def prepare_evidence(self):
        self.write_json('build-metadata.json', {
            'containerimage.digest': IMAGE_DIGEST,
            'containerimage.config.digest': CONFIG_DIGEST,
        })
        self.inspected = {
            'Id': CONFIG_DIGEST, 'RepoDigests': [IMAGE], 'Architecture': 'amd64', 'Os': 'linux',
            'Config': {'Labels': {'org.opencontainers.image.revision': GIT_SHA}},
        }
        self.write_json('image-inspect.json', [self.inspected])
        (self.root / 'image-digest.txt').write_text(IMAGE + '\n', encoding='utf-8')
        self.write_json('container-smoke.json', {'healthy': True})
        self.write_json('recovery-drill.json', {'production_rpo_rto_validated': False})
        self.scan = {
            'SchemaVersion': 2, 'Trivy': {'Version': '0.70.0'},
            'CreatedAt': '2026-09-25T00:00:00+00:00',
            'ArtifactName': IMAGE, 'ArtifactType': 'container_image',
            'Metadata': {'RepoDigests': [IMAGE], 'ImageConfig': {'architecture': 'amd64', 'os': 'linux'}},
            'Results': [{'Target': 'fixture', 'Class': 'os-pkgs', 'Type': 'ubuntu',
                         'Packages': [{'Name': 'fixture', 'Version': '1.0'}], 'Vulnerabilities': []}],
        }
        self.write_json('image-vulnerabilities.json', self.scan)
        self.sbom = {
            'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1,
            'metadata': {'component': {'type': 'container', 'purl': (
                'pkg:oci/google-manager@' + IMAGE_DIGEST
                + '?arch=amd64&repository_url=ghcr.io%2Fexample%2Fgoogle-manager'
            )}},
            'components': [{'type': 'library', 'name': 'fixture', 'version': '1.0',
                            'licenses': [{'expression': 'MIT'}]}],
        }
        self.write_json('google-manager-sbom.cdx.json', self.sbom)
        (self.root / 'deploy').mkdir()
        self.write_json('deploy/browser-artifacts.json', {'version': 'fixture'})
        self.write_json('deploy/chromium-seccomp.json', {'defaultAction': 'SCMP_ACT_ERRNO'})
        (self.root / 'Dockerfile').write_text(
            'ARG RUNTIME_IMAGE=ubuntu@' + IMAGE_DIGEST + '\n', encoding='utf-8',
        )

    def evidence(self):
        return json.loads((self.root / 'build-evidence.json').read_text(encoding='utf-8'))

    def test_manual_main_run_produces_unique_registry_tag(self):
        result = self.run_step('release-target')
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = (self.root / 'outputs').read_text(encoding='utf-8')
        self.assertIn('registry=ghcr.io\n', outputs)
        self.assertIn(f'tag=ghcr.io/example/google-manager:{GIT_SHA}-12345-2\n', outputs)

    def test_pull_request_and_non_main_cannot_request_publication(self):
        for changes in ({'GITHUB_EVENT_NAME': 'pull_request'}, {'GITHUB_REF': 'refs/heads/topic'}):
            with self.subTest(changes=changes):
                result = self.run_step('release-target', **changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'outputs').exists())

    def test_checks_need_no_registry_configuration(self):
        result = self.run_step('release-target', PUBLISH_IMAGE='false', RELEASE_IMAGE='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('publish=false\nregistry=\ntag=google-manager:ci\n',
                      (self.root / 'outputs').read_text(encoding='utf-8'))

    def test_invalid_registry_targets_are_rejected_before_login(self):
        for image in ('', 'google-manager', 'owner/app', 'https://ghcr.io/owner/app',
                      'ghcr.io/owner/app:latest', 'ghcr.io/owner/app@' + IMAGE_DIGEST,
                      'registry.invalid:5000/owner/app', 'ghcr.io/owner/app\npublish=false'):
            with self.subTest(image=image):
                result = self.run_step('release-target', RELEASE_IMAGE=image)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'outputs').exists())

    def test_registry_evidence_binds_files_without_granting_go(self):
        self.prepare_evidence()
        result = self.run_step('release-evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.evidence()
        self.assertTrue(evidence['image_checks_passed'])
        self.assertTrue(evidence['registry_published'])
        self.assertFalse(evidence['production_approved'])
        self.assertEqual(evidence['missing_license_metadata_count'], 0)
        self.assertEqual(evidence['image'], IMAGE)
        for line in (self.root / 'EVIDENCE-SHA256SUMS').read_text(encoding='utf-8').splitlines():
            digest, filename = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.root / filename).read_bytes()).hexdigest())

    def test_pulled_image_revision_platform_or_digest_mismatch_blocks_evidence(self):
        self.prepare_evidence()
        for changed in ({'Id': 'sha256:' + 'd' * 64}, {'Architecture': 'arm64'}, {'RepoDigests': []},
                        {'Config': {'Labels': {'org.opencontainers.image.revision': 'd' * 40}}}):
            with self.subTest(changed=changed):
                self.write_json('image-inspect.json', [{**self.inspected, **changed}])
                result = self.run_step('release-evidence')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.evidence()['image_checks_passed'])

    def test_buildx_metadata_without_optional_config_digest_is_supported(self):
        self.prepare_evidence()
        self.write_json('build-metadata.json', {'containerimage.digest': IMAGE_DIGEST})
        result = self.run_step('release-evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.evidence()['image_config_digest'], CONFIG_DIGEST)

    def test_containerd_inspect_index_identity_is_not_mislabeled_as_config(self):
        self.prepare_evidence()
        self.write_json('image-inspect.json', [{**self.inspected, 'Id': IMAGE_DIGEST}])
        for config_digest in (CONFIG_DIGEST, None):
            with self.subTest(config_digest=config_digest):
                metadata = {'containerimage.digest': IMAGE_DIGEST}
                if config_digest is not None:
                    metadata['containerimage.config.digest'] = config_digest
                self.write_json('build-metadata.json', metadata)
                result = self.run_step('release-evidence')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.evidence()['image_inspect_id'], IMAGE_DIGEST)
                self.assertEqual(self.evidence()['image_config_digest'], config_digest)

    def test_scan_from_another_image_is_rejected(self):
        self.prepare_evidence()
        self.scan['Metadata']['RepoDigests'] = ['ghcr.io/example/other@' + IMAGE_DIGEST]
        self.write_json('image-vulnerabilities.json', self.scan)
        self.assertNotEqual(self.run_step('release-evidence').returncode, 0)
        self.assertFalse(self.evidence()['image_checks_passed'])

    def test_sbom_from_another_image_is_rejected(self):
        self.prepare_evidence()
        self.sbom['metadata']['component']['purl'] = self.sbom['metadata']['component']['purl'].replace('a' * 64, 'd' * 64)
        self.write_json('google-manager-sbom.cdx.json', self.sbom)
        self.assertNotEqual(self.run_step('release-evidence').returncode, 0)

    def test_vulnerabilities_block_publication_evidence_even_with_green_step_status(self):
        self.prepare_evidence()
        for field in ('Vulnerabilities', 'SuppressedVulnerabilities'):
            with self.subTest(field=field):
                self.scan['Results'][0][field] = [{'VulnerabilityID': 'CVE-fixture', 'Severity': 'HIGH'}]
                self.write_json('image-vulnerabilities.json', self.scan)
                result = self.run_step('release-evidence')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.evidence()['image_checks_passed'])
                self.scan['Results'][0].pop(field)

    def test_missing_or_failed_checks_cannot_be_reported_as_success(self):
        self.prepare_evidence()
        checks = json.loads(self.environment['CHECK_RESULTS'])
        checks['compose'] = 'failure'
        result = self.run_step('release-evidence', CHECK_RESULTS=json.dumps(checks))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.evidence()['image_checks_passed'])
        result = self.run_step('release-evidence', CHECK_RESULTS='{}')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.evidence()['image_checks_passed'])
        (self.root / 'recovery-drill.json').unlink()
        result = self.run_step('release-evidence')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.evidence()['image_checks_passed'])

    def test_missing_licenses_are_recorded_for_b03_without_fabrication(self):
        self.prepare_evidence()
        self.sbom['components'][0].pop('licenses')
        self.write_json('google-manager-sbom.cdx.json', self.sbom)
        result = self.run_step('release-evidence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.evidence()['missing_license_metadata_count'], 1)
        self.assertIn('B03 remains blocked', result.stdout)
        inventory = json.loads((self.root / 'LICENSE-INVENTORY.json').read_text(encoding='utf-8'))
        self.assertFalse(inventory['license_metadata_complete'])


if __name__ == '__main__':
    unittest.main()
