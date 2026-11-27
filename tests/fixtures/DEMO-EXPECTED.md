# demo-src: expected findings (kept OUTSIDE src/ so the auditor gets no hints)

Intentionally vulnerable test target, ~45 lines of Python (stdlib only).

| Route   | Class                          | CWE     | Why |
|---------|--------------------------------|---------|-----|
| /user   | SQL injection                  | CWE-89  | `name` is formatted into the SQL string |
| /ping   | OS command injection           | CWE-78  | `host` is concatenated into a `shell=True` command |
| /file   | Path traversal                 | CWE-22  | `name` is joined to PUBLIC_DIR without normalisation |

Also a smaller one a good auditor may mention: the server has no authentication at all.
An audit that reports the three above (and rejects nothing real) counts as a pass.
