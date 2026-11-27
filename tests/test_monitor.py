# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import json
import os
import signal
import subprocess
import sys
import time

from app.monitor import (LogTail, ProcSampler, TranscriptTail, beat_summary, classify,
                         host_info, is_heartbeat_line)


def test_sampler_measures_real_cpu_and_memory():
    p = subprocess.Popen([sys.executable, "-c", "while True: pass"], start_new_session=True)
    try:
        s = ProcSampler(p.pid)
        s.sample()
        time.sleep(0.6)
        m = s.sample()
        assert m["procs"] == 1 and p.pid in m["pids"]
        assert m["cpu_pct"] > 40 and m["rss"] > 1_000_000
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_sampler_idle_process_uses_no_cpu():
    p = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        s = ProcSampler(p.pid)
        s.sample()
        time.sleep(0.4)
        assert s.sample()["cpu_pct"] < 5
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_sampler_empty_when_group_gone():
    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait()
    assert ProcSampler(p.pid).sample()["procs"] == 0


def test_classify_states():
    assert classify(age=1, agent_status="busy", tps=12)[0] == "generating"
    assert classify(age=5, agent_status=None, tps=0)[0] == "working"
    assert classify(age=60, agent_status="busy", tps=0)[0] == "waiting_model"
    assert classify(age=60, agent_status="idle", tps=0)[0] == "quiet"
    assert classify(age=200, agent_status="busy", tps=0)[0] == "stalled"
    assert classify(age=5, agent_status="retry", tps=0)[0] == "retrying"
    assert classify(age=200, agent_status=None, tps=0) == ("stalled", {"age": 200})
    assert classify(age=60, agent_status="busy", tps=0) == ("waiting_model", {"age": 60})
    assert classify(age=1, agent_status="busy", tps=12.4) == ("generating", {"rate": 12})


def _write(p, *events, raw=""):
    with open(p, "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.write(raw)


def test_transcript_only_adopts_files_of_our_pids(tmp_path):
    mine, other = tmp_path / "last-phase-1-x-pid111.jsonl", tmp_path / "last-phase-1-x-pid222.jsonl"
    _write(mine, {"type": "session.status", "properties": {"status": {"type": "busy"}}})
    _write(other, {"type": "session.status", "properties": {"status": {"type": "idle"}}})
    t = TranscriptTail(tmp_path)
    t.poll({111}, now=100.0)
    assert t.agent_status == "busy" and t.events == 1


def test_transcript_heartbeat_is_not_progress_and_partial_lines_wait(tmp_path):
    p = tmp_path / "last-phase-1-x-pid5.jsonl"
    _write(p, {"type": "server.heartbeat", "properties": {}}, raw="basura\n" + '{"type": "session.stat')
    t = TranscriptTail(tmp_path)
    t.poll({5}, now=10.0)
    assert t.last_event_at is None and t.last_heartbeat_at == 10.0 and t.events == 0
    _write(p, raw='us", "properties": {"status": {"type": "idle"}}}\n')
    t.poll({5}, now=11.0)
    assert t.agent_status == "idle" and t.last_event_at == 11.0


def test_transcript_tokens_model_and_rate(tmp_path):
    p = tmp_path / "last-phase-2-x-pid7.jsonl"
    _write(p, *([{"type": "message.part.delta", "properties": {}}] * 50),
           {"type": "session.updated", "properties": {"info": {
               "agent": "recon", "model": {"id": "m", "providerID": "prov"},
               "tokens": {"input": 10, "output": 20}, "cost": 0.5}}},
           {"type": "message.part.updated", "properties": {"part": {"type": "tool", "tool": "bash"}}})
    t = TranscriptTail(tmp_path)
    t.poll({7}, now=50.0)
    s = t.snapshot(50.0)
    assert s["tps"] == 5.0 and s["model"] == "prov/m" and s["tokens"]["output"] == 20
    assert s["last_tool"] == "bash" and s["cost"] == 0.5
    assert t.tps(100.0) == 0  # la ventana caduca


def test_transcript_retry_status(tmp_path):
    p = tmp_path / "last-phase-1-x-pid9.jsonl"
    _write(p, {"type": "session.status", "properties": {"status": {"type": "retry", "attempt": 3, "message": "429"}}})
    t = TranscriptTail(tmp_path)
    t.poll({9}, now=1.0)
    assert t.snapshot(1.0)["retry"] == {"attempt": 3, "message": "429"}


def test_logtail(tmp_path):
    p = tmp_path / "x.log"
    lt = LogTail(p)
    lt.poll(1.0)
    assert lt.last_output_at is None
    p.write_bytes(b"uno\n\x1b[31mdos rojo\x1b[0m\n\n")
    lt.poll(2.0)
    assert lt.last_line == "dos rojo" and lt.last_output_at == 2.0
    lt.poll(5.0)
    assert lt.last_output_at == 2.0  # sin salida nueva


def test_host_info_has_load():
    assert "load1" in host_info()


def test_heartbeat_line_detection():
    assert is_heartbeat_line("server heartbeat") and is_heartbeat_line("  \x1b[2mserver heartbeat  ".replace("\x1b[2m", ""))
    assert is_heartbeat_line("[info] Server Heartbeat")
    assert not is_heartbeat_line("the server heartbeat is something I explain in a really long sentence")
    assert not is_heartbeat_line("paso 3/30")


def test_logtail_heartbeats_are_not_output(tmp_path):
    p = tmp_path / "x.log"
    lt = LogTail(p)
    p.write_bytes(b"server heartbeat\nserver heartbeat\n")
    lt.poll(10.0)
    assert lt.last_output_at is None and len(lt.beats) == 2 and lt.last_line == ""
    with open(p, "ab") as f:
        f.write(b"[1a] Target Profile: step-start\nserver heartbeat\n")
    lt.poll(20.0)
    assert lt.last_output_at == 20.0 and lt.last_line == "[1a] Target Profile: step-start"
    with open(p, "ab") as f:
        f.write(b"server heartbeat\n")
    lt.poll(35.0)
    assert lt.last_output_at == 20.0  # one more heartbeat does not renew the "last output"
    assert len(lt.beats) == 4


def test_logtail_partial_line_and_big_burst(tmp_path):
    p = tmp_path / "x.log"
    lt = LogTail(p)
    p.write_bytes(b"server hea")
    lt.poll(1.0)
    assert not lt.beats and lt.last_output_at is None  # incomplete line: waits
    with open(p, "ab") as f:
        f.write(b"rtbeat\nfin\n")
    lt.poll(2.0)
    assert len(lt.beats) == 1 and lt.last_line == "fin"
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * 600_000 + b"\nultima linea\n")
    lt2 = LogTail(big)
    lt2.poll(3.0)
    assert lt2.last_line == "ultima linea"


def test_beat_summary():
    s = beat_summary([100, 111, 122, 133], now=140.0)
    assert s["interval"] == 11 and s["last_age"] == 7.0 and s["count"] == 4 and s["ages"][-1] == 7.0
    assert beat_summary([], 1.0)["last_age"] is None


def test_transcript_keeps_heartbeat_times(tmp_path):
    p = tmp_path / "last-phase-1-x-pid3.jsonl"
    _write(p, *([{"type": "server.heartbeat", "properties": {}}] * 3))
    t = TranscriptTail(tmp_path)
    t.poll({3}, now=5.0)
    assert len(t.beats) == 3 and t.events == 0


def test_host_info_named_temperatures(tmp_path):
    hw = tmp_path / "hwmon"
    for n, (name, t) in enumerate([("amdgpu", 56000), ("k10temp", 56000), ("drivetemp", 32000)]):
        d = hw / f"hwmon{n}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        (d / "temp1_input").write_text(str(t))
    (hw / "hwmon9").mkdir()  # sin temperaturas
    (hw / "hwmon9" / "name").write_text("sin_temp\n")
    h = host_info(hwmon_root=str(hw), thermal_root=str(tmp_path / "no"))
    assert h["temps"] == {"amdgpu": 56.0, "k10temp": 56.0, "drivetemp": 32.0}
    assert h["temp_c"] == 56.0


def test_host_info_ignores_garbage_and_missing_dirs(tmp_path):
    d = tmp_path / "hwmon" / "hwmon0"
    d.mkdir(parents=True)
    (d / "name").write_text("x\n")
    (d / "temp1_input").write_text("basura")
    (d / "temp2_input").write_text("999999")  # fuera de rango
    h = host_info(hwmon_root=str(tmp_path / "hwmon"), thermal_root=str(tmp_path / "nada"))
    assert "temps" not in h and "load1" in h
