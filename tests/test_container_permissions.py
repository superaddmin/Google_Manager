import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'Container umask is verified on Linux')
class ContainerPermissionsTests(unittest.TestCase):
    def test_entrypoint_keeps_sqlite_child_process_files_and_directories_private(self):
        entrypoint = Path(__file__).resolve().parents[1] / 'deploy' / 'container-entrypoint.sh'
        code = '''import json, os, pathlib, sqlite3, subprocess, sys
root = pathlib.Path(sys.argv[1])
with sqlite3.connect(root / 'accounts.db') as database:
    database.execute('CREATE TABLE fixture (id INTEGER)')
(root / 'worker.lock').touch()
subprocess.run([sys.executable, '-c', "import pathlib, sys; p=pathlib.Path(sys.argv[1]); p.mkdir(); (p/'result.json').write_text('{}')", str(root/'task')], check=True)
print(json.dumps({str(p.relative_to(root)): p.stat().st_mode & 0o777 for p in root.rglob('*')}))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['/bin/sh', str(entrypoint), sys.executable, '-c', code, directory],
                                    capture_output=True, text=True, check=True, timeout=30)
            self.assertEqual(json.loads(result.stdout), {
                'accounts.db': 0o600, 'worker.lock': 0o600,
                'task': 0o700, 'task/result.json': 0o600,
            })
            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        failure = subprocess.run(['/bin/sh', str(entrypoint), sys.executable, '-c', 'raise SystemExit(19)'],
                                 capture_output=True, check=False, timeout=10)
        self.assertEqual(failure.returncode, 19)


if __name__ == '__main__':
    unittest.main()
