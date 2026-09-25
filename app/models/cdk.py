import time
import uuid

from app import db


def new_id():
    return uuid.uuid4().hex


class Entity:
    id = db.Column(db.String(32), primary_key=True, default=new_id)
    created_at = db.Column(db.Float, nullable=False, default=time.time)
    version = db.Column(db.Integer, nullable=False, default=1)


class CdkStaff(Entity, db.Model):
    __tablename__ = 'cdk_staff'
    username = db.Column(db.String(64), nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(24), nullable=False)
    scopes = db.Column(db.JSON, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)


class CdkStaffSession(db.Model):
    __tablename__ = 'cdk_staff_sessions'
    token_hash = db.Column(db.String(64), primary_key=True)
    staff_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'), nullable=False)
    auth_version = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.Float, nullable=False)
    authenticated_at = db.Column(db.Float, nullable=False, default=time.time)
    csrf_token = db.Column(db.String(64), nullable=False)


class CdkCustomer(Entity, db.Model):
    __tablename__ = 'cdk_customers'
    email_ciphertext = db.Column(db.Text, nullable=False)
    email_digest = db.Column(db.String(64), nullable=False, unique=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)


class CdkCustomerSession(db.Model):
    __tablename__ = 'cdk_customer_sessions'
    token_hash = db.Column(db.String(64), primary_key=True)
    customer_id = db.Column(db.String(32), db.ForeignKey('cdk_customers.id'), nullable=False)
    expires_at = db.Column(db.Float, nullable=False)
    authenticated_at = db.Column(db.Float, nullable=False, default=time.time)


class CdkChannel(Entity, db.Model):
    __tablename__ = 'cdk_channels'
    code = db.Column(db.String(64), nullable=False, unique=True)
    name = db.Column(db.String(128), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)


class CdkBenefit(Entity, db.Model):
    __tablename__ = 'cdk_benefits'
    product_code = db.Column(db.String(64), nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    plan_type = db.Column(db.String(64), nullable=False)
    provider = db.Column(db.String(64), nullable=False, default='default')
    renewal_allowed = db.Column(db.Boolean, nullable=False, default=False)
    reference_amount_minor = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default='CNY')
    __table_args__ = (
        db.UniqueConstraint('product_code', 'revision'),
        db.CheckConstraint('revision > 0 AND reference_amount_minor >= 0'),
    )


class CdkBatch(Entity, db.Model):
    __tablename__ = 'cdk_batches'
    name = db.Column(db.String(128), nullable=False)
    benefit_id = db.Column(db.String(32), db.ForeignKey('cdk_benefits.id'), nullable=False)
    channel_id = db.Column(db.String(32), db.ForeignKey('cdk_channels.id'), nullable=False, index=True)
    maker_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    state = db.Column(db.String(16), nullable=False, default='draft')
    not_before = db.Column(db.Float, nullable=False)
    expires_at = db.Column(db.Float, nullable=False)
    customer_required = db.Column(db.Boolean, nullable=False, default=False)
    is_mock = db.Column(db.Boolean, nullable=False)
    __table_args__ = (
        db.CheckConstraint('quantity > 0 AND expires_at > not_before'),
        db.CheckConstraint("state IN ('draft','active','frozen','closed')"),
    )


class CdkCard(Entity, db.Model):
    __tablename__ = 'cdk_cards'
    batch_id = db.Column(db.String(32), db.ForeignKey('cdk_batches.id'), nullable=False, index=True)
    ciphertext = db.Column(db.Text, nullable=False)
    last4 = db.Column(db.String(4), nullable=False)
    control = db.Column(db.String(16), nullable=False, default='draft')
    usage = db.Column(db.String(16), nullable=False, default='unused')
    distribution = db.Column(db.String(16), nullable=False, default='unassigned')
    expires_at = db.Column(db.Float, nullable=False)
    owner_id = db.Column(db.String(32), db.ForeignKey('cdk_customers.id'), index=True)
    active_redemption_id = db.Column(db.String(32), unique=True)
    successful_redemption_id = db.Column(db.String(32), unique=True)
    replacement_of = db.Column(db.String(32), db.ForeignKey('cdk_cards.id'), unique=True)
    __table_args__ = (
        db.CheckConstraint("control IN ('draft','active','frozen','void')"),
        db.CheckConstraint("usage IN ('unused','reserved','redeemed')"),
        db.CheckConstraint("distribution IN ('unassigned','distributed','claimed')"),
        db.CheckConstraint("(usage = 'unused' AND active_redemption_id IS NULL AND successful_redemption_id IS NULL) OR (usage = 'reserved' AND active_redemption_id IS NOT NULL AND successful_redemption_id IS NULL) OR (usage = 'redeemed' AND successful_redemption_id IS NOT NULL)"),
    )


class CdkFingerprint(db.Model):
    __tablename__ = 'cdk_fingerprints'
    kind = db.Column(db.String(8), primary_key=True)
    key_id = db.Column(db.String(32), primary_key=True)
    digest = db.Column(db.String(64), primary_key=True)
    resource_id = db.Column(db.String(32), nullable=False, index=True)
    __table_args__ = (db.UniqueConstraint('kind', 'key_id', 'resource_id'),)


class CdkStock(Entity, db.Model):
    __tablename__ = 'cdk_stocks'
    benefit_id = db.Column(db.String(32), db.ForeignKey('cdk_benefits.id'), nullable=False, index=True)
    ciphertext = db.Column(db.Text, nullable=False)
    last4 = db.Column(db.String(4), nullable=False)
    state = db.Column(db.String(16), nullable=False, default='quarantine', index=True)
    card_id = db.Column(db.String(32), db.ForeignKey('cdk_cards.id'), unique=True)
    valid_until = db.Column(db.Float, nullable=False)
    verified_at = db.Column(db.Float)
    verification = db.Column(db.String(32))
    purchase_ref = db.Column(db.String(128), nullable=False)
    is_mock = db.Column(db.Boolean, nullable=False)
    __table_args__ = (
        db.CheckConstraint("state IN ('quarantine','available','allocated','reserved','consumed','unusable')"),
        db.CheckConstraint("state NOT IN ('allocated','reserved','consumed') OR card_id IS NOT NULL"),
    )


class CdkJob(Entity, db.Model):
    __tablename__ = 'cdk_jobs'
    kind = db.Column(db.String(32), nullable=False)
    actor_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'), nullable=False)
    channel_id = db.Column(db.String(32), db.ForeignKey('cdk_channels.id'))
    state = db.Column(db.String(16), nullable=False, default='prepared')
    ciphertext = db.Column(db.Text)
    result = db.Column(db.JSON, nullable=False, default=dict)
    expires_at = db.Column(db.Float, nullable=False)


class CdkApproval(Entity, db.Model):
    __tablename__ = 'cdk_approvals'
    maker_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'), nullable=False)
    checker_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'))
    channel_id = db.Column(db.String(32), db.ForeignKey('cdk_channels.id'), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    target_id = db.Column(db.String(32), nullable=False)
    target_version = db.Column(db.Integer, nullable=False)
    parameters = db.Column(db.JSON, nullable=False)
    reason = db.Column(db.String(256), nullable=False)
    state = db.Column(db.String(16), nullable=False, default='pending')
    expires_at = db.Column(db.Float, nullable=False)
    result = db.Column(db.JSON)
    __table_args__ = (db.CheckConstraint('checker_id IS NULL OR maker_id != checker_id'),)


class CdkDistribution(Entity, db.Model):
    __tablename__ = 'cdk_distributions'
    card_id = db.Column(db.String(32), db.ForeignKey('cdk_cards.id'), nullable=False, unique=True)
    recipient_ref = db.Column(db.String(128), nullable=False)
    ticket_hash = db.Column(db.String(64), nullable=False, unique=True)
    ticket_ciphertext = db.Column(db.Text, nullable=False)
    audience_digest = db.Column(db.String(64))
    expires_at = db.Column(db.Float, nullable=False)
    claimed_at = db.Column(db.Float)
    actor_id = db.Column(db.String(32), db.ForeignKey('cdk_staff.id'), nullable=False)


class CdkRedemption(Entity, db.Model):
    __tablename__ = 'cdk_redemptions'
    card_id = db.Column(db.String(32), db.ForeignKey('cdk_cards.id'), nullable=False, index=True)
    stock_id = db.Column(db.String(32), db.ForeignKey('cdk_stocks.id'), nullable=False)
    task_no = db.Column(db.String(64), db.ForeignKey('recharge_tasks.task_no'), nullable=False, unique=True)
    principal = db.Column(db.String(80), nullable=False, index=True)
    fingerprint = db.Column(db.String(64), nullable=False)
    state = db.Column(db.String(24), nullable=False, default='prepared', index=True)
    active_card_key = db.Column(db.String(32), unique=True)
    active_stock_key = db.Column(db.String(32), unique=True)
    dispatch_started_at = db.Column(db.Float)
    lease_token = db.Column(db.String(64))
    updated_at = db.Column(db.Float, nullable=False, default=time.time)
    release_decision = db.Column(db.String(24), nullable=False, default='unresolved')
    evidence_hash = db.Column(db.String(64), unique=True)
    mutation_action = db.Column(db.String(16))
    mutation_id = db.Column(db.String(64))
    mutation_state = db.Column(db.String(16))
    __table_args__ = (
        db.CheckConstraint("state IN ('prepared','dispatching','processing','unknown','succeeded','released','cancelled')"),
        db.CheckConstraint("release_decision IN ('unresolved','not_consumed','consumed')"),
        db.CheckConstraint("(state IN ('released','cancelled') AND active_card_key IS NULL AND active_stock_key IS NULL) OR (state NOT IN ('released','cancelled') AND active_card_key IS NOT NULL AND active_stock_key IS NOT NULL)"),
    )


class CdkRequest(db.Model):
    __tablename__ = 'cdk_requests'
    principal = db.Column(db.String(80), primary_key=True)
    operation = db.Column(db.String(128), primary_key=True)
    request_key = db.Column(db.String(80), primary_key=True)
    fingerprint = db.Column(db.String(64), nullable=False)
    result = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.Float, nullable=False, default=time.time)


class CdkEvidence(db.Model):
    __tablename__ = 'cdk_evidence'
    digest = db.Column(db.String(64), primary_key=True)
    redemption_id = db.Column(db.String(32), db.ForeignKey('cdk_redemptions.id'), nullable=False)
    approval_id = db.Column(db.String(32), db.ForeignKey('cdk_approvals.id'), nullable=False, unique=True)
    created_at = db.Column(db.Float, nullable=False, default=time.time)


class CdkAudit(Entity, db.Model):
    __tablename__ = 'cdk_audits'
    actor = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(64), nullable=False)
    target_id = db.Column(db.String(64), nullable=False, index=True)
    channel_id = db.Column(db.String(32), db.ForeignKey('cdk_channels.id'), index=True)
    details = db.Column(db.JSON, nullable=False, default=dict)


class CdkOutbox(Entity, db.Model):
    __tablename__ = 'cdk_outbox'
    redemption_id = db.Column(db.String(32), db.ForeignKey('cdk_redemptions.id'), nullable=False)
    aggregate_version = db.Column(db.Integer, nullable=False)
    state = db.Column(db.String(24), nullable=False)
    processed_at = db.Column(db.Float)
    __table_args__ = (db.UniqueConstraint('redemption_id', 'aggregate_version'),)
