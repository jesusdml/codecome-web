#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
# Demo of the web UI with invented data: no CodeCome run, no model. Open http://127.0.0.1:8199/
# Usage: tools/run-demo.sh [--reseed]   (reseed needs CODECOME_TOOLS=/path/to/CodeCome/tools for real gates)
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--reseed" || ! -d demo/workspace ]]; then
  .venv/bin/python tools/seed_demo.py demo ${CODECOME_TOOLS:+--tools-from "$CODECOME_TOOLS"}
fi
.venv/bin/python tools/seed_demo.py demo --beat &
BEAT=$!
trap 'kill $BEAT 2>/dev/null' EXIT
CODECOME_ROOT="$PWD/demo/workspace" CODECOME_DATA="$PWD/demo/data" CODECOME_USERS="$PWD/demo/data/users.json" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8199}"
