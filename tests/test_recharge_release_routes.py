import unittest
from unittest.mock import patch

from app import create_app, db
from app.models.recharge_task import RechargeTask
from app.services.recharge_service import RechargeService


class RechargeReleaseRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.context = self.app.app_context()
        self.context.push()
        RechargeService.clear_local_data()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def client(self):
        client = self.app.test_client()
        client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        return client

    @staticmethod
    def task_payload():
        return {
            'redeem_code': 'PLUS-RELEASE-ROUTES',
            'token_input': 'synthetic-release-credential',
            'plan_type': 'PLUS',
            'account_email': 'release@example.test',
            'agreement_accepted': True,
            'email_verified': True,
            'acknowledge_non_free': True,
        }

    def create_completed_task(self, owner):
        payload = self.task_payload()
        challenge = owner.post('/api/recharge/submission-challenges', json=payload)
        self.assertEqual(challenge.status_code, 200, challenge.get_json())
        payload['challenge_token'] = challenge.get_json()['data']['challenge_token']
        created = owner.post('/api/recharge/tasks', json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        task = RechargeTask.query.filter_by(
            task_no=created.get_json()['data']['task_no']
        ).one()
        task.status = 'completed'
        task.status_text = 'completed'
        db.session.commit()
        return task

    def test_write_endpoint_rejects_non_json_media_type_with_415(self):
        response = self.client().post(
            '/api/recharge/redeem-codes/validate',
            data='{"redeem_code":"PLUS-RELEASE-ROUTES"}',
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 415, response.get_json())
        self.assertEqual(response.get_json()['error_code'], 'unsupported_media_type')

    def test_task_receipt_is_restricted_to_creator_session(self):
        owner = self.client()
        stranger = self.client()
        task = self.create_completed_task(owner)

        rejected = stranger.post('/api/recharge/tasks/invoice/download', json={
            'redeem_code': task.redeem_code,
        })
        accepted = owner.post('/api/recharge/tasks/invoice/download', json={
            'redeem_code': task.redeem_code,
        })

        self.assertEqual(rejected.status_code, 403, rejected.get_json())
        self.assertEqual(rejected.get_json()['error_code'], 'forbidden')
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.content_type, 'text/plain; charset=utf-8')

    def test_invoice_download_rejects_ambiguous_identifier_and_file_type(self):
        client = self.client()
        billing = client.post('/api/recharge/billing/query', json={
            'token_input': 'synthetic-release-billing-token',
        })
        self.assertEqual(billing.status_code, 200, billing.get_json())
        slug = billing.get_json()['data']['invoices'][0]['slug']

        ambiguous = client.post('/api/recharge/tasks/invoice/download', json={
            'redeem_code': 'PLUS-RELEASE-ROUTES',
            'slug': slug,
        })
        unsupported = client.post('/api/recharge/tasks/invoice/download', json={
            'slug': slug,
            'file_type': 'pdf',
        })

        self.assertEqual(ambiguous.status_code, 400, ambiguous.get_json())
        self.assertEqual(unsupported.status_code, 400, unsupported.get_json())

    def test_invoice_file_descriptor_requires_safe_explicit_slug(self):
        client = self.client()

        missing = client.post('/api/recharge/billing/invoice-file', json={})
        control_character = client.post('/api/recharge/billing/invoice-file', json={
            'slug': 'invoice\nheader',
            'file_type': 'txt',
        })

        self.assertEqual(missing.status_code, 400, missing.get_json())
        self.assertEqual(control_character.status_code, 400, control_character.get_json())

    def test_duplicate_padded_batch_still_consumes_per_card_rate_limit(self):
        def consume(key, limit, window):
            return not key.startswith('card:')

        with patch(
            'app.services.request_security.RequestLimit.consume', side_effect=consume
        ), patch.object(RechargeService, 'lookup_batch_tasks', return_value=[]) as lookup:
            response = self.client().post('/api/recharge/tasks/lookup-batch', json={
                'redeem_codes': ['PLUS-RATE-LIMIT-PADDING'] * 51,
            })

        self.assertEqual(response.status_code, 429, response.get_json())
        self.assertEqual(response.get_json()['error_code'], 'rate_limited')
        lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
