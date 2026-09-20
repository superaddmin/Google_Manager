"""Authenticate API fixtures through the real session issuance endpoint."""
import time

from app.services.auth_service import AuthService


def login_admin(client):
    response = client.post('/api/auth/login', json={
        'password': client.application.config['ADMIN_PASSWORD'],
        'salt': AuthService.generate_salt(int(time.time())),
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    if response.status_code != 200:
        raise AssertionError(f'Fixture login failed: HTTP {response.status_code}')
