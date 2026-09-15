"""
Googlemail 任务历史模型
持久化已结束的 Googlemail 任务记录，用于历史回溯
"""
from app import db


class GooglemailTask(db.Model):
    """Googlemail 任务历史记录"""
    __tablename__ = 'googlemail_tasks'

    task_id = db.Column(db.String(32), primary_key=True)
    status = db.Column(db.String(20), nullable=False)
    total_count = db.Column(db.Integer, default=0, nullable=False)
    completed_count = db.Column(db.Integer, default=0, nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    synced_count = db.Column(db.Integer, default=0, nullable=False)
    manual_review_count = db.Column(db.Integer, default=0, nullable=False)
    exit_code = db.Column(db.Integer, nullable=True)
    error_code = db.Column(db.String(64), nullable=True)
    headless = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.String(32), nullable=False, index=True)
    started_at = db.Column(db.String(32), nullable=True)
    finished_at = db.Column(db.String(32), nullable=True)

    def to_dict(self):
        """转换为字典，字段形状与运行时任务状态保持一致"""
        processed = min(self.completed_count + self.failed_count, self.total_count)
        return {
            'taskId': self.task_id,
            'status': self.status,
            'totalCount': self.total_count,
            'completedCount': self.completed_count,
            'failedCount': self.failed_count,
            'pendingCount': max(self.total_count - processed, 0),
            'syncedCount': self.synced_count,
            'manualReviewCount': self.manual_review_count,
            'exitCode': self.exit_code,
            'errorCode': self.error_code,
            'headless': self.headless,
            'createdAt': self.created_at,
            'startedAt': self.started_at,
            'finishedAt': self.finished_at,
        }

    def __repr__(self):
        return f'<GooglemailTask {self.task_id}>'
