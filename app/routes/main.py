"""
主页面路由
负责渲染前端页面
"""
from flask import Blueprint, send_from_directory, current_app
import os

main_bp = Blueprint('main', __name__)


@main_bp.route('/health/ready')
def readiness():
    from app import db
    from sqlalchemy import text
    from app.services.runtime_queue import RuntimeQueue
    from app.models.runtime_job import RuntimeJob
    from app.models.gmail_task_log import GmailTaskLog
    from app.models.gmail_rule import GmailRule
    from app.models.gmail_execution import GmailExecution
    from app import validate_recharge_upstream_url
    import time
    try:
        db.session.execute(text('SELECT 1'))
        from app.services.schema_migration import probe_gmail_schema
        probe_gmail_schema(db.engine)
        worker_required = current_app.config['BACKGROUND_TASK_MODE'] == 'queue'
        worker_ready = RuntimeQueue.state('worker').get('heartbeat', 0) > time.time() - 30
        maintenance = RuntimeQueue.state('maintenance')
        maintenance_ready = maintenance.get('consecutiveFailures', 0) < 3
        if RuntimeQueue.state('gmail_daemon').get('enabled'):
            maintenance_ready = maintenance_ready and RuntimeQueue.state('gmail_daemon_status').get('consecutiveFailures', 0) < 3
        gmail_actions_ready = GmailTaskLog.query.join(GmailRule, GmailTaskLog.rule_id == GmailRule.id).filter(
            GmailTaskLog.status == 'failed', GmailRule.enabled.is_(True),
            GmailExecution.query.filter_by(log_id=GmailTaskLog.id).exists(),
        ).first() is None
        automation_ready = RuntimeJob.query.filter(RuntimeJob.status == 'failed', RuntimeJob.active_key.isnot(None)).first() is None
        credential_path = current_app.config.get('GMAIL_CLIENT_SECRET_FILE')
        gmail_ready = not credential_path or (os.path.isfile(credential_path) and os.access(credential_path, os.R_OK))
        recharge_mode = current_app.config.get('RECHARGE_MODE', 'disabled')
        recharge_ready = True
        if recharge_mode == 'live':
            try:
                validate_recharge_upstream_url(
                    current_app.config.get('RECHARGE_UPSTREAM_URL'),
                    current_app.config.get('RECHARGE_UPSTREAM_ALLOWED_HOSTS', ''),
                )
            except RuntimeError:
                recharge_ready = False
        sensitive_data_ready = True
        if current_app.config.get('SENSITIVE_DATA_REQUIRE_ENCRYPTION'):
            try:
                from app.services.schema_migration import validate_sensitive_data
                validate_sensitive_data(
                    db.engine,
                    current_app.config.get('GMAIL_TOKEN_ENCRYPTION_KEY'),
                    max_rows=20,
                )
            except Exception:
                sensitive_data_ready = False
        healthy = (
            (not worker_required or worker_ready)
            and gmail_ready and maintenance_ready and automation_ready
            and gmail_actions_ready and recharge_ready and sensitive_data_ready
        )
        cdk_ready = True
        if current_app.config.get('CDK_ENABLED'):
            try:
                from app.services.cdk_maintenance import validate_cdk
                validate_cdk(limit=20)
            except Exception:
                cdk_ready = False
        healthy = healthy and cdk_ready
        return {'ready': healthy, 'database': True, 'worker': worker_ready,
                'gmailConfiguration': bool(gmail_ready), 'maintenance': maintenance_ready,
                'automation': automation_ready, 'gmailActions': gmail_actions_ready,
                'rechargeConfiguration': recharge_ready,
                'cdkConfiguration': cdk_ready,
                'sensitiveData': sensitive_data_ready,
                'sensitiveDataSampleLimit': 20}, 200 if healthy else 503
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'ready': False}, 503


@main_bp.route('/admin/<path:subpath>', strict_slashes=False)
@main_bp.route('/admin', strict_slashes=False)
@main_bp.route('/Googlemail/<path:subpath>', strict_slashes=False)
@main_bp.route('/Googlemail', strict_slashes=False)
@main_bp.route('/recharge', strict_slashes=False)
@main_bp.route('/recharge/cdk', strict_slashes=False)
@main_bp.route('/')
def index(subpath=None):
    """
    首页路由
    返回编译后的 React 前端页面
    """
    return send_from_directory(current_app.static_folder, 'index.html')


@main_bp.route('/assets/<path:filename>')
def serve_assets(filename):
    """
    静态资源路由
    服务 JS、CSS 等资源文件
    """
    assets_path = os.path.join(current_app.static_folder, 'assets')
    return send_from_directory(assets_path, filename)
