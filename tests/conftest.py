# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import pytest

from app import main
from app.gates import GateResult


class PassingGates:
    """Default for API tests: the fixture workspaces have no tools/gate-check.py."""

    def check(self, key, finding=None, max_age=None):
        return GateResult(True)


@pytest.fixture(autouse=True)
def _passing_gates(monkeypatch):
    monkeypatch.setattr(main, "gates", PassingGates())


@pytest.fixture(autouse=True)
def _code_present(request, monkeypatch):
    """Most tests build bare workspaces without src/: pretend the code is there, except in the
    tests that check the empty-src rule itself (marked `real_src`)."""
    if "real_src" not in request.keywords:
        from app import gates
        monkeypatch.setattr(gates, "src_has_code", lambda root: True)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_src: use the real empty-src/ launch rule")
