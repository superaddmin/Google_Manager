"""
充值交付功能单元测试
覆盖蓝图鉴权、契约核验、任务创建与流转、批量查询、账单操作、
防死锁回归、挑战令牌生命周期、模式隔离 (503/502)、状态机防回滚与跨域 Origin 防护
"""
import time
import unittest
from cryptography.fernet import Fernet
from flask import url_for
from unittest.mock import patch
from app import create_app, db
from tests.auth_helpers import login_admin
from app.services.recharge_service import RechargeService, RechargeUpstreamError


class RechargeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        RechargeService.clear_local_data()
        self.client = self.app.test_client()
        self.client.environ_base['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'

    def tearDown(self):
        db.drop_all()
        db.session.remove()
        db.engine.dispose()
        RechargeService.clear_local_data()
        self.context.pop()

    def login(self):
        login_admin(self.client)

    def issue_challenge(self, code, token_input='dummy', plan_type='PLUS'):
        response = self.client.post('/api/recharge/submission-challenges', json={
            'redeem_code': code, 'token_input': token_input, 'plan_type': plan_type,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['data']

    def test_public_recharge_endpoints_do_not_require_admin_login(self):
        """C 端充值接口不应依赖管理员登录会话。"""
        endpoints = [
            ("/api/recharge/config", "GET", None),
            ("/api/recharge/agreement", "GET", None),
            ("/api/recharge/features", "GET", None),
            ("/api/recharge/stats/avg-processing-time", "GET", None),
            ("/api/recharge/redeem-codes/validate", "POST", {"redeem_code": "TEST1234"}),
            ("/api/recharge/tasks", "POST", {}),
            ("/api/recharge/tasks/lookup", "POST", {"redeem_code": "TEST1234"}),
            ("/api/recharge/billing/query", "POST", {"token_input": "sample_token"}),
        ]
        for url, method, payload in endpoints:
            with self.subTest(url=url):
                if method == "GET":
                    resp = self.client.get(url)
                else:
                    resp = self.client.post(url, json=payload or {})
                expected_status = 400 if url == '/api/recharge/tasks' else 200
                self.assertEqual(resp.status_code, expected_status, resp.get_json())
                self.assertEqual(resp.get_json()['success'], expected_status == 200)

    def test_public_and_admin_pages_are_separate_spa_entries(self):
        with self.app.test_request_context():
            self.assertEqual(url_for('main.index'), '/')
        for url in (
            '/', '/recharge', '/recharge/', '/admin', '/admin/',
            '/admin/accounts', '/admin/accounts/',
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'<div id="root"></div>', response.data)
                finally:
                    response.close()

    def test_authenticated_metadata_and_cdk_validation(self):
        """验证登录后获取配置、协议、耗时以及 CDK 校验"""
        self.login()

        # 1. Config
        resp = self.client.get("/api/recharge/config")
        self.assertEqual(resp.status_code, 200)
        json_data = resp.get_json()["data"]
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(json_data["mode"], "mock")

        # 2. Agreement
        resp = self.client.get("/api/recharge/agreement")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("content", resp.get_json()["data"])

        # 3. Features
        resp = self.client.get("/api/recharge/features")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["data"]["renewal_enabled"])

        # 4. Avg processing time
        resp = self.client.get("/api/recharge/stats/avg-processing-time")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json()["data"], list)

        # 5. CDK Validate (Plus)
        resp = self.client.post("/api/recharge/redeem-codes/validate", json={"redeem_code": "PLUS-TEST-8888"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["data"]
        self.assertEqual(data["plan_type"], "PLUS")

        # 6. CDK Validate (Pro 5x)
        resp = self.client.post("/api/recharge/redeem-codes/validate", json={"redeem_code": "PRO5X-TEST-9999"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["data"]
        self.assertEqual(data["product"], "claude_code")

        # 7. CDK Validate (Too short)
        resp = self.client.post("/api/recharge/redeem-codes/validate", json={"redeem_code": "abc"})
        self.assertEqual(resp.status_code, 400)

    def test_task_creation_contract_validation(self):
        """验证创建任务的严格契约核验"""
        self.login()

        # 缺少 challenge token
        payload = {
            "redeem_code": "PLUS-TEST-1234",
            "token_input": "{\"accessToken\":\"test-token\"}",
            "plan_type": "PLUS",
            "account_email": "test@gmail.com",
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
        }
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("challenge_token", resp.get_json()["message"])

        # 未勾选协议
        ch = self.issue_challenge("PLUS-TEST-1234", payload['token_input'])
        payload["challenge_token"] = ch["challenge_token"]
        payload["agreement_accepted"] = False
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("协议", resp.get_json()["message"])

        # 重新生成令牌测试邮箱格式非法
        ch = self.issue_challenge("PLUS-TEST-1234", payload['token_input'])
        payload["challenge_token"] = ch["challenge_token"]
        payload["agreement_accepted"] = True
        payload["account_email"] = "not-an-email"
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("邮箱", resp.get_json()["message"])

        # 合法提交
        ch = self.issue_challenge("PLUS-TEST-1234", payload['token_input'])
        payload["challenge_token"] = ch["challenge_token"]
        payload["account_email"] = "valid_user@example.com"
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 201)
        task = resp.get_json()["data"]
        self.assertIn("TK-", task["task_no"])
        self.assertEqual(task["status"], "processing")
        self.assertNotIn("redeem_code", task)

    def test_task_lookup_and_batch_lookup(self):
        """验证单任务查询与批量查询"""
        self.login()

        # 生成 challenge 并创建任务
        code = "BATCH-TEST-001"
        ch = self.issue_challenge(code, 'token_content')
        payload = {
            "redeem_code": code,
            "token_input": "token_content",
            "plan_type": "PLUS",
            "account_email": "batch1@gmail.com",
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch["challenge_token"]
        }
        create_res = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(create_res.status_code, 201)

        # 单个查询
        resp = self.client.post("/api/recharge/tasks/lookup", json={"redeem_code": "BATCH-TEST-001"})
        self.assertEqual(resp.status_code, 200)
        task = resp.get_json()["data"]
        self.assertNotIn("redeem_code", task)
        self.assertNotIn("account_email", task)

        # 批量查询
        resp = self.client.post("/api/recharge/tasks/lookup-batch", json={
            "redeem_codes": ["BATCH-TEST-001", "BATCH-TEST-999"]
        })
        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()["data"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["redeem_code"], "BATCH-TEST-001")
        self.assertFalse(results[1]["ok"])

    def test_task_recall_and_close_with_contract_confirmation(self):
        """验证撤回与关闭任务时须显式授权确认"""
        self.login()

        # 创建任务
        code = "ACTION-TEST-001"
        email = "action@gmail.com"
        ch = self.issue_challenge(code)
        create_res = self.client.post("/api/recharge/tasks", json={
            "redeem_code": code,
            "token_input": "dummy",
            "plan_type": "PLUS",
            "account_email": email,
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch["challenge_token"]
        })
        self.assertEqual(create_res.status_code, 201)

        # 撤回任务
        resp = self.client.post("/api/recharge/tasks/recall", json={
            "task_no": create_res.get_json()['data']['task_no'],
            "redeem_code": code,
            "email": email,
            "confirmed": True
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"]["status"], "recalled")

        # 关闭任务（破坏性写操作，未确认被拒绝）
        resp = self.client.post("/api/recharge/tasks/close", json={
            "task_no": create_res.get_json()['data']['task_no'],
            "redeem_code": code,
            "email": email,
            "confirmed": False
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("确认", resp.get_json()["message"])

        # 关闭任务（确认后执行成功）
        resp = self.client.post("/api/recharge/tasks/close", json={
            "task_no": create_res.get_json()['data']['task_no'],
            "redeem_code": code,
            "email": email,
            "confirmed": True
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"]["status"], "closed")

    def test_billing_operations_and_contract_confirmation(self):
        """验证账单查询及取消/恢复自动续费写操作的授权确认"""
        self.login()

        token = "test_session_token_xyz"

        # 1. 账单查询
        resp = self.client.post("/api/recharge/billing/query", json={"token_input": token})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["data"]
        self.assertTrue(data["has_active_subscription"])
        self.assertEqual(data["card_last4"], "8866")

        # 2. 取消自动续费（未确认）
        resp = self.client.post("/api/recharge/billing/cancel-subscription", json={
            "token_input": token,
            "confirmed": False
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("确认", resp.get_json()["message"])

        # 3. 取消自动续费（确认后成功）
        resp = self.client.post("/api/recharge/billing/cancel-subscription", json={
            "token_input": token,
            "confirmed": True
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["data"]["auto_renew"])

        # SUP-12: 取消后紧接查询账单，状态必须保持一致 (auto_renew=False, status=canceled_at_period_end)
        query_after_cancel = self.client.post("/api/recharge/billing/query", json={"token_input": token})
        self.assertEqual(query_after_cancel.status_code, 200)
        self.assertFalse(query_after_cancel.get_json()["data"]["auto_renew"])
        self.assertEqual(query_after_cancel.get_json()["data"]["status"], "canceled_at_period_end")

        # 4. 恢复自动续费（确认后成功）
        resp = self.client.post("/api/recharge/billing/resume-subscription", json={
            "token_input": token,
            "confirmed": True
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["data"]["auto_renew"])

        # SUP-12: 恢复后紧接查询账单，状态必须保持一致 (auto_renew=True, status=active)
        query_after_resume = self.client.post("/api/recharge/billing/query", json={"token_input": token})
        self.assertEqual(query_after_resume.status_code, 200)
        self.assertTrue(query_after_resume.get_json()["data"]["auto_renew"])
        self.assertEqual(query_after_resume.get_json()["data"]["status"], "active")

        # SUP-07 / SUP-11: 账单收据下载凭据事实核验
        invoice = query_after_resume.get_json()['data']['invoices'][0]
        resp_inv = self.client.post(
            '/api/recharge/tasks/invoice/download', json={'slug': invoice['slug']}
        )
        self.assertEqual(resp_inv.status_code, 200)
        self.assertIn("账单收据凭据", resp_inv.data.decode("utf-8"))
        self.assertIn(invoice['id'], resp_inv.data.decode("utf-8"))

    def test_deadlock_regression_lookup_unsubmitted_cdk(self):
        """R01 防死锁回归：查询未提交的卡密不得发生锁自锁，须在 1 秒内正常返回"""
        self.login()
        start = time.time()
        resp = self.client.post("/api/recharge/tasks/lookup", json={"redeem_code": "UNSUBMITTED-PLUS-CDK"})
        duration = time.time() - start
        self.assertLess(duration, 1.0, "lookup_task for unsubmitted CDK took too long, likely deadlocked")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()["data"]
        self.assertEqual(data["status"], "idle")

    def test_challenge_token_lifecycle(self):
        """R03 防刷令牌：单次消费、套餐绑定与过期阻断"""
        self.login()
        code = "CH-LIFECYCLE-1234"
        ch = self.issue_challenge(code, 'valid-token')
        token = ch["challenge_token"]

        # 1. 提交卡密不匹配
        payload = {
            "redeem_code": "DIFFERENT-CODE-9999",
            "token_input": "valid-token",
            "plan_type": "PLUS",
            "account_email": "user@example.com",
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": token
        }
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不匹配", resp.get_json()["message"])

        # 2. 正常消费令牌
        payload["redeem_code"] = code
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 201)

        # 3. 二次使用同一令牌被阻断
        ch2 = self.issue_challenge("SECOND-CODE-0001", 'valid-token')
        payload["redeem_code"] = "SECOND-CODE-0001"
        payload["challenge_token"] = token  # 已使用的令牌
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("已被使用", resp.get_json()["message"])

    def test_mode_isolation_disabled_returns_503(self):
        """P0-01 模式隔离：disabled 模式直接阻断返回 503"""
        self.login()
        with patch.object(RechargeService, 'get_mode', return_value='disabled'):
            resp = self.client.post("/api/recharge/redeem-codes/validate", json={"redeem_code": "PLUS-TEST-8888"})
            self.assertEqual(resp.status_code, 503)
            self.assertIn("未启用", resp.get_json()["message"])

            resp = self.client.post("/api/recharge/tasks", json={"redeem_code": "PLUS-TEST-8888"})
            self.assertEqual(resp.status_code, 503)

    def test_mode_isolation_live_upstream_failure_returns_502_not_mock(self):
        """P0-01 模式隔离：live 模式上游失败返回 502，绝不虚假降级返回 mock 成功"""
        self.login()
        with patch.object(RechargeService, 'get_mode', return_value='live'):
            # live challenge 会先调用卡密校验上游，任务创建阶段才注入故障。
            with patch.object(RechargeService, '_upstream_post', return_value={
                'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
            }):
                ch = self.issue_challenge("LIVE-FAIL-001", 'valid-token')
            payload = {
                "redeem_code": "LIVE-FAIL-001",
                "token_input": "valid-token",
                "plan_type": "PLUS",
                "account_email": "live@example.com",
                "agreement_accepted": True,
                "email_verified": True,
                "acknowledge_non_free": True,
                "challenge_token": ch["challenge_token"]
            }
            with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError("上游接口超时")):
                resp = self.client.post("/api/recharge/tasks", json=payload)
                self.assertEqual(resp.status_code, 502)
                self.assertIn("上游接口超时", resp.get_json()["message"])

    def test_state_machine_constraints(self):
        """R04 状态机约束：已关闭 CDK 严禁再次提交，查询返回最新记录"""
        self.login()
        code = "SM-TEST-CODE-001"
        email = "sm@example.com"
        ch = self.issue_challenge(code)
        create_res = self.client.post("/api/recharge/tasks", json={
            "redeem_code": code,
            "token_input": "dummy",
            "plan_type": "PLUS",
            "account_email": email,
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch["challenge_token"]
        })
        self.assertEqual(create_res.status_code, 201)

        # 关闭前先获取重提交所需的 challenge；关闭后生成 challenge 会被卡密状态校验提前拒绝。
        ch2 = self.issue_challenge(code)

        # 关闭任务
        close_res = self.client.post("/api/recharge/tasks/close", json={
            "task_no": create_res.get_json()['data']['task_no'],
            "redeem_code": code,
            "email": email,
            "confirmed": True
        })
        self.assertEqual(close_res.status_code, 200)

        # 尝试再次使用已关闭 CDK 提交创建新任务：必须被拒绝
        retry_res = self.client.post("/api/recharge/tasks", json={
            "redeem_code": code,
            "token_input": "dummy",
            "plan_type": "PLUS",
            "account_email": email,
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch2["challenge_token"]
        })
        self.assertEqual(retry_res.status_code, 400)
        self.assertIn("已关闭销毁", retry_res.get_json()["message"])

    def test_invoice_download_requires_json_post(self):
        """SUP-11 发票/凭据下载：必须验证任务真实存在；disabled 503；不存在 404；存在返回事实对账凭据"""
        from app.models.recharge_task import RechargeTask
        self.login()

        # 1. 任务不存在时下载返回 404；GET/query 不再接收敏感标识
        resp_get = self.client.get("/api/recharge/tasks/invoice/download?redeem_code=NON-EXISTENT-CDK")
        self.assertEqual(resp_get.status_code, 405)
        resp_404 = self.client.post(
            "/api/recharge/tasks/invoice/download", json={"redeem_code": "NON-EXISTENT-CDK"}
        )
        self.assertEqual(resp_404.status_code, 404)
        self.assertIn("未找到关联任务", resp_404.get_json()["message"])

        # 2. 缺少参数报错 400
        resp_err = self.client.post("/api/recharge/tasks/invoice/download", json={})
        self.assertEqual(resp_err.status_code, 400)

        # 3. 创建持久化真实任务后仅通过 JSON POST 下载
        task = RechargeTask(
            task_no="TK-DOWNLOAD-001",
            redeem_code="INV-GET-123456",
            plan_type="PLUS",
            account_email="download@example.com",
            status="processing",
            status_text="任务处理中",
            card_last4="8866",
            is_mock=True
        )
        db.session.add(task)
        db.session.commit()

        # 未完成任务不能生成看似已付款的收据
        resp_pending = self.client.post(
            "/api/recharge/tasks/invoice/download", json={"redeem_code": "INV-GET-123456"}
        )
        self.assertEqual(resp_pending.status_code, 409)

        # 任务完成后，POST 方式按卡密下载
        task.status = "completed"
        task.status_text = "已完成"
        db.session.commit()
        resp_post = self.client.post("/api/recharge/tasks/invoice/download", json={"redeem_code": "INV-GET-123456"})
        self.assertEqual(resp_post.status_code, 200)
        self.assertIn("TK-DOWNLOAD-001", resp_post.data.decode("utf-8"))
        self.assertNotIn("download@example.com", resp_post.data.decode("utf-8"))

        # 4. disabled 模式下请求下载阻断返回 503
        with patch.object(RechargeService, 'get_mode', return_value='disabled'):
            resp_disabled = self.client.post(
                "/api/recharge/tasks/invoice/download", json={"redeem_code": "INV-GET-123456"}
            )
            self.assertEqual(resp_disabled.status_code, 503)

    def test_invoice_file_contract_is_mode_gated_and_query_free(self):
        """账单文件请求不能在 disabled/live 模式伪造，也不能把标识放入 query。"""
        self.login()
        encoded = self.client.post('/api/recharge/billing/invoice-file', json={
            'slug': 'inv&redirect=https://evil.example.test',
            'file_type': 'txt',
        })
        self.assertEqual(encoded.status_code, 200)
        data = encoded.get_json()['data']
        self.assertEqual(data['url'], '/api/recharge/tasks/invoice/download')
        self.assertEqual(data['method'], 'POST')
        self.assertEqual(data['payload']['slug'], 'inv&redirect=https://evil.example.test')

        invalid_type = self.client.post('/api/recharge/billing/invoice-file', json={
            'slug': 'invoice-1', 'file_type': 'pdf',
        })
        self.assertEqual(invalid_type.status_code, 400)

        with patch.object(RechargeService, 'get_mode', return_value='disabled'):
            self.assertEqual(
                self.client.post('/api/recharge/billing/invoice-file', json={}).status_code,
                503,
            )
        with patch.object(RechargeService, 'get_mode', return_value='live'):
            self.assertEqual(
                self.client.post('/api/recharge/billing/invoice-file', json={}).status_code,
                503,
            )

    def test_recharge_input_types_are_not_coerced_to_credentials(self):
        """卡密、凭证和批量列表中的数字等非文本输入必须被拒绝。"""
        self.login()
        challenge = self.client.post('/api/recharge/submission-challenges', json={
            'redeem_code': 1234, 'token_input': 'credential', 'plan_type': 'PLUS',
        })
        self.assertEqual(challenge.status_code, 400)
        challenge = self.client.post('/api/recharge/submission-challenges', json={
            'redeem_code': 'PLUS-TYPE-001', 'token_input': 1234, 'plan_type': 'PLUS',
        })
        self.assertEqual(challenge.status_code, 400)
        batch = self.client.post('/api/recharge/tasks/lookup-batch', json={
            'redeem_codes': ['PLUS-TYPE-001', 1234],
        })
        self.assertEqual(batch.status_code, 400)

    def test_redeem_validation_ignores_query_credentials(self):
        """卡密校验只接受 JSON，避免卡密进入浏览器历史和代理日志。"""
        response = self.client.post(
            '/api/recharge/redeem-codes/validate?redeem_code=PLUS-QUERY-SECRET',
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('卡密', response.get_json()['message'])

    def test_recharge_mode_invalid_fallback_disabled(self):
        """SUP-04 严格枚举：非法模式配置必须回退至 disabled 并在请求时返回 503"""
        self.login()
        with patch.dict(self.app.config, {"RECHARGE_MODE": "invalid_unknown_mode"}):
            self.assertEqual(RechargeService.get_mode(), "disabled")
            resp = self.client.post("/api/recharge/redeem-codes/validate", json={"redeem_code": "PLUS-TEST-8888"})
            self.assertEqual(resp.status_code, 503)
            self.assertIn("未启用", resp.get_json()["message"])

    def test_production_rejects_mock_and_invalid_mode(self):
        """SUP-04 生产环境门禁：拒绝启用 mock 或非法 RECHARGE_MODE"""
        import os
        valid_env = {
            "FLASK_ENV": "production",
            "SECRET_KEY": "a-very-secure-secret-key-that-is-at-least-32-chars-long!",
            "ADMIN_PASSWORD": "ProductionCustomSecretPassword2026!",
            "GMAIL_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "RECHARGE_MODE": "mock"
        }
        with patch.dict(os.environ, valid_env, clear=False):
            with self.assertRaises(RuntimeError) as cm:
                create_app("production")
            self.assertIn("生产环境禁止启用 mock 充值模式", str(cm.exception))

        with patch.dict(os.environ, {**valid_env, "RECHARGE_MODE": "invalid_mode"}, clear=False):
            with self.assertRaises(RuntimeError) as cm:
                create_app("production")
            self.assertIn("不支持未知的 RECHARGE_MODE", str(cm.exception))

    def test_challenge_plan_type_mismatch_rejected(self):
        """SUP-06 校验令牌强化：PLUS challenge 提交 PRO 请求必须被 400 阻断"""
        self.login()
        code = "MISMATCH-PLAN-001"
        ch = self.issue_challenge(code, '{"accessToken":"test-token"}')

        payload = {
            "redeem_code": code,
            "token_input": "{\"accessToken\":\"test-token\"}",
            "plan_type": "PRO",  # 失配的套餐类型
            "account_email": "mismatch@gmail.com",
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch["challenge_token"]
        }
        resp = self.client.post("/api/recharge/tasks", json=payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不匹配", resp.get_json()["message"])

    def test_cross_origin_mutating_requests_rejected(self):
        """R03 Origin 防护：跨域非法 Origin 写操作被 403 阻断"""
        self.login()
        resp = self.client.post(
            "/api/recharge/redeem-codes/validate",
            json={"redeem_code": "PLUS-TEST-8888"},
            headers={"Origin": "https://malicious-attacker-site.com"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Origin", resp.get_json()["message"])
        self.assertEqual(resp.get_json()['error_code'], 'forbidden')

    def test_task_creation_upstream_failure_persists_unknown_and_prevents_duplicate(self):
        """SUP-05: live 模式上游异常前已落库 pending，超时后转为 unknown 并阻断重复提交"""
        from app.models.recharge_task import RechargeTask
        self.login()
        code = "UPSTREAM-TIMEOUT-CDK-001"
        self.app.config['RECHARGE_MODE'] = 'live'
        with patch.object(RechargeService, '_upstream_post', return_value={
            'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
        }):
            ch = self.issue_challenge(code, '{"accessToken":"test-token"}')

        payload = {
            "redeem_code": code,
            "token_input": "{\"accessToken\":\"test-token\"}",
            "plan_type": "PLUS",
            "account_email": "timeout@gmail.com",
            "agreement_accepted": True,
            "email_verified": True,
            "acknowledge_non_free": True,
            "challenge_token": ch["challenge_token"]
        }

        with patch.object(RechargeService, 'get_mode', return_value='live'):
            with patch.object(RechargeService, '_upstream_post', side_effect=RechargeUpstreamError("上游接口网络超时")):
                resp = self.client.post("/api/recharge/tasks", json=payload)
                self.assertEqual(resp.status_code, 502)

            # 验证数据库中已经有持久化记录且状态为 unknown
            persisted = RechargeTask.query.filter_by(redeem_code=code).first()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.status, "unknown")
            self.assertIn("核对结果", persisted.notice)

            # 验证同一卡密已有任务，禁止重入与重复提交
            with patch.object(RechargeService, '_upstream_post', return_value={
                'ok': True, 'result': {'plan_type': 'PLUS', 'status': 'unused'},
            }):
                ch2 = self.issue_challenge(code, payload['token_input'])
            payload["challenge_token"] = ch2["challenge_token"]
            with patch.object(RechargeService, '_upstream_post', return_value={"ok": True, "task": {"status": "processing"}}):
                retry_res = self.client.post("/api/recharge/tasks", json=payload)
                self.assertEqual(retry_res.status_code, 400)
                self.assertIn("已有正在执行或待核对中的充值任务", retry_res.get_json()["message"])


if __name__ == '__main__':
    unittest.main()
