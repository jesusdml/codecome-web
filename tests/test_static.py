# SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 @jesusdml
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

STATIC = Path(main.__file__).with_name("static")


def test_ui_page_and_static_files_are_public_and_typed():
    c = TestClient(main.app)
    r = c.get("/ui")
    assert r.status_code == 200 and '<script type="module" src="/static/js/app.js">' in r.text
    for path, ctype in (("/static/js/app.js", "javascript"), ("/static/style.css", "text/css"),
                        ("/static/vendor/preact-htm.js", "javascript")):
        r = c.get(path)
        assert r.status_code == 200 and ctype in r.headers["content-type"], path
    assert c.get("/static/nope.js").status_code == 404
    assert c.get("/static/../main.py").status_code in (400, 404)
    assert c.get("/static/%2e%2e/main.py").status_code in (400, 404)


def test_ui_csp_is_strict_and_legacy_page_is_unchanged():
    c = TestClient(main.app)
    for url in ("/", "/ui", "/static/js/app.js", "/static/style.css", "/api/me"):
        csp = c.get(url).headers["content-security-policy"]
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "script-src 'self';" in csp and "style-src 'self';" in csp and "base-uri 'none'" in csp
    assert c.get("/").text == c.get("/ui").text  # the legacy page is retired


def test_ui_page_has_no_inline_code_or_style():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\ssrc=)", html) and "<style" not in html
    assert not re.search(r"\son\w+\s*=", html) and 'style="' not in html


def test_modules_use_no_inline_style_attributes_or_eval():
    for p in STATIC.rglob("*.js"):
        if "vendor" in p.parts:
            continue
        src = p.read_text(encoding="utf-8")
        assert "innerHTML" not in src and "eval(" not in src and "new Function" not in src, p
        assert 'style="' not in src and "style=${" not in src, p


def test_vendored_library_matches_its_recorded_hash():
    import hashlib
    rec = (STATIC / "vendor" / "VERSIONS.md").read_text(encoding="utf-8")
    sha = hashlib.sha256((STATIC / "vendor" / "preact-htm.js").read_bytes()).hexdigest()
    assert sha in rec


def test_every_named_import_between_modules_exists():
    """A missing export only fails in the browser (SyntaxError at load): catch it here."""
    import re
    js = Path(main.__file__).parent / "static" / "js"
    vendor = Path(main.__file__).parent / "static" / "vendor"
    exports = {}
    for f in list(js.rglob("*.js")) + list(vendor.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        names = set(re.findall(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", src))
        for block in re.findall(r"export\s*\{([^}]*)\}", src):
            for part in block.split(","):
                part = part.strip()
                if part:
                    names.add(part.split(" as ")[-1].strip())
        exports["/static/" + str(f.relative_to(f.parents[1] if f.parent.name == "vendor" else js.parent)).replace("\\", "/")] = names
    missing = []
    for f in js.rglob("*.js"):
        for names, mod in re.findall(r"import\s*\{([^}]*)\}\s*from\s*\"([^\"]+)\"", f.read_text(encoding="utf-8")):
            for n in [x.strip().split(" as ")[0].strip() for x in names.split(",") if x.strip()]:
                if mod in exports and n not in exports[mod]:
                    missing.append(f"{f.name}: {n} from {mod}")
    assert not missing, missing
