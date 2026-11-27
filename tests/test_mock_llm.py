# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import http.client
import json
import threading
import time

import pytest

from tools import mock_llm


def start(tmp_path, script, rate=0.0):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(script))
    srv = mock_llm.serve(p, port=0, rate=rate, quiet=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def post(srv, messages, usage=False, read=True):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
    body = {"model": "mockmodel", "messages": messages, "stream": True}
    if usage:
        body["stream_options"] = {"include_usage": True}
    t0 = time.time()
    c.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse()
    t_headers = time.time() - t0
    if not read:
        return c, r, t_headers
    chunks, raw = [], r.read().decode()
    for line in raw.split("\n\n"):
        if line.startswith("data: ") and line != "data: [DONE]":
            chunks.append(json.loads(line[6:]))
    return r.status, chunks, raw, t_headers


USER = {"role": "user", "content": "hola"}
ASSIST = {"role": "assistant", "content": "x"}


def text_of(chunks):
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])


def test_text_then_done(tmp_path):
    srv = start(tmp_path, [{"type": "text", "content": "Hello brave world"}, {"type": "done"}])
    status, chunks, raw, _ = post(srv, [USER])
    assert status == 200 and text_of(chunks) == "Hello brave world"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and raw.rstrip().endswith("[DONE]")


def test_turns_follow_assistant_message_count_like_codecome(tmp_path):
    srv = start(tmp_path, [
        {"type": "text", "content": "reading"},
        {"type": "tool_call", "id": "c1", "name": "read", "arguments": {"filePath": "a.md"}},
        {"type": "text", "content": "finished"},
        {"type": "done"},
    ])
    _, c0, _, _ = post(srv, [USER])
    tc = [c["choices"][0]["delta"]["tool_calls"][0] for c in c0 if c["choices"] and "tool_calls" in c["choices"][0]["delta"]]
    assert tc[0]["function"]["name"] == "read" and json.loads(tc[0]["function"]["arguments"]) == {"filePath": "a.md"}
    assert c0[-1]["choices"][0]["finish_reason"] == "tool_calls"
    _, c1, _, _ = post(srv, [USER, ASSIST, {"role": "tool", "content": "..."}])
    assert text_of(c1) == "finished"
    _, c2, _, _ = post(srv, [USER, ASSIST, ASSIST, ASSIST])  # beyond the script: repeats the last turn
    assert c2[-1]["choices"][0]["finish_reason"] == "stop"


def test_leading_sleep_delays_the_headers_like_ollama_prefill(tmp_path):
    srv = start(tmp_path, [{"type": "sleep", "seconds": 0.6}, {"type": "text", "content": "ok"}, {"type": "done"}])
    _, _, _, t_headers = post(srv, [USER])
    assert t_headers >= 0.55


def test_sleep_between_tool_calls_is_a_pause_mid_stream(tmp_path):
    srv = start(tmp_path, [
        {"type": "text", "content": "uno"},
        {"type": "tool_call", "name": "read", "arguments": {}},
        {"type": "sleep", "seconds": 0.5},
        {"type": "tool_call", "name": "glob", "arguments": {}},
        {"type": "done"},
    ])
    t0 = time.time()
    _, chunks, _, t_headers = post(srv, [USER])
    assert t_headers < 0.3 and time.time() - t0 >= 0.45  # headers already sent, the rest is delayed


def test_sleep_before_a_new_turn_belongs_to_that_turn():
    turns = mock_llm.split_turns([
        {"type": "text", "content": "a"}, {"type": "tool_call", "name": "x"},
        {"type": "sleep", "seconds": 1}, {"type": "text", "content": "b"}, {"type": "done"},
    ])
    assert [a["type"] for a in turns[0]] == ["text", "tool_call"]
    assert [a["type"] for a in turns[1]] == ["sleep", "text"] and turns[2] == [{"type": "done"}]


def test_usage_chunk_only_when_requested_and_overridable(tmp_path):
    srv = start(tmp_path, [{"type": "usage", "prompt_tokens": 14135}, {"type": "text", "content": "a b c"}, {"type": "done"}])
    _, chunks, _, _ = post(srv, [USER], usage=True)
    u = chunks[-1]
    assert u["choices"] == [] and u["usage"]["prompt_tokens"] == 14135 and u["usage"]["completion_tokens"] == 3
    assert u["usage"]["total_tokens"] == 14138
    _, chunks, _, _ = post(srv, [USER], usage=False)
    assert all("usage" not in c for c in chunks)


def test_http_error_fails_the_first_n_requests_then_recovers(tmp_path):
    srv = start(tmp_path, [{"type": "http_error", "status": 429, "times": 2, "message": "slow down"},
                           {"type": "text", "content": "fine"}, {"type": "done"}])
    for _ in range(2):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("POST", "/v1/chat/completions", json.dumps({"messages": [USER]}), {"Content-Type": "application/json"})
        r = c.getresponse()
        assert r.status == 429 and "slow down" in r.read().decode()
    status, chunks, _, _ = post(srv, [USER])
    assert status == 200 and text_of(chunks) == "fine"


def test_scenario_is_chosen_by_the_first_user_message(tmp_path):
    srv = start(tmp_path, {"scenarios": [
        {"match": "Phase 1a", "script": [{"type": "text", "content": "profile"}, {"type": "done"}]},
        {"match": "phase 1b", "script": [{"type": "text", "content": "sandbox"}, {"type": "done"}]},
    ], "default": [{"type": "text", "content": "other"}, {"type": "done"}]})
    assert text_of(post(srv, [{"role": "user", "content": "You are in PHASE 1A of the audit"}])[1]) == "profile"
    assert text_of(post(srv, [{"role": "system", "content": "phase 1a"}, {"role": "user", "content": "Phase 1b now"}])[1]) == "sandbox"
    assert text_of(post(srv, [{"role": "user", "content": "something else"}])[1]) == "other"
    # and a new conversation (no assistant messages) goes back to its turn 0
    assert text_of(post(srv, [{"role": "user", "content": "Phase 1a again"}])[1]) == "profile"


def test_rate_paces_the_words(tmp_path):
    srv = start(tmp_path, [{"type": "text", "content": "uno dos tres cuatro cinco seis", "tokens_per_second": 20}, {"type": "done"}])
    t0 = time.time()
    post(srv, [USER])
    assert time.time() - t0 >= 0.25


def test_client_disconnect_does_not_break_the_server(tmp_path):
    srv = start(tmp_path, [{"type": "sleep", "seconds": 0.3}, {"type": "text", "content": "x " * 50}, {"type": "done"}])
    conn, resp, _ = post(srv, [USER], read=False)
    resp.read(10)
    conn.close()
    time.sleep(0.5)
    assert post(srv, [USER])[0] == 200


def test_models_endpoint(tmp_path):
    srv = start(tmp_path, [{"type": "done"}])
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/v1/models")
    assert json.loads(c.getresponse().read())["data"][0]["id"] == "mockmodel"


def test_script_is_reloaded_when_the_file_changes_and_a_broken_one_is_ignored(tmp_path):
    srv = start(tmp_path, [{"type": "text", "content": "v1"}, {"type": "done"}])
    assert text_of(post(srv, [USER])[1]) == "v1"
    p = tmp_path / "s.json"
    p.write_text(json.dumps([{"type": "text", "content": "v2"}, {"type": "done"}]))
    import os
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert text_of(post(srv, [USER])[1]) == "v2"
    p.write_text("{this is not json")
    os.utime(p, (time.time() + 10, time.time() + 10))
    assert text_of(post(srv, [USER])[1]) == "v2"  # keeps the last valid one
