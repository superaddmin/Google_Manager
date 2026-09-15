import os
import re
import runpy
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pyotp

from app import create_app, db
from app.config import ProductionConfig
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.services.auth_service import AuthService, login_attempts
from app.services.googlemail_service import googlemail_tasks


TEST_SECRET = "JBSWY3DPEHPK3PXP"


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        login_attempts.clear()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def tearDown(self):
        db.drop_all()
        db.session.remove()
        db.engine.dispose()
        login_attempts.clear()
        self.context.pop()

    @staticmethod
    def current_salt():
        return AuthService.generate_salt(int(time.time()))

    def test_production_requires_strong_secret_and_secure_cookie_settings(self):
        for secret in ("", "   ", "x" * 31):
            with self.subTest(secret_length=len(secret)):
                with patch.dict(os.environ, {"SECRET_KEY": secret}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "32"):
                        create_app("production")

        with (
            patch.dict(
                os.environ,
                {"SECRET_KEY": "x" * 32, "ADMIN_PASSWORD": "test-admin-password", "GMAIL_TOKEN_ENCRYPTION_KEY": "x" * 44},
                clear=True,
            ),
            patch.object(
                ProductionConfig,
                "SQLALCHEMY_DATABASE_URI",
                "sqlite:///:memory:",
            ),
        ):
            production_app = create_app("production")

        self.assertEqual(production_app.config["SECRET_KEY"], "x" * 32)
        self.assertTrue(production_app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(production_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(production_app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(
            production_app.config["PERMANENT_SESSION_LIFETIME"].days,
            7,
        )

    def test_non_testing_app_requires_admin_password_from_environment(self):
        with patch.dict(os.environ, {"SECRET_KEY": "x" * 32}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ADMIN_PASSWORD"):
                create_app("production")

    def create_account(self, email="user@example.test", **overrides):
        payload = {
            "email": email,
            "password": "Password-1",
            "recovery": "recovery@example.test",
            "secret": TEST_SECRET,
            "remark": "primary",
        }
        payload.update(overrides)
        response = self.client.post("/api/accounts", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]

    def test_index_and_auth_check_are_available(self):
        index_response = self.client.get("/")
        self.assertEqual(index_response.status_code, 200)
        index_response.close()
        response = self.client.get("/api/auth/check")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"success": True, "banned": False, "authenticated": True},
        )

    def test_startup_keeps_configured_debug_mode_and_binds_loopback(self):
        with patch("app.create_app", return_value=self.app), patch.object(self.app, "run") as run:
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "run.py"), run_name="__main__")
        run.assert_called_once_with(host="127.0.0.1", port=8002)

    def test_account_api_requires_login_and_logout_clears_session(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/accounts").status_code, 401)

        login = anonymous.post(
            "/api/auth/login",
            json={"password": "admin123", "salt": self.current_salt()},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(anonymous.get("/api/accounts").status_code, 200)
        self.assertTrue(anonymous.get("/api/auth/check").get_json()["authenticated"])

        logout = anonymous.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(anonymous.get("/api/accounts").status_code, 401)

    def test_login_requires_valid_salt_and_clears_failures(self):
        missing = self.client.post("/api/auth/login", json={"password": "admin123"})
        invalid = self.client.post(
            "/api/auth/login", json={"password": "admin123", "salt": "invalid"}
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)

        headers = {"X-Forwarded-For": "192.0.2.10, 127.0.0.1"}
        failed = self.client.post(
            "/api/auth/login",
            json={"password": "wrong", "salt": self.current_salt()},
            headers=headers,
        )
        self.assertEqual(failed.status_code, 401)
        self.assertEqual(login_attempts["127.0.0.1"]["attempts"], 1)

        success = self.client.post(
            "/api/auth/login",
            json={"password": "admin123", "salt": self.current_salt()},
            headers=headers,
        )
        self.assertEqual(success.status_code, 200)
        self.assertTrue(success.get_json()["success"])
        self.assertEqual(login_attempts["127.0.0.1"]["attempts"], 0)

    def test_third_failed_login_bans_ip(self):
        headers = {"X-Forwarded-For": "192.0.2.20"}
        for expected_status in (401, 401, 403):
            response = self.client.post(
                "/api/auth/login",
                json={"password": "wrong", "salt": self.current_salt()},
                headers=headers,
            )
            self.assertEqual(response.status_code, expected_status)

        blocked = self.client.get("/api/auth/check", headers=headers)
        self.assertEqual(blocked.status_code, 200)
        self.assertTrue(blocked.get_json()["banned"])

    def test_create_requires_nonblank_fields_and_rejects_duplicate(self):
        for payload in (
            {},
            {"email": "", "password": "Password-1"},
            {"email": "   ", "password": "Password-1"},
            {"email": "user@example.test", "password": ""},
        ):
            response = self.client.post("/api/accounts", json=payload)
            self.assertEqual(response.status_code, 400, payload)

        account = self.create_account(email="  user@example.test  ")
        self.assertEqual(account["email"], "user@example.test")

        duplicate = self.client.post(
            "/api/accounts",
            json={"email": "user@example.test", "password": "Password-2"},
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_email_format_validation_applies_to_all_import_paths(self):
        for email in ("not-an-email", "user@", "@example.test", "user @example.test"):
            response = self.client.post(
                "/api/accounts",
                json={"email": email, "password": "Password-1"},
            )
            self.assertEqual(response.status_code, 400, email)

        account = self.create_account("valid-email@example.test")
        invalid_update = self.client.put(
            f"/api/accounts/{account['id']}",
            json={"recovery": "not-a-recovery-email"},
        )
        self.assertEqual(invalid_update.status_code, 400)

        batch = self.client.post(
            "/api/accounts/batch",
            json={
                "accounts": [
                    {"email": "valid-batch@example.test", "password": "Password-1"},
                    {"email": "invalid-batch", "password": "Password-1"},
                    {
                        "email": "invalid-recovery@example.test",
                        "password": "Password-1",
                        "recovery": "not-a-recovery-email",
                    },
                ]
            },
        )
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.get_json()["data"]["success_count"], 1)
        self.assertEqual(batch.get_json()["data"]["failed_count"], 2)

    def test_batch_import_counts_duplicates_and_invalid_rows(self):
        self.create_account("existing@example.test")
        response = self.client.post(
            "/api/accounts/batch",
            json={
                "accounts": [
                    {"email": "new@example.test", "password": "Password-1"},
                    {"email": "existing@example.test", "password": "Password-1"},
                    {"email": "", "password": "Password-1"},
                    {"email": "missing-password@example.test", "password": ""},
                    "not-an-object",
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()["data"]
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 4)
        self.assertEqual(Account.query.count(), 2)

        invalid_container = self.client.post(
            "/api/accounts/batch", json={"accounts": "not-a-list"}
        )
        self.assertEqual(invalid_container.status_code, 400)

    def test_forwarded_headers_cannot_bypass_login_ban(self):
        anonymous = self.app.test_client()
        for index, expected_status in enumerate((401, 401, 403, 403)):
            response = anonymous.post(
                "/api/auth/login",
                json={"password": "wrong", "salt": self.current_salt()},
                headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
            )
            self.assertEqual(response.status_code, expected_status)
        self.assertEqual(set(login_attempts), {"127.0.0.1"})

    def test_optional_account_fields_reject_non_string_values(self):
        account = self.create_account()
        for field in ("recovery", "secret", "remark", "status"):
            for value in ([], {}, 123, True):
                with self.subTest(field=field, value=value):
                    response = self.client.put(
                        f"/api/accounts/{account['id']}", json={field: value}
                    )
                    db.session.rollback()
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn("Password-1", response.get_data(as_text=True))
        invalid_status = self.client.put(
            f"/api/accounts/{account['id']}", json={"status": "unknown"}
        )
        self.assertEqual(invalid_status.status_code, 400)
        self.assertEqual(AccountHistory.query.count(), 0)

    def test_invalid_batch_row_does_not_discard_valid_rows(self):
        response = self.client.post(
            "/api/accounts/batch",
            json={"accounts": [
                {"email": "valid@example.test", "password": "Password-1"},
                {"email": "invalid@example.test", "password": "Password-2", "secret": {}},
                {"email": "another@example.test", "password": "Password-3", "remark": None},
            ]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["success_count"], 2)
        self.assertEqual(response.get_json()["data"]["failed_count"], 1)
        self.assertEqual(Account.query.count(), 2)

    def test_database_failure_is_redacted_and_session_is_rolled_back(self):
        with (
            patch("app.services.account_service.db.session.commit", side_effect=RuntimeError("PRIVATE_PASSWORD")),
            patch("app.db.session.rollback", wraps=db.session.rollback) as rollback,
            self.assertLogs(self.app.logger, level="ERROR") as logs,
        ):
            response = self.client.post(
                "/api/accounts", json={"email": "fixture@example.test", "password": "Password-1"}
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_PASSWORD", response.get_data(as_text=True))
        self.assertNotIn("PRIVATE_PASSWORD", " ".join(logs.output))
        rollback.assert_called_once()
        self.assertEqual(Account.query.count(), 0)

    def test_invalid_json_returns_the_api_error_contract(self):
        for path in ("/api/accounts", "/api/accounts/batch", "/api/auth/login"):
            with self.subTest(path=path):
                response = self.client.post(path, data="{", content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)
                self.assertFalse(response.get_json()["success"])

    def test_list_search_update_and_history(self):
        first = self.create_account("alpha@example.test", remark="first group")
        self.create_account("beta@example.test", remark="second group")

        listed = self.client.get("/api/accounts").get_json()["data"]
        self.assertEqual([item["email"] for item in listed], [
            "alpha@example.test",
            "beta@example.test",
        ])
        searched = self.client.get("/api/accounts?search=SECOND").get_json()["data"]
        self.assertEqual([item["email"] for item in searched], ["beta@example.test"])

        updated = self.client.put(
            f"/api/accounts/{first['id']}",
            json={
                "email": "alpha-updated@example.test",
                "password": "Password-2",
                "recovery": "new-recovery@example.test",
                "secret": "KRUGS4ZANFZSAYJA",
                "remark": "updated",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()["data"]["email"], "alpha-updated@example.test")

        history = self.client.get(f"/api/accounts/{first['id']}/history")
        fields = {item["fieldName"] for item in history.get_json()["data"]}
        self.assertEqual(fields, {"password", "recovery", "secret"})

        blank_update = self.client.put(
            f"/api/accounts/{first['id']}", json={"email": "   "}
        )
        self.assertEqual(blank_update.status_code, 400)

    def test_status_sold_status_and_history(self):
        account = self.create_account()
        account_id = account["id"]

        active = self.client.patch(f"/api/accounts/{account_id}/status")
        self.assertEqual(active.get_json()["data"]["status"], "pro")
        inactive = self.client.patch(f"/api/accounts/{account_id}/status")
        self.assertEqual(inactive.get_json()["data"]["status"], "inactive")

        sold = self.client.patch(f"/api/accounts/{account_id}/sold")
        self.assertEqual(sold.get_json()["data"]["soldStatus"], "sold")
        unsold = self.client.patch(f"/api/accounts/{account_id}/sold")
        self.assertEqual(unsold.get_json()["data"]["soldStatus"], "unsold")

        history = self.client.get(f"/api/accounts/{account_id}/history").get_json()["data"]
        sold_history = [item for item in history if item["fieldName"] == "sold_status"]
        self.assertEqual(len(sold_history), 2)

    def test_totp_valid_missing_and_invalid_secret(self):
        valid = self.create_account()
        response = self.client.get(f"/api/accounts/{valid['id']}/2fa")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertRegex(data["code"], re.compile(r"^\d{6}$"))
        self.assertTrue(pyotp.TOTP(TEST_SECRET).verify(data["code"], valid_window=1))
        self.assertGreaterEqual(data["expiry"], 1)
        self.assertLessEqual(data["expiry"], 30)

    def test_batch_import_rejects_oversized_payload(self):
        accounts = [
            {"email": f"bulk-{index}@example.test", "password": "Password-1"}
            for index in range(501)
        ]
        response = self.client.post("/api/accounts/batch", json={"accounts": accounts})
        self.assertEqual(response.status_code, 400)
        self.assertIn("500", response.get_json()["message"])
        self.assertEqual(Account.query.count(), 0)

        missing = self.create_account("missing@example.test", secret="")
        invalid = self.create_account("invalid@example.test", secret="not-base32!")
        self.assertEqual(
            self.client.get(f"/api/accounts/{missing['id']}/2fa").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/api/accounts/{invalid['id']}/2fa").status_code, 404
        )

    def test_delete_removes_history_and_missing_resources_return_404(self):
        account = self.create_account()
        account_id = account["id"]
        self.client.patch(f"/api/accounts/{account_id}/sold")
        self.assertEqual(AccountHistory.query.filter_by(account_id=account_id).count(), 1)

        deleted = self.client.delete(f"/api/accounts/{account_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(AccountHistory.query.filter_by(account_id=account_id).count(), 0)

        for method, path, payload in (
            (self.client.delete, "/api/accounts/9999", None),
            (self.client.put, "/api/accounts/9999", {"remark": "missing"}),
            (self.client.patch, "/api/accounts/9999/status", None),
            (self.client.patch, "/api/accounts/9999/sold", None),
            (self.client.get, "/api/accounts/9999/2fa", None),
            (self.client.get, "/api/accounts/9999/history", None),
        ):
            response = method(path, json=payload) if payload is not None else method(path)
            self.assertEqual(response.status_code, 404, path)

    def test_googlemail_status_reports_capability_and_testing_guard(self):
        with (
            patch.object(
                googlemail_tasks,
                "availability",
                return_value={"available": True, "reasons": []},
            ),
            patch.object(googlemail_tasks, "active_task", return_value=None),
            patch.object(googlemail_tasks, "latest_task", return_value=None),
        ):
            response = self.client.get("/api/googlemail/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertTrue(data["available"])
        self.assertFalse(data["executionEnabled"])
        self.assertIsNone(data["activeTask"])
        self.assertIsNone(data["latestTask"])

        account = self.create_account()
        blocked = self.client.post(
            "/api/googlemail/tasks", json={"accountIds": [account["id"]]}
        )
        self.assertEqual(blocked.status_code, 503)

    def test_googlemail_task_start_query_cancel_contract(self):
        account = self.create_account()
        task = {
            "taskId": "fixture-task",
            "status": "running",
            "totalCount": 1,
            "completedCount": 0,
            "failedCount": 0,
            "pendingCount": 1,
            "syncedCount": 0,
            "manualReviewCount": 0,
            "exitCode": None,
            "errorCode": None,
            "headless": True,
            "createdAt": "2026-07-25T00:00:00+00:00",
            "startedAt": "2026-07-25T00:00:00+00:00",
            "finishedAt": None,
        }
        self.app.config["GOOGLEMAIL_EXECUTION_ENABLED"] = True

        with patch.object(googlemail_tasks, "start_task", return_value=task) as start:
            response = self.client.post(
                "/api/googlemail/tasks",
                json={
                    "accountIds": [account["id"]],
                    "options": {"headless": True, "slowMo": 100},
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["data"]["taskId"], "fixture-task")
        self.assertEqual(start.call_args.args[1][0].id, account["id"])

        with patch.object(googlemail_tasks, "get_task", return_value=task):
            queried = self.client.get("/api/googlemail/tasks/fixture-task")
        self.assertEqual(queried.status_code, 200)

        cancelled_task = {**task, "status": "cancelled"}
        with patch.object(googlemail_tasks, "cancel_task", return_value=cancelled_task):
            cancelled = self.client.post("/api/googlemail/tasks/fixture-task/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["data"]["status"], "cancelled")

        missing = self.client.post(
            "/api/googlemail/tasks", json={"accountIds": [9999]}
        )
        self.assertEqual(missing.status_code, 404)


    def test_export_accounts_in_multiple_formats(self):
        alpha = self.create_account("alpha@example.test")
        self.create_account("beta@example.test", recovery="", secret="")
        self.client.patch(f"/api/accounts/{alpha['id']}/sold")

        csv_response = self.client.get("/api/accounts/export?format=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response.headers["Content-Type"])
        self.assertIn("attachment", csv_response.headers["Content-Disposition"])
        self.assertIn(".csv", csv_response.headers["Content-Disposition"])
        csv_text = csv_response.get_data(as_text=True)
        self.assertTrue(csv_text.startswith(chr(0xFEFF)))
        self.assertIn("邮箱,密码,恢复邮箱,2FA密钥,备注,状态,出售状态,导入时间", csv_text)
        self.assertIn("alpha@example.test", csv_text)
        self.assertIn("beta@example.test", csv_text)

        txt_response = self.client.get("/api/accounts/export?format=txt")
        self.assertEqual(txt_response.status_code, 200)
        txt_lines = txt_response.get_data(as_text=True).strip().splitlines()
        self.assertEqual(len(txt_lines), 2)
        for line in txt_lines:
            self.assertEqual(len(line.split("----")), 4)
        self.assertIn("alpha@example.test----Password-1----recovery@example.test----", txt_lines[0])
        self.assertIn("beta@example.test----Password-1--------", txt_lines[1])

        json_response = self.client.get("/api/accounts/export?format=json")
        self.assertEqual(json_response.status_code, 200)
        json_accounts = json_response.get_json()
        self.assertEqual(len(json_accounts), 2)
        self.assertEqual({item["email"] for item in json_accounts}, {
            "alpha@example.test", "beta@example.test",
        })

        filtered = self.client.get("/api/accounts/export?format=json&sold=sold").get_json()
        self.assertEqual([item["email"] for item in filtered], ["alpha@example.test"])
        searched = self.client.get("/api/accounts/export?format=json&search=beta").get_json()
        self.assertEqual([item["email"] for item in searched], ["beta@example.test"])

        invalid = self.client.get("/api/accounts/export?format=xml")
        self.assertEqual(invalid.status_code, 400)

    def test_stats_endpoint_reports_counts_and_trends(self):
        alpha = self.create_account("alpha@example.test")
        self.create_account("beta@example.test", recovery="", secret="")
        self.client.patch(f"/api/accounts/{alpha['id']}/sold")

        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        stats = response.get_json()["data"]
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["sold"], 1)
        self.assertEqual(stats["unsold"], 1)
        self.assertEqual(stats["with2fa"], 1)
        self.assertEqual(stats["without2fa"], 1)
        self.assertEqual(stats["withRecovery"], 1)
        self.assertEqual(stats["withoutRecovery"], 1)
        self.assertEqual(len(stats["recentImports"]), 14)
        self.assertEqual(sum(item["count"] for item in stats["recentImports"]), 2)
        self.assertEqual(len(stats["recentSales"]), 14)
        self.assertEqual(sum(item["count"] for item in stats["recentSales"]), 1)

    def test_batch_delete_sold_and_remark_operations(self):
        first = self.create_account("first@example.test")
        second = self.create_account("second@example.test")
        third = self.create_account("third@example.test")

        sold = self.client.patch("/api/accounts/batch-sold", json={
            "accountIds": [first["id"], second["id"], 9999],
            "status": "sold",
        })
        self.assertEqual(sold.status_code, 200, sold.get_json())
        result = sold.get_json()["data"]
        self.assertEqual(result["updated_count"], 2)
        self.assertEqual(result["missing_ids"], [9999])
        self.assertEqual(
            AccountHistory.query.filter_by(
                account_id=first["id"], field_name="sold_status"
            ).count(),
            1,
        )

        remark = self.client.patch("/api/accounts/batch-remark", json={
            "accountIds": [first["id"]],
            "remark": "  vip  ",
        })
        self.assertEqual(remark.status_code, 200)
        self.assertEqual(remark.get_json()["data"]["updated_count"], 1)
        self.assertEqual(
            db.session.get(Account, first["id"]).remark, "vip"
        )

        deleted = self.client.post("/api/accounts/batch-delete", json={
            "accountIds": [first["id"], second["id"]],
        })
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["data"]["deleted_count"], 2)
        self.assertEqual(Account.query.count(), 1)
        self.assertEqual(
            AccountHistory.query.filter_by(account_id=first["id"]).count(), 0
        )

        for payload in (
            {},
            {"accountIds": []},
            {"accountIds": ["x"]},
            {"accountIds": [True]},
            {"accountIds": list(range(501))},
        ):
            response = self.client.post("/api/accounts/batch-delete", json=payload)
            self.assertEqual(response.status_code, 400, payload)

        self.assertEqual(
            self.client.patch(
                "/api/accounts/batch-sold",
                json={"accountIds": [third["id"]], "status": "unknown"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.patch(
                "/api/accounts/batch-remark",
                json={"accountIds": [third["id"]], "remark": 123},
            ).status_code,
            400,
        )

    def test_googlemail_task_history_persisted_and_listed(self):
        from pathlib import Path

        from app.models.googlemail_task import GooglemailTask
        from app.services.googlemail_service import GooglemailTaskRecord

        task_id = "a" * 32
        record = GooglemailTaskRecord(
            task_id=task_id,
            task_dir=Path("unused") / "task",
            input_file=Path("unused") / "accounts.txt",
            output_dir=Path("unused") / "output",
            account_ids_by_email={"user@example.test": 1},
            total_count=2,
            options={
                "headless": True,
                "slowMo": 200,
                "accountDelay": 5000,
                "accountsPerRecovery": 5,
                "maxRuntimeMinutes": 120,
                "recoveryEmails": [],
            },
            status="completed",
            completed_count=1,
            failed_count=1,
            synced_count=1,
            exit_code=0,
        )

        googlemail_tasks._persist_task(self.app, record)
        self.assertEqual(GooglemailTask.query.count(), 1)

        response = self.client.get("/api/googlemail/tasks")
        self.assertEqual(response.status_code, 200)
        tasks = response.get_json()["data"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["taskId"], task_id)
        self.assertEqual(tasks[0]["status"], "completed")
        self.assertEqual(tasks[0]["totalCount"], 2)
        self.assertEqual(tasks[0]["completedCount"], 1)
        self.assertEqual(tasks[0]["failedCount"], 1)
        self.assertEqual(tasks[0]["pendingCount"], 0)
        self.assertTrue(tasks[0]["headless"])

        # 重复落库应更新而非新增
        record.status = "failed"
        record.error_code = "TASK_TIMEOUT"
        googlemail_tasks._persist_task(self.app, record)
        self.assertEqual(GooglemailTask.query.count(), 1)
        updated = self.client.get("/api/googlemail/tasks").get_json()["data"]
        self.assertEqual(updated[0]["status"], "failed")
        self.assertEqual(updated[0]["errorCode"], "TASK_TIMEOUT")

        self.assertEqual(
            self.client.get("/api/googlemail/tasks?limit=0").status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
