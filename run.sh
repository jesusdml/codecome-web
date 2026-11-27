#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
# Development start: loopback only.
cd "$(dirname "$0")"
export CODECOME_ROOT="${CODECOME_ROOT:-$PWD/tests/fixtures}"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8100}"
