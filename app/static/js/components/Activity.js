// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html } from "/static/js/lib.js";
import { api } from "/static/js/api.js";
import { t, has } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";

const title = k => has(`phase.${k}.title`) ? t(`phase.${k}.title`) : k;

// Global bar under the header: worker state, the running job and the queue (design 1.3).
export function Activity() {
  const { data: d } = usePoll(() => api("/api/audit"), 2000);
  if (!d) return null;
  const a = d.active;
  if (!d.worker.alive)
    return html`<div class="activity down">${t("jobs.worker_down", { detail: "" })}</div>`;
  if (!a && !d.queue.length) return null;
  return html`<div class="activity">
    ${a && html`<a href=${"#/job/" + a.job}>${t("next.running", { job: a.job })} · ${title(a.sub || a.phase)}${a.finding ? " · " + a.finding : ""}</a>`}
    ${d.queue.length > 0 && html`<span class="mut"> · ${t("activity.queued", { n: d.queue.length })}</span>`}
  </div>`;
}
