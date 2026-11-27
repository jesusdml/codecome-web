# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
"""Tiny demo web service used as a test target for the audit pipeline."""
import os
import sqlite3
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, secret TEXT)")
DB.execute("INSERT INTO users VALUES (1, 'alice', 'alice-secret'), (2, 'bob', 'bob-secret')")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/user":
            name = query.get("name", [""])[0]
            rows = DB.execute("SELECT id, name FROM users WHERE name = '%s'" % name).fetchall()
            self._send(200, str(rows))
        elif url.path == "/ping":
            host = query.get("host", ["127.0.0.1"])[0]
            result = subprocess.run("ping -c 1 " + host, shell=True, capture_output=True, text=True)
            self._send(200, result.stdout)
        elif url.path == "/file":
            name = query.get("name", ["index.html"])[0]
            with open(os.path.join(PUBLIC_DIR, name)) as handle:
                self._send(200, handle.read())
        else:
            self._send(404, "not found")

    def _send(self, status, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
