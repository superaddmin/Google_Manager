import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import create_app, db
from app.services.recharge_service import (
    RechargeService, RechargeUpstreamError, _DeadlineHTTPSHandler,
)


class RechargeTransportTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='recharge-tls-')
        root = Path(cls.directory.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256())
        )
        cert_path = root / 'synthetic-cert.pem'
        key_path = root / 'synthetic-key.pem'
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        cls.client_context = ssl.create_default_context(cafile=str(cert_path))
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({'ok': True, 'result': {'synthetic': True}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.do_GET()

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.socket = server_context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.directory.cleanup()

    def setUp(self):
        self.app = create_app('testing')
        self.app.config.update(
            RECHARGE_MODE='live',
            RECHARGE_UPSTREAM_URL=f'https://localhost:{self.server.server_port}',
        )
        self.context = self.app.app_context()
        self.context.push()
        self.proxy_patch = patch.dict(os.environ, {
            'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
        })
        self.proxy_patch.start()

    def tearDown(self):
        self.proxy_patch.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def test_https_get_and_post_preserve_certificate_validation(self):
        handler = _DeadlineHTTPSHandler(context=self.client_context)
        with patch('app.services.recharge_service._DeadlineHTTPSHandler', return_value=handler):
            self.assertEqual(RechargeService._upstream_get('/fixture')['ok'], True)
            self.assertEqual(RechargeService._upstream_post('/fixture', {})['ok'], True)
        self.assertTrue(self.client_context.check_hostname)
        self.assertEqual(self.client_context.verify_mode, ssl.CERT_REQUIRED)

    def test_https_rejects_untrusted_certificate(self):
        with self.assertRaises(RechargeUpstreamError):
            RechargeService._upstream_get('/fixture')

    def test_https_rejects_certificate_for_wrong_hostname(self):
        self.app.config['RECHARGE_UPSTREAM_URL'] = f'https://127.0.0.1:{self.server.server_port}'
        handler = _DeadlineHTTPSHandler(context=self.client_context)
        with patch('app.services.recharge_service._DeadlineHTTPSHandler', return_value=handler):
            with self.assertRaises(RechargeUpstreamError):
                RechargeService._upstream_get('/fixture')


if __name__ == '__main__':
    unittest.main()
