from datetime import datetime, timezone

from app import db


class RechargeMutationReconciliation(db.Model):
    __tablename__ = 'recharge_mutation_reconciliations'

    operation_id = db.Column(db.String(32), primary_key=True)
    task_no = db.Column(db.String(64), db.ForeignKey('recharge_tasks.task_no'), nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)
    resolution = db.Column(db.String(20), nullable=False)
    basis = db.Column(db.String(1024), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    evidence_fingerprint = db.Column(db.String(64), nullable=False, unique=True)
    evidence_source = db.Column(db.String(32), nullable=False)
    evidence_reference = db.Column(db.String(256), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)
    evidence_observed_at = db.Column(db.DateTime, nullable=False)
    mutation_started_at = db.Column(db.Float, nullable=False)
    previous_state = db.Column(db.String(20), nullable=False)
    final_state = db.Column(db.String(20), nullable=False)
    upstream_task_no = db.Column(db.String(128), nullable=False)
    previous_status = db.Column(db.String(32), nullable=False)
    final_status = db.Column(db.String(32), nullable=False)
    actor_id = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def to_dict(self):
        result = {
            name: getattr(self, name) for name in (
                'operation_id', 'task_no', 'action', 'resolution', 'basis',
                'evidence_source', 'evidence_reference', 'evidence_sha256',
                'mutation_started_at', 'previous_state', 'final_state',
                'upstream_task_no', 'previous_status', 'final_status', 'actor_id',
            )
        }
        for name in ('evidence_observed_at', 'created_at'):
            value = getattr(self, name)
            result[name] = value.replace(tzinfo=timezone.utc).isoformat() if value else None
        return result
