"""Headless regression tests for GUI preview invalidation logic."""
import unittest
from odd1_intent.gui_logic import handle_parse_failure, refresh_approval_state, clear_completed_preview, clear_failed_preview, invalidate_after_input_change


class Widget:
    def __init__(self, parent=None):
        self.parent = parent
        self.children = []
        self.state = None
        if parent is not None:
            parent.children.append(self)

    def winfo_children(self):
        return list(self.children)

    def destroy(self):
        if self.parent and self in self.parent.children:
            self.parent.children.remove(self)

    def configure(self, **kwargs):
        self.state = kwargs.get("state", self.state)


class Value:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class App:
    def __init__(self):
        self.plan = object()
        self.vars = [object()]
        self.rows = Widget()
        Widget(self.rows)
        self.approve = Widget()
        self.approve.state = "normal"
        self.state = Value()


class ApprovalStateTests(unittest.TestCase):
    def test_new_unselected_addition_can_be_enabled_by_checking_it(self):
        app = App()
        app.plan = object()
        app.vars = [Value(False)]
        self.assertFalse(refresh_approval_state(app))
        self.assertEqual(app.approve.state, "disabled")
        app.vars[0].set(True)
        self.assertTrue(refresh_approval_state(app))
        self.assertEqual(app.approve.state, "normal")

    def test_deselecting_all_or_losing_plan_disables_approval(self):
        app = App()
        app.plan = object()
        app.vars = [Value(True)]
        self.assertTrue(refresh_approval_state(app))
        app.vars[0].set(False)
        self.assertFalse(refresh_approval_state(app))
        self.assertEqual(app.approve.state, "disabled")
        app.vars = [Value(True)]
        app.plan = None
        self.assertFalse(refresh_approval_state(app))
        self.assertEqual(app.approve.state, "disabled")

    def test_completed_plan_is_cleared_and_cannot_be_reenabled(self):
        app = App()
        stale_selection = Value(True)
        app.vars = [stale_selection]
        clear_completed_preview(app)
        self.assertIsNone(app.plan)
        self.assertEqual(app.vars, [])
        self.assertEqual(app.rows.winfo_children(), [])
        self.assertFalse(refresh_approval_state(app))
        stale_selection.set(True)
        self.assertFalse(refresh_approval_state(app))
        self.assertEqual(app.approve.state, "disabled")


class InvalidatePreviewTests(unittest.TestCase):
    def test_clears_old_plan_controls_and_rows(self):
        app = App()
        handle_parse_failure(app, ValueError("bad paths"), lambda *args: None)
        self.assertIsNone(app.plan)
        self.assertEqual(app.vars, [])
        self.assertEqual(app.rows.winfo_children(), [])
        self.assertEqual(app.approve.state, "disabled")

    def test_safe_when_already_empty(self):
        app = App()
        app.plan = None
        app.vars = []
        app.rows.children.clear()
        app.approve.state = "disabled"
        seen = []
        handle_parse_failure(app, ValueError("unsupported"), lambda *args: seen.append(args))
        self.assertIsNone(app.plan)
        self.assertEqual(app.vars, [])
        self.assertEqual(app.rows.children, [])
        self.assertEqual(app.approve.state, "disabled")
        self.assertEqual(seen, [("Cannot plan request", "unsupported")])
        self.assertIn("No action taken", app.state.get())

    def test_editing_inputs_invalidates_visible_approval_and_plan(self):
        app = App()
        self.assertTrue(invalidate_after_input_change(app))
        self.assertIsNone(app.plan)
        self.assertEqual(app.vars, [])
        self.assertEqual(app.rows.winfo_children(), [])
        self.assertEqual(app.approve.state, "disabled")
        self.assertIn("preview again", app.state.get())
        self.assertFalse(invalidate_after_input_change(app))

    def test_apply_failure_retires_preview_and_disables_reapproval(self):
        app = App()
        app.plan = object()
        app.vars = [Value(True)]
        self.assertTrue(refresh_approval_state(app))
        clear_failed_preview(app)
        self.assertIsNone(app.plan)
        self.assertEqual(app.vars, [])
        self.assertEqual(app.rows.winfo_children(), [])
        self.assertEqual(app.approve.state, "disabled")
        self.assertIn("inspect details", app.state.get())


if __name__ == "__main__":
    unittest.main()
