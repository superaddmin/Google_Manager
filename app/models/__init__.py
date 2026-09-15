"""
数据模型包
导出所有数据模型供其他模块使用
"""
from app.models.account import Account
from app.models.googlemail_task import GooglemailTask
from app.models.gmail_connection import GmailConnection
from app.models.gmail_rule import GmailRule
from app.models.gmail_task_log import GmailActionConfirmation, GmailTaskLog
from app.models.gmail_watch import GmailWatch

__all__ = [
    'Account',
    'GooglemailTask',
    'GmailConnection',
    'GmailRule',
    'GmailTaskLog',
    'GmailActionConfirmation',
    'GmailWatch',
]
