#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""OpenAI-compatible test LLM server (/v1/chat/completions, streaming).

Reads the same JSON scripts as CodeCome's mock (text / tool_call / done) and adds what is
needed to test the panel of a slow phase with no GPU and no spending:

  {"type": "sleep", "seconds": 8}            waits. At the start of a turn it delays the response
                                             HEADERS, like Ollama does during prefill.
  {"type": "text", "content": "...", "tokens_per_second": 20}   its own pace for that text
  {"type": "usage", "prompt_tokens": 14135}  sets the input tokens that are reported
  {"type": "http_error", "status": 429, "times": 2, "message": "..."}  fails the first N times

The script can be a list, or {"scenarios": [{"match": "Phase 1a", "script": [...]}], "default": [...]}.
`match` is searched (case-insensitive) in the user's first message: each sub-phase opens a new session.
Turns are assigned by the number of assistant messages already in the history.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONTROL = ("sleep", "http_error", "usage")


def split_turns(script: list[dict]) -> list[list[dict]]:
    """A turn = optional text + the tool_calls that follow it. `done` is its own final turn.

    A sleep/http_error/usage right before the text that opens a new turn, after tool calls,
    belongs to that new turn (that is how "wait first, then talk" is written).
    """
    turns: list[list[dict]] = []
    cur: list[dict] = []
    pending: list[dict] = []
    in_tools = False
    for a in script:
        t = a.get("type")
        if t == "done":
            cur.extend(pending)
            pending = []
            if cur:
                turns.append(cur)
            turns.append([a])
            return turns
        if t in CONTROL:
            (pending if in_tools else cur).append(a)
        elif t == "text":
            if in_tools and cur:
                turns.append(cur)
                cur = list(pending)
                pending = []
            cur.append(a)
            in_tools = False
        elif t == "tool_call":
            cur.extend(pending)
            pending = []
            cur.append(a)
            in_tools = True
        else:
            cur.append(a)
    cur.extend(pending)
    if cur:
        turns.append(cur)
    return turns


def load_scenarios(path: Path) -> tuple[list[tuple[str, list[list[dict]]]], list[list[dict]]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [], split_turns(data)
    scen = [(str(s["match"]).lower(), split_turns(s["script"])) for s in data.get("scenarios", [])]
    default = split_turns(data["default"]) if "default" in data else (scen[0][1] if scen else [])
    return scen, default


def first_user_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return str(c or "")
    return ""


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, scenarios, default, rate: float, quiet: bool = False, path: Path | None = None):
        super().__init__(addr, Handler)
        self.scenarios, self.default, self.rate, self.quiet = scenarios, default, rate, quiet
        self.path = Path(path) if path else None
        self._mtime = self.path.stat().st_mtime if self.path and self.path.exists() else 0.0
        self.attempts: dict[tuple[str, int], int] = {}
        self.lock = threading.Lock()

    def refresh(self) -> None:
        """If the script changed on disk it is re-read: tune it with no restart and no sudo.
        A broken script breaks nothing: it is reported and the previous one is kept."""
        if not self.path:
            return
        try:
            m = self.path.stat().st_mtime
            if m == self._mtime:
                return
            scen, default = load_scenarios(self.path)
        except (OSError, ValueError, KeyError, TypeError) as e:
            if not self.quiet:
                print(f"{time.strftime('%H:%M:%S')} invalid script, keeping the previous one: {e}", flush=True)
            self._mtime = self.path.stat().st_mtime if self.path.exists() else self._mtime
            return
        with self.lock:
            self.scenarios, self.default, self._mtime = scen, default, m
            self.attempts.clear()
        if not self.quiet:
            print(f"{time.strftime('%H:%M:%S')} guion recargado", flush=True)

    def pick(self, messages: list[dict]) -> tuple[str, list[list[dict]]]:
        self.refresh()
        head = first_user_text(messages).lower()
        for match, turns in self.scenarios:
            if match in head:
                return match, turns
        return "default", self.default


class Handler(BaseHTTPRequestHandler):
    server_version = "MockLLM/2.0"

    def log_message(self, *a):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            return self._json({"object": "list", "data": [{"id": "mockmodel", "object": "model"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode()) if n else {}
        except (ValueError, OSError):
            payload = {}
        messages = payload.get("messages", [])
        srv: Server = self.server  # type: ignore[assignment]
        name, turns = srv.pick(messages)
        idx = min(sum(1 for m in messages if m.get("role") == "assistant"), max(len(turns) - 1, 0))
        with srv.lock:
            attempt = srv.attempts[(name, idx)] = srv.attempts.get((name, idx), 0) + 1
        turn = turns[idx] if turns else [{"type": "done"}]
        t0 = time.time()
        try:
            for a in turn:  # errors first: if this attempt must fail, nothing else is sent
                if a.get("type") == "http_error" and attempt <= int(a.get("times", 1)):
                    self._json({"error": {"type": "mock_error", "message": a.get("message", "mock error")}},
                               status=int(a.get("status", 500)))
                    self._log(name, idx, attempt, f"error {a.get('status', 500)}", t0)
                    return
            self._stream(turn, payload, messages, srv.rate)
            self._log(name, idx, attempt, "ok", t0)
        except (BrokenPipeError, ConnectionResetError):
            self._log(name, idx, attempt, "client closed", t0)

    def _log(self, name, idx, attempt, res, t0):
        if not self.server.quiet:  # type: ignore[attr-defined]
            print(f"{time.strftime('%H:%M:%S')} escenario={name!r} turno={idx} intento={attempt} {res} {time.time() - t0:.1f}s",
                  flush=True)

    def _stream(self, turn, payload, messages, default_rate):
        started = False
        completion = 0
        usage_over: dict = {}
        include_usage = bool((payload.get("stream_options") or {}).get("include_usage"))
        cid = f"mock-{int(time.time() * 1000)}"

        def send(delta=None, finish=None, usage=None):
            ch = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": "mockmodel",
                  "choices": [] if usage is not None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
            if usage is not None:
                ch["usage"] = usage
            self.wfile.write(f"data: {json.dumps(ch)}\n\n".encode())
            self.wfile.flush()

        def begin():
            nonlocal started
            if started:
                return
            started = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            send({"role": "assistant"})

        tools = 0
        for a in turn:
            t = a.get("type")
            if t == "sleep":
                time.sleep(float(a.get("seconds", 0)))
            elif t == "usage":
                usage_over = {k: int(v) for k, v in a.items() if k in ("prompt_tokens", "completion_tokens")}
            elif t == "text":
                begin()
                rate = float(a.get("tokens_per_second", default_rate))
                words = str(a.get("content", "")).split(" ")
                for i, w in enumerate(words):
                    send({"content": w + (" " if i < len(words) - 1 else "")})
                    completion += 1
                    if rate > 0:
                        time.sleep(1.0 / rate)
            elif t == "tool_call":
                begin()
                args = json.dumps(a.get("arguments", {}))
                send({"tool_calls": [{"index": tools, "id": a.get("id", f"call_{tools + 1}"), "type": "function",
                                      "function": {"name": a["name"], "arguments": args}}]})
                completion += est_tokens(args)
                tools += 1
        begin()
        done = any(a.get("type") == "done" for a in turn)
        send(finish="tool_calls" if tools and not done else "stop")
        if include_usage:
            prompt = usage_over.get("prompt_tokens", est_tokens(json.dumps(messages)))
            comp = usage_over.get("completion_tokens", completion)
            send(usage={"prompt_tokens": prompt, "completion_tokens": comp, "total_tokens": prompt + comp})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def serve(script: Path, host="127.0.0.1", port=0, rate=40.0, quiet=False) -> Server:
    scen, default = load_scenarios(script)
    return Server((host, port), scen, default, rate, quiet, path=script)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--rate", type=float, default=40.0, help="default words/s (0 = no pause)")
    a = ap.parse_args()
    srv = serve(a.script, a.host, a.port, a.rate)
    print(f"MockLLM escuchando en http://{a.host}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
