import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import create_app, db
from app.models.account import Account
from app.models.account_history import AccountHistory
from app.services.googlemail_service import (
    GooglemailTaskError,
    GooglemailTaskManager,
    GooglemailTaskRecord,
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


class BlockingSyncTaskManager(GooglemailTaskManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sync_started = threading.Event()
        self.release_sync = threading.Event()

    def _sync_results(self, app, record):
        self.sync_started.set()
        if not self.release_sync.wait(1):
            raise TimeoutError("test sync barrier timed out")
        return super()._sync_results(app, record)


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

    @staticmethod
    def write_result(environment, secret=NEW_SECRET):
        fields = Path(environment["ACCOUNTS_FILE"]).read_text(
            encoding="utf-8"
        ).strip().split("----")
        fields[2] = "new-recovery@example.test"
        fields[3] = secret
        output_dir = Path(environment["OUTPUT_DIR"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.txt").write_text(
            "----".join(fields) + "\n",
            encoding="utf-8",
        )
        return output_dir

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

    def test_options_reject_fractional_nonfinite_and_invalid_containers(self):
        for options in ([], False, 0, "", {"slowMo": 1.5}, {"slowMo": float("inf")}):
            with self.subTest(options=options):
                with self.assertRaises(GooglemailValidationError):
                    normalize_googlemail_options(options)

    def test_invalid_progress_document_does_not_break_task_status(self):
        manager = GooglemailTaskManager(project_root=self.project_root)
        record = GooglemailTaskRecord(
            task_id="fixture-task",
            task_dir=self.project_root,
            input_file=self.project_root / "accounts.txt",
            output_dir=self.project_root / "output",
            account_ids_by_email={"fixture@example.test": 1},
            total_count=1,
            options={"headless": True},
        )
        record.output_dir.mkdir()
        for progress in ([], None, "invalid", {"completed": [{}], "failed": []}):
            with self.subTest(progress=progress):
                (record.output_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
                task = manager._public_task(record)
                self.assertEqual(task["pendingCount"], 1)

        record.account_ids_by_email["another@example.test"] = 2
        record.total_count = 2
        (record.output_dir / "progress.json").write_text(json.dumps({
            "completed": ["fixture@example.test", "unknown@example.test"],
            "failed": ["fixture@example.test", "another@example.test"],
        }), encoding="utf-8")
        task = manager._public_task(record)
        self.assertEqual((task["completedCount"], task["failedCount"], task["pendingCount"]), (1, 1, 0))

    def test_mixed_invalid_results_are_retained_instead_of_deleted(self):
        account = self.create_account()

        def fake_popen(_command, **kwargs):
            output_dir = self.write_result(kwargs["env"])
            with (output_dir / "result.txt").open("a", encoding="utf-8") as output:
                output.write("incomplete-result\n")
            return FakeProcess()

        manager = GooglemailTaskManager(
            project_root=self.project_root, node_path="node-fixture", popen_factory=fake_popen
        )
        task = manager.start_task(self.app, [account])
        task = self.wait_for_terminal_task(manager, task["taskId"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["errorCode"], "RESULT_SYNC_FAILED")
        result_file = self.project_root / "runtime" / "tasks" / task["taskId"] / "output" / "result.txt"
        self.assertTrue(result_file.exists())

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
            process = FakeProcess()
            process.stdout = io.StringIO("CHILD_SECRET_OUTPUT\n")
            return process

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=fake_popen,
        )
        chrome_executable = str(self.project_root / "chrome" / "chrome")
        with patch.dict("os.environ", {
            "SECRET_KEY": "must-not-pass",
            "FLASK_ENV": "production",
            "GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH": chrome_executable,
        }):
            task = manager.start_task(
                self.app,
                [account],
                {"headless": True, "maxRuntimeMinutes": 1},
            )

            task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "completed", task)
        self.assertEqual(task["completedCount"], 1)
        self.assertEqual(task["syncedCount"], 1)
        public_payload = json.dumps(task)
        for sensitive_value in (
            "fixture@example.test",
            "Password-1",
            "old-recovery@example.test",
            "JBSWY3DPEHPK3PXP",
            NEW_SECRET,
            "CHILD_SECRET_OUTPUT",
        ):
            self.assertNotIn(sensitive_value, public_payload)
        self.assertNotIn("SECRET_KEY", captured_environment)
        self.assertEqual(captured_environment["FLASK_ENV"], "production")
        self.assertEqual(
            captured_environment["GOOGLE_MANAGER_CHROME_EXECUTABLE_PATH"],
            chrome_executable,
        )
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertEqual(
            captured_environment["USER_DATA_DIR"],
            str(task_dir / "browser-data"),
        )
        self.assertFalse((task_dir / "accounts.txt").exists())
        self.assertFalse((task_dir / "output" / "result.txt").exists())
        self.assertFalse((task_dir / "browser-data").exists())

        db.session.expire_all()
        updated = db.session.get(Account, account_id)
        self.assertEqual(updated.secret, NEW_SECRET)
        self.assertEqual(updated.recovery, "new-recovery@example.test")
        history_fields = {
            item.field_name
            for item in AccountHistory.query.filter_by(account_id=account_id).all()
        }
        self.assertEqual(history_fields, {"secret", "recovery"})

    def test_result_sync_is_atomic_when_a_later_account_is_missing(self):
        first = self.create_account("first-sync@example.test")
        second = self.create_account("second-sync@example.test")
        output_dir = self.project_root / "output"
        output_dir.mkdir()
        (output_dir / "result.txt").write_text(
            "\n".join([
                "----".join([first.email, first.password, "", "KRUGS4ZANFZSAYJA"]),
                "----".join([second.email, second.password, "", "MFRGGZDFMZTWQ2LK"]),
            ]) + "\n",
            encoding="utf-8",
        )
        record = GooglemailTaskRecord(
            task_id="atomic-sync-task",
            task_dir=self.project_root,
            input_file=self.project_root / "accounts.txt",
            output_dir=output_dir,
            account_ids_by_email={first.email: first.id, second.email: second.id},
            total_count=2,
            options={},
        )
        db.session.delete(second)
        db.session.commit()

        manager = GooglemailTaskManager(project_root=self.project_root)
        self.assertEqual(manager._sync_results(self.app, record), 1)

        db.session.expire_all()
        self.assertEqual(db.session.get(Account, first.id).secret, "KRUGS4ZANFZSAYJA")

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

    def test_cancel_after_partial_result_syncs_before_terminal_status(self):
        account = self.create_account()
        account_id = account.id
        process = ControlledProcess()

        def partial_result_popen(_command, **kwargs):
            self.write_result(kwargs["env"])
            return process

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=partial_result_popen,
        )
        task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
        record = manager._tasks[task["taskId"]]
        deadline = time.time() + 1
        while time.time() < deadline and record.process is None:
            time.sleep(0.01)
        self.assertIs(record.process, process)

        manager.cancel_task(task["taskId"])
        task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "cancelled", task)
        self.assertEqual(task["syncedCount"], 1)
        db.session.expire_all()
        self.assertEqual(db.session.get(Account, account_id).secret, NEW_SECRET)
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "accounts.txt").exists())
        self.assertFalse((task_dir / "output" / "result.txt").exists())

    def test_cancel_during_finalizing_syncs_result_before_publishing_terminal(self):
        account = self.create_account()
        account_id = account.id

        def completed_popen(_command, **kwargs):
            self.write_result(kwargs["env"])
            return FakeProcess()

        manager = BlockingSyncTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=completed_popen,
        )
        task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
        self.assertTrue(manager.sync_started.wait(1))

        finalizing_task = manager.get_task(task["taskId"])
        self.assertEqual(finalizing_task["status"], "finalizing")
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertTrue((task_dir / "accounts.txt").exists())
        with self.assertRaises(GooglemailTaskError):
            manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})

        manager.cancel_task(task["taskId"])
        manager.release_sync.set()
        task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "cancelled", task)
        self.assertEqual(task["syncedCount"], 1)
        self.assertFalse((task_dir / "accounts.txt").exists())
        self.assertFalse((task_dir / "output" / "result.txt").exists())
        db.session.expire_all()
        self.assertEqual(db.session.get(Account, account_id).secret, NEW_SECRET)

    def test_timeout_terminates_process_and_sets_error_code(self):
        account = self.create_account()
        account_id = account.id
        process = ControlledProcess()

        def partial_result_popen(_command, **kwargs):
            self.write_result(kwargs["env"])
            return process

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=partial_result_popen,
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
        self.assertEqual(task["syncedCount"], 1)
        self.assertTrue(process.terminated.is_set())
        db.session.expire_all()
        self.assertEqual(db.session.get(Account, account_id).secret, NEW_SECRET)

    def test_nonzero_exit_syncs_and_removes_sensitive_result_file(self):
        account = self.create_account()
        account_id = account.id

        def failing_popen(_command, **kwargs):
            self.write_result(kwargs["env"])
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
        self.assertEqual(task["syncedCount"], 1)
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "output" / "result.txt").exists())
        db.session.expire_all()
        self.assertEqual(db.session.get(Account, account_id).secret, NEW_SECRET)

    def test_result_sync_failure_retains_recovery_file(self):
        account = self.create_account()

        def completed_popen(_command, **kwargs):
            self.write_result(kwargs["env"])
            return FakeProcess()

        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=completed_popen,
        )
        with patch.object(manager, "_sync_results", side_effect=RuntimeError):
            task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
            task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "failed", task)
        self.assertEqual(task["errorCode"], "RESULT_SYNC_FAILED")
        task_dir = self.project_root / "runtime" / "tasks" / task["taskId"]
        self.assertFalse((task_dir / "accounts.txt").exists())
        self.assertTrue((task_dir / "output" / "result.txt").exists())

    def test_sensitive_file_cleanup_retries_and_reports_failure(self):
        retry_path = Mock()
        retry_path.unlink.side_effect = [OSError, OSError, None]
        with patch("app.services.googlemail_service.time.sleep") as sleep:
            self.assertTrue(GooglemailTaskManager._remove_file(retry_path))
        self.assertEqual(retry_path.unlink.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        account = self.create_account()
        manager = GooglemailTaskManager(
            project_root=self.project_root,
            node_path="node-fixture",
            popen_factory=lambda _command, **_kwargs: FakeProcess(),
        )
        with patch.object(manager, "_remove_file", return_value=False):
            task = manager.start_task(self.app, [account], {"maxRuntimeMinutes": 1})
            task = self.wait_for_terminal_task(manager, task["taskId"])

        self.assertEqual(task["status"], "failed", task)
        self.assertEqual(task["errorCode"], "SENSITIVE_CLEANUP_FAILED")

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
