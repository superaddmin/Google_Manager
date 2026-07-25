import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app, db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.services.googlemail_service import (
    GooglemailTaskManager,
    GooglemailValidationError,
    normalize_googlemail_options,
)


NEW_SECRET = "KRUGS4ZANFZSAYJAON2HE2LOM4"


class FakeProcess:
    def __init__(self, return_code=0):
        self.stdout = io.StringIO("")
        self.return_code = return_code

    def wait(self, timeout=None):
        return self.return_code

    def poll(self):
        return self.return_code

    def terminate(self):
        self.return_code = 143

    def kill(self):
        self.return_code = 137


class GooglemailTaskManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        (self.project_root / "src").mkdir()
        (self.project_root / "src" / "main.mjs").write_text("", encoding="utf-8")
        (self.project_root / "node_modules" / "playwright").mkdir(parents=True)

    def tearDown(self):
        db.drop_all()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp_dir.cleanup()

    def test_options_are_normalized_and_validated(self):
        options = normalize_googlemail_options({
            "headless": False,
            "slowMo": "300",
            "recoveryEmails": "one@example.test\ntwo@example.test",
        })
        self.assertFalse(options["headless"])
        self.assertEqual(options["slowMo"], 300)
        self.assertEqual(
            options["recoveryEmails"],
            ["one@example.test", "two@example.test"],
        )
        with self.assertRaises(GooglemailValidationError):
            normalize_googlemail_options({"maxRuntimeMinutes": 0})

    def test_completed_task_syncs_secret_and_recovery_without_exposing_them(self):
        account = Account(
            email="fixture@example.test",
            password="Password-1",
            recovery="old-recovery@example.test",
            secret="JBSWY3DPEHPK3PXP",
        )
        db.session.add(account)
        db.session.commit()
        account_id = account.id

        captured_environment = {}

        def fake_popen(_command, **kwargs):
            env = kwargs["env"]
            captured_environment.update(env)
            input_line = Path(env["ACCOUNTS_FILE"]).read_text(encoding="utf-8").strip()
            email, password, _recovery, _secret = input_line.split("----")
            output_dir = Path(env["OUTPUT_DIR"])
            output_dir.mkdir(parents=True)
            (output_dir / "result.txt").write_text(
                "----".join([
                    email,
                    password,
                    "new-recovery@example.test",
                    NEW_SECRET,
                ]) + "\n",
                encoding="utf-8",
            )
            (output_dir / "progress.json").write_text(
                json.dumps({"completed": [email], "failed": [], "lastIndex": 0}),
                encoding="utf-8",
            )
            return FakeProcess()

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=fake_popen,
        )
        with patch.dict("os.environ", {"SECRET_KEY": "must-not-pass"}):
            task = manager.start_task(
                self.app,
                [account],
                {"headless": True, "maxRuntimeMinutes": 1},
            )

            deadline = time.time() + 2
            while time.time() < deadline:
                task = manager.get_task(task["taskId"])
                if task["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)

        self.assertEqual(task["status"], "completed", task)
        self.assertEqual(task["completedCount"], 1)
        self.assertEqual(task["syncedCount"], 1)
        self.assertNotIn("email", json.dumps(task).lower())
        self.assertNotIn(NEW_SECRET, json.dumps(task))
        self.assertNotIn("SECRET_KEY", captured_environment)
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "accounts.txt").exists())
        self.assertFalse((task_dir / "output" / "result.txt").exists())

        db.session.expire_all()
        updated = db.session.get(Account, account_id)
        self.assertEqual(updated.secret, NEW_SECRET)
        self.assertEqual(updated.recovery, "new-recovery@example.test")
        history_fields = {
            item.field_name
            for item in AccountHistory.query.filter_by(account_id=account_id).all()
        }
        self.assertEqual(history_fields, {"secret", "recovery"})


if __name__ == "__main__":
    unittest.main()
