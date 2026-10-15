"""End-to-end CLI approval/cancellation/undo tests in isolated temporary homes."""
import sys
import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Intent's engine is Linux-only (fcntl.flock, renameat2, /proc/self/fd); run on Odd1 or another Linux host",
                allow_module_level=True)

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

COMPONENT = Path(__file__).resolve().parent.parent


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="orbit-cli-test-")
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PYTHONPATH=str(COMPONENT))
        self.env.pop("ODD1_INTENT_STATE", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, answers=""):
        return subprocess.run(
            [sys.executable, "-m", "odd1_intent.cli", *map(str, args)],
            input=answers, text=True, capture_output=True, env=self.env, timeout=15,
        )

    def test_backup_cli_approval_and_undo_preserve_exact_bytes(self):
        source = self.base / "notes.bin"
        original = b"exact\x00bytes\xff\n"
        source.write_bytes(original)
        plan = self.run_cli(f'Make a backup of "{source}"', answers="\nYES\n")
        backup = self.base / "notes.bin.bak"
        self.assertEqual(plan.returncode, 0, plan.stderr + plan.stdout)
        self.assertIn("PLAN (nothing changed)", plan.stdout)
        self.assertIn("ABOUT TO APPLY", plan.stdout)
        self.assertIn("APPLIED 1 action", plan.stdout)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), original)

        undone = self.run_cli("--undo", answers="")
        self.assertEqual(undone.returncode, 0, undone.stderr + undone.stdout)
        self.assertIn("UNDONE", undone.stdout)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(backup.exists())

    def test_update_cli_select_all_apply_and_undo(self):
        source = self.base / "Downloads"
        target = self.base / "project"
        source.mkdir(); target.mkdir()
        (source / "new.txt").write_text("new bytes")
        (target / "new.txt").write_text("old bytes")
        os.utime(source / "new.txt", ns=(3_000_000_000, 3_000_000_000))
        os.utime(target / "new.txt", ns=(2_000_000_000, 2_000_000_000))
        (source / "add.txt").write_text("add bytes")
        (target / "keep.txt").write_text("unrelated project file")
        intent = f'Update files in "{target}" with the ones from "{source}"'

        applied = self.run_cli(intent, answers="all\nYES\n")
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertIn("REPLACE", applied.stdout)
        self.assertIn("ABOUT TO APPLY", applied.stdout)
        self.assertIn("APPLIED 2 action", applied.stdout)
        self.assertEqual((target / "new.txt").read_text(), "new bytes")
        self.assertEqual((target / "add.txt").read_text(), "add bytes")
        self.assertEqual((target / "keep.txt").read_text(), "unrelated project file")

        undone = self.run_cli("--undo")
        self.assertEqual(undone.returncode, 0, undone.stderr + undone.stdout)
        self.assertEqual((target / "new.txt").read_text(), "old bytes")
        self.assertFalse((target / "add.txt").exists())
        self.assertEqual((target / "keep.txt").read_text(), "unrelated project file")

    def test_cancelled_cli_request_does_not_mutate_or_create_history(self):
        source = self.base / "notes.txt"
        source.write_bytes(b"keep")
        result = self.run_cli(f'Make a backup of "{source}"', answers="none\n")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("Cancelled; nothing changed", result.stdout)
        self.assertEqual(source.read_bytes(), b"keep")
        self.assertFalse(source.with_name("notes.txt.bak").exists())
        self.assertFalse((self.home / ".local/share/odd1-intent/history.json").exists())

    def test_cli_refuses_selection_of_skip_row(self):
        source = self.base / "Downloads"; target = self.base / "project"
        source.mkdir(); target.mkdir()
        (source / "a-add.txt").write_text("new file")
        (source / "same.txt").write_text("source")
        (target / "same.txt").write_text("destination")
        os.utime(source / "same.txt", ns=(2_000_000_000, 2_000_000_000))
        os.utime(target / "same.txt", ns=(3_000_000_000, 3_000_000_000))
        intent = f'Update files in "{target}" with the ones from "{source}"'
        result = self.run_cli(intent, answers="2\n")
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("non-action or invalid number", result.stderr)
        self.assertEqual((target / "same.txt").read_text(), "destination")
        self.assertFalse((target / "a-add.txt").exists())
        self.assertFalse((self.home / ".local/share/odd1-intent/history.json").exists())

    def test_cli_eof_at_selection_fails_without_mutation(self):
        source = self.base / "notes.txt"
        source.write_bytes(b"keep")
        result = self.run_cli(f'Make a backup of "{source}"', answers="")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("input ended before approval", result.stdout)
        self.assertFalse(source.with_name("notes.txt.bak").exists())
        self.assertFalse((self.home / ".local/share/odd1-intent/history.json").exists())

    def test_cli_eof_at_final_approval_fails_without_mutation(self):
        source = self.base / "notes.txt"
        source.write_bytes(b"keep")
        result = self.run_cli(f'Make a backup of "{source}"', answers="\n")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("input ended before final approval", result.stdout)
        self.assertFalse(source.with_name("notes.txt.bak").exists())
        self.assertFalse((self.home / ".local/share/odd1-intent/history.json").exists())


if __name__ == "__main__":
    unittest.main()
