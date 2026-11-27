// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html } from "/static/js/lib.js";
import { t, has } from "/static/js/i18n.js";

export const STATUSES = ["PENDING", "CONFIRMED", "EXPLOITED", "REJECTED", "DUPLICATE"];
const JSTATUS = { queued: "mut", running: "info", done: "ok", failed: "bad", cancelled: "mut", interrupted: "warn" };
export const LSTATE = { generating: "ok", working: "ok", waiting_model: "info", quiet: "mut", retrying: "warn", stalled: "bad", stopping: "warn", finished: "mut" };

export const secs = s => {
  s = Math.round(s);
  if (s < 60) return t("dur.s", { s });
  if (s < 3600) return t("dur.ms", { m: Math.floor(s / 60), s: String(s % 60).padStart(2, "0") });
  return t("dur.hm", { h: Math.floor(s / 3600), m: String(Math.floor(s % 3600 / 60)).padStart(2, "0") });
};
export const mb = b => (b / 1048576).toFixed(0) + " MB";
export const rate = b => b > 1048576 ? (b / 1048576).toFixed(1) + " MB/s" : (b / 1024).toFixed(0) + " KB/s";
export const chip = (txt, cls) => html`<span class=${"chip c-" + cls}>${txt}</span>`;
export const phaseName = a => has("phase." + a) ? t("phase." + a) : a;
export const profileName = n => has("profile." + n) ? t("profile." + n) : (n || "—");
export const jstatus = s => chip(has("jstatus." + s) ? t("jstatus." + s) : s, JSTATUS[s] || "mut");

// Live-state explanation shared by the job list and the pulse.
export function reasonText(l) {
  const p = l.params || {};
  let s = has("reason." + l.state) ? t("reason." + l.state, p) : l.state;
  if (l.state === "retrying" && p.attempt != null) s += " — " + t("reason.retry_detail", p);
  return s;
}

// A 409 launch_blocked carries the reasons: show the first one instead of a bare "blocked".
export function launchError(e) {
  const r = e && e.detail && e.detail.code === "launch_blocked" && (e.detail.params.reasons || [])[0];
  if (!r) return e.message;
  const p = { ...r.params };
  if (r.code === "gate_unavailable") p.reason = t("reason.gate_unavailable." + p.reason);
  return has("reason." + r.code) ? t("reason." + r.code, p) : e.message;
}
