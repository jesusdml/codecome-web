// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t, has } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";
import { secs } from "/static/js/format.js";

// Hand-drawn SVG, no chart library: colours come from CSS classes (the CSP forbids inline styles),
// and tooltips are native <title> elements so they work without any script.
const TK = ["cache_read", "cache_write", "input", "output"];  // fixed order = fixed colour slot
const num = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(Math.round(n));
const usd = n => n >= 100 ? "$" + Math.round(n) : n >= 1 ? "$" + n.toFixed(2) : "$" + n.toFixed(3);
const sumTk = tk => TK.reduce((a, k) => a + (tk[k] || 0), 0);
const phaseLabel = k => has(`phase.${k}.title`) ? `${k} · ${t(`phase.${k}.title`)}` : k;

function Kpis({ usage, jobs, matrix }) {
  const tot = usage.total;
  const saved = tot.cost_uncached > 0 ? 1 - tot.cost / tot.cost_uncached : 0;
  const machine = jobs.reduce((a, j) => a + (j.started_at ? ((j.finished_at || Date.now() / 1000) - j.started_at) : 0), 0);
  const live = matrix.filter(r => !r.discarded);
  const proven = live.filter(r => r.status === "CONFIRMED" || r.status === "EXPLOITED").length;
  const tiles = [
    ["dash.cost", usd(tot.cost), t("dash.uncached", { v: usd(tot.cost_uncached) })],
    ["dash.saved", Math.round(saved * 100) + "%", t("dash.saved_hint")],
    ["dash.tokens", num(sumTk(tot.tokens)), t("dash.steps", { n: tot.steps })],
    ["dash.machine", secs(machine), t("dash.jobs", { n: jobs.length })],
    ["dash.findings", `${proven} / ${live.length}`, t("dash.findings_hint")],
  ];
  return html`<div class="kpis">${tiles.map(([k, v, s]) => html`<div class="kpi">
    <div class="k">${t(k)}</div><div class="v">${v}</div><div class="s">${s}</div></div>`)}</div>`;
}

function TokensByPhase({ usage }) {
  const rows = usage.groups.filter(g => sumTk(g.tokens) > 0);
  if (!rows.length) return html`<p class="mut small">${t("dash.no_data")}</p>`;
  const max = Math.max(...rows.map(g => sumTk(g.tokens)));
  const W = 640, L = 150, R = 70, H = 26, gap = 8, h = rows.length * (H + gap);
  const x = v => (v / max) * (W - L - R);
  return html`<figure class="chart">
    <figcaption>${t("dash.tokens_by_phase")}</figcaption>
    <div class="legend">${TK.map((k, i) => html`<span><i class=${"sw s" + (i + 1)}></i>${t("dash.tk." + k)}</span>`)}</div>
    <svg viewBox=${`0 0 ${W} ${h}`} role="img" aria-label=${t("dash.tokens_by_phase")}>
      ${rows.map((g, r) => {
        let acc = 0;
        const y = r * (H + gap);
        return html`<g>
          <text class="lbl" x=${L - 8} y=${y + H / 2 + 4} text-anchor="end">${phaseLabel(g.key)}</text>
          ${TK.map((k, i) => {
            const w = x(g.tokens[k] || 0), x0 = L + acc;
            acc += w;
            return w > 0.5 && html`<rect class=${"s" + (i + 1)} x=${x0} y=${y} width=${Math.max(w - 2, 1)} height=${H} rx="3">
              <title>${phaseLabel(g.key)} · ${t("dash.tk." + k)}: ${num(g.tokens[k])}</title></rect>`;
          })}
          <text class="val" x=${L + acc + 6} y=${y + H / 2 + 4}>${usd(g.cost)}</text>
        </g>`;
      })}
    </svg></figure>`;
}

const JOB_CLS = { done: "ok", failed: "bad", running: "info", queued: "mut", cancelled: "mut" };

function Timeline({ jobs }) {
  const now = Date.now() / 1000;
  const runs = jobs.filter(j => j.started_at).sort((a, b) => a.started_at - b.started_at);
  if (!runs.length) return html`<p class="mut small">${t("dash.no_data")}</p>`;
  const t0 = runs[0].started_at, t1 = Math.max(now, ...runs.map(j => j.finished_at || now));
  const lanes = ["1", "2", "3", "4", "5", "6"];
  const W = 640, L = 150, R = 10, H = 20, gap = 8, top = 4, h = top + lanes.length * (H + gap) + 18;
  const x = v => L + ((v - t0) / (t1 - t0 || 1)) * (W - L - R);
  const fmt = s => new Date(s * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  return html`<figure class="chart">
    <figcaption>${t("dash.timeline")}</figcaption>
    <svg viewBox=${`0 0 ${W} ${h}`} role="img" aria-label=${t("dash.timeline")}>
      ${lanes.map((k, i) => html`<g>
        <line class="grid" x1=${L} x2=${W - R} y1=${top + i * (H + gap) + H / 2} y2=${top + i * (H + gap) + H / 2} />
        <text class="lbl" x=${L - 8} y=${top + i * (H + gap) + H / 2 + 4} text-anchor="end">${phaseLabel(k)}</text></g>`)}
      ${runs.map(j => {
        const lane = lanes.indexOf(j.action.replace(/^phase-/, ""));
        if (lane < 0) return null;
        const y = top + lane * (H + gap), a = x(j.started_at), b = x(j.finished_at || now);
        const dur = (j.finished_at || now) - j.started_at;
        return html`<a href=${"#/job/" + j.id}><rect class=${"tl " + (JOB_CLS[j.status] || "mut")} x=${a} y=${y}
            width=${Math.max(b - a, 4)} height=${H} rx="4">
          <title>#${j.id} · ${phaseLabel(j.action.replace(/^phase-/, ""))}${j.finding ? " · " + j.finding : ""} · ${t("jstatus." + j.status)} · ${secs(dur)}</title></rect></a>`;
      })}
      <line class="now" x1=${x(now)} x2=${x(now)} y1="0" y2=${h - 16} />
      <text class="axis" x=${L} y=${h - 2}>${fmt(t0)}</text>
      <text class="axis" x=${W - R} y=${h - 2} text-anchor="end">${t("dash.now")}</text>
    </svg></figure>`;
}

function Funnel({ matrix }) {
  const live = matrix.filter(r => !r.discarded);
  const steps = [
    ["dash.f.detected", matrix.length],
    ["dash.f.kept", live.length],
    ["dash.f.validated", live.filter(r => r.status === "CONFIRMED" || r.status === "EXPLOITED").length],
    ["dash.f.exploited", live.filter(r => r.status === "EXPLOITED").length],
  ];
  const assumed = live.filter(r => r.p4.status === "assumed" || r.p5.status === "assumed").length;
  const max = Math.max(1, steps[0][1]);
  const W = 640, H = 34, gap = 6, h = steps.length * (H + gap);
  return html`<figure class="chart">
    <figcaption>${t("dash.funnel")}</figcaption>
    <svg viewBox=${`0 0 ${W} ${h}`} role="img" aria-label=${t("dash.funnel")}>
      ${steps.map(([k, n], i) => {
        const w = Math.max((n / max) * W * 0.9, 6), y = i * (H + gap);
        return html`<g><rect class=${"fn f" + i} x=${(W - w) / 2} y=${y} width=${w} height=${H} rx="4"><title>${t(k)}: ${n}</title></rect>
          <text class="fnl" x=${W / 2} y=${y + H / 2 + 5} text-anchor="middle">${t(k)} · ${n}</text></g>`;
      })}
    </svg>
    ${assumed > 0 && html`<p class="small mut"><${Icon} name="warn" /> ${t("dash.f.assumed")}: <b>${assumed}</b></p>`}</figure>`;
}

export function Dashboard({ matrix }) {
  // Only THIS audit: its phase jobs and what they spent (archive/upload/reset jobs are not phases).
  const { data: usage } = usePoll(() => api("/api/usage?group=phase&scope=current"), 10000);
  const { data: jobs } = usePoll(async () => (await api("/api/jobs?limit=200")).jobs.filter(j => j.action.startsWith("phase-")), 5000);
  if (!usage || !jobs) return html`<p class="mut">…</p>`;
  return html`<section class="dash">
    <${Kpis} usage=${usage} jobs=${jobs} matrix=${matrix} />
    <div class="charts">
      <${TokensByPhase} usage=${usage} />
      <${Timeline} jobs=${jobs} />
      <${Funnel} matrix=${matrix} />
    </div>
  </section>`;
}
