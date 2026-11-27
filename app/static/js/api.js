// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { errText } from "/static/js/i18n.js";

export class AuthError extends Error {}
export class ApiError extends Error {
  constructor(detail) { super(errText(detail)); this.detail = detail; }
}

export async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") {
    window.dispatchEvent(new Event("auth-lost")); // the shell shows the login form
    throw new AuthError("login");
  }
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new ApiError(b.detail); }
  return r.json();
}

export const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

const FINAL_JOB = ["done", "failed", "cancelled", "interrupted"];
const wait = ms => new Promise(ok => setTimeout(ok, ms));

// Follow a job until it ends; resolves with the final job row (maintenance jobs: archive, rescue, upload).
export async function follow(id, every = 1500) {
  for (;;) {
    await wait(every);
    const { job } = await api(`/api/jobs/${id}`);
    if (FINAL_JOB.includes(job.status)) return job;
  }
}
