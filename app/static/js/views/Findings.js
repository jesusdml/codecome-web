// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";
import { navigate } from "/static/js/router.js";
import { STATUSES, chip } from "/static/js/format.js";
import { PageHero } from "/static/js/components/PageHero.js";
import { Markdown } from "/static/js/views/Report.js";
import { CastPlayer } from "/static/js/components/CastPlayer.js";

// Read-only views: phases (also 4 and 5) are launched only from the Journey.
let current = "ALL"; // remembered when coming back from a finding; "ALL" = no filter

function useLoad(fn, deps) {
  const [s, set] = useState({ data: null, error: null });
  useEffect(() => { let ok = true; fn().then(d => ok && set({ data: d, error: null }), e => ok && set({ data: null, error: e })); return () => { ok = false; }; }, deps);
  return s;
}

const FS_CLS = { PENDING: "info", CONFIRMED: "warn", EXPLOITED: "bad", REJECTED: "mut", DUPLICATE: "mut" };
const FS_TC = { PENDING: "tc-blue", CONFIRMED: "tc-yellow", EXPLOITED: "tc-red", REJECTED: "tc-dim", DUPLICATE: "tc-dim" };
const SEV_CLS = { CRITICAL: "bad", HIGH: "warn", MEDIUM: "info", LOW: "mut", INFO: "mut" };
const fchip = s => chip(t("fstatus." + s), FS_CLS[s] || "mut");
const schip = s => chip(s || "—", SEV_CLS[s] || "mut");

export function Findings() {
  const [status, setStatus] = useState(current);
  const { data, error } = useLoad(() => api("/api/findings"), []);
  if (error) return html`<p class="c-bad">${error.message}</p>`;
  if (!data) return null;
  const SEV = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, INFO: 4 };
  const rows = data.findings.filter(f => status === "ALL" || f.status === status)
    .sort((a, b) => (SEV[a.severity] ?? 5) - (SEV[b.severity] ?? 5) || a.id.localeCompare(b.id));
  const total = Object.values(data.counts).reduce((a, n) => a + n, 0);
  const pick = s => { current = s; setStatus(s); };
  return html`
    <${PageHero} eyebrow=${t("findings.eyebrow")} title=${t("findings.hero", { n: total })} lede=${t("findings.lede")}
      term=${{ title: "~/codecome — itemdb", cmd: "ls itemdb/findings/",
        lines: STATUSES.map(st => [st + "/", FS_TC[st], String(data.counts[st] || 0), FS_TC[st]]) }} />
    <div class="tabs pills"><button class=${status === "ALL" ? "on" : ""} onClick=${() => pick("ALL")}>${t("findings.tab", { name: t("findings.all"), n: total })}</button>${STATUSES.map(s => html`<button class=${s === status ? "on" : ""} onClick=${() => pick(s)}>
      ${t("findings.tab", { name: t("fstatus." + s), n: data.counts[s] })}</button>`)}</div>
    ${rows.length ? html`<div class="tablecard"><table>
      <tr><th>${t("findings.col.id")}</th><th>${t("findings.col.title")}</th><th>${t("matrix.status")}</th><th>${t("findings.col.severity")}</th><th>${t("findings.col.confidence")}</th></tr>
      ${rows.map(f => html`<tr class="row" onClick=${() => navigate("/finding/" + f.id)}>
        <td class="mono">${f.id}</td><td>${f.title}</td><td>${fchip(f.status)}</td><td>${schip(f.severity)}</td><td class="small mut">${f.confidence}</td></tr>`)}
    </table></div>` : html`<p class="mut">${status === "ALL" ? t("matrix.empty") : t("findings.empty", { status: t("fstatus." + status) })}</p>`}`;
}

export function Finding({ id }) {
  const { data: f, error } = useLoad(() => api("/api/findings/" + encodeURIComponent(id)), [id]);
  if (error) return html`<p class="c-bad">${error.message}</p>`;
  if (!f) return null;
  return html`
    <${PageHero} eyebrow=${f.id} title=${f.title} extraBack=${html` <span class="mut">·</span> <a href="#/findings">${t("finding.back")}</a>`}
      lede=${html`${fchip(f.status)} ${schip(f.severity)} <span>${t("findings.col.confidence")}: ${f.confidence || "—"} · ${f.category || ""}</span>`}
      term=${{ title: "~/codecome — itemdb", cmd: `cat itemdb/findings/${f.status}/${f.id}…`,
        lines: [["status:", "tc-dim", f.status, FS_TC[f.status]], ["severity:", "tc-dim", f.severity || "—"],
                ["confidence:", "tc-dim", f.confidence || "—"], ["recording:", "tc-dim", f.has_cast || f.has_recording ? "✓" : "—", f.has_cast || f.has_recording ? "tc-green" : "tc-dim"]] }} />
    ${Object.entries(f.marks || {}).map(([ph, m]) => html`<div class="assumebox">
      <b><${Icon} name="warn" /> ${t("report.assumed")}</b> · ${t(`phase.${ph}.title`)} · <span class="mut">${t("matrix.assumed_by", { who: m.by, when: new Date(m.at * 1000).toLocaleString() })}</span>
      <div class="why">${m.reason}</div></div>`)}
    <p class="small mut">${t("report.untrusted")}</p>
    ${f.has_cast && html`<${CastPlayer} id=${f.id} />`}
    ${f.has_recording && (f.has_cast
      ? html`<details class="small"><summary>${t("cast.gif")}</summary><figure class="rec"><img src=${`/api/findings/${f.id}/recording.gif`} alt=${t("report.recording", { id: f.id })} loading="lazy" /></figure></details>`
      : html`<figure class="rec"><img src=${`/api/findings/${f.id}/recording.gif`} alt=${t("report.recording", { id: f.id })} loading="lazy" />
      <figcaption>${t("report.recording", { id: f.id })}</figcaption></figure>`)}
    ${f.tree ? html`<${Markdown} tree=${f.tree} />` : html`<pre>${f.body}</pre>`}`;
}
