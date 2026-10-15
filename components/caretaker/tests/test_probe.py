"""The parsers and the whole look, on a fake machine (runs anywhere)."""
from fakes import PSI_CALM, PSI_HOT, Runner, desktop_runner, machine
from odd1_caretaker import probe


def test_parse_psi_and_levels():
    psi = probe.parse_psi(PSI_HOT)
    assert psi["some"]["avg10"] == 55.0 and psi["full"]["total"] == 5000
    assert probe.psi_level(psi) == "high"
    assert probe.psi_level(probe.parse_psi(PSI_CALM)) == "ok"
    assert probe.psi_level(probe.parse_psi("some avg10=15.00 avg60=1 avg300=1 total=1\n")) == "elevated"
    assert probe.psi_level(None) == "ok"
    assert probe.worst(["ok", "high", "elevated"]) == "high"


def test_parse_meminfo_cgroup_failed_units():
    assert probe.parse_meminfo("MemTotal:  10 kB\nHugePages_Total: 0\n") == {"MemTotal": 10240, "HugePages_Total": 0}
    assert probe.parse_cgroup("12:cpu:/x\n0::/system.slice/nginx.service\n") == "/system.slice/nginx.service"
    assert probe.parse_cgroup("12:cpu:/x\n") is None
    out = "● nginx.service loaded failed failed nginx\nfoo@1.service loaded failed failed Foo\n\n"
    assert probe.parse_failed_units(out) == ["nginx.service", "foo@1.service"]


def test_snapshot_finds_foreground_pressure_and_problems(tmp_path):
    proc, cg = machine(tmp_path)
    snap = probe.snapshot(proc, desktop_runner(), cg)
    fg = snap["foreground"]
    assert fg["pid"] == 4242 and fg["name"] == "soffice.bin" and fg["rss"] == 512000 * 1024
    assert fg["cgroup"] == "/user.slice/user-1000.slice/session-2.scope" and fg["memory_max"] is None
    assert fg["level"] == "high" and snap["level"] == "high"
    assert snap["failed_units"] == ["nginx.service"] and snap["load"] == [0.5, 0.4, 0.3]
    text = "\n".join(snap["problems"])
    assert "systemd unit failed: nginx.service" in text
    assert "memory low" in text and "foreground soffice.bin" in text


def test_missing_tools_are_stated_not_hidden(tmp_path):
    proc, cg = machine(tmp_path)
    snap = probe.snapshot(proc, Runner([]), cg)
    assert "unavailable" in snap["foreground"] and snap["failed_units"] is None
    assert snap["failed_units_unavailable"].startswith("systemctl exit")
    assert any(p.startswith("cannot see failed units") for p in snap["problems"])
    blind = probe.snapshot(tmp_path / "nothing", Runner([]), cg)
    assert any(p.startswith("cannot see pressure") for p in blind["problems"])
