import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIG = ROOT / 'deploy' / 'nginx' / 'google-manager.conf'


def extract_block(config, marker):
    marker_start = config.index(marker)
    block_start = config.index('{', marker_start)
    depth = 0
    for position in range(block_start, len(config)):
        if config[position] == '{':
            depth += 1
        elif config[position] == '}':
            depth -= 1
            if depth == 0:
                return config[block_start + 1:position]
    raise AssertionError(f'Unclosed Nginx block: {marker}')


class NginxHealthAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = NGINX_CONFIG.read_text(encoding='utf-8')
        cls.health_block = extract_block(cls.config, 'location ^~ /health/')

    def test_health_location_is_loopback_only(self):
        directives = [
            line.strip()
            for line in self.health_block.splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        ]
        ipv4_allow = directives.index('allow 127.0.0.1;')
        ipv6_allow = directives.index('allow ::1;')
        deny_all = directives.index('deny all;')

        self.assertLess(ipv4_allow, deny_all)
        self.assertLess(ipv6_allow, deny_all)
        self.assertNotIn('satisfy any;', directives)

    def test_health_proxy_overwrites_forwarded_client_headers(self):
        self.assertIn('proxy_pass http://127.0.0.1:8002;', self.health_block)
        self.assertIn('proxy_set_header X-Real-IP $remote_addr;', self.health_block)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', self.health_block)
        self.assertIn('proxy_set_header X-Forwarded-Proto $scheme;', self.health_block)
        self.assertNotIn('$proxy_add_x_forwarded_for', self.health_block)
        self.assertNotIn('proxy_hide_header Cache-Control;', self.health_block)

    def test_template_does_not_rewrite_remote_address(self):
        self.assertIsNone(re.search(r'^\s*real_ip_header\b', self.config, re.MULTILINE))
        self.assertIsNone(re.search(r'^\s*set_real_ip_from\b', self.config, re.MULTILINE))


if __name__ == '__main__':
    unittest.main()
