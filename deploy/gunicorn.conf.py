"""Gunicorn 生产环境配置文件。

适用于部署在 Linux VPS 服务器上驱动 Google Manager Web 服务及后台任务。
采用 gthread 工作模式，避免阻塞后台线程与子进程。
"""

import multiprocessing
import os

# 绑定监听地址与端口
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8002')

# 工作模式与并发配置
# 使用 gthread 模式，避免多进程 fork 污染 Playwright 子进程与后台守护线程
worker_class = 'gthread'
workers = int(os.environ.get('GUNICORN_WORKERS', '2'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
worker_connections = 1000

# 超时设置
# 给批量请求和文件上传留出足够的处理时间
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5

# 日志输出配置
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
accesslog = '-'  # 标准输出
errorlog = '-'   # 标准错误
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'

# 进程管理与安全
# 不预加载应用，确保每个 Worker 独立初始化数据库连接与锁
preload_app = False
daemon = False

def on_starting(server):
    server.log.info("Google Manager Gunicorn 服务正在启动...")

def on_exit(server):
    server.log.info("Google Manager Gunicorn 服务已平滑退出。")
