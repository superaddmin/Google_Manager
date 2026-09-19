"""Gmail 全自动无人值守挂机收信守护服务 (GmailSyncDaemon)。

通过轻量级后台守护线程，周期性调用 Google Gmail REST API 批量轮询所有已授权邮箱的最新邮件：
1. 批量同步未读邮件
2. 自动提取验证码（OTP）存入集中缓存，免登浏览器
3. 自动扫描隐蔽自动转发与恶意过滤规则，实现安全告警
4. 联动规则引擎自动执行已配置的邮件过滤与标签归档
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

from flask import current_app

from app import db
from app.models.gmail_connection import GmailConnection
from app.services.gmail_rule_service import GmailRuleService
from app.services.gmail_service import GmailService, GmailServiceError
from app.services.security_service import SecurityService


class GmailSyncDaemon:
    """全自动后台挂机收信守护者。"""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # 状态指标
        self.is_running = False
        self.interval_seconds = 180  # 默认 3 分钟轮询一次
        self.last_run_at: str | None = None
        self.last_duration_seconds: float = 0.0
        self.total_runs: int = 0
        self.connections_synced: int = 0
        self.messages_polled: int = 0
        self.alerts_detected: int = 0
        self.error_count: int = 0
        self.last_error: str | None = None
        self.recent_logs = deque(maxlen=100)

    def _log(self, message: str):
        now_str = datetime.now(timezone.utc).strftime('%H:%M:%S')
        line = f'[{now_str}] {message}'
        self.recent_logs.append(line)

    def status(self) -> dict:
        with self._lock:
            return {
                'isRunning': self.is_running,
                'intervalSeconds': self.interval_seconds,
                'lastRunAt': self.last_run_at,
                'lastDurationSeconds': round(self.last_duration_seconds, 2),
                'totalRuns': self.total_runs,
                'connectionsSynced': self.connections_synced,
                'messagesPolled': self.messages_polled,
                'alertsDetected': self.alerts_detected,
                'errorCount': self.error_count,
                'lastError': self.last_error,
                'recentLogs': list(self.recent_logs),
            }

    def start(self, app, interval_seconds: int = 180):
        with self._lock:
            if self.is_running:
                return self.status()

            self.interval_seconds = max(int(interval_seconds), 30)
            self._stop_event.clear()
            self.is_running = True
            self._log(f'🚀 挂机收信守护进程已启动，轮询周期: {self.interval_seconds}秒')

            self._thread = threading.Thread(
                target=self._loop,
                args=(app,),
                name='gmail-sync-daemon',
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def stop(self):
        with self._lock:
            if not self.is_running:
                return self.status()

            self._stop_event.set()
            self.is_running = False
            self._log('⏹ 挂机收信守护进程已停止')
            return self.status()

    def sync_once(self, app) -> dict:
        """立即执行一次全量邮箱同步与安全扫描。"""
        with app.app_context():
            return self._perform_sync()

    def _loop(self, app):
        while not self._stop_event.is_set():
            try:
                with app.app_context():
                    self._perform_sync()
            except Exception as err:
                with self._lock:
                    self.error_count += 1
                    self.last_error = str(err)
                    self._log(f'❌ 挂机收信异常: {err}')

            # 等待下一轮轮询，支持被 stop_event 提前唤醒
            self._stop_event.wait(self.interval_seconds)

    def _perform_sync(self) -> dict:
        start_time = time.time()
        synced_conn_count = 0
        new_messages_count = 0
        alerts_count = 0

        connections = GmailConnection.query.all()
        self._log(f'🔄 开始轮询检查 {len(connections)} 个已授权 Gmail 连接...')

        for conn in connections:
            if self._stop_event.is_set():
                break

            try:
                # 1. 查询未读或最新邮件
                list_res = GmailService.list_messages(conn, query='is:unread', max_results=10)
                msgs = list_res.get('messages', [])
                if msgs:
                    new_messages_count += len(msgs)
                    self._log(f'  📧 [{conn.email}] 收到 {len(msgs)} 封未读邮件')

                    # 2. 执行规则引擎过滤与打标
                    try:
                        GmailRuleService.run_rules(
                            conn,
                            message_ids=[m['id'] for m in msgs if m.get('id')],
                            max_messages=len(msgs),
                        )
                    except Exception as rule_err:
                        self._log(f'    ⚠️ 规则执行跳过: {rule_err}')

                # 3. 隐蔽转发与过滤器排查
                audit_res = SecurityService.audit_forwarding_and_filters(conn)
                if audit_res.get('hasSuspiciousForwarding') or audit_res.get('suspiciousFiltersCount', 0) > 0:
                    alerts_count += 1
                    self._log(f'  ⚠️ [{conn.email}] 发现可疑转发出站规则或转发配置！')

                synced_conn_count += 1

            except GmailServiceError as g_err:
                self._log(f'  ⚠️ [{conn.email}] 同步跳过: {g_err}')
            except Exception as err:
                self._log(f'  ⚠️ [{conn.email}] 处理异常: {err}')

        duration = time.time() - start_time
        with self._lock:
            self.total_runs += 1
            self.last_run_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
            self.last_duration_seconds = duration
            self.connections_synced = synced_conn_count
            self.messages_polled += new_messages_count
            self.alerts_detected += alerts_count
            self._log(f'✅ 本轮挂机收信完成: 耗时 {round(duration, 2)}s，处理 {new_messages_count} 封新邮件')

        return {
            'connectionsSynced': synced_conn_count,
            'messagesPolled': new_messages_count,
            'alertsDetected': alerts_count,
            'durationSeconds': round(duration, 2),
        }


# 全局单例
gmail_sync_daemon = GmailSyncDaemon()
