# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Imitates a CodeCome phase: output, real CPU and a JSONL transcript with its PID."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
tmp = Path.cwd() / "tmp"
tmp.mkdir(exist_ok=True)


def transcript(events):
    p = tmp / f"last-phase-1-no-finding-attempt-1-20261124-000000-pid{os.getpid()}.jsonl"
    with open(p, "a", buffering=1) as f:
        f.write("this is not json\n")
        for e in events:
            f.write(json.dumps(e) + "\n")
            time.sleep(0.02)


def busy(sec):
    end = time.time() + sec
    while time.time() < end:
        pass


if mode == "ok":
    print("phase start", flush=True)
    transcript(
        [{"type": "server.heartbeat", "properties": {}},
         {"type": "session.status", "properties": {"status": {"type": "busy"}}}]
        + [{"type": "message.part.delta", "properties": {"field": "text", "delta": "x"}}] * 30
        + [{"type": "session.updated", "properties": {"info": {
            "agent": "recon", "model": {"id": "qwen3.5:9b", "providerID": "local-ollama"},
            "tokens": {"input": 2099, "output": 1132, "reasoning": 0, "cache": {"read": 5, "write": 0}},
            "cost": 0}}}]
    )
    busy(0.6)
    print("phase end", flush=True)
elif mode in ("steps", "steps_hang"):
    def sf(pid_, i, o):
        return {"type": "message.part.updated", "properties": {"time": 1795539907581, "sessionID": "ses_1", "part": {
            "id": pid_, "type": "step-finish", "messageID": "msg_1", "sessionID": "ses_1",
            "tokens": {"input": i, "output": o, "reasoning": 0, "cache": {"read": 5, "write": 0}}, "cost": 0}}}
    transcript([{"type": "message.updated", "properties": {"info": {
                    "id": "msg_1", "agent": "recon", "providerID": "local-ollama", "modelID": "qwen"}}},
                sf("prt_1", 100, 10), sf("prt_2", 200, 20), sf("prt_1", 100, 10)])  # prt_1 repeated
    if mode == "steps_hang":
        time.sleep(600)
elif mode == "fail":
    print("boom", flush=True)
    sys.exit(3)
elif mode == "retry":
    transcript([{"type": "session.status", "properties": {"status": {"type": "retry", "attempt": 2, "message": "429"}}}])
    time.sleep(2)
elif mode == "hang":
    print("empiezo y me callo", flush=True)
    time.sleep(600)
elif mode == "fork":
    child = subprocess.Popen(["sleep", "600"])
    Path(sys.argv[2]).write_text(str(child.pid))
    time.sleep(600)
elif mode == "long":
    print("long demo phase", flush=True)
    for i in range(1, 31):
        transcript(
            [{"type": "session.status", "properties": {"status": {"type": "busy"}}}]
            + [{"type": "message.part.delta", "properties": {"field": "text", "delta": "x"}}] * 6
            + [{"type": "session.updated", "properties": {"info": {
                "agent": "recon", "model": {"id": "qwen3.5:9b", "providerID": "local-ollama"},
                "tokens": {"input": 2000 + i * 40, "output": i * 55, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                "cost": 0}}}]
        )
        print(f"paso {i}/30: analizando src/modulo_{i}.py", flush=True)
        busy(0.3)
        time.sleep(0.7)
    print("fin", flush=True)
elif mode == "beats":
    # Only console heartbeats: the server "lives" but the job does not move.
    print("arranque", flush=True)
    while True:
        print("server heartbeat", flush=True)
        time.sleep(0.2)
elif mode == "env":
    print("MODEL=%s PORT=%s STALL=%s KEY=%s" % (os.environ.get("CODECOME_MODEL"), os.environ.get("MOCKLLM_PORT"),
          os.environ.get("CODECOME_BUSY_STALL_TIMEOUT"), os.environ.get("ANTHROPIC_API_KEY")), flush=True)
