import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deploy import install_browser as installer


VERSION = '153.0.8010.52'
LINUX_AMD64_URL = (
    'https://storage.googleapis.com/chrome-for-testing-public/'
    f'{VERSION}/linux64/chrome-linux64.zip'
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, url=LINUX_AMD64_URL, content_length=None, status=200):
        super().__init__(payload)
        self.status = status
        self.headers = {
            'Content-Length': str(len(payload) if content_length is None else content_length),
        }
        self._url = url

    def getcode(self):
        return self.status

    def geturl(self):
        return self._url


def zip_entry(name, mode, payload=b'', compression=zipfile.ZIP_DEFLATED):
    info = zipfile.ZipInfo(name)
    # ZipInfo normalizes host separators in its constructor on Windows. Restore
    # the requested archive name so unsafe-name tests exercise the raw value.
    info.filename = name
    info.orig_filename = name
    info.create_system = 3
    info.external_attr = (mode & 0xFFFF) << 16
    if stat.S_ISDIR(mode):
        info.external_attr |= 0x10
    info.compress_type = compression
    return info, payload


def build_archive(extra_entries=()):
    output = io.BytesIO()
    entries = [
        zip_entry('chrome-linux64/', stat.S_IFDIR | 0o755),
        zip_entry('chrome-linux64/chrome', stat.S_IFREG | 0o755, b'fixture-browser'),
        zip_entry('chrome-linux64/resources.pak', stat.S_IFREG | 0o644, b'fixture-resource'),
        *extra_entries,
    ]
    with zipfile.ZipFile(output, 'w') as archive:
        for info, payload in entries:
            archive.writestr(info, payload)
    return output.getvalue()


class BrowserArtifactInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.minimum_size = patch.object(installer, 'MIN_ARCHIVE_BYTES', 1)
        self.minimum_size.start()

    def tearDown(self):
        self.minimum_size.stop()
        self.temporary_directory.cleanup()

    def write_manifest(self, payload, **overrides):
        artifact = {
            'url': LINUX_AMD64_URL,
            'sha256': hashlib.sha256(payload).hexdigest(),
            'size': len(payload),
            'archive_root': 'chrome-linux64',
            'executable': 'chrome',
            **overrides,
        }
        manifest = {
            'schema_version': 1,
            'browser': 'chrome-for-testing',
            'version': VERSION,
            'artifacts': {'linux/amd64': artifact},
        }
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        return path

    def install(self, payload, manifest_path=None, response=None, destination=None):
        manifest_path = manifest_path or self.write_manifest(payload)
        response = response or FakeResponse(payload)
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return response

        destination = destination or self.root / 'chrome'
        result = installer.install_browser(
            manifest_path,
            'linux/amd64',
            destination,
            opener=opener,
        )
        return result, destination, calls

    def assert_no_download_temporary_files(self):
        self.assertEqual(list(self.root.glob('.chrome-for-testing-*.zip')), [])

    def test_installs_verified_archive_into_new_read_only_directory(self):
        payload = build_archive()

        artifact, destination, calls = self.install(payload)

        self.assertEqual(artifact['version'], VERSION)
        self.assertEqual((destination / 'chrome').read_bytes(), b'fixture-browser')
        self.assertEqual((destination / 'resources.pak').read_bytes(), b'fixture-resource')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].full_url, LINUX_AMD64_URL)
        self.assertEqual(calls[0][1], installer.NETWORK_TIMEOUT_SECONDS)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE((destination / 'chrome').stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE((destination / 'resources.pak').stat().st_mode), 0o444)
        self.assert_no_download_temporary_files()

    def test_rejects_sha256_tampering_and_removes_partial_output(self):
        approved = build_archive()
        tampered = approved + b'tampered'
        manifest_path = self.write_manifest(approved, size=len(tampered))
        destination = self.root / 'chrome'

        with self.assertRaisesRegex(installer.BrowserInstallError, 'SHA-256'):
            self.install(
                tampered,
                manifest_path=manifest_path,
                destination=destination,
            )

        self.assertFalse(destination.exists())
        self.assert_no_download_temporary_files()

    def test_rejects_missing_or_incorrect_content_length(self):
        payload = build_archive()
        manifest_path = self.write_manifest(payload)
        for length in ('missing', len(payload) + 1):
            with self.subTest(length=length):
                response = FakeResponse(payload, content_length=length if length != 'missing' else len(payload))
                if length == 'missing':
                    response.headers = {}
                with self.assertRaisesRegex(installer.BrowserInstallError, 'Content-Length'):
                    self.install(payload, manifest_path=manifest_path, response=response)
                self.assert_no_download_temporary_files()

    def test_rejects_redirect_away_from_approved_url(self):
        payload = build_archive()
        with self.assertRaisesRegex(installer.BrowserInstallError, 'redirected'):
            self.install(payload, response=FakeResponse(payload, url='https://example.test/chrome.zip'))

    def test_refuses_existing_destination_without_downloading_or_overwriting(self):
        payload = build_archive()
        manifest_path = self.write_manifest(payload)
        destination = self.root / 'chrome'
        destination.mkdir()
        marker = destination / 'marker'
        marker.write_text('keep', encoding='ascii')
        called = False

        def opener(unused_request, unused_timeout):
            nonlocal called
            called = True
            return FakeResponse(payload)

        with self.assertRaisesRegex(installer.BrowserInstallError, 'already exists'):
            installer.install_browser(
                manifest_path,
                'linux/amd64',
                destination,
                opener=opener,
            )
        self.assertFalse(called)
        self.assertEqual(marker.read_text(encoding='ascii'), 'keep')

    def test_rejects_unapproved_platform_before_downloading(self):
        payload = build_archive()
        manifest_path = self.write_manifest(payload)
        with self.assertRaisesRegex(installer.BrowserInstallError, 'unsupported browser platform'):
            installer.install_browser(
                manifest_path,
                'windows/amd64',
                self.root / 'chrome',
                opener=lambda *_: self.fail('network must not be called'),
            )

    def test_rejects_non_official_or_version_mismatched_url(self):
        payload = build_archive()
        for url in (
            'http://storage.googleapis.com/chrome.zip',
            'https://example.test/chrome-linux64.zip',
            LINUX_AMD64_URL.replace(VERSION, '153.0.8010.12'),
        ):
            with self.subTest(url=url):
                manifest_path = self.write_manifest(payload, url=url)
                with self.assertRaisesRegex(installer.BrowserInstallError, 'official URL'):
                    installer.load_artifact(manifest_path, 'linux/amd64')

    def test_rejects_path_traversal_absolute_and_backslash_entries(self):
        unsafe_entries = (
            zip_entry('chrome-linux64/../escape', stat.S_IFREG | 0o644, b'x'),
            zip_entry('/chrome-linux64/escape', stat.S_IFREG | 0o644, b'x'),
            zip_entry('chrome-linux64/C:escape', stat.S_IFREG | 0o644, b'x'),
        )
        for index, entry in enumerate(unsafe_entries):
            with self.subTest(name=entry[0].filename):
                payload = build_archive([entry])
                with self.assertRaisesRegex(
                    installer.BrowserInstallError,
                    'unsafe path|path traversal|absolute platform path',
                ):
                    self.install(payload, destination=self.root / f'chrome-{index}')
                self.assertFalse((self.root / 'escape').exists())
                self.assertFalse((self.root / f'chrome-{index}').exists())

    def test_rejects_raw_backslash_entry_before_extraction(self):
        root, executable, unsafe = (
            zip_entry('chrome-linux64/', stat.S_IFDIR | 0o755)[0],
            zip_entry('chrome-linux64/chrome', stat.S_IFREG | 0o755, b'x')[0],
            zip_entry('placeholder', stat.S_IFREG | 0o644, b'x')[0],
        )
        executable.file_size = executable.compress_size = 1
        unsafe.filename = unsafe.orig_filename = 'chrome-linux64\\escape'
        unsafe.file_size = unsafe.compress_size = 1

        class RawArchive:
            @staticmethod
            def infolist():
                return [root, executable, unsafe]

        with self.assertRaisesRegex(installer.BrowserInstallError, 'unsafe path'):
            installer._validated_members(RawArchive(), {
                'archive_root': 'chrome-linux64',
                'executable': 'chrome',
            })

    def test_rejects_symbolic_links_and_special_files(self):
        unsafe_entries = (
            zip_entry('chrome-linux64/link', stat.S_IFLNK | 0o777, b'chrome'),
            zip_entry('chrome-linux64/pipe', stat.S_IFIFO | 0o644, b''),
        )
        for entry in unsafe_entries:
            with self.subTest(name=entry[0].filename):
                payload = build_archive([entry])
                with self.assertRaisesRegex(installer.BrowserInstallError, 'special files'):
                    self.install(payload)
                self.assertFalse((self.root / 'chrome').exists())

    def test_rejects_high_compression_ratio_archive(self):
        bomb = zip_entry(
            'chrome-linux64/expanded.bin',
            stat.S_IFREG | 0o644,
            b'0' * (2 * 1024 * 1024),
        )
        payload = build_archive([bomb])
        with self.assertRaisesRegex(installer.BrowserInstallError, 'compression ratio'):
            self.install(payload)
        self.assertFalse((self.root / 'chrome').exists())

    def test_accepts_directory_entry_after_a_child_file(self):
        payload = build_archive([
            zip_entry('chrome-linux64/nested/data.txt', stat.S_IFREG | 0o644, b'data'),
            zip_entry('chrome-linux64/nested/', stat.S_IFDIR | 0o755),
        ])
        unused_artifact, destination, unused_calls = self.install(payload)
        self.assertEqual((destination / 'nested' / 'data.txt').read_bytes(), b'data')

    def test_rejects_invalid_zip_and_cleans_destination(self):
        payload = b'not-a-zip-archive'
        with self.assertRaisesRegex(installer.BrowserInstallError, 'cannot extract'):
            self.install(payload)
        self.assertFalse((self.root / 'chrome').exists())
        self.assert_no_download_temporary_files()


class BrowserArtifactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.manifest_path = cls.repository_root / 'deploy' / 'browser-artifacts.json'

    def test_production_manifest_has_two_real_approved_linux_artifacts(self):
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['browser'], 'chrome-for-testing')
        self.assertEqual(manifest['version'], VERSION)
        self.assertEqual(set(manifest['artifacts']), {'linux/amd64', 'linux/arm64'})
        for platform in ('linux/amd64', 'linux/arm64'):
            with self.subTest(platform=platform):
                artifact = installer.load_artifact(self.manifest_path, platform)
                self.assertNotEqual(artifact['sha256'], '0' * 64)
                self.assertGreater(artifact['size'], 100 * 1024 * 1024)

    def test_manifest_matches_googlemail_runtime_contract(self):
        source_root = self.repository_root / 'googlemail' / 'src'
        source = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in source_root.glob('*.mjs')
        )
        self.assertIn('EXPECTED_CHROME_VERSION', source)
        self.assertIn(VERSION, source)
        package = json.loads(
            (self.repository_root / 'googlemail' / 'package.json').read_text(encoding='utf-8')
        )
        self.assertEqual(package['dependencies']['playwright'], '1.63.0')

    def test_dockerfile_uses_verified_browser_installer_when_source_is_available(self):
        dockerfile_path = self.repository_root / 'Dockerfile'
        if not dockerfile_path.exists():
            self.skipTest('Dockerfile is intentionally absent from the runtime image')
        dockerfile = dockerfile_path.read_text(encoding='utf-8')
        self.assertIn('python install_browser.py', dockerfile)
        self.assertIn('--platform "${TARGETPLATFORM}"', dockerfile)
        self.assertIn('GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH=/opt/google-manager/chrome/chrome', dockerfile)
        self.assertIn('COPY --from=browser-runtime', dockerfile)
        self.assertNotIn('playwright install chromium', dockerfile)
        self.assertNotIn('/ms-playwright', dockerfile)


if __name__ == '__main__':
    unittest.main()
