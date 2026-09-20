"""充值任务人工对账审计记录。"""
from datetime import datetime, timezone

from app import db


class RechargeReconciliation(db.Model):
    """保存 unknown 创建请求的一次性、不可覆盖人工结论。"""

    __tablename__ = 'recharge_reconciliations'

    id = db.Column(db.Integer, primary_key=True)
    task_no = db.Column(
        db.String(64),
        db.ForeignKey('recharge_tasks.task_no'),
        unique=True,
        nullable=False,
        index=True,
    )
    resolution = db.Column(db.String(20), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    evidence_fingerprint = db.Column(db.String(64), unique=True, nullable=False, index=True)
    evidence_source = db.Column(db.String(32), nullable=False)
    evidence_reference = db.Column(db.String(256), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)
    evidence_observed_at = db.Column(db.DateTime, nullable=False)
    upstream_task_no = db.Column(db.String(128), nullable=True)
    upstream_status = db.Column(db.String(32), nullable=True)
    previous_status = db.Column(db.String(32), nullable=False)
    final_status = db.Column(db.String(32), nullable=False)
    actor_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'task_no': self.task_no,
            'resolution': self.resolution,
            'evidence_source': self.evidence_source,
            'evidence_reference': self.evidence_reference,
            'evidence_sha256': self.evidence_sha256,
            'evidence_observed_at': (
                self.evidence_observed_at.replace(tzinfo=timezone.utc).isoformat()
                if self.evidence_observed_at else None
            ),
            'upstream_task_no': self.upstream_task_no,
            'upstream_status': self.upstream_status,
            'previous_status': self.previous_status,
            'final_status': self.final_status,
            'actor_id': self.actor_id,
            'created_at': (
                self.created_at.replace(tzinfo=timezone.utc).isoformat()
                if self.created_at else None
            ),
        }
