"""Display-independent safety helpers for the Intent GUI."""


def invalidate_preview(app):
    """Clear stale planned actions after a new request cannot be planned.

    A failed request must never leave a prior plan approvable or visible as if
    it described the current intent. ``app`` is the minimal GUI state object.
    """
    app.plan = None
    app.vars = []
    for widget in app.rows.winfo_children():
        widget.destroy()
    app.approve.configure(state="disabled")


def handle_parse_failure(app, error, show_error):
    """Make an invalid request visibly inert and report why it was refused."""
    invalidate_preview(app)
    show_error("Cannot plan request", str(error))
    app.state.set("No action taken. Clarify folders or use the supported wording.")


def refresh_approval_state(app):
    """Enable approval iff a current plan has at least one selected action."""
    enabled = app.plan is not None and any(variable.get() for variable in app.vars)
    app.approve.configure(state="normal" if enabled else "disabled")
    return enabled


def clear_completed_preview(app):
    """Discard an approved plan after apply; it is no longer actionable/current."""
    invalidate_preview(app)


def clear_failed_preview(app):
    """Discard a plan after apply refuses/fails; filesystem state may have changed."""
    invalidate_preview(app)
    app.state.set("Operation failed or was refused. No plan remains approved; inspect details and preview again.")


def invalidate_after_input_change(app):
    """Discard a preview as soon as request or folder inputs are edited."""
    if app.plan is None:
        return False
    invalidate_preview(app)
    app.state.set("Request or folders changed. No action taken; preview again to review the new request.")
    return True
