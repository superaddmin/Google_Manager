from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from werkzeug.serving import WSGIRequestHandler, make_server

from app import create_app, db
from app.config import TestingConfig
from app.models.account import Account
from deploy.online_smoke import SmokeFailure, run_smoke


class QuietRequestHandler(WSGIRequestHandler):
    def log(self, log_type, message, *args):
        pass


class OnlineSmokeTestCase(unittest.TestCase):
    def test_http_requires_explicit_test_option(self):
        with self.assertRaisesRegex(SmokeFailure, 'HTTPS_ORIGIN_REQUIRED'):
            run_smoke('http://127.0.0.1', Path('unused.env'))

    def test_test_option_still_rejects_invalid_origins(self):
        for url in ['ftp://127.0.0.1', 'http://127.0.0.1/admin', 'http://user:password@127.0.0.1']:
            with self.subTest(url=url), self.assertRaises(SmokeFailure):
                run_smoke(url, Path('unused.env'), allow_http_test=True)

    def test_http_smoke_preserves_login_and_cleans_synthetic_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / 'test.env'
            environment.write_text('ADMIN_PASSWORD=http-smoke-synthetic-password\n', encoding='utf-8')
            database_url = 'sqlite:///' + (root / 'test.db').as_posix()
            with patch.object(TestingConfig, 'SQLALCHEMY_DATABASE_URI', database_url):
                application = create_app('testing')
            application.config.update(
                ADMIN_PASSWORD='http-smoke-synthetic-password',
                RECHARGE_MODE='disabled',
            )
            server = make_server('127.0.0.1', 0, application, request_handler=QuietRequestHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                report = run_smoke(
                    f'http://127.0.0.1:{server.server_port}', environment,
                    exercise_account=True, allow_http_test=True,
                )
                self.assertEqual(report['status'], 'passed')
                self.assertEqual(report['transport'], 'http_test')
                self.assertIn('http_test_login_session', report['checks'])
                self.assertIn('three_portal_entries', report['checks'])
                self.assertIn('recharge_admin_read_only', report['checks'])
                self.assertIn('logout_revokes_session', report['checks'])
                self.assertEqual(report['capacity_sample']['failures'], 0)
                self.assertFalse(report['production_capacity_validated'])
                with application.app_context():
                    self.assertEqual(Account.query.count(), 0)
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)
                with application.app_context():
                    db.session.remove()
                    db.engine.dispose()


if __name__ == '__main__':
    unittest.main()
