"""Install the approved Chrome for Testing artifact without extra dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import sys
import tempfile
from typing import BinaryIO, Callable
import urllib.request
import zipfile


SCHEMA_VERSION = 1
NETWORK_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MIN_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
OFFICIAL_ORIGIN = "https://storage.googleapis.com"

PLATFORMS = {
    "linux/amd64": {
        "artifact_directory": "linux64",
        "archive_name": "chrome-linux64.zip",
        "archive_root": "chrome-linux64",
    },
    "linux/arm64": {
        "artifact_directory": "linux-arm64",
        "archive_name": "chrome-linux-arm64.zip",
        "archive_root": "chrome-linux-arm64",
    },
}


class BrowserInstallError(RuntimeError):
    """Raised when an artifact fails a release-safety check."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrowserInstallError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _require_exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BrowserInstallError(f"{label} must contain exactly: {', '.join(sorted(expected))}")


def load_artifact(manifest_path: Path, platform: str) -> dict:
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BrowserInstallError(f"cannot read browser artifact manifest: {error}") from error

    _require_exact_keys(
        manifest,
        {"schema_version", "browser", "version", "artifacts"},
        "manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BrowserInstallError("unsupported browser artifact manifest schema")
    if manifest["browser"] != "chrome-for-testing":
        raise BrowserInstallError("manifest browser must be chrome-for-testing")

    version = manifest["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
        raise BrowserInstallError("manifest version must be an exact four-part version")
    if platform not in PLATFORMS:
        raise BrowserInstallError(f"unsupported browser platform: {platform}")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict):
        raise BrowserInstallError("manifest artifacts must be an object")
    unknown_platforms = set(artifacts) - set(PLATFORMS)
    if unknown_platforms:
        raise BrowserInstallError(f"manifest contains unsupported platforms: {sorted(unknown_platforms)}")
    if platform not in artifacts:
        raise BrowserInstallError(f"browser artifact is not approved for platform: {platform}")

    artifact = artifacts[platform]
    _require_exact_keys(
        artifact,
        {"url", "sha256", "size", "archive_root", "executable"},
        f"artifact {platform}",
    )
    expected = PLATFORMS[platform]
    expected_url = (
        f"{OFFICIAL_ORIGIN}/chrome-for-testing-public/{version}/"
        f"{expected['artifact_directory']}/{expected['archive_name']}"
    )
    if artifact["url"] != expected_url:
        raise BrowserInstallError(f"artifact URL is not the approved official URL for {platform}")
    if artifact["archive_root"] != expected["archive_root"]:
        raise BrowserInstallError(f"unexpected archive root for {platform}")
    if artifact["executable"] != "chrome":
        raise BrowserInstallError(f"unexpected executable path for {platform}")
    if not isinstance(artifact["sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", artifact["sha256"]
    ):
        raise BrowserInstallError(f"artifact SHA-256 is invalid for {platform}")
    size = artifact["size"]
    if isinstance(size, bool) or not isinstance(size, int) or not MIN_ARCHIVE_BYTES <= size <= MAX_ARCHIVE_BYTES:
        raise BrowserInstallError(f"artifact size is outside the approved bounds for {platform}")

    return {**artifact, "platform": platform, "version": version}


def _open_official_url(request, timeout):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _download_artifact(
    artifact: dict,
    archive_path: Path,
    opener: Callable | None = None,
) -> None:
    request = urllib.request.Request(
        artifact["url"],
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "google-manager-browser-installer/1",
        },
    )
    response = (opener or _open_official_url)(request, NETWORK_TIMEOUT_SECONDS)
    digest = hashlib.sha256()
    written = 0

    with response, archive_path.open("xb") as output:
        status_code = getattr(response, "status", None) or response.getcode()
        if status_code != 200:
            raise BrowserInstallError(f"browser download returned HTTP {status_code}")
        if response.geturl() != artifact["url"]:
            raise BrowserInstallError("browser download redirected away from the approved URL")
        content_length = response.headers.get("Content-Length")
        if content_length is None or not content_length.isdigit():
            raise BrowserInstallError("browser download omitted a valid Content-Length")
        if int(content_length) != artifact["size"]:
            raise BrowserInstallError("browser download Content-Length does not match the manifest")

        while True:
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > artifact["size"] or written > MAX_ARCHIVE_BYTES:
                raise BrowserInstallError("browser download exceeded the approved size")
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())

    if written != artifact["size"]:
        raise BrowserInstallError("browser download size does not match the manifest")
    if digest.hexdigest() != artifact["sha256"]:
        raise BrowserInstallError("browser download SHA-256 does not match the manifest")


def _validated_members(
    archive: zipfile.ZipFile,
    artifact: dict,
) -> list[tuple[zipfile.ZipInfo, Path, int]]:
    members = archive.infolist()
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise BrowserInstallError("browser archive member count is outside the approved bounds")

    validated = []
    normalized_paths = set()
    total_uncompressed = 0
    total_compressed = 0
    executable_found = False

    for member in members:
        name = member.filename
        if not name or "\x00" in name or "\\" in name or name.startswith("/"):
            raise BrowserInstallError("browser archive contains an unsafe path")
        pure_path = PurePosixPath(name)
        if pure_path.is_absolute() or any(part in {"", ".", ".."} for part in pure_path.parts):
            raise BrowserInstallError("browser archive contains path traversal")
        if not pure_path.parts or any(":" in part for part in pure_path.parts):
            raise BrowserInstallError("browser archive contains an absolute platform path")
        if pure_path.parts[0] != artifact["archive_root"]:
            raise BrowserInstallError("browser archive contains an unexpected root directory")

        relative_parts = pure_path.parts[1:]
        if not relative_parts:
            if not member.is_dir():
                raise BrowserInstallError("browser archive root must be a directory")
            continue
        relative_path = Path(*relative_parts)
        normalized = relative_path.as_posix().casefold()
        if normalized in normalized_paths:
            raise BrowserInstallError("browser archive contains duplicate paths")
        normalized_paths.add(normalized)

        if member.flag_bits & 0x1:
            raise BrowserInstallError("encrypted browser archive members are not allowed")
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode)
        if member.is_dir():
            if file_type not in {0, stat.S_IFDIR}:
                raise BrowserInstallError("browser archive directory has an unsafe file type")
        elif file_type not in {0, stat.S_IFREG}:
            raise BrowserInstallError("browser archive links and special files are not allowed")

        if member.file_size < 0 or member.file_size > MAX_SINGLE_FILE_BYTES:
            raise BrowserInstallError("browser archive member exceeds the per-file limit")
        total_uncompressed += member.file_size
        total_compressed += member.compress_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise BrowserInstallError("browser archive exceeds the uncompressed size limit")
        if member.file_size >= 1024 * 1024:
            if member.compress_size <= 0 or member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise BrowserInstallError("browser archive member has a suspicious compression ratio")

        if relative_path.as_posix() == artifact["executable"]:
            if member.is_dir() or not (unix_mode & 0o111):
                raise BrowserInstallError("browser executable is missing executable permission")
            executable_found = True
        validated.append((member, relative_path, unix_mode))

    if total_compressed <= 0 or total_uncompressed / total_compressed > MAX_COMPRESSION_RATIO:
        raise BrowserInstallError("browser archive has a suspicious aggregate compression ratio")
    if not executable_found:
        raise BrowserInstallError("browser archive does not contain the expected executable")
    return validated


def _copy_member(source: BinaryIO, destination: BinaryIO, expected_size: int) -> None:
    written = 0
    while True:
        chunk = source.read(DOWNLOAD_CHUNK_BYTES)
        if not chunk:
            break
        written += len(chunk)
        if written > expected_size:
            raise BrowserInstallError("browser archive member expanded beyond its declared size")
        destination.write(chunk)
    if written != expected_size:
        raise BrowserInstallError("browser archive member size is inconsistent")


def _extract_archive(archive_path: Path, destination: Path, artifact: dict) -> None:
    destination_root = destination.resolve()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _validated_members(archive, artifact)
            for member, relative_path, unix_mode in members:
                target = destination / relative_path
                try:
                    target.resolve(strict=False).relative_to(destination_root)
                except ValueError as error:
                    raise BrowserInstallError("browser archive path escapes the destination") from error
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink() or not target.is_dir():
                        raise BrowserInstallError("browser archive directory target is invalid")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, target.open("xb") as output:
                    _copy_member(source, output, member.file_size)
                target.chmod(0o755 if unix_mode & 0o111 else 0o644)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        if isinstance(error, BrowserInstallError):
            raise
        raise BrowserInstallError(f"cannot extract browser archive: {error}") from error

    executable = destination / artifact["executable"]
    if executable.is_symlink() or not executable.is_file() or executable.stat().st_size <= 0:
        raise BrowserInstallError("installed browser executable is invalid")

    directories = []
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise BrowserInstallError("installed browser tree contains a symbolic link")
        if path.is_dir():
            directories.append(path)
        elif path.is_file():
            source_mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(0o555 if source_mode & 0o111 else 0o444)
        else:
            raise BrowserInstallError("installed browser tree contains a special file")
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o555)
    destination.chmod(0o555)


def _remove_tree(path: Path) -> None:
    def make_writable(function, failing_path, unused_error):
        os.chmod(failing_path, 0o700)
        function(failing_path)

    shutil.rmtree(path, onerror=make_writable)


def install_browser(
    manifest_path: Path,
    platform: str,
    destination: Path,
    opener: Callable | None = None,
) -> dict:
    artifact = load_artifact(manifest_path, platform)
    destination = destination.absolute()
    if os.path.lexists(destination):
        raise BrowserInstallError(f"browser destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise BrowserInstallError("browser destination parent must be a real directory")

    archive_descriptor, archive_name = tempfile.mkstemp(
        prefix=".chrome-for-testing-",
        suffix=".zip",
        dir=destination.parent,
    )
    os.close(archive_descriptor)
    archive_path = Path(archive_name)
    archive_path.unlink()
    destination_created = False
    try:
        _download_artifact(artifact, archive_path, opener=opener)
        destination.mkdir(mode=0o700)
        destination_created = True
        _extract_archive(archive_path, destination, artifact)
    except Exception:
        if destination_created and os.path.lexists(destination):
            _remove_tree(destination)
        raise
    finally:
        archive_path.unlink(missing_ok=True)
    return artifact


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("browser-artifacts.json"),
    )
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS))
    parser.add_argument("--destination", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        artifact = install_browser(args.manifest, args.platform, args.destination)
    except BrowserInstallError as error:
        print(f"browser installation failed: {error}", file=sys.stderr)
        return 1
    print(
        f"installed Chrome for Testing {artifact['version']} for {artifact['platform']} "
        f"at {args.destination.absolute()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
