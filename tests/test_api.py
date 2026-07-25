import re
import time
import unittest
from unittest.mock import patch

import pyotp

from app import create_app, db
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
        self.assertEqual(login_attempts["192.0.2.10"]["attempts"], 1)

        success = self.client.post(
            "/api/auth/login",
            json={"password": "admin123", "salt": self.current_salt()},
            headers=headers,
        )
        self.assertEqual(success.status_code, 200)
        self.assertTrue(success.get_json()["success"])
        self.assertEqual(login_attempts["192.0.2.10"]["attempts"], 0)

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


if __name__ == "__main__":
    unittest.main()
