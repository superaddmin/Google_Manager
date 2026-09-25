import os
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy.preflight import _restricted_file_permissions, _runtime_permissions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREPARATION_SCRIPT = PROJECT_ROOT / 'deploy' / 'prepare-compose-host.sh'
RUNTIME_PATHS = ('instance', 'googlemail/runtime', 'googlemail/output')


@unittest.skipUnless(
    sys.platform == 'linux' and os.geteuid() == 0,
    'Compose host preparation requires Linux and root for real ownership checks',
)
class ComposeHostPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.temporary_root = Path(self.temporary_directory.name)
        self.command_directory = self.temporary_root / 'commands'
        self.command_directory.mkdir()
        self.outside_file = self.temporary_root / 'outside-project.txt'
        self.outside_file.write_bytes(b'synthetic outside target\x00\n')
        self.outside_file.chmod(0o644)
        self._create_project('synthetic project')

    def _create_project(self, name):
        self.root = self.temporary_root / name
        deployment = self.root / 'deploy'
        deployment.mkdir(parents=True)
        self.script = deployment / PREPARATION_SCRIPT.name
        shutil.copyfile(PREPARATION_SCRIPT, self.script)
        self.env_file = self.root / '.env'
        self.env_file.write_bytes(b'SYNTHETIC_ENV=preserve-this-value\n')
        self.credentials = self.root / 'credentials.json'
        self.credentials.write_bytes(b'{"synthetic": "preserve these bytes"}\n')
        for path in (self.env_file, self.credentials):
            os.chown(path, 1000, 1000)
            path.chmod(0o640)
        self.runtime_directories = tuple(self.root / path for path in RUNTIME_PATHS)

    def _create_runtime_trees(self):
        for directory in self.runtime_directories:
            nested = directory / 'nested directory\nsecond line'
            nested.mkdir(parents=True)
            for path in (directory, nested):
                os.chown(path, 1000, 1000)
                path.chmod(0o755)
            for path in (directory / '.hidden.db', nested / '-session token\n.json'):
                path.write_bytes(b'synthetic runtime bytes\x00\xff\n')
                os.chown(path, 1000, 1000)
                path.chmod(0o664)

    def _write_command(self, name, body):
        command = self.command_directory / name
        command.write_text('#!/bin/bash\nset -eu\n' + body, encoding='utf-8', newline='\n')
        command.chmod(0o755)

    def _run(self, overrides=None, **options):
        environment = {
            'PATH': f'{self.command_directory}:/usr/bin:/bin',
            'LC_ALL': 'C.UTF-8',
        }
        environment.update(overrides or {})
        return subprocess.run(
            ['/bin/bash', str(self.script)],
            cwd=self.temporary_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=15,
            check=False,
            **options,
        )

    def _snapshot(self, include_ctime=True):
        snapshot = {}
        for path in [self.outside_file, self.root, *self.root.rglob('*')]:
            metadata = path.lstat()
            content = None
            if stat.S_ISREG(metadata.st_mode):
                content = path.read_bytes()
            elif stat.S_ISLNK(metadata.st_mode):
                content = os.readlink(path)
            snapshot[path] = (
                metadata.st_mode, metadata.st_uid, metadata.st_gid,
                metadata.st_ino, metadata.st_nlink, metadata.st_size,
                metadata.st_mtime_ns, content,
                metadata.st_ctime_ns if include_ctime else None,
            )
        return snapshot

    def _file_contents(self):
        return {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in [self.outside_file, *self.root.rglob('*')]
            if path.is_file()
        }

    def _assert_rejected_without_changes(self, message, **options):
        before = self._snapshot()
        result = self._run(**options)
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn('Compose 宿主目录已准备', result.stdout)
        self.assertEqual(before, self._snapshot())
        return result

    def _assert_prepared(self):
        for path, owner in ((self.env_file, 0), (self.credentials, 10001)):
            metadata = path.stat()
            self.assertEqual((owner, owner, 0o600), (
                metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode),
            ))
        self.assertTrue(_restricted_file_permissions(self.env_file, {0, os.geteuid()}))
        self.assertTrue(_restricted_file_permissions(self.credentials, {10001}))
        self.assertTrue(_runtime_permissions(self.runtime_directories, 10001))
        for directory in self.runtime_directories:
            for path in [directory, *directory.rglob('*')]:
                metadata = path.stat()
                expected_mode = 0o700 if path.is_dir() else 0o600
                self.assertEqual((10001, 10001, expected_mode), (
                    metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode),
                ), str(path))

    def _assert_success_and_idempotence(self):
        before = self._file_contents()
        for iteration in range(2):
            result = self._run({
                'COMPOSE_CONTAINER_UID': '10001', 'COMPOSE_CONTAINER_GID': '10001',
            })
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual('', result.stderr)
            self.assertIn('Compose 宿主目录已准备：UID/GID=10001:10001', result.stdout)
            self._assert_prepared()
            self.assertEqual(before, self._file_contents())
            current = self._snapshot(include_ctime=False)
            if iteration == 0:
                prepared = current
            else:
                self.assertEqual(prepared, current)

    def test_creates_missing_directories_without_changing_content_and_is_idempotent(self):
        self._assert_success_and_idempotence()

    def test_existing_trees_are_prepared_once_without_install_and_are_idempotent(self):
        self._create_runtime_trees()
        validation_log = self.temporary_root / 'validation.log'
        real_find = shlex.quote(shutil.which('find'))
        self._write_command('find', (
            'if [[ "${!#}" == "-print0" ]]; then\n'
            f'    printf "%s\\0" "$2" >> {shlex.quote(str(validation_log))}\n'
            'fi\n'
            f'exec {real_find} "$@"\n'
        ))
        self._write_command('install', 'printf "unexpected install\\n" >&2\nexit 73\n')

        self._assert_success_and_idempotence()

        validated = validation_log.read_bytes().split(b'\0')[:-1]
        expected = [os.fsencode(path) for path in self.runtime_directories] * 2
        self.assertEqual(expected, validated)

    def test_uid_and_gid_overrides_are_rejected_before_any_changes(self):
        self._create_runtime_trees()
        for variable in ('COMPOSE_CONTAINER_UID', 'COMPOSE_CONTAINER_GID'):
            for value in ('0', '1000', '10002', '010001', '-1', 'invalid'):
                with self.subTest(variable=variable, value=value):
                    message = '不允许覆盖' if value.isdigit() else '必须是数字'
                    self._assert_rejected_without_changes(
                        message, overrides={variable: value},
                    )

    def test_non_root_execution_is_rejected_before_any_changes(self):
        self.temporary_root.chmod(0o755)
        self.root.chmod(0o755)
        self.script.parent.chmod(0o755)
        self.script.chmod(0o644)
        self._assert_rejected_without_changes(
            '请使用 root 用户或 sudo', user=1000, group=1000, extra_groups=(),
        )

    def test_required_files_reject_missing_links_directories_and_special_files(self):
        for name in ('.env', 'credentials.json'):
            for kind in ('missing', 'directory', 'symlink', 'dangling', 'hardlink', 'fifo'):
                with self.subTest(name=name, kind=kind):
                    self._create_project(f'{name}-{kind}')
                    candidate = self.root / name
                    candidate.unlink()
                    if kind == 'directory':
                        candidate.mkdir()
                    elif kind == 'symlink':
                        candidate.symlink_to(self.outside_file)
                    elif kind == 'dangling':
                        candidate.symlink_to(self.temporary_root / 'missing-target')
                    elif kind == 'hardlink':
                        candidate.hardlink_to(self.outside_file)
                    elif kind == 'fifo':
                        os.mkfifo(candidate)
                    self._assert_rejected_without_changes(name)

    def test_top_level_paths_reject_links_and_non_directories(self):
        for relative_path in ('googlemail', *RUNTIME_PATHS):
            for kind in ('file', 'symlink', 'dangling'):
                with self.subTest(path=relative_path, kind=kind):
                    self._create_project(f'{relative_path.replace("/", "-")}-{kind}')
                    candidate = self.root / relative_path
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    if kind == 'file':
                        candidate.write_bytes(b'synthetic invalid directory')
                    elif kind == 'symlink':
                        candidate.symlink_to(self.command_directory, target_is_directory=True)
                    else:
                        candidate.symlink_to(self.temporary_root / 'missing-target')
                    self._assert_rejected_without_changes('必须是普通目录')

    def test_nested_anomalies_in_last_tree_abort_before_any_changes(self):
        messages = {
            'symlink': '符号链接', 'dangling': '符号链接',
            'hardlink': '硬链接', 'fifo': '只能包含普通目录或普通文件',
            'socket': '只能包含普通目录或普通文件',
        }
        for kind, message in messages.items():
            with self.subTest(kind=kind):
                self._create_project(f'nested-{kind}')
                self._create_runtime_trees()
                nested = self.runtime_directories[-1] / 'another' / 'nested'
                nested.mkdir(parents=True)
                candidate = nested / 'unexpected'
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_socket:
                    if kind == 'symlink':
                        candidate.symlink_to(self.outside_file)
                    elif kind == 'dangling':
                        candidate.symlink_to(self.temporary_root / 'missing-target')
                    elif kind == 'hardlink':
                        candidate.hardlink_to(self.outside_file)
                    elif kind == 'fifo':
                        os.mkfifo(candidate)
                    else:
                        unix_socket.bind(str(candidate))
                    self._assert_rejected_without_changes(message)

    def test_cross_device_entry_is_rejected_before_any_changes(self):
        self._create_runtime_trees()
        candidate = self.runtime_directories[-1] / '.hidden.db'
        real_stat = shlex.quote(shutil.which('stat'))
        self._write_command('stat', (
            f'if [[ "$2" == "%d" && "${{!#}}" == {shlex.quote(str(candidate))} ]]; then\n'
            f'    printf "%s\\n" "{candidate.stat().st_dev + 1}"\n'
            '    exit 0\n'
            'fi\n'
            f'exec {real_stat} "$@"\n'
        ))
        self._assert_rejected_without_changes('不能跨文件系统挂载')

    def test_nested_stat_failure_aborts_before_any_changes(self):
        self._create_runtime_trees()
        candidate = self.runtime_directories[-1] / '.hidden.db'
        real_stat = shlex.quote(shutil.which('stat'))
        self._write_command('stat', (
            f'if [[ "${{!#}}" == {shlex.quote(str(candidate))} ]]; then\n'
            '    printf "synthetic stat failure\\n" >&2\n'
            '    exit 74\n'
            'fi\n'
            f'exec {real_stat} "$@"\n'
        ))
        self._assert_rejected_without_changes('synthetic stat failure')

    def test_find_failure_with_empty_or_partial_output_aborts_before_any_changes(self):
        for partial_output in (False, True):
            with self.subTest(partial_output=partial_output):
                self._create_project(f'find-failure-{partial_output}')
                directory = self.runtime_directories[-1]
                directory.mkdir(parents=True)
                candidate = directory / 'synthetic.db'
                candidate.write_bytes(b'preserve this runtime file')
                candidate.chmod(0o664)
                real_find = shlex.quote(shutil.which('find'))
                output = (
                    f'    printf "%s\\0" {shlex.quote(str(directory))} '
                    f'{shlex.quote(str(candidate))}\n'
                    if partial_output else ''
                )
                self._write_command('find', (
                    'if [[ "${!#}" == "-print0" ]]; then\n'
                    + output
                    + '    printf "synthetic find failure\\n" >&2\n'
                    '    exit 74\n'
                    'fi\n'
                    f'exec {real_find} "$@"\n'
                ))

                result = self._assert_rejected_without_changes('synthetic find failure')

                self.assertEqual(74, result.returncode)

    def test_env_chmod_failure_reports_partial_changes_and_stops(self):
        self._create_runtime_trees()
        before = self._snapshot()
        real_chmod = shlex.quote(shutil.which('chmod'))
        self._write_command('chmod', (
            f'if [[ "${{!#}}" == {shlex.quote(str(self.env_file))} ]]; then\n'
            '    printf "synthetic chmod failure\\n" >&2\n'
            '    exit 75\n'
            'fi\n'
            f'exec {real_chmod} "$@"\n'
        ))

        result = self._run()

        self.assertEqual(75, result.returncode)
        self.assertIn('synthetic chmod failure', result.stderr)
        self.assertIn('已停止', result.stderr)
        self.assertIn('部分权限', result.stderr)
        self.assertNotIn('Compose 宿主目录已准备', result.stdout)
        self.assertEqual(0, self.env_file.stat().st_uid)
        self.assertEqual(0o640, stat.S_IMODE(self.env_file.stat().st_mode))
        after = self._snapshot()
        self.assertEqual(before.keys(), after.keys())
        for path in before:
            if path != self.env_file:
                self.assertEqual(before[path], after[path], str(path))

    def test_runtime_chmod_failure_stops_before_later_trees(self):
        self._create_runtime_trees()
        before = self._snapshot()
        contents = self._file_contents()
        real_chmod = shlex.quote(shutil.which('chmod'))
        self._write_command('chmod', (
            'for argument in "$@"; do\n'
            f'    if [[ "$argument" == {shlex.quote(str(self.runtime_directories[0]))} ]]; then\n'
            '        printf "synthetic runtime chmod failure\\n" >&2\n'
            '        exit 75\n'
            '    fi\n'
            'done\n'
            f'exec {real_chmod} "$@"\n'
        ))

        result = self._run()

        self.assertNotEqual(0, result.returncode)
        self.assertIn('synthetic runtime chmod failure', result.stderr)
        self.assertIn('已停止', result.stderr)
        self.assertNotIn('Compose 宿主目录已准备', result.stdout)
        self.assertEqual(contents, self._file_contents())
        after = self._snapshot()
        for directory in self.runtime_directories[1:]:
            for path in [directory, *directory.rglob('*')]:
                self.assertEqual(before[path], after[path], str(path))


if __name__ == '__main__':
    unittest.main()
