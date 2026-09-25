import json
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import text

from app import create_app, db
from app.models.cdk import (CdkApproval, CdkAudit, CdkBatch, CdkCard, CdkFingerprint, CdkJob,
                            CdkOutbox, CdkRedemption, CdkRequest, CdkStock)
from app.models.one_time_token import OneTimeToken
from app.models.recharge_task import RechargeTask
from app.services.cdk_crypto import decrypt, generate_code, normalize_code
from app.services.cdk_identity import create_staff
from app.services.cdk_redemption import apply_observation, recover_interrupted
from app.services.recharge_service import RechargeService, RechargeUpstreamError
from app.services.schema_migration import initialize_database, validate_schema
from tests.auth_helpers import login_admin


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class CdkFixture(unittest.TestCase):
    def setUp(self):
        self.application = create_app('testing')
        self.application.config.update(CDK_ENABLED=True, CDK_ENCRYPTION_KEYS={'v1': Fernet.generate_key().decode()},
                                       CDK_LOOKUP_KEYS={'v1': Fernet.generate_key().decode()},
                                       GMAIL_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())
        self.context = self.application.app_context()
        self.context.push()
        db.create_all()
        create_staff('maker', 'maker-test-password', 'admin', ['*'])
        create_staff('checker', 'checker-test-password', 'admin', ['*'])
        db.session.commit()
        self.maker = self.staff_client('maker')
        self.checker = self.staff_client('checker')
        self.customer = self.application.test_client()
        self.customer.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def staff_client(self, name):
        client = self.application.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        response = client.post('/api/cdk/admin/session/login', json={'username': name, 'password': name + '-test-password'})
        self.assertEqual(response.status_code, 200, response.get_json())
        client.environ_base['HTTP_X_CSRF_TOKEN'] = response.get_json()['data']['csrf_token']
        return client

    def post(self, path, payload, client=None, key=None, status=200):
        response = (client or self.maker).post('/api/cdk' + path, json=payload,
            headers={'Idempotency-Key': key or uuid.uuid4().hex})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()['data']

    def create_catalog(self, quantity=1, customer_required=False):
        now = time.time()
        self.channel = self.post('/admin/channels', {'code': 'test-' + uuid.uuid4().hex, 'name': '测试渠道'})['id']
        self.benefit = self.post('/admin/benefits', {'product_code': 'plus-' + uuid.uuid4().hex, 'revision': 1,
            'name': 'Plus 月权益', 'plan_type': 'PLUS', 'reference_amount_minor': 10000, 'currency': 'CNY'})['id']
        codes = ['PLUS-PRIVATE-' + uuid.uuid4().hex for _ in range(quantity)]
        self.supplier_codes = codes
        preview = self.post('/admin/imports/preview', {'benefit_id': self.benefit, 'content': '\n'.join(codes),
            'purchase_ref': 'PURCHASE-TEST', 'valid_until': iso(now + 30 * 86400)})
        imported = self.post('/admin/imports/' + preview['id'] + '/commit', {'version': 1, 'accept_valid_rows': True})
        for stock_id in imported['stock_ids']:
            self.post('/admin/stocks/' + stock_id + '/verify', {'version': 1})
        self.batch = self.post('/admin/batches', {'channel_id': self.channel, 'benefit_id': self.benefit, 'name': 'CDK 测试批次',
            'quantity': quantity, 'not_before': iso(now - 60), 'expires_at': iso(now + 7 * 86400),
            'customer_required': customer_required})['id']
        self.post('/admin/batches/' + self.batch + '/generate', {'version': 1})
        approval = self.post('/admin/approvals', {'action': 'activate', 'target_id': self.batch, 'version': 2, 'reason': '确认测试库存与权益'})
        self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker)
        self.cards = CdkCard.query.filter_by(batch_id=self.batch).order_by(CdkCard.id).all()
        self.card_id = self.cards[0].id
        self.code = decrypt(self.cards[0].ciphertext)
        return self.code

    def intent(self, code=None):
        return {'code': code or self.code, 'plan_type': 'PLUS', 'account_email': 'customer@example.test',
                'token_input': json.dumps({'accessToken': 'synthetic-secret-do-not-store', 'user': {'email': 'customer@example.test'}}),
                'agreement_accepted': True, 'acknowledge_non_free': True, 'is_renewal': False}

    def submit(self, payload=None, key=None):
        payload = payload or self.intent()
        challenge = self.post('/challenges', payload, self.customer)
        payload = {**payload, 'challenge_token': challenge['challenge_token']}
        result = self.post('/redemptions', payload, self.customer, key=key, status=202)
        return result, payload

class CdkTestCase(CdkFixture):
    def test_public_config_distinguishes_cdk_from_fulfillment_availability(self):
        for mode in ('disabled', 'mock', 'live'):
            with self.subTest(mode=mode):
                self.application.config['RECHARGE_MODE'] = mode
                response = self.customer.get('/api/cdk/config')
                self.assertEqual(response.status_code, 200)
                config = response.get_json()['data']
                self.assertTrue(config['enabled'])
                self.assertEqual(config['fulfillment_enabled'], mode != 'disabled')
                self.assertEqual(config['format'], 'GM1')
        self.assertEqual(CdkRedemption.query.count(), 0)

    def customer_login(self, email='customer@example.test', client=None):
        client = client or self.customer
        self.application.config.update(CDK_SMTP_HOST='smtp.example.test', CDK_SMTP_FROM='cdk@example.test')
        with patch('app.services.cdk_identity.smtplib.SMTP_SSL') as smtp:
            issued = self.post('/customer/otp/send', {'email': email}, client)
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            import re
            code = re.search(r'\b\d{6}\b', message.get_content()).group()
        return self.post('/customer/otp/verify', {'challenge_id': issued['challenge_id'], 'code': code}, client)

    def test_complete_catalog_redemption_and_close(self):
        self.create_catalog()
        result, _ = self.submit()
        self.assertEqual(result['state'], 'processing')
        task = RechargeTask.query.filter_by(task_no=result['task_no']).one()
        self.assertEqual(task.redeem_code, self.supplier_codes[0])
        self.assertNotEqual(task.redeem_code, self.code)
        result = self.post('/redemptions/' + result['id'] + '/refresh', {}, self.customer)
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(db.session.get(CdkCard, self.card_id).usage, 'redeemed')
        self.assertEqual(CdkStock.query.filter_by(card_id=self.card_id).one().state, 'consumed')
        closed = self.post('/redemptions/' + result['id'] + '/close', {'version': result['version'], 'confirmed': True}, self.customer)
        self.assertEqual(closed['task']['status'], 'closed')
        self.assertEqual(db.session.get(CdkCard, self.card_id).usage, 'redeemed')
        self.assertGreater(CdkOutbox.query.count(), 2)

    def test_idempotent_replay_returns_original_and_conflict_has_no_side_effect(self):
        self.create_catalog()
        key = uuid.uuid4().hex
        result, payload = self.submit(key=key)
        replay = self.post('/redemptions', payload, self.customer, key=key, status=202)
        self.assertEqual(replay['id'], result['id'])
        self.assertEqual(CdkRedemption.query.count(), 1)
        altered = {**payload, 'token_input': json.dumps({'accessToken': 'different-token', 'user': {'email': 'customer@example.test'}})}
        self.post('/redemptions', altered, self.customer, key=key, status=409)
        recovered = self.post('/redemptions/recover', {'idempotency_key': key}, self.customer)
        self.assertEqual(recovered['id'], result['id'])

    def test_challenge_binds_email_credential_plan_and_expiry(self):
        self.create_catalog()
        payload = self.intent()
        challenge = self.post('/challenges', payload, self.customer)
        changed = {**payload, 'challenge_token': challenge['challenge_token'], 'account_email': 'different@example.test'}
        self.post('/redemptions', changed, self.customer, status=422)
        self.assertEqual(CdkRedemption.query.count(), 0)
        changed['token_input'] = json.dumps({'accessToken': 'different', 'user': {'email': 'different@example.test'}})
        self.post('/redemptions', changed, self.customer, status=409)
        self.assertEqual(CdkCard.query.first().usage, 'unused')

    def test_duplicate_import_and_history_are_quarantined(self):
        self.create_catalog()
        preview = self.post('/admin/imports/preview', {'benefit_id': self.benefit,
            'content': self.supplier_codes[0] + '\nINVALID!!!\nPLUS-NEW-CODE\nPLUS-NEW-CODE',
            'purchase_ref': 'PURCHASE-DUPLICATE', 'valid_until': iso(time.time() + 86400)})
        self.assertEqual(preview['valid_count'], 1)
        self.assertEqual(preview['error_count'], 3)
        self.assertNotIn(self.supplier_codes[0], json.dumps(preview))

    def test_approval_cannot_self_approve_or_apply_stale_snapshot(self):
        self.create_catalog()
        card = db.session.get(CdkCard, self.card_id)
        approval = self.post('/admin/approvals', {'action': 'void', 'target_id': card.id, 'version': card.version, 'reason': '申请作废'})
        self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, status=403)
        self.post('/admin/cards/' + card.id + '/freeze', {'version': card.version, 'reason': '先冻结'})
        self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker, status=409)

    def test_supplier_and_platform_codes_cannot_bypass_legacy_routes(self):
        self.create_catalog()
        result, _ = self.submit()
        for code in (self.code, self.supplier_codes[0]):
            response = self.customer.post('/api/recharge/redeem-codes/validate', json={'redeem_code': code})
            self.assertEqual(response.status_code, 400, response.get_json())
        response = self.customer.get('/api/recharge/tasks/' + result['task_no'])
        self.assertEqual(response.status_code, 403)
        anonymous = self.application.test_client()
        self.assertEqual(anonymous.get('/api/cdk/redemptions/' + result['id']).status_code, 404)
        login_admin(anonymous)
        self.assertEqual(anonymous.get('/api/cdk/admin/cards').status_code, 401)
        self.assertEqual(anonymous.get('/api/recharge/admin/tasks').get_json()['data']['total'], 0)

    def test_scope_read_only_csrf_and_employee_revocation(self):
        self.create_catalog()
        create_staff('support', 'support-test-password', 'support', [self.channel])
        db.session.commit()
        support = self.staff_client('support')
        self.post('/admin/cards/' + self.card_id + '/freeze', {'version': 2, 'reason': '不应允许'}, support, status=403)
        self.assertEqual(support.get('/api/accounts').status_code, 401)
        response = self.maker.post('/api/cdk/admin/channels', json={'code': 'no-csrf', 'name': '测试'}, headers={'X-CSRF-Token': ''})
        self.assertEqual(response.status_code, 403)
        from app.models.cdk import CdkStaff
        staff = CdkStaff.query.filter_by(username='support').one()
        self.post('/admin/users/' + staff.id + '/update', {'version': staff.version, 'enabled': False})
        self.assertEqual(support.get('/api/cdk/admin/cards').status_code, 401)

    def test_unknown_retains_stock_and_never_resends(self):
        self.create_catalog()
        CdkBatch.query.update({'is_mock': False})
        CdkStock.query.update({'is_mock': False})
        db.session.commit()
        self.application.config['RECHARGE_MODE'] = 'live'
        key = uuid.uuid4().hex
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('timeout')) as upstream:
            result, payload = self.submit(key=key)
            self.assertEqual(result['state'], 'unknown')
            self.post('/redemptions', payload, self.customer, key=key, status=202)
            self.assertEqual(upstream.call_count, 1)
        self.assertEqual(CdkCard.query.first().usage, 'reserved')
        self.assertEqual(CdkStock.query.first().state, 'reserved')

    def test_mismatched_upstream_task_identity_does_not_consume(self):
        self.create_catalog()
        result, _ = self.submit()
        with self.assertRaises(RechargeUpstreamError):
            apply_observation(result['id'], {'task_no': 'WRONG', 'client_task_no': result['task_no'], 'status': 'completed'}, result['version'])
        db.session.rollback()
        self.assertEqual(CdkCard.query.first().usage, 'reserved')

    def test_freeze_expiration_and_missing_stock_block_submission(self):
        self.create_catalog()
        self.post('/admin/batches/' + self.batch + '/freeze', {'version': 3, 'reason': '暂停测试'})
        self.post('/validate', {'code': self.code}, self.customer, status=409)
        batch = db.session.get(CdkBatch, self.batch)
        batch.state = 'active'
        card = db.session.get(CdkCard, self.card_id)
        card.expires_at = time.time() - 1
        db.session.commit()
        self.post('/challenges', self.intent(), self.customer, status=409)
        self.assertEqual(CdkRedemption.query.count(), 0)

    def test_export_approval_never_exposes_provider_secret_and_revalidates_freeze(self):
        self.create_catalog()
        distribution = self.post('/admin/distributions', {'card_id': self.card_id, 'version': 2, 'recipient_ref': 'delivery-001'})
        card = db.session.get(CdkCard, self.card_id)
        approval = self.post('/admin/approvals', {'action': 'export', 'target_id': self.batch, 'version': 3,
            'card_ids': [card.id], 'reason': '线下安全交付'})
        result = self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker)
        export_id = result['result']['export_id']
        response = self.maker.post('/api/cdk/admin/exports/' + export_id + '/download', json={})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.code, response.get_data(as_text=True))
        self.assertNotIn(self.supplier_codes[0], response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.post('/admin/cards/' + card.id + '/freeze', {'version': card.version, 'reason': '停止交付'})
        self.assertEqual(self.maker.post('/api/cdk/admin/exports/' + export_id + '/download', json={}).status_code, 409)
        self.assertIsNotNone(distribution['id'])

    def test_transaction_failure_rolls_back_challenge_and_inventory(self):
        self.create_catalog()
        payload = self.intent()
        issued = self.post('/challenges', payload, self.customer)
        payload['challenge_token'] = issued['challenge_token']
        with patch('app.services.cdk_redemption.changed', side_effect=RuntimeError('transaction injected failure')):
            self.post('/redemptions', payload, self.customer, status=500)
        self.assertEqual(RechargeTask.query.count(), 0)
        self.assertEqual(CdkCard.query.first().usage, 'unused')
        self.assertIsNone(OneTimeToken.lookup('cdk_redeem', issued['challenge_token']).consumed_at)
        self.post('/redemptions', payload, self.customer, status=202)

    def test_recovery_does_not_release_dispatching_or_unknown(self):
        self.create_catalog()
        result, _ = self.submit()
        record = db.session.get(CdkRedemption, result['id'])
        record.state, record.dispatch_started_at = 'dispatching', time.time() - 200
        db.session.commit()
        recover_interrupted()
        self.assertEqual(db.session.get(CdkRedemption, record.id).state, 'unknown')
        self.assertEqual(CdkCard.query.first().usage, 'reserved')

    def test_secrets_are_encrypted_and_sqlite_constraints_enabled(self):
        self.create_catalog()
        self.submit()
        raw = '\n'.join(str(row) for table in ('cdk_cards', 'cdk_stocks', 'cdk_requests', 'cdk_audits', 'cdk_redemptions', 'recharge_tasks')
                        for row in db.session.execute(text('SELECT * FROM ' + table)))
        self.assertNotIn(self.code, raw)
        self.assertNotIn(self.supplier_codes[0], raw)
        self.assertNotIn('synthetic-secret-do-not-store', raw)
        self.assertEqual(db.session.execute(text('PRAGMA foreign_keys')).scalar(), 1)
        db.session.rollback()
        initialize_database(db.engine)
        validate_schema(db.engine)
        self.assertEqual(initialize_database(db.engine), [])

    def test_code_normalization_checks_prefix_body_and_checksum(self):
        for _ in range(30):
            code = generate_code()
            self.assertEqual(normalize_code('  ' + code[:3] + '-' + code[3:].lower() + ' '), code)
            changed = code[:-1] + ('0' if code[-1] != '0' else '1')
            with self.assertRaises(ValueError):
                normalize_code(changed)

    def test_customer_claim_is_bound_single_use_and_cross_device(self):
        self.customer_login()
        self.create_catalog(customer_required=True)
        distribution = self.post('/admin/distributions', {'card_id': self.card_id, 'version': 2, 'recipient_ref': 'CUSTOMER-DELIVERY',
            'audience_email': 'customer@example.test'})
        ticket = self.post('/admin/distributions/' + distribution['id'] + '/ticket', {})['ticket']
        self.post('/validate', {'code': self.code}, self.customer, status=403)
        wrong_customer = self.application.test_client()
        wrong_customer.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        self.customer_login('other@example.test', wrong_customer)
        self.post('/claims', {'ticket': ticket}, wrong_customer, status=403)
        request_key = uuid.uuid4().hex
        claimed = self.post('/claims', {'ticket': ticket}, self.customer, key=request_key)
        self.assertEqual(claimed, self.post('/claims', {'ticket': ticket}, self.customer, key=request_key))
        self.post('/claims', {'ticket': ticket}, self.customer, status=409)
        self.post('/cards/' + self.card_id + '/reveal', {}, wrong_customer, status=404)
        self.assertEqual(self.post('/cards/' + self.card_id + '/reveal', {}, self.customer)['code'], self.code)
        result, _ = self.submit()
        another_device = self.application.test_client()
        another_device.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        self.customer_login(client=another_device)
        response = another_device.get('/api/cdk/redemptions/' + result['id'])
        self.assertEqual(response.status_code, 200)

    def test_otp_is_disabled_without_service_and_limited_to_five_attempts(self):
        self.post('/customer/otp/send', {'email': 'customer@example.test'}, self.customer, status=503)
        self.application.config.update(CDK_SMTP_HOST='smtp.example.test', CDK_SMTP_FROM='cdk@example.test')
        with patch('app.services.cdk_identity.smtplib.SMTP_SSL'):
            issued = self.post('/customer/otp/send', {'email': 'customer@example.test'}, self.customer)
        record = OneTimeToken.lookup('cdk_otp', issued['challenge_id'])
        record.payload = {**record.payload, 'code_hash': '0' * 64}
        db.session.commit()
        for _ in range(6):
            self.post('/customer/otp/verify', {'challenge_id': issued['challenge_id'], 'code': '000000'}, self.customer, status=422)
        self.assertEqual(OneTimeToken.lookup('cdk_otp', issued['challenge_id']).payload['attempts'], 5)

    def test_blocked_customer_cannot_redeem_with_revoked_session(self):
        customer = self.customer_login()
        self.create_catalog(customer_required=True)
        self.post('/admin/customers/' + customer['id'] + '/update', {'version': 1, 'enabled': False})
        self.post('/challenges', self.intent(), self.customer, status=401)

    def test_prepared_crash_cancels_only_before_dispatch_and_does_not_leak_token(self):
        self.create_catalog()
        with patch('app.services.cdk_redemption.dispatch'):
            result, payload = self.submit()
        record = db.session.get(CdkRedemption, result['id'])
        self.assertEqual(record.state, 'prepared')
        record.created_at = time.time() - 301
        db.session.commit()
        recover_interrupted()
        self.assertEqual(db.session.get(CdkRedemption, record.id).state, 'cancelled')
        self.assertEqual(CdkCard.query.first().usage, 'unused')
        self.assertEqual(CdkStock.query.first().state, 'allocated')

    def test_reissue_transfers_unique_stock_and_permanently_voids_old_code(self):
        self.create_catalog()
        self.post('/admin/cards/' + self.card_id + '/freeze', {'version': 2, 'reason': '测试补发'})
        approval = self.post('/admin/approvals', {'action': 'reissue', 'target_id': self.card_id, 'version': 3, 'reason': '独立核对未使用补发'})
        result = self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker)
        replacement = db.session.get(CdkCard, result['result']['replacement_id'])
        self.assertEqual(replacement.replacement_of, self.card_id)
        self.assertEqual(CdkStock.query.one().card_id, replacement.id)
        self.post('/challenges', self.intent(), self.customer, status=409)
        self.code = decrypt(replacement.ciphertext)
        result, _ = self.submit()
        self.assertEqual(result['state'], 'processing')

    def test_two_person_evidence_release_allows_a_new_intent(self):
        self.create_catalog()
        result, _ = self.submit()
        record = db.session.get(CdkRedemption, result['id'])
        record.state, record.dispatch_started_at = 'unknown', time.time() - 180
        RechargeTask.query.filter_by(task_no=record.task_no).one().status = 'unknown'
        db.session.commit()
        approval = self.post('/admin/approvals', {'action': 'release', 'target_id': record.id, 'version': record.version,
            'reason': '供应商已确认未消费', 'reference': 'TEST-PROVIDER-TICKET-001', 'evidence_sha256': 'a' * 64,
            'observed_at': iso(time.time()), 'decision': 'not_consumed'})
        self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker)
        self.assertEqual(CdkCard.query.first().usage, 'unused')
        new_result, _ = self.submit()
        self.assertNotEqual(new_result['id'], result['id'])
        self.assertEqual(CdkRedemption.query.count(), 2)

    def test_key_rotation_keeps_old_and_new_indexes_and_detects_missing_keys(self):
        from app.services.cdk_maintenance import rotate_keys, validate_cdk, validate_cdk_ciphertexts
        self.create_catalog()
        self.application.config['CDK_ENCRYPTION_KEYS']['v2'] = Fernet.generate_key().decode()
        self.application.config['CDK_LOOKUP_KEYS']['v2'] = Fernet.generate_key().decode()
        self.application.config['CDK_ACTIVE_KEY_ID'] = 'v2'
        with self.assertRaisesRegex(ValueError, '查询索引不完整'):
            validate_cdk()
        self.assertEqual(rotate_keys(False)['indexes'], 2)
        self.assertTrue(CdkCard.query.first().ciphertext.startswith('v1:'))
        rotate_keys(True)
        self.assertEqual(validate_cdk()['cards'], 1)
        validate_cdk_ciphertexts(db.engine)
        self.assertTrue(CdkCard.query.first().ciphertext.startswith('v2:'))
        self.post('/admin/imports/preview', {'benefit_id': self.benefit, 'content': self.supplier_codes[0],
            'purchase_ref': 'ROTATED', 'valid_until': iso(time.time() + 86400)})
        self.assertEqual(CdkJob.query.order_by(CdkJob.created_at.desc()).first().result['valid_count'], 0)
        self.application.config['CDK_ENCRYPTION_KEYS'].pop('v2')
        with self.assertRaisesRegex(RuntimeError, 'CDK 业务密文'):
            validate_cdk_ciphertexts(db.engine)

    def test_json_with_line_breaks_is_a_valid_credential(self):
        self.create_catalog()
        payload = self.intent()
        payload['token_input'] = json.dumps(json.loads(payload['token_input']), indent=2)
        result, _ = self.submit(payload)
        self.assertEqual(result['state'], 'processing')

    def test_same_role_outside_channel_cannot_read_or_replay_resources(self):
        self.create_catalog()
        other = self.post('/admin/channels', {'code': 'outside', 'name': '其他渠道'})['id']
        create_staff('scoped', 'scoped-test-password', 'operator', [other])
        db.session.commit()
        client = self.staff_client('scoped')
        result = client.get('/api/cdk/admin/cards').get_json()['data']
        self.assertEqual(result['items'], [])
        self.assertEqual(client.get('/api/cdk/admin/cards/' + self.card_id).status_code, 404)
        self.post('/admin/cards/search', {'code': self.code}, client, status=404)

    def test_rate_limit_is_stable_for_normalized_code_variants(self):
        self.create_catalog()
        for _ in range(20):
            self.post('/validate', {'code': self.code}, self.customer)
        self.post('/validate', {'code': self.code[:3] + '-' + self.code[3:].lower()}, self.customer, status=429)

    def test_legacy_persistence_rechecks_stock_ownership_and_rolls_back(self):
        from app.services.recharge_service import RechargeContractError
        self.create_catalog()
        task = RechargeTask(task_no='TK-LATE-LEGACY', redeem_code=self.supplier_codes[0], plan_type='PLUS',
                            account_email='customer@example.test', status='processing', is_mock=True)
        with self.assertRaises(RechargeContractError):
            RechargeService._persist_new_task(task, 'mock')
        db.session.commit()
        self.assertEqual(RechargeTask.query.count(), 0)

    def test_unknown_mutation_can_only_be_unlocked_with_two_person_evidence(self):
        self.create_catalog()
        result, _ = self.submit()
        task = RechargeTask.query.filter_by(task_no=result['task_no']).one()
        task.is_mock = False
        db.session.commit()
        self.application.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('timeout')):
            result = self.post('/redemptions/' + result['id'] + '/recall', {'version': result['version'], 'confirmed': True}, self.customer)
        self.assertEqual(result['mutation_state'], 'unknown')
        approval = self.post('/admin/approvals', {'action': 'resolve_mutation', 'target_id': result['id'], 'version': result['version'],
            'reason': '供应商确认撤回操作未执行', 'reference': 'MUTATION-PROVIDER-TICKET', 'evidence_sha256': 'b' * 64,
            'observed_at': iso(time.time()), 'decision': 'not_applied', 'mutation_id': result['mutation_id']})
        self.post('/admin/approvals/' + approval['id'] + '/decide', {'version': 1, 'approved': True}, self.checker)
        record = db.session.get(CdkRedemption, result['id'])
        self.assertEqual(record.mutation_state, 'not_applied')
        self.assertEqual(db.session.get(CdkCard, record.card_id).usage, 'reserved')

    def test_worker_reconciles_unknown_close_on_completed_managed_task(self):
        from app.models.recharge_operation import RechargeOperation
        from app.worker import maintenance
        self.create_catalog()
        result, _ = self.submit()
        result = self.post('/redemptions/' + result['id'] + '/refresh', {}, self.customer)
        task = RechargeTask.query.filter_by(task_no=result['task_no']).one()
        task.is_mock = False
        db.session.commit()
        self.application.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError('timeout')):
            result = self.post('/redemptions/' + result['id'] + '/close', {'version': result['version'], 'confirmed': True}, self.customer)
        self.assertEqual(result['mutation_state'], 'unknown')
        self.assertEqual(result['task']['status'], 'completed')
        upstream_no = db.session.get(RechargeOperation, result['task_no']).upstream_task_no
        with patch.object(RechargeService, '_upstream_get', return_value={'ok': True, 'task': {
            'task_no': upstream_no, 'client_task_no': result['task_no'], 'plan_type': 'PLUS', 'status': 'closed',
        }}) as lookup, patch.object(RechargeService, '_upstream_post', side_effect=AssertionError('Must not resend')):
            maintenance(self.application)
        lookup.assert_called_once()
        db.session.expire_all()
        record = db.session.get(CdkRedemption, result['id'])
        self.assertEqual(record.mutation_state, 'done')
        self.assertEqual(record.state, 'succeeded')
        self.assertEqual(RechargeTask.query.filter_by(task_no=result['task_no']).one().status, 'closed')
        self.assertEqual(db.session.get(CdkCard, record.card_id).usage, 'redeemed')
        self.assertEqual(db.session.get(CdkStock, record.stock_id).state, 'consumed')

    def test_worker_keeps_polling_unresolved_consumption_after_failed_task(self):
        from app.models.recharge_operation import RechargeOperation
        from app.worker import maintenance
        self.create_catalog()
        result, _ = self.submit()
        task = RechargeTask.query.filter_by(task_no=result['task_no']).one()
        task.is_mock = False
        db.session.commit()
        self.application.config['RECHARGE_MODE'] = 'live'
        upstream_no = db.session.get(RechargeOperation, result['task_no']).upstream_task_no
        remote = {'task_no': upstream_no, 'client_task_no': result['task_no'], 'plan_type': 'PLUS', 'status': 'failed'}
        apply_observation(result['id'], remote, result['version'])
        self.assertEqual(db.session.get(CdkRedemption, result['id']).state, 'unknown')
        with patch.object(RechargeService, '_upstream_get', return_value={'ok': True, 'task': {**remote, 'status': 'completed'}}) as lookup:
            maintenance(self.application)
        lookup.assert_called_once()
        db.session.expire_all()
        self.assertEqual(db.session.get(CdkRedemption, result['id']).state, 'succeeded')
        self.assertEqual(db.session.get(CdkCard, self.card_id).usage, 'redeemed')


if __name__ == '__main__':
    unittest.main()
