# Roadmap

Every component serves one of these. Its README says which, on the line after its purpose.

## M1 Caretaker (in progress)
Agents keep a live machine healthy: diagnose, repair and verify. Faults are injected; the machine's real state is
judged. Measured by: recovery, no data loss, no collateral damage.

## M2 Optimizer
Make things faster and leaner: build times, service latency, boot time, memory. Measured by: benchmark speedups the
kernel times itself.
Cheapest lever first: a program's own configuration (find what it reads by tracing its file opens; one change at a
time, original backed up, measured before and after), then flags and environment (e.g. JVM memory and GC
settings), then OS-level slack under pressure (cgroup priority, pausing background agents), and only then
profiling and patching code.

## M3 Kernel
eBPF for observability, then sched_ext CPU schedulers written as BPF programs (the kernel falls back to its default
if they misbehave), then real drivers: QEMU's edu device, virtio, rewriting existing drivers. Measured by: workload
benchmarks, stability under snapshot and restore.

## M4 Agent-native OS
Agents as first-class citizens: an agent process manager, model calls scheduled with token budgets as a resource,
shared tool and memory services, messaging between agents, a user-facing assistant layer. Measured by: user-facing
metrics and uptime.

## ★ Any app, through interfaces agents write themselves
The ultimate goal. Agents control any program as naturally as a person, after writing their own interface for it.
Because we own the OS, the screen never has to be guessed: the compositor knows every surface, the toolkit every
widget (browser layout snapshots, GTK and Qt trees), the app its object model (LibreOffice UNO, Excel COM), and the
font and drawing libraries every piece of text and shape drawn. A colour-coded ID render maps every pixel to its
owner. Vision fills the gaps, trained on labels this produces for free. A screen-model service merges it all into a
compact, queryable view with stable IDs and change events, and per-app adapters (agent-written drivers) turn
interfaces into APIs. Measured by: GUI tasks on a virtual desktop in the VM (success, steps and tokens per step) on
apps never seen before.
