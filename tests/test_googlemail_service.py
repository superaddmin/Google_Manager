import io
import json
import subprocess
import tempfile
import threading
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


class ControlledProcess:
    def __init__(self):
        self.stdout = io.StringIO("")
        self.return_code = None
        self.finished = threading.Event()
        self.terminated = threading.Event()

    def wait(self, timeout=None):
        if not self.finished.wait(timeout):
            raise subprocess.TimeoutExpired("node-fixture", timeout)
        return self.return_code

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated.set()
        self.return_code = 143
        self.finished.set()

    def kill(self):
        self.return_code = 137
        self.finished.set()


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

    def create_account(self, email="fixture@example.test"):
        account = Account(
            email=email,
            password="Password-1",
            recovery="old-recovery@example.test",
            secret="JBSWY3DPEHPK3PXP",
        )
        db.session.add(account)
        db.session.commit()
        return account

    def wait_for_terminal_task(self, manager, task_id):
        deadline = time.time() + 2
        task = manager.get_task(task_id)
        while time.time() < deadline and task["status"] not in {
            "completed",
            "failed",
            "cancelled",
        }:
            time.sleep(0.01)
            task = manager.get_task(task_id)
        return task

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
        account = self.create_account()
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

            task = self.wait_for_terminal_task(manager, task["taskId"])

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

    def test_cancel_before_process_registration_terminates_process(self):
        account = self.create_account()
        process = ControlledProcess()
        factory_entered = threading.Event()
        release_factory = threading.Event()

        def delayed_popen(_command, **_kwargs):
            factory_entered.set()
            release_factory.wait(1)
            return process

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=delayed_popen,
        )
        task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
        self.assertTrue(factory_entered.wait(1))

        manager.cancel_task(task["taskId"])
        release_factory.set()
        task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "cancelled", task)
        self.assertEqual(task["errorCode"], "TASK_CANCELLED")
        self.assertTrue(process.terminated.is_set())
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "accounts.txt").exists())

    def test_timeout_terminates_process_and_sets_error_code(self):
        account = self.create_account()
        process = ControlledProcess()
        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=lambda _command, **_kwargs: process,
        )
        task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})

        deadline = time.time() + 1
        record = manager._tasks[task["taskId"]]
        while time.time() < deadline and record.process is None:
            time.sleep(0.01)
        self.assertIs(record.process, process)

        manager._timeout_task(task["taskId"])
        task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "failed", task)
        self.assertEqual(task["errorCode"], "TASK_TIMEOUT")
        self.assertTrue(process.terminated.is_set())

    def test_nonzero_exit_removes_sensitive_result_file(self):
        account = self.create_account()

        def failing_popen(_command, **kwargs):
            input_line = Path(kwargs["env"]["ACCOUNTS_FILE"]).read_text(
                encoding="utf-8"
            ).strip()
            output_dir = Path(kwargs["env"]["OUTPUT_DIR"])
            output_dir.mkdir(parents=True)
            (output_dir / "result.txt").write_text(
                input_line + "\n",
                encoding="utf-8",
            )
            return FakeProcess(return_code=1)

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=failing_popen,
        )
        task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
        task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "failed", task)
        self.assertEqual(task["errorCode"], "PROCESS_EXIT_NONZERO")
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "output" / "result.txt").exists())

    def test_windows_termination_kills_the_process_tree(self):
        process = ControlledProcess()
        process.pid = 4321

        def fake_taskkill(command, **_kwargs):
            process.terminate()
            return subprocess.CompletedProcess(command, 0)

        with (
            patch("app.services.googlemail_service.os.name", "nt"),
            patch(
                "app.services.googlemail_service.subprocess.run",
                side_effect=fake_taskkill,
            ) as taskkill,
        ):
            GooglemailTaskManager._terminate_process(process)

        self.assertEqual(
            taskkill.call_args.args[0],
            ["taskkill", "/PID", "4321", "/T", "/F"],
        )
        self.assertTrue(process.terminated.is_set())


if __name__ == "__main__":
    unittest.main()
