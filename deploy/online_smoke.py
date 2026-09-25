"""Verify a deployment; HTTP testing requires explicit opt-in."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import re
import secrets
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

try:
    from .preflight import load_candidate_environment
except ImportError:
    from preflight import load_candidate_environment


class SmokeFailure(RuntimeError):
    pass


def run_smoke(url, env_file, ca_file=None, exercise_account=False, allow_http_test=False):
    parsed = urlsplit(url)
    allowed_schemes = {'https', 'http'} if allow_http_test else {'https'}
    if parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.path not in {'', '/'}:
        raise SmokeFailure('HTTPS_ORIGIN_REQUIRED')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SmokeFailure('INVALID_ORIGIN')
    origin = url.rstrip('/')
    context = ssl.create_default_context(cafile=ca_file)
    cookies = CookieJar()
    opener = build_opener(HTTPSHandler(context=context), HTTPCookieProcessor(cookies))
    completed = []

    def request(path, method='GET', payload=None, expected=200):
        headers = {'Origin': origin, 'X-Requested-With': 'XMLHttpRequest'}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            response = opener.open(Request(origin + path, data=data, method=method, headers=headers), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise SmokeFailure(f'HTTP_{response.status}_EXPECTED_{expected}')
            content = response.read(4 * 1024 * 1024 + 1)
            if len(content) > 4 * 1024 * 1024:
                raise SmokeFailure('RESPONSE_TOO_LARGE')
            return content

    def api(path, method='GET', payload=None, expected=200):
        result = json.loads(request(path, method, payload, expected))
        if expected == 200 and result.get('success') is False:
            raise SmokeFailure('API_REJECTED')
        return result

    page = request('/').decode('utf-8')
    assets = set(re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', page))
    if not assets:
        raise SmokeFailure('FRONTEND_ASSETS_MISSING')
    for asset in assets:
        request(asset)
    completed.append('homepage_and_assets')
    for path in ('/recharge', '/Googlemail', '/admin'):
        if request(path).decode('utf-8') != page:
            raise SmokeFailure('PORTAL_ENTRY_MISMATCH')
    completed.append('three_portal_entries')
    api('/api/accounts', expected=401)
    api('/api/recharge/admin/overview', expected=401)
    config = api('/api/recharge/config')['data']
    if config['mode'] != 'disabled':
        raise SmokeFailure('TEST_REQUIRES_RECHARGE_DISABLED')
    completed.append('anonymous_access_and_recharge_disabled')
    values = load_candidate_environment(env_file)
    account_id = None
    authenticated = False
    try:
        salt = hashlib.md5(str(int(time.time()) - 2003).encode()).hexdigest()
        api('/api/auth/login', 'POST', {'password': values['ADMIN_PASSWORD'], 'salt': salt})
        authenticated = True
        if not api('/api/auth/check')['authenticated']:
            raise SmokeFailure('SESSION_NOT_PERSISTED')
        if parsed.scheme == 'https' and not any(cookie.secure for cookie in cookies):
            raise SmokeFailure('SECURE_SESSION_COOKIE_MISSING')
        completed.append('https_login_session' if parsed.scheme == 'https' else 'http_test_login_session')
        overview = api('/api/recharge/admin/overview')['data']
        tasks = api('/api/recharge/admin/tasks?page_size=1')['data']
        if overview['mode'] != 'disabled' or tasks['total'] != overview['total']:
            raise SmokeFailure('RECHARGE_ADMIN_OVERVIEW_MISMATCH')
        for task in tasks['items']:
            detail = api('/api/recharge/admin/tasks/' + task['task_no'])['data']
            if any(detail['actions'].values()):
                raise SmokeFailure('DISABLED_RECHARGE_ADMIN_ACTION_ENABLED')
        completed.append('recharge_admin_read_only')
        if exercise_account:
            email = 'deployment-smoke-' + secrets.token_hex(10) + '@example.test'
            created = api('/api/accounts', 'POST', {
                'email': email.upper(), 'password': secrets.token_urlsafe(24), 'status': 'pro',
            })['data']
            account_id = created['id']
            if created['email'] != email:
                raise SmokeFailure('EMAIL_CANONICALIZATION_FAILED')
            api('/api/accounts', 'POST', {'email': email, 'password': 'synthetic-duplicate'}, expected=400)
            api(f'/api/security/accounts/{account_id}/lock', 'POST', {})
            api(f'/api/security/accounts/{account_id}/unlock', 'POST', {})
            accounts = api('/api/accounts?' + urlencode({'search': email}))['data']
            matched = [account for account in accounts if account['id'] == account_id]
            if len(matched) != 1 or matched[0]['status'] != 'pro':
                raise SmokeFailure('LOCK_STATUS_RESTORE_FAILED')
            completed.append('account_create_duplicate_lock_unlock_query')
    finally:
        try:
            if account_id is not None:
                api(f'/api/accounts/{account_id}', 'DELETE')
                completed.append('synthetic_account_deleted')
        finally:
            if authenticated:
                api('/api/auth/logout', 'POST', {})
    api('/api/accounts', expected=401)
    api('/api/recharge/admin/tasks', expected=401)
    completed.append('logout_revokes_session')

    def measure(unused_index):
        started = time.perf_counter()
        with build_opener(HTTPSHandler(context=context)).open(origin + '/', timeout=15) as response:
            if response.status != 200:
                raise SmokeFailure('CAPACITY_REQUEST_FAILED')
            response.read(4 * 1024 * 1024)
        return (time.perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=4) as pool:
        durations = sorted(pool.map(measure, range(40)))
    return {
        'status': 'passed', 'checks': completed,
        'transport': 'https' if parsed.scheme == 'https' else 'http_test',
        'capacity_sample': {'requests': 40, 'concurrency': 4, 'failures': 0,
                            'p95_ms': round(durations[math.ceil(len(durations) * .95) - 1], 2),
                            'max_ms': round(durations[-1], 2)},
        'production_capacity_validated': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--ca-file')
    parser.add_argument('--exercise-account', action='store_true')
    parser.add_argument('--allow-http-test', action='store_true', help='Allow HTTP for an isolated test deployment')
    options = parser.parse_args()
    try:
        report = run_smoke(**vars(options))
    except Exception as error:
        report = {'status': 'failed', 'code': str(error) if isinstance(error, SmokeFailure) else type(error).__name__}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
