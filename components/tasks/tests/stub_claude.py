#!/usr/bin/env python3
"""A stand-in for `claude -p` in the tests: records how it was called, then acts out $STUB_PLAN (JSON):
log (lines for tasks_log), touch (files to create), sleep (seconds), finish, blocked (reason), subtype, turns."""
import json
import os
import subprocess
import sys
import time

plan = json.loads(os.environ.get("STUB_PLAN", "{}"))
prompt = sys.stdin.read()
with open(os.environ["STUB_RECORD"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(), "pid": os.getpid(),
                        "agent": os.environ.get("ODD1_TASKS_AGENT"), "task": os.environ.get("ODD1_TASKS_TASK")}) + "\n")
task, cli = os.environ["ODD1_TASKS_TASK"], [sys.executable, "-m", "odd1_tasks.cli"]
for line in plan.get("log", []):
    subprocess.run(cli + ["log", task, line], check=True)
for path in plan.get("touch", []):
    open(path, "w").write("x")
time.sleep(plan.get("sleep", 0))
if plan.get("blocked"):
    subprocess.run(cli + ["release", task, "--blocked", plan["blocked"]], check=True)
elif plan.get("finish"):
    subprocess.run(cli + ["finish", task, "stub finished"], check=True)
print(json.dumps({"type": "result", "subtype": plan.get("subtype", "success"), "num_turns": plan.get("turns", 1),
                  "total_cost_usd": 0.0, "result": plan.get("result", "stub")}))
