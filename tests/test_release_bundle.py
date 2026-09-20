import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import release_bundle
from deploy.preflight import _is_https_evidence_url, _validate_release_manifest
from deploy.release_bundle import (
    BundleError,
    LOCKFILES,
    PUBLIC_FILES,
    build_release_bundle,
)


DIGEST = "a" * 64
IMAGE = "registry.production.example.org/google-manager@sha256:" + DIGEST


class ReleaseBundleTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for relative in PUBLIC_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"public fixture: {relative}\n", encoding="utf-8")
        production_template = (
            Path(__file__).resolve().parents[1] / "deploy/env.production.example"
        )
        (self.root / "deploy/env.production.example").write_bytes(
            production_template.read_bytes()
        )
        for relative in LOCKFILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"locked fixture: {relative}\n", encoding="utf-8")
        self.scan_path = self.root / "evidence-source/trivy.json"
        self.sbom_path = self.root / "evidence-source/sbom.json"
        self.scan_path.parent.mkdir()
        self._write_scan()
        self._write_sbom()
        self._git("init")
        self._git("config", "user.email", "release-test@example.invalid")
        self._git("config", "user.name", "Release Test")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def _write_scan(self, vulnerabilities=None, suppressed=None, digest=DIGEST, results=True):
        result = {
            "Target": "fixture (debian)",
            "Class": "os-pkgs",
            "Type": "debian",
            "Packages": [{"Name": "fixture-package", "Version": "1.0"}],
            "Vulnerabilities": vulnerabilities or [],
        }
        if suppressed is not None:
            result["SuppressedVulnerabilities"] = suppressed
        document = {
            "SchemaVersion": 2,
            "Trivy": {"Version": "0.72.0"},
            "CreatedAt": "2026-09-20T00:00:00+00:00",
            "ArtifactName": "fixture:release",
            "ArtifactType": "container_image",
            "Metadata": {
                "RepoDigests": [IMAGE.rsplit("@sha256:", 1)[0] + "@sha256:" + digest],
                "ImageConfig": {"architecture": "amd64", "os": "linux"},
            },
            "Results": [result] if results else [],
        }
        self.scan_path.write_text(json.dumps(document), encoding="utf-8")

    def _write_sbom(self, *, licenses=None, digest=DIGEST):
        component_licenses = [{"expression": "MIT OR Apache-2.0"}] if licenses is None else licenses
        document = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000001",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "container",
                    "name": "google-manager",
                    "purl": (
                        "pkg:oci/google-manager@sha256:" + digest
                        + "?arch=amd64&repository_url="
                        "registry.production.example.org%2Fgoogle-manager"
                    ),
                }
            },
            "components": [
                {
                    "bom-ref": "pkg:pypi/example@1.0",
                    "type": "library",
                    "name": "example",
                    "version": "1.0",
                    "licenses": component_licenses,
                }
            ],
        }
        self.sbom_path.write_text(json.dumps(document), encoding="utf-8")

    def _build(self, name="release-1", **overrides):
        options = {
            "image": IMAGE,
            "scan": self.scan_path.relative_to(self.root),
            "sbom": self.sbom_path.relative_to(self.root),
            "output": name,
            "platform": "linux/amd64",
            "ci_url": "https://ci.example.org/runs/123",
            "draft": False,
            "project_root": self.root,
            "generated_at": "2026-09-20T00:00:00+00:00",
        }
        options.update(overrides)
        return build_release_bundle(**options)

    def _commit_evidence(self):
        self._git("add", "evidence-source")
        self._git("commit", "-m", "update evidence")

    def test_formal_bundle_contains_only_allowlist_and_review_artifacts(self):
        output = self._build()
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["release_status"], "AWAITING_MANUAL_SIGNOFF")
        self.assertFalse(manifest["production_approved"])
        self.assertEqual(manifest["signoff_decision"], "NO-GO")
        self.assertEqual(manifest["scope"]["included"], "CDK workbench")
        self.assertEqual(manifest["scan"]["high_critical_count"], 0)
        self.assertEqual(manifest["sbom"]["missing_license_metadata_count"], 0)
        self.assertTrue(manifest["license_inventory"]["license_metadata_complete"])
        self.assertEqual(
            manifest["license_inventory"]["compliance_review_status"], "NOT_REVIEWED"
        )
        self.assertFalse(manifest["license_inventory"]["compliance_approved"])
        self.assertEqual(set(manifest["public_file_sha256"]), set(PUBLIC_FILES))
        self.assertEqual(set(manifest["lockfile_sha256"]), set(LOCKFILES))
        self.assertTrue(_validate_release_manifest(manifest, IMAGE, "linux/amd64"))
        self.assertFalse((output / ".env").exists())
        self.assertFalse((output / "credentials.json").exists())
        self.assertNotIn("locked fixture", (output / "SHA256SUMS").read_text(encoding="ascii"))
        self.assertIn("Status: NO-GO", (output / "RELEASE-SIGNOFF.md").read_text(encoding="utf-8"))
        self.assertTrue(
            (output / "README.md").read_text(encoding="utf-8")
            .startswith("# Release bundle - NOT APPROVED FOR DEPLOYMENT")
        )
        self.assertIn(
            "CI URL provenance are trust boundaries",
            (output / "README.md").read_text(encoding="utf-8"),
        )

        checksum_lines = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        for line in checksum_lines:
            expected, relative = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), expected)
        self.assertNotIn("SHA256SUMS", {line.split("  ", 1)[1] for line in checksum_lines})

    def test_formal_bundle_rejects_open_release_gates_without_creating_output(self):
        cases = (
            ("dirty", "DIRTY_WORKTREE"),
            ("vulnerable", "SCAN_BLOCKED"),
            ("suppressed", "SCAN_BLOCKED"),
            ("license", "LICENSE_INCOMPLETE"),
            ("ci", "CI_URL_REQUIRED"),
        )
        for index, (case, expected_code) in enumerate(cases):
            with self.subTest(case=case):
                if case == "dirty":
                    (self.root / "dirty.txt").write_text("dirty", encoding="utf-8")
                elif case == "vulnerable":
                    self._write_scan([{"VulnerabilityID": "CVE-SYNTHETIC", "Severity": "HIGH"}])
                    self._commit_evidence()
                elif case == "suppressed":
                    self._write_scan(suppressed=[{"VulnerabilityID": "CVE-SUPPRESSED"}])
                    self._commit_evidence()
                elif case == "license":
                    self._write_sbom(licenses=[])
                    self._commit_evidence()
                name = f"release-failed-{index}"
                arguments = {"ci_url": None} if case == "ci" else {}
                with self.assertRaises(BundleError) as raised:
                    self._build(name, **arguments)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse((self.root / name).exists())

                self._git("reset", "--hard", "HEAD")
                untracked = self.root / "dirty.txt"
                if untracked.exists():
                    untracked.unlink()
                self._write_scan()
                self._write_sbom()
                self._git("add", "evidence-source")
                if self._git("status", "--porcelain").stdout:
                    self._git("commit", "-m", f"restore fixture {index}")

    def test_draft_records_blockers_and_requires_blocked_output_name(self):
        self._write_scan([{"VulnerabilityID": "CVE-SYNTHETIC", "Severity": "CRITICAL"}])
        self._write_sbom(licenses=[])
        output = self._build(
            "BLOCKED-review-1", draft=True, ci_url=None
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["release_status"], "BLOCKED")
        self.assertEqual(
            set(manifest["blockers"]),
            {
                "dirty_git_worktree",
                "ci_run_url_missing",
                "high_or_critical_vulnerabilities_present",
                "sbom_license_metadata_incomplete",
            },
        )
        self.assertTrue(
            (output / "README.md").read_text(encoding="utf-8")
            .startswith("# BLOCKED - DO NOT DEPLOY")
        )
        with self.assertRaises(BundleError) as raised:
            self._build("review-without-prefix", draft=True)
        self.assertEqual(raised.exception.code, "OUTPUT_INVALID")

    def test_rejects_mismatched_or_structurally_empty_evidence(self):
        self._write_scan(digest="b" * 64)
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-mismatch")
        self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

        self._write_scan(results=False)
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-empty-scan")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

    def test_evidence_is_bound_to_exact_repository_digest_and_platform(self):
        scan = json.loads(self.scan_path.read_text(encoding="utf-8"))
        scan["Metadata"]["RepoDigests"] = ["registry.other.example/app@sha256:" + DIGEST]
        self.scan_path.write_text(json.dumps(scan), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-wrong-repository")
        self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_repository_binding_is_not_satisfied_by_same_digest_elsewhere(self):
        other_image = "registry.other.example.org/google-manager@sha256:" + DIGEST
        with self.assertRaises(BundleError) as raised:
            self._build("release-other-repository", image=other_image)
        self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

        self._write_scan()
        sbom = json.loads(self.sbom_path.read_text(encoding="utf-8"))
        sbom["metadata"]["component"]["purl"] = (
            "pkg:oci/google-manager@sha256:" + DIGEST
            + "?repository_url=registry.production.example.org%2Fgoogle-manager"
        )
        self.sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-missing-platform")
        self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

        self._write_sbom()
        sbom = json.loads(self.sbom_path.read_text(encoding="utf-8"))
        sbom["metadata"]["component"]["purl"] = sbom["metadata"]["component"][
            "purl"
        ].replace("?arch=", "extra?arch=")
        self.sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-digest-suffix")
        self.assertEqual(raised.exception.code, "EVIDENCE_MISMATCH")

    def test_nested_components_and_spdx_identifiers_fail_closed(self):
        sbom = json.loads(self.sbom_path.read_text(encoding="utf-8"))
        sbom["components"][0]["components"] = [{
            "bom-ref": "pkg:pypi/nested@1.0",
            "type": "library",
            "name": "nested",
            "version": "1.0",
        }]
        self.sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-nested-missing-license")
        self.assertEqual(raised.exception.code, "LICENSE_INCOMPLETE")

        self._write_sbom(licenses=[{"expression": "MIT WITH FakeException"}])
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-fake-exception")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

        self._write_sbom(licenses=[{"license": {"id": "FakeLicense"}}])
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-fake-license")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

        self._write_sbom(licenses=[{"expression": "MIT OR"}])
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-invalid-license")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

    def test_malformed_schema_and_template_secret_are_rejected(self):
        scan = json.loads(self.scan_path.read_text(encoding="utf-8"))
        scan["Results"][0]["Packages"] = [42]
        self.scan_path.write_text(json.dumps(scan), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-malformed-package")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

        self._write_scan()
        sbom = json.loads(self.sbom_path.read_text(encoding="utf-8"))
        sbom["specVersion"] = "1.evil"
        self.sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        self._commit_evidence()
        with self.assertRaises(BundleError) as raised:
            self._build("release-malformed-version")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

        self._write_sbom()
        template = self.root / "deploy/env.production.example"
        original_template = template.read_text(encoding="utf-8")
        template.write_text(
            original_template + "UNEXPECTED_TOKEN=real-secret\n",
            encoding="utf-8",
        )
        with self.assertRaises(BundleError) as raised:
            self._build("release-template-secret")
        self.assertEqual(raised.exception.code, "FILES_INVALID")

        template.write_text(
            "\n".join(
                line for line in original_template.splitlines()
                if not line.startswith("FLASK_ENV=")
            ) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(BundleError) as raised:
            self._build("release-template-missing-field")
        self.assertEqual(raised.exception.code, "FILES_INVALID")

    def test_ci_url_contract_matches_production_preflight(self):
        cases = {
            "https://ci.example.org/runs/123": True,
            "https://subdomain.github.com/actions/runs/123": True,
            "https://localhost/runs/123": False,
            "https://127.0.0.1/runs/123": False,
            "https://ci.example.invalid/runs/123": False,
            "https://your-domain.com/runs/123": False,
            "https://ci.example.org:8443/runs/123": False,
            "https://ci.example.org/runs/123?token=secret": False,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(_is_https_evidence_url(value), expected)
                if expected:
                    self.assertEqual(
                        release_bundle._validate_ci_url(value, required=True), value
                    )
                else:
                    with self.assertRaises(BundleError) as raised:
                        release_bundle._validate_ci_url(value, required=True)
                    self.assertEqual(raised.exception.code, "CI_URL_INVALID")

    def test_git_commands_clear_inherited_git_environment_and_use_timeout(self):
        inherited = {
            "GIT_DIR": "malicious",
            "GIT_WORK_TREE": "malicious",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "malicious",
        }
        with patch.dict("os.environ", inherited, clear=False):
            with patch(
                "deploy.release_bundle.subprocess.run", wraps=subprocess.run
            ) as run:
                state = release_bundle._git_state(self.root)
        self.assertFalse(state["dirty"])
        self.assertGreaterEqual(run.call_count, 3)
        for call in run.call_args_list:
            environment = call.kwargs["env"]
            self.assertFalse(any(key.upper().startswith("GIT_") for key in environment))
            self.assertEqual(
                call.kwargs["timeout"], release_bundle.GIT_TIMEOUT_SECONDS
            )

    def test_evidence_is_read_once_and_output_uses_validated_snapshot(self):
        original_validate_scan = release_bundle._validate_scan

        def mutate_after_validation(document, image, architecture):
            result = original_validate_scan(document, image, architecture)
            self._write_scan([{
                "VulnerabilityID": "CVE-AFTER-SNAPSHOT",
                "Severity": "CRITICAL",
            }])
            return result

        with patch("deploy.release_bundle._validate_scan", side_effect=mutate_after_validation):
            output = self._build("BLOCKED-snapshot", draft=True)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        packaged_scan = json.loads(
            (output / "evidence/trivy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["scan"]["high_critical_count"], 0)
        self.assertEqual(packaged_scan["Results"][0]["Vulnerabilities"], [])
        self.assertIn("dirty_git_worktree", manifest["blockers"])

    def test_rejects_existing_output_and_cross_directory_evidence(self):
        existing = self.root / "release-existing"
        existing.mkdir()
        with self.assertRaises(BundleError) as raised:
            self._build(existing.name)
        self.assertEqual(raised.exception.code, "OUTPUT_INVALID")

        with tempfile.NamedTemporaryFile(suffix=".json") as outside:
            outside.write(b"{}")
            outside.flush()
            with self.assertRaises(BundleError) as raised:
                self._build("release-outside", scan=outside.name)
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

    def test_symlinked_evidence_parent_is_rejected_when_supported(self):
        link = self.root / "linked-evidence"
        try:
            link.symlink_to(self.scan_path.parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks require platform support")
        with self.assertRaises(BundleError) as raised:
            self._build("release-linked-evidence", scan=link / "trivy.json")
        self.assertEqual(raised.exception.code, "EVIDENCE_INVALID")

    def test_cli_redacts_unexpected_failure(self):
        with patch("deploy.release_bundle.build_release_bundle", side_effect=RuntimeError("SECRET")):
            with patch("sys.stderr") as stderr:
                exit_code = release_bundle.main([
                    "--image", IMAGE,
                    "--scan", "evidence-source/trivy.json",
                    "--sbom", "evidence-source/sbom.json",
                    "--output", "release-cli-error",
                    "--platform", "linux/amd64",
                    "--ci-url", "https://ci.example.org/runs/123",
                ])
        self.assertEqual(exit_code, 1)
        output = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("ERROR WRITE_FAILED", output)
        self.assertNotIn("SECRET", output)

    def test_concurrent_output_creation_is_never_overwritten(self):
        original_mkdir = Path.mkdir

        def race_mkdir(path, *arguments, **keywords):
            if path == self.root / "release-race":
                original_mkdir(path)
            return original_mkdir(path, *arguments, **keywords)

        with patch("deploy.release_bundle.Path.mkdir", new=race_mkdir):
            with self.assertRaises(BundleError) as raised:
                self._build("release-race")
        self.assertEqual(raised.exception.code, "WRITE_FAILED")
        self.assertTrue((self.root / "release-race").is_dir())


if __name__ == "__main__":
    unittest.main()
