// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect, useRef } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api, post, follow } from "/static/js/api.js";
import { t, errText } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";
import { STATUSES, chip } from "/static/js/format.js";
import { PageHero } from "/static/js/components/PageHero.js";

// Archive: the history of archived audits, read from the worker's unencrypted cards.
// Archiving lives in Settings; here admins can download an archive or RESCUE it into an empty
// workspace (the worker decrypts, checks every entry and the manifest, then moves it in place).
const FS_CLS = { PENDING: "info", CONFIRMED: "warn", EXPLOITED: "bad", REJECTED: "mut", DUPLICATE: "mut" };
const PH_CLS = { done: "ok", partial: "warn", failed: "bad", running: "info", queued: "info" };

const size = b => b == null ? "—" : b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB"
  : b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
const when = s => s ? new Date(s * 1000).toLocaleString() : "—";

function Phases({ p }) {
  return html`<span class="phticks">${["1", "2", "3", "6"].map(k =>
    html`<i class=${"c-" + (PH_CLS[p[k]] || "mut")} title=${`${k} · ${t(`phase.${k}.title`)}: ${t("status." + (p[k] || "none"))}`}>${k}</i>`)}</span>`;
}

function RescueDialog({ a, onClose }) {
  const [pw, setPw] = useState(""), [busy, setBusy] = useState(false), [ok, setOk] = useState(null), [err, setErr] = useState("");
  const box = useRef(null);
  useEffect(() => {
    const esc = e => { if (e.key === "Escape" && !busy) onClose(!!ok); };
    document.addEventListener("keydown", esc);
    if (box.current) box.current.focus();
    return () => document.removeEventListener("keydown", esc);
  }, [busy, ok]);
  const go = async e => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await post(`/api/archives/${a.id}/rescue`, { password: pw });
      setPw("");
      const job = await follow(r.id);
      if (job.status === "done") setOk(job.result || {});
      else setErr(job.error ? errText(job.error) : t("upload.failed"));
    } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  return html`<div class="modalbg" onMouseDown=${e => { if (e.target === e.currentTarget && !busy) onClose(!!ok); }}>
    <form class="modal" role="dialog" aria-modal="true" aria-labelledby="rescue-title" onSubmit=${go}>
      <h3 id="rescue-title"><${Icon} name="box" /> ${t("rescue.title")} <span class="mut">· ${a.project || a.id}</span></h3>
      <p class="small mut mono">${a.id}</p>
      ${ok ? html`
        <p class="c-ok">${t("rescue.done", { files: ok.files, jobs: ok.jobs, assumed: ok.assumed })}</p>
        ${ok.config === "kept_current" && html`<p class="small c-warn">${t("rescue.config_kept")}</p>`}
        <div class="modalbtns"><a class="btn primary" href="#/">${t("report.back")} →</a></div>` : html`
        <p class="small">${t("rescue.hint")}</p>
        <label class="small"><b>${t("rescue.password")}</b>
          <input ref=${box} type="password" class="pwfield" autocomplete="off" disabled=${busy} value=${pw}
            onInput=${e => { setPw(e.target.value); setErr(""); }} /></label>
        ${busy && html`<p class="small">${t("rescue.working")}</p>`}
        ${err && html`<p class="c-bad small">${err}</p>`}
        <div class="modalbtns">
          <button type="button" class="btn" disabled=${busy} onClick=${() => onClose(false)}>${t("assume.cancel")}</button>
          <button class="btn primary" disabled=${busy || !pw}><${Icon} name="box" /> ${t("rescue.btn")}</button>
        </div>`}
    </form>
  </div>`;
}

export function Archive() {
  const { data: d, error, reload } = usePoll(() => api("/api/archives"), 15000);
  const [rescuing, setRescuing] = useState(null);
  if (error && !d) return html`<p class="c-bad">${errText(error.detail) || error.message}</p>`;
  if (!d) return html`<p class="mut">…</p>`;
  const rows = d.archives;
  return html`
    <${PageHero} eyebrow=${t("archive.eyebrow")} title=${rows.length === 1 ? t("archive.hero_one") : t("archive.hero", { n: rows.length })} lede=${t("archive.lede")}
      term=${{ title: "~/codecome-archives", cmd: "ls -t *.tar.gz.gpg",
        lines: rows.length ? rows.slice(0, 5).map(a => [a.id + ".tar.gz.gpg", "cmd", size(a.size), "tc-dim"])
          : [[t("archive.empty_term"), "tc-dim"]] }} />
    ${rows.length ? html`<div class="tablecard"><div class="tablewrap"><table class="archives">
      <tr><th>${t("archive.col.date")}</th><th>${t("archive.col.project")}</th><th>${t("archive.col.phases")}</th>
        <th>${t("archive.col.findings")}</th><th>${t("archive.col.content")}</th><th>${t("archive.col.cost")}</th><th></th></tr>
      ${rows.map(a => html`<tr>
        <td><div>${when(a.created_at)}</div><div class="small mut">${t("archive.by", { who: a.by || "?" })}</div></td>
        <td><b>${a.project || "—"}</b><div class="small mut mono" title=${"sha256 " + (a.sha256 || "")}>${a.id}</div></td>
        <td><${Phases} p=${a.phases} /></td>
        <td>${STATUSES.filter(s => a.findings[s]).map(s => html`${chip(`${t("fstatus." + s)} ${a.findings[s]}`, FS_CLS[s])} `)}
          ${!Object.keys(a.findings).length && html`<span class="mut">—</span>`}
          ${a.assumed ? html`<div class="small mut"><${Icon} name="warn" /> ${t("archive.assumed", { n: a.assumed })}</div>` : null}</td>
        <td class="small">${t("archive.files", { n: a.files ?? "?" })} · ${size(a.bytes)}
          <div class="mut">${t("archive.encrypted", { size: size(a.size) })}</div></td>
        <td class="mono small">${a.cost_usd == null ? "—" : "$" + a.cost_usd.toFixed(2)}
          ${a.models && a.models.length ? html`<div class="mut models">${a.models.join(" · ")}</div>` : null}</td>
        <td class="actions">${d.can_rescue && html`<button class="btn small primary" disabled=${!d.workspace_empty}
            title=${d.workspace_empty ? t("rescue.hint_short") : t("rescue.not_empty")} onClick=${() => setRescuing(a)}>
            <${Icon} name="box" /> ${t("rescue.btn")}</button> `}
          ${d.can_download && html`<a class="btn small" href=${`/api/archives/${a.id}/file`} download
          title=${t("archive.download_hint")}><${Icon} name="download" /> ${t("archive.download")}</a>`}</td>
      </tr>`)}
    </table></div></div>
    ${d.can_rescue && !d.workspace_empty && html`<p class="small c-warn">${t("rescue.not_empty")}</p>`}
    ${rescuing && html`<${RescueDialog} a=${rescuing} onClose=${done => { setRescuing(null); if (done && reload) reload(); }} />`}
    <p class="small mut"><${Icon} name="lock" /> ${t("archive.how_open")} <code>gpg -d ${rows[0].id}.tar.gz.gpg | tar xz</code></p>`
    : html`<div class="learn"><p class="mut">${t("archive.none")}</p></div>`}`;
}
