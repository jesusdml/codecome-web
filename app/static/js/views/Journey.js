// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api, post } from "/static/js/api.js";
import { t, has } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";
import { secs, chip, phaseName, profileName, launchError } from "/static/js/format.js";
import { setProfiles, selectedProfile, ModelPick, pickedModel, modelLabel, isLocal } from "/static/js/profiles.js";
import { Log } from "/static/js/views/Jobs.js";
import { reasonText as liveText, LSTATE } from "/static/js/format.js";
import { Dashboard } from "/static/js/components/Charts.js";
import { AssumeDialog } from "/static/js/components/AssumeDialog.js";

// Design doc section 2: one screen, six steps, every card explains itself.
const STATUS_CLS = { none: "mut", queued: "mut", running: "info", done: "ok", failed: "bad", partial: "info", assumed: "warn", na: "mut" };
const title = k => has(`phase.${k}.title`) ? t(`phase.${k}.title`) : k;
const statusChip = s => chip(t("status." + s), STATUS_CLS[s] || "mut");
const num = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);

export function reasonText(r) {
  const p = { ...r.params };
  if (r.code === "needs_phase") p.phase = `${title(p.phase)} (${p.phase})`;
  if (r.code === "gate_unavailable") p.reason = t("reason.gate_unavailable." + p.reason);
  if (r.code === "finding_wrong_state") p.action = phaseName(p.action);
  return has("reason." + r.code) ? t("reason." + r.code, p) : r.code;
}

function Blocked({ reasons }) {
  const [open, setOpen] = useState(false);
  const detail = reasons.find(r => r.params && r.params.detail);
  return html`<div class="blocked">
    <span class="lock"><${Icon} name="lock" /></span> ${reasonText(reasons[0])}
    ${(reasons.length > 1 || detail) && html` <a href="#" class="small" onClick=${e => { e.preventDefault(); setOpen(!open); }}>${t("card.why")} ${open ? "▾" : "▸"}</a>`}
    ${open && html`<ul class="small">${reasons.map(r => html`<li>${reasonText(r)}</li>`)}</ul>
      ${detail && html`<details class="small"><summary>${t("card.detail")}</summary><pre>${detail.params.detail}</pre></details>`}`}
  </div>`;
}

async function launch(action, finding, key, repeat, setMsg) {
  const model = pickedModel(key);
  let q = t("jobs.confirm_launch", { phase: finding ? `${title(key)} · ${finding}` : title(key), profile: profileName(selectedProfile()) });
  if (model) q += "\n" + t("model.label") + ": " + modelLabel(model);
  if (model && isLocal(model)) q += "\n\n⚠ " + t("settings.local_warn");
  if (repeat) q = t(`phase.${key}.repeat`) + "\n\n" + q;
  if (!confirm(q)) return;
  try { await post("/api/jobs", { action, finding }); setMsg(""); }  // profile and model: from Settings
  catch (e) { setMsg(launchError(e)); }
}

function LastRun({ p }) {
  const l = p.last_run;
  if (!l) return html`<span class="mut">${t("card.never")}</span>`;
  const tk = l.tokens || {};
  const tokens = tk.input != null ? ` · ${num((tk.input || 0) + (tk.output || 0))} tokens` : "";
  return html`<span><a href=${"#/job/" + l.job}>#${l.job}</a> · ${profileName(l.profile)}${l.elapsed != null ? " · " + secs(l.elapsed) : ""}${tokens}</span>
    ${p.reference && html`<div class="small mut">${t("card.reference", { time: secs(p.reference.median_elapsed), profile: profileName(p.reference.profile) })}</div>`}`;
}

// Follow a running job from the home page: state, time, tokens, last tool, and the output on demand.
function LiveLine({ run }) {
  const [open, setOpen] = useState(false);
  const l = run.live || {}, tk = run.tokens || {};
  const toks = tk.input != null ? num((tk.input || 0) + (tk.output || 0)) + " tokens" : null;
  return html`<div class="liveline">
    <span class="dotlive"></span>
    ${l.state && chip(has("state." + l.state) ? t("state." + l.state) : l.state, LSTATE[l.state] || "mut")}
    <span><${Icon} name="clock" /> ${secs(run.elapsed || 0)}</span>${toks && html`<span>· ${toks}</span>`}
    ${l.last_tool && html`<span class="mut">· <${Icon} name="wrench" /> ${l.last_tool}</span>`}
    ${l.state && html`<span class="mut small">· ${liveText(l)}</span>`}
    <span class="grow"></span>
    <a href="#" class="small" onClick=${e => { e.preventDefault(); setOpen(!open); }}>${t("card.output")} ${open ? "▾" : "▸"}</a>
    ${open && html`<div class="liveout"><${Log} id=${run.job} short=${true} /></div>`}
  </div>`;
}

function Field({ k, children }) {
  return html`<div class="field"><div class="fk">${t("card." + k)}</div><div class="fv">${children}</div></div>`;
}

function Actions({ p, primary }) {
  const [msg, setMsg] = useState("");
  const L = p.launch;
  const done = p.status === "done";
  const label = p.status === "failed" ? t("card.retry") : done ? t("card.repeat") : t("card.launch");
  return html`<div class="actions">
    ${L.allowed
      ? html`<button class=${"btn" + (primary ? " primary" : "")} onClick=${() => launch("phase-" + p.key, null, p.key, done, setMsg)}><${Icon} name="play" /> ${label}</button>`
      : html`<${Blocked} reasons=${L.reasons} />`}
    ${p.key === "6" && p.artifacts && p.artifacts.found > 0 && html` <a class="btn" href="#/report"><${Icon} name="file" /> ${t("report.open")}</a>`}
    ${p.last_run && (p.last_run.status === "running" || p.last_run.status === "queued") &&
      html` <a class="small" href=${"#/job/" + p.last_run.job}>${t("card.view_job")} ›</a>`}
    ${msg && html`<div class="c-bad small">${msg}</div>`}
  </div>`;
}

function Steps({ steps }) {
  return html`<ol class="substeps">${steps.map(s => html`<li class=${"sub s-" + s.status}>
    <span class="dot"></span><b>${s.key}</b> ${title(s.key)} ${statusChip(s.status)}
    <span class="small mut"> · ${t("card.artifacts", { found: s.artifacts.found, expected: s.artifacts.expected })}</span>
    ${s.external && html` <span class="small mut">· ${t("card.external")}</span>`}
  </li>`)}</ol>`;
}

function Card({ p, primary }) {
  return html`<section class=${"card st-" + p.status}>
    <header><span class="num">${t("learn.phase")} ${p.key.padStart(2, "0")}</span><h3>${title(p.key)}</h3><span class="grow"></span>
      <${ModelPick} phase=${p.key} used=${p.last_run && p.last_run.model}
        locked=${p.last_run && (p.last_run.status === "running" || p.last_run.status === "queued")} />
      ${statusChip(p.status)}</header>
    ${p.last_run && p.last_run.status === "running" && html`<${LiveLine} run=${p.last_run} />`}
    <${Field} k="does">${t(`phase.${p.key}.does`)}</${Field}>
    <${Field} k="scope">${t("card.scope." + p.scope.kind)}</${Field}>
    <${Field} k="needs">${t(`phase.${p.key}.needs`)}</${Field}>
    <${Field} k="produces">${t(`phase.${p.key}.produces`)}
      ${p.artifacts && p.artifacts.expected > 0 && html` <span class="small mut">(${t("card.artifacts", { found: p.artifacts.found, expected: p.artifacts.expected })})</span>`}</${Field}>
    ${p.steps && html`<${Steps} steps=${p.steps} />`}
    <${Field} k="last"><${LastRun} p=${p} /></${Field}>
    <${Actions} p=${p} primary=${primary} />
  </section>`;
}

function Cell({ row, k }) {
  const [msg, setMsg] = useState(""), [asking, setAsking] = useState(false);
  const c = row["p" + k];
  if (c.status === "done")  // opens the job that produced it, to review before the next phase
    return c.job ? html`<a class="plain" href=${"#/job/" + c.job} title=${"#" + c.job}>${statusChip("done")}</a>` : html`${statusChip("done")}`;
  if (c.status === "running" || c.status === "queued")
    return html`${statusChip(c.status)} <a class="small" href=${"#/job/" + c.job}>#${c.job}</a>`;
  if (c.status === "na") return html`<span class="small mut">${t("status.na")}</span>`;
  if (c.status === "assumed") {
    const undo = async () => {
      try { await api(`/api/findings/${row.id}/assume/${k}`, { method: "DELETE" }); } catch (x) { setMsg(x.message); }
    };
    // The reason can be long: it opens in its own window (view, then edit), never inline in the table.
    return html`<button class="chipbtn" title=${t("assume.view")} onClick=${() => setAsking(true)}>${chip(t("status.assumed"), "warn")}</button>
      <button class="btn small grey" onClick=${undo}>${t("matrix.undo")}</button>
      ${asking && html`<${AssumeDialog} id=${row.id} phase=${k} phaseName=${title(k)} mark=${c.mark || {}} onClose=${() => setAsking(false)} />`}
      ${msg && html`<div class="c-bad small">${msg}</div>`}`;
  }
  // Assume = accept without proof (with a reason); only where this phase would apply to the finding.
  const canAssume = (k === "4" && row.status === "PENDING") || (k === "5" && row.status === "CONFIRMED");
  const assume = canAssume && html` <button class="btn small ghost" onClick=${() => setAsking(true)}>${t("matrix.assume")}</button>
    ${asking && html`<${AssumeDialog} id=${row.id} phase=${k} phaseName=${title(k)} onClose=${() => setAsking(false)} />`}`;
  if (c.launch.allowed)
    return html`<button class="btn small" onClick=${() => launch("phase-" + k, row.id, k, false, setMsg)}><${Icon} name="play" /> ${t("card.launch")}</button>${assume}
      ${msg && html`<div class="c-bad small">${msg}</div>`}`;
  // A blocked cell only says why when it is not simply "not this finding's turn".
  const r = c.launch.reasons[0];
  if (!r) return assume || null;
  return html`<span class="small mut" title=${reasonText(r)}><${Icon} name="lock" /> ${r.code === "finding_wrong_state" ? t("matrix.needs", { needs: r.params.needs }) : reasonText(r)}</span>${assume}`;
}

const SEV = { CRITICAL: "bad", HIGH: "warn", MEDIUM: "info", LOW: "mut", INFO: "mut" };

function Matrix({ rows, p4, p5, active }) {
  const live = rows.filter(r => !r.discarded), gone = rows.filter(r => r.discarded);
  const table = rs => html`<table class="matrix">
    <tr><th>${t("matrix.finding")}</th><th>${t("matrix.severity")}</th><th>${t("matrix.status")}</th><th>${t("matrix.p4")}<br /><${ModelPick} phase="4" /></th><th>${t("matrix.p5")}<br /><${ModelPick} phase="5" /></th></tr>
    ${rs.map(r => html`<tr>
      <td><a href=${"#/finding/" + r.id}>${r.id}</a> <span class="mut">${r.title}</span></td>
      <td>${chip(r.severity || "—", SEV[r.severity] || "mut")}</td>
      <td class="small">${t("fstatus." + r.status)}</td>
      <td><${Cell} row=${r} k="4" /></td><td><${Cell} row=${r} k="5" /></td></tr>`)}
  </table>`;
  return html`<section class=${"card wide st-" + (p4.status === "running" || p5.status === "running" ? "running" : p5.status)}>
    <header><span class="num">${t("learn.phase")} 04·05</span><h3>${t("card.45.title")}</h3><span class="grow"></span>
      <span class="small mut">${t("matrix.counts", { v: p4.status_counts.done || 0, e: p5.status_counts.done || 0, n: live.length })}</span>
      <span class="small">4 ${statusChip(p4.status)} 5 ${statusChip(p5.status)}</span></header>
    <${Field} k="does">${t("phase.4.does")} ${t("phase.5.does")}</${Field}>
    ${active && (active.phase === "4" || active.phase === "5") && active.run &&
      html`<div class="small"><b>${active.finding}</b> · ${title(active.phase)}</div><${LiveLine} run=${active.run} />`}
    ${rows.length ? table(live) : html`<p class="mut">${t("matrix.empty")}</p>`}
    ${gone.length > 0 && html`<details><summary class="small">${t("matrix.discarded", { n: gone.length })}</summary>${table(gone)}</details>`}
  </section>`;
}

function Progress({ phases }) {
  return html`<div class="progress" role="img" aria-label=${t("journey.progress", { done: phases.filter(p => p.status === "done").length, total: 6 })}>
    ${phases.map(p => html`<div class=${"seg s-" + p.status}><span>${p.key}</span>
      ${p.steps && html`<span class="ticks">${p.steps.map(s => html`<i class=${"s-" + s.status} title=${`${s.key} · ${title(s.key)} · ${t("status." + s.status)}`}>${s.key.slice(1)}</i>`)}</span>`}</div>`)}
  </div>`;
}

// The CodeCome look: the six phases as the terminal you would type them in, with their real state.
const TERM_ICON = { done: "✓", running: "●", queued: "●", failed: "✗", partial: "◐", none: "○" };
function PhaseTerminal({ phases, project, active }) {
  return html`<div class="term-card" aria-label=${t("hero.term")}>
    <div class="bar3"><i></i><i></i><i></i><span>~/codecome${project ? " — " + project : ""}</span></div>
    <ol>${phases.map(p => {
      const fin = (p.key === "4" || p.key === "5") ? (active && active.phase === p.key ? active.finding : "CC-…") : null;
      return html`<li><span class="ps">$</span><span class="cmd">make phase-${p.key}</span>${fin && html`<span class="arg">FINDING=${fin}</span>`}
        <span class=${"st st-" + p.status} title=${t("status." + p.status)}>${TERM_ICON[p.status] || "○"} ${t("status." + p.status)}</span></li>`;
    })}</ol>
  </div>`;
}

function Hero({ d }) {
  const done = d.progress.phases_done, total = d.progress.phases_total;
  const eyebrow = d.next.code === "all_done" ? t("hero.done") : d.active ? t("hero.running") : t("hero.ready");
  return html`<section class="hero">
    <div>
      <span class="eyebrow">${eyebrow}</span>
      <h1>${d.project || t("hero.untitled")}</h1>
      <p class="lede"><${NextBanner} next=${d.next} inline=${true} /></p>
      <div class="meta"><span><b>${done}/${total}</b> ${t("hero.phases_done")}</span>
        <span title=${t("settings.active_profile_hint")}>${t("settings.active_profile")}: <b>${profileName(selectedProfile())}</b></span></div>
    </div>
    <${PhaseTerminal} phases=${d.phases} project=${d.project} active=${d.active} />
  </section>`;
}

function NextBanner({ next, inline }) {
  const p = { ...next.params };
  if (p.phase) p.phase = `${p.phase} · ${title(p.phase)}`;
  const text = t("next." + next.code, p);
  const extra = next.code === "blocked" ? reasonText(next.params.reason) : null;
  if (inline) return html`<span>${t("journey.next")}: ${next.code === "running"
      ? html`<a href=${"#/job/" + next.params.job}>${text}</a>` : text}${extra ? " — " + extra : ""}</span>`;
  return html`<div class=${"next n-" + next.code}>
    <span class="nk">${t("journey.next")}</span> ${next.code === "running"
      ? html`<a href=${"#/job/" + next.params.job}>${text}</a>` : text}
    ${extra && html`<span class="small"> — ${extra}</span>`}
  </div>`;
}

export function Journey() {
  const { data: d, error } = usePoll(async () => {
    const r = await api("/api/audit");
    setProfiles({ default_profile: r.profiles.default, profiles: r.profiles.list, models: r.profiles.models });
    return r;
  }, 2000);
  if (error && !d) return html`<p class="c-bad">${error.message}</p>`;
  if (!d) return html`<p class="mut">…</p>`;
  const by = Object.fromEntries(d.phases.map(p => [p.key, p]));
  const primaryKey = d.next.code === "launch_phase" ? d.next.params.phase : null;
  return html`<div class="journey">
    <${Hero} d=${d} />
    <${Progress} phases=${d.phases} />
    <${Dashboard} matrix=${d.matrix} />
    <div class="ladder">
      ${["1", "2", "3"].map(k => html`<${Card} p=${by[k]} primary=${k === primaryKey} />`)}
      <${Matrix} rows=${d.matrix} p4=${by["4"]} p5=${by["5"]} active=${d.active} />
      <${Card} p=${by["6"]} primary=${primaryKey === "6"} />
    </div>
  </div>`;
}
