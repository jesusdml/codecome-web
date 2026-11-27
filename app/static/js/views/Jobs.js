// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect, useRef } from "/static/js/lib.js";
import { api, post } from "/static/js/api.js";
import { t, has, errText } from "/static/js/i18n.js";
import { navigate } from "/static/js/router.js";
import { PageHero } from "/static/js/components/PageHero.js";
import { usePoll } from "/static/js/poll.js";
import { secs, mb, rate, chip, phaseName, profileName, jstatus, reasonText, LSTATE } from "/static/js/format.js";

export function Jobs() {
  // Debug view: the list of runs. Phases are launched only from the Journey.
  const { data: d, error } = usePoll(() => api("/api/jobs"), 2000);
  if (error && !d) return html`<p class="c-bad">${error.message}</p>`;
  if (!d) return null;
  const detail = d.worker.since != null ? t("jobs.worker_since", { age: secs(d.worker.since) }) : t("jobs.worker_never");
  const count = st => d.jobs.filter(j => j.status === st).length;
  return html`
    <${PageHero} eyebrow=${t("jobs.eyebrow")} title=${t("tab.jobs")} lede=${t("jobs.debug_hint") + " " + t("tab.journey") + "."}
      term=${{ title: "~/codecome-web", cmd: "systemctl status codecome-worker",
        lines: [[d.worker.alive ? "● active (running)" : "○ inactive", d.worker.alive ? "tc-green" : "tc-red"],
                ["running:", "tc-dim", String(count("running")), "tc-blue"], ["queued:", "tc-dim", String(count("queued")), "tc-blue"],
                ["failed:", "tc-dim", String(count("failed")), count("failed") ? "tc-red" : "tc-dim"]] }} />
    ${!d.worker.alive && html`<div class="banner">${t("jobs.worker_down", { detail })}</div>`}
    ${d.jobs.length ? html`<div class="tablecard"><table>
      <tr>${["id", "phase", "finding", "status", "now", "by"].map(c => html`<th>${t("jobs.col." + c)}</th>`)}</tr>
      ${d.jobs.map(j => html`<tr class="row" onClick=${() => navigate("/job/" + j.id)}>
        <td class="mono">${j.id}</td><td>${phaseName(j.action)}</td><td>${j.finding || ""}</td><td>${jstatus(j.status)}</td>
        <td class="small mut">${j.status === "running" && j.live ? reasonText(j.live) : (j.error ? errText(j.error) : "")}</td>
        <td>${j.requested_by}</td></tr>`)}
    </table></div>` : html`<p class="mut">${t("jobs.none")}</p>`}`;
}

function Spark({ h }) {
  if (!h || h.length < 2) return null;
  const max = Math.max(100, ...h), w = 200, ht = 30;
  const pts = h.map((v, i) => `${(i / (h.length - 1) * w).toFixed(1)},${(ht - v / max * (ht - 2) - 1).toFixed(1)}`).join(" ");
  return html`<svg class="spark" viewBox=${`0 0 ${w} ${ht}`} preserveAspectRatio="none"><polyline points=${pts} /></svg>`;
}
const temps = h => !h.temps ? (h.temp_c != null ? h.temp_c + " °C" : "")
  : Object.entries(h.temps).map(([k, v]) => (has("sensor." + k) ? t("sensor." + k) : k) + " " + v + " °C").join(" · ");

function Pulse({ j, worker }) {
  const head = html`<p>${jstatus(j.status)} <b>${phaseName(j.action)}</b> ${j.finding || ""}
    <span class="mut small"> · ${t("job.meta", { id: j.id, user: j.requested_by, profile: profileName(j.profile) })}</span></p>
    ${j.error && html`<p class="c-bad">${errText(j.error)}</p>`}`;
  if (j.status === "queued") return html`${head}<p class="mut">${t("pulse.queued")}</p>
    ${!worker.alive && html`<div class="banner">${t("jobs.worker_down", { detail: "" })}</div>`}`;
  const l = j.live;
  let warn = null;
  if (j.status === "running" && !worker.alive) warn = t("pulse.worker_down", { age: worker.since != null ? secs(worker.since) : "?" });
  else if (j.status === "running" && j.live_age != null && j.live_age > 10) warn = t("pulse.stale", { age: secs(j.live_age) });
  if (!l) return html`${head}${warn && html`<div class="banner">${warn}</div>`}<p class="mut">${t("pulse.starting")}</p>`;
  const a = l.agent || {}, tk = a.tokens || {}, host = l.host || {};
  const box = (k, v, s) => html`<div class="box"><div class="k">${t("box." + k)}</div><div class="v">${v}</div>${s && html`<div class="s">${s}</div>`}</div>`;
  return html`${head}${warn && html`<div class="banner">${warn}</div>`}
    <div class="grid">
      ${box("state", chip(has("state." + l.state) ? t("state." + l.state) : l.state, LSTATE[l.state] || "mut"), reasonText(l))}
      ${box("time", secs(l.elapsed), t("box.since_signal", { age: secs(l.since_signal) }))}
      ${box("cpu", (l.cpu_pct ?? 0).toFixed(0) + " %", html`<${Spark} h=${l.cpu_hist} />`)}
      ${box("memory", mb(l.rss || 0), t("box.procs", { n: l.procs, io: rate(l.io_bps || 0) }))}
      ${box("agent", a.status || t("box.no_data"), [a.model, a.name].filter(Boolean).join(" · ") + (a.retry ? " · " + t("box.retry", { n: a.retry.attempt }) : ""))}
      ${box("model", a.tps ? t("box.rate", { rate: a.tps }) : "—", tk.input != null ? t("box.tokens", { input: tk.input, output: tk.output, reasoning: tk.reasoning ?? 0 }) : t("box.no_totals"))}
      ${box("last_step", a.last_tool || a.last_part || "—", a.events != null ? t("box.events", { n: a.events }) : "")}
      ${box("host", host.load1 != null ? t("box.load", { load: host.load1, cpus: host.cpus }) : "—", temps(host))}
    </div>
    <p class="small mut">${l.since_output == null ? t("pulse.last_output_none") : t("pulse.last_output", { age: secs(l.since_output) })} <code>${l.last_line || ""}</code></p>`;
}

export function Log({ id, short }) {
  const ref = useRef(null);
  useEffect(() => {
    let text = "";
    const es = new EventSource(`/api/jobs/${id}/log`);
    es.addEventListener("log", e => {
      const el = ref.current; if (!el) return;
      const near = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
      text = (text + JSON.parse(e.data)).slice(-400000);
      el.textContent = text; // plain text: the log is untrusted output
      if (near) el.scrollTop = el.scrollHeight;
    });
    es.addEventListener("end", () => es.close());
    es.onerror = () => {}; // EventSource reconnects on its own with Last-Event-ID
    return () => es.close();
  }, [id]);
  return html`<pre id=${short ? null : "log"} class=${short ? "log short" : "log"} ref=${ref}></pre>`;
}

export function Job({ id }) {
  const { data: d, error } = usePoll(() => api(`/api/jobs/${id}`), 2000, [id]);
  const active = d && (d.job.status === "queued" || d.job.status === "running");
  const cancel = async () => {
    if (!confirm(t("job.confirm_cancel"))) return;
    try { await post(`/api/jobs/${id}/cancel`); } catch (e) { alert(e.message); }
  };
  const j = d && d.job;
  const cmd = !j ? "make …" : j.action.startsWith("phase-") ? `make ${j.action}${j.finding ? " FINDING=" + j.finding : ""}`
    : `worker ${j.action}${j.target ? " " + j.target : ""}`;  // archive / rescue / upload: Python in the worker, no make
  return html`
    <${PageHero} eyebrow=${`#${id}`} title=${j ? phaseName(j.action) + (j.finding ? " · " + j.finding : "") : "…"}
      extraBack=${html` <span class="mut">·</span> <a href="#/jobs">${t("job.back")}</a>`}
      lede=${j && html`${jstatus(j.status)} <span>${t("job.meta", { id, user: j.requested_by, profile: profileName(j.profile) })}</span>`}
      actions=${active && html`<button class="btn danger ondark" onClick=${cancel}>${t("job.cancel")}</button>`}
      term=${{ title: "~/codecome", cmd, lines: j ? [[j.status, j.status === "done" ? "tc-green" : j.status === "failed" ? "tc-red" : "tc-blue"],
        ...(j.exit_code != null ? [["exit:", "tc-dim", String(j.exit_code)]] : [])] : [] }} />
    ${error && !d && html`<p class="c-bad">${error.message}</p>`}
    ${d && html`<${Pulse} j=${d.job} worker=${d.worker} />`}
    <h2>${t("job.output")}</h2>
    <${Log} id=${id} />`;
}
