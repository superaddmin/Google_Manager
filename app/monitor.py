"""Read-only operational checks; emits counts and exit status, never credentials."""
import argparse
from datetime import datetime, timezone
import json
import shutil
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import text

from app import create_app, db
from app.models.recharge_billing_mutation import RechargeBillingMutation
from app.models.recharge_mutation import RechargeMutation
from app.models.recharge_task import RechargeTask
from app.models.runtime_job import RuntimeJob


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_readiness(url, timeout=3):
    """Probe the actual web listener with a bounded, non-redirecting request."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return False
        request = Request(url, headers={'Accept': 'application/json'})
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = json.loads(response.read(65537))
            return response.status == 200 and isinstance(data, dict) and data.get('ready') is True
    except Exception:
        return False


def collect_status(application, *, web_ready, max_queue_age=300, max_unknown_age=300,
                   min_free_bytes=1024 ** 3, now=None):
    """Return non-sensitive health evidence; callers alert on a non-empty issues list."""
    now = time.time() if now is None else now
    result = {'healthy': False, 'checked_at': int(now), 'issues': [], 'counts': {}}
    if not web_ready:
        result['issues'].append('web_not_ready')
    with application.app_context():
        try:
            db.session.execute(text('SELECT 1'))
            from app.services.runtime_queue import RuntimeQueue
            worker = RuntimeQueue.state('worker')
            if application.config['BACKGROUND_TASK_MODE'] == 'queue':
                heartbeat = worker.get('heartbeat', 0)
                if not isinstance(heartbeat, (int, float)) or not 0 <= now - heartbeat <= 30:
                    result['issues'].append('worker_heartbeat_stale')
            cutoff = datetime.fromtimestamp(now - max_unknown_age, timezone.utc).replace(tzinfo=None)
            counts = {
                'queue_overdue': RuntimeJob.query.filter(
                    RuntimeJob.status == 'pending', RuntimeJob.available_at <= now,
                    RuntimeJob.created_at <= now - max_queue_age,
                ).count(),
                'automation_needs_review': RuntimeJob.query.filter(
                    RuntimeJob.status == 'failed', RuntimeJob.active_key.isnot(None),
                ).count(),
                'recharge_unknown_overdue': RechargeTask.query.filter(
                    RechargeTask.status == 'unknown', RechargeTask.created_at <= cutoff,
                ).count(),
                'recharge_mutation_overdue': RechargeMutation.query.filter(
                    RechargeMutation.state.in_(('pending', 'unknown')),
                    RechargeMutation.started_at <= now - max_unknown_age,
                ).count(),
                'billing_mutation_overdue': RechargeBillingMutation.query.filter(
                    RechargeBillingMutation.state.in_(('pending', 'unknown')),
                    RechargeBillingMutation.started_at <= now - max_unknown_age,
                ).count(),
            }
            result['counts'] = counts
            result['issues'].extend(name for name, count in counts.items() if count > 0)
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            result['issues'].append('database_check_failed')
        finally:
            db.session.remove()
        try:
            free_bytes = shutil.disk_usage(application.instance_path).free
            result['disk_free_bytes'] = free_bytes
            if free_bytes < min_free_bytes:
                result['issues'].append('disk_space_low')
        except OSError:
            result['issues'].append('disk_check_failed')
    result['healthy'] = not result['issues']
    return result


def positive_integer(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return parsed


def main():
    parser = argparse.ArgumentParser(description='Read-only readiness, queue and disk checks')
    parser.add_argument('--url', default='http://127.0.0.1:8002/health/ready')
    parser.add_argument('--max-queue-age', type=positive_integer, default=300)
    parser.add_argument('--max-unknown-age', type=positive_integer, default=300)
    parser.add_argument('--min-free-mib', type=positive_integer, default=1024)
    options = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    try:
        application = create_app()
        result = collect_status(
            application, web_ready=probe_readiness(options.url),
            max_queue_age=options.max_queue_age, max_unknown_age=options.max_unknown_age,
            min_free_bytes=options.min_free_mib * 1024 ** 2,
        )
    except Exception:
        result = {'healthy': False, 'issues': ['monitor_configuration_failed']}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['healthy'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
