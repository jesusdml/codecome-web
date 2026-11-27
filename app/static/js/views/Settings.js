// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api, post, follow, ApiError } from "/static/js/api.js";
import { t, errText } from "/static/js/i18n.js";
import { LangPicker } from "/static/js/components/LangPicker.js";
import { profileName } from "/static/js/format.js";

const PH = ["1", "2", "3", "4", "5", "6"];
const phTitle = k => `${k} · ${t(`phase.${k}.title`)}`;

// The page edits a draft and sends it whole; the server re-validates everything against its own lists.
function draftFrom(d) {
  return {
    default_lang: d.default_lang,
    active_profile: d.default_profile,
    models: Object.fromEntries(d.catalog.map(m => [m.name, { enabled: m.enabled, phases: [...m.phases] }])),
    profile_models: Object.fromEntries(d.profiles.map(p => [p.name, { ...p.models }])),
  };
}

function LocalWarn() {
  return html`<div class="warnbox"><${Icon} name="warn" /> ${t("settings.local_warn")}</div>`;
}

const ARCHIVE_RE = /\.(zip|tgz|tar\.gz)$/i;

// PUT the raw file. XHR and not fetch: fetch has no upload progress.
function putFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", "/api/upload");
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let b = {};
      try { b = JSON.parse(x.responseText); } catch (e) { /* not JSON: generic error below */ }
      if (x.status === 401) { window.dispatchEvent(new Event("auth-lost")); reject(new Error("login")); return; }
      if (x.status >= 200 && x.status < 300) resolve(b); else reject(new ApiError(b.detail));
    };
    x.onerror = () => reject(new Error(t("upload.net_error")));
    x.send(file);
  });
}



// Empty WITHOUT archiving: folded away under the archive button, its own confirmation
// (type the project name + "everything is deleted for good"). No password: nothing is kept.
function ResetBox({ d, nothing, onDone }) {
  const [name, setName] = useState(""), [ack, setAck] = useState(false), [busy, setBusy] = useState(false);
  const [ok, setOk] = useState(""), [bad, setBad] = useState("");
  const ready = !busy && name.trim() === d.archive_confirm && ack;
  const go = async () => {
    if (!confirm(t("reset.last_chance", { name: d.archive_confirm }))) return;
    setBad(""); setOk(""); setBusy(true);
    try {
      const r = await post("/api/reset", { confirm: name.trim(), understood: ack });
      const job = await follow(r.id);
      if (job.status === "done") { setOk(t("reset.done", { files: (job.result || {}).files })); setName(""); setAck(false); onDone(); }
      else setBad(job.error ? errText(job.error) : t("upload.failed"));
    } catch (e) { setBad(e.message); }
    setBusy(false);
  };
  if (nothing && !ok) return null;  // nothing to empty (but keep showing the result once done)
  return html`<details class="resetbox" open=${!!ok || undefined}>
    <summary>${t("reset.summary")}</summary>
    <p class="small">${t("reset.hint")}</p>
    <label class="small">${t("archive.type_name", { name: d.archive_confirm })}
      <input class="pwfield mono" autocomplete="off" spellcheck="false" disabled=${busy} value=${name}
        placeholder=${d.archive_confirm} onInput=${e => setName(e.target.value)} /></label>
    <label class="small check"><input type="checkbox" disabled=${busy} checked=${ack} onChange=${e => setAck(e.target.checked)} />
      ${t("reset.ack")}</label>
    ${busy && html`<p class="small">${t("reset.working")}</p>`}
    ${ok && html`<p class="small c-ok">${ok}</p>`}
    ${bad && html`<p class="small c-bad">${bad}</p>`}
    <button class="btn danger outline" disabled=${!ready} onClick=${go}><${Icon} name="reset" /> ${t("reset.btn")}</button>
  </details>`;
}

// Archive & reset: admin only. Typing the project name + the password twice + the "I understand"
// box. The password is never stored: if it is lost, the archive cannot be opened.
function ArchiveCard({ d, onDone }) {
  const [name, setName] = useState(""), [pw, setPw] = useState(""), [pw2, setPw2] = useState("");
  const [ack, setAck] = useState(false), [busy, setBusy] = useState(false), [ok, setOk] = useState(""), [bad, setBad] = useState("");
  const min = d.archive_min_password;
  const nothing = d.src_empty;
  const problem = !name ? "" : name.trim() !== d.archive_confirm ? t("archive.name_mismatch")
    : pw && pw.length < min ? t("error.password_too_short", { min }) : pw2 && pw !== pw2 ? t("archive.pw_mismatch") : "";
  const ready = d.can_edit && !busy && name.trim() === d.archive_confirm && pw.length >= min && pw === pw2 && ack;
  const go = async () => {
    setBad(""); setOk(""); setBusy(true);
    try {
      const r = await post("/api/archive", { confirm: name.trim(), password: pw });
      setPw(""); setPw2("");
      const job = await follow(r.id);
      if (job.status === "done") {
        setOk(t("archive.done", { id: (job.result || {}).archive, files: (job.result || {}).files }));
        setName(""); setAck(false); onDone();
      } else setBad(job.error ? errText(job.error) : t("upload.failed"));
    } catch (e) { setBad(e.message); }
    setBusy(false);
  };
  const off = !d.can_edit || busy;
  return html`<section class="learn danger archive">
    <h3>${t("settings.reset")}</h3>
    <p class="small mut">${t("archive.hint")}</p>
    ${!d.can_edit && html`<p class="small c-warn">${t("archive.admin_only")}</p>`}
    ${d.can_edit && nothing && html`<p class="small mut">${t("archive.nothing")}</p>`}
    <label class="small">${t("archive.type_name", { name: d.archive_confirm })}
      <input class="pwfield mono" autocomplete="off" spellcheck="false" disabled=${off} value=${name}
        placeholder=${d.archive_confirm} onInput=${e => setName(e.target.value)} /></label>
    <label class="small">${t("archive.password", { min })}
      <input type="password" class="pwfield" autocomplete="new-password" disabled=${off} value=${pw} onInput=${e => setPw(e.target.value)} /></label>
    <label class="small">${t("archive.password2")}
      <input type="password" class="pwfield" autocomplete="new-password" disabled=${off} value=${pw2} onInput=${e => setPw2(e.target.value)} /></label>
    <label class="small check"><input type="checkbox" disabled=${off} checked=${ack} onChange=${e => setAck(e.target.checked)} />
      ${t("archive.ack")}</label>
    <p class="small mut">${t("archive.http_note")}</p>
    ${problem && html`<p class="small c-warn">${problem}</p>`}
    ${busy && html`<p class="small">${t("archive.working")}</p>`}
    ${ok && html`<p class="small c-ok">${ok}</p>`}
    ${bad && html`<p class="small c-bad">${bad}</p>`}
    <button class="btn danger" disabled=${!ready} onClick=${go}><${Icon} name="reset" /> ${t("settings.reset_btn")}</button>
    ${d.can_edit && html`<${ResetBox} d=${d} nothing=${nothing} onDone=${onDone} />`}
  </section>`;
}

// Upload code: admin only, src/ must be empty. The browser sends the file; the WORKER extracts it
// (hardened extractor) as an "upload" job, so this card just follows that job to the end.
function UploadCard({ d, onDone }) {
  const [file, setFile] = useState(null), [pw, setPw] = useState(""), [stage, setStage] = useState(""), [proj, setProj] = useState("");
  const [pct, setPct] = useState(0), [ok, setOk] = useState(""), [bad, setBad] = useState(""), [over, setOver] = useState(false);
  const busy = stage === "sending" || stage === "extracting";
  const blocked = !d.can_edit ? t("upload.admin_only") : !d.src_empty ? t("upload.empty_src") : "";
  const off = !!blocked || busy;
  const pick = f => {
    setBad(""); setOk(""); setFile(null);
    if (!f) return;
    if (!ARCHIVE_RE.test(f.name)) { setBad(t("upload.bad_ext")); return; }
    if (f.size > d.upload_max_mb * 1048576) { setBad(t("error.upload_too_large", { max_mb: d.upload_max_mb })); return; }
    setFile(f);
    // A starting point for the new audit's name: the file name without extension or "-main"/"-master".
    setProj(f.name.replace(/\.(zip|tgz|tar\.gz)$/i, "").replace(/[-_](main|master)$/i, "").replace(/[^\p{L}\p{N} ._-]+/gu, "-").slice(0, 80));
  };
  const projOk = /^[\p{L}\p{N}_][\p{L}\p{N}_ .-]{0,79}$/u.test(proj.trim()) && !/[ .]$/.test(proj.trim());
  const isZip = file && /\.zip$/i.test(file.name);
  const go = async () => {
    setBad(""); setOk(""); setStage("sending"); setPct(0);
    try {
      const up = await putFile(file, setPct);
      setStage("extracting");
      const r = await post(`/api/upload/${up.id}/extract`, { project: proj.trim(), ...(isZip && pw ? { password: pw } : {}) });
      setPw("");
      const job = await follow(r.id);
      if (job.status === "done") {
        const res = job.result || {};
        setStage("done"); setFile(null);
        setOk(t("upload.done", { files: res.files, mb: ((res.bytes || 0) / 1048576).toFixed(1) }) + " " + t("upload.named", { name: res.project || proj }));
        onDone();
      } else {
        setStage(""); setBad(job.error ? errText(job.error) : t("upload.failed"));
      }
    } catch (e) { setStage(""); setBad(e.message); }
  };
  const drop = e => { e.preventDefault(); setOver(false); if (!off) pick(e.dataTransfer.files[0]); };
  return html`<section class="learn upload">
    <h3>${t("upload.title")}</h3>
    <p class="small mut">${t("upload.hint")}</p>
    <label class=${"dropzone" + (off ? "" : " live") + (over ? " over" : "")} aria-disabled=${off ? "true" : "false"}
      onDragOver=${e => { e.preventDefault(); if (!off) setOver(true); }} onDragLeave=${() => setOver(false)} onDrop=${drop}>
      <input type="file" hidden disabled=${off} accept=".zip,.tgz,.tar.gz,application/zip,application/gzip"
        onChange=${e => pick(e.target.files[0])} />
      <span class="dzicon"><${Icon} name="upload" /></span>
      <span>${file ? html`<b class="mono">${file.name}</b> · ${(file.size / 1048576).toFixed(1)} MB` : t("upload.drop")}</span>
      <span class="small mut mono">.tar.gz · .tgz · .zip · ≤ ${d.upload_max_mb} MB</span>
    </label>
    ${file && !busy && html`<label class="small">${t("upload.project")}
      <input class="pwfield" autocomplete="off" spellcheck="false" maxlength="80" value=${proj} onInput=${e => setProj(e.target.value)} /></label>
      <p class="small mut">${t("upload.project_hint")}</p>
      ${proj && !projOk && html`<p class="small c-warn">${t("error.project_name_invalid")}</p>`}`}
    ${isZip && !busy && html`<label class="small">${t("upload.password")}
      <input type="password" class="pwfield" autocomplete="off" value=${pw} onInput=${e => setPw(e.target.value)} /></label>
      <p class="small mut">${t("upload.password_hint")}</p>`}
    ${blocked && html`<p class="small c-warn">${blocked}</p>`}
    ${stage === "sending" && html`<p class="small"><progress max="1" value=${pct}></progress> ${t("upload.sending", { pct: Math.round(pct * 100) })}</p>`}
    ${stage === "extracting" && html`<p class="small">${t("upload.extracting")}</p>`}
    ${ok && html`<p class="small c-ok">${ok} <a href="#/">${t("report.back")} →</a></p>`}
    ${bad && html`<p class="small c-bad">${bad}</p>`}
    <button class="btn primary" disabled=${off || !file || !projOk} onClick=${go}><${Icon} name="upload" /> ${t("upload.btn")}</button>
  </section>`;
}

export function Settings() {
  const [d, setD] = useState(null), [draft, setDraft] = useState(null);
  const [prof, setProf] = useState(null), [msg, setMsg] = useState(""), [err, setErr] = useState("");
  const load = async () => {
    try { const r = await api("/api/settings"); setD(r); setDraft(draftFrom(r)); setProf(p => p || r.default_profile); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);
  if (err && !d) return html`<p class="c-bad">${err}</p>`;
  if (!d || !draft) return html`<p class="mut">…</p>`;
  const ro = !d.can_edit;
  const byName = Object.fromEntries(d.catalog.map(m => [m.name, m]));
  const set = f => { setDraft(f(JSON.parse(JSON.stringify(draft)))); setMsg(""); };
  const save = async () => {
    try {
      const r = await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft) });
      setD(r); setDraft(draftFrom(r)); setMsg(t("settings.saved")); setErr("");
    } catch (e) { setErr(e.message); }
  };
  const usable = (name, k) => draft.models[name] && draft.models[name].enabled && draft.models[name].phases.includes(k);
  const pm = draft.profile_models[prof] || {};
  const localPicked = PH.some(k => pm[k] && byName[pm[k]] && byName[pm[k]].local);
  // One "Save all" under every row of cards: it always saves the whole page.
  const saveBar = () => ro ? null : html`<div class="savebar">
    ${msg && html`<span class="c-ok small">${msg}</span>`} ${err && html`<span class="c-bad small">${err}</span>`}
    <button class="btn primary" onClick=${save}><${Icon} name="save" /> ${t("settings.save_all")}</button></div>`;
  return html`<div class="settings">
    <p><a href="#/">← ${t("report.back")}</a></p>
    <h2>${t("settings.title")}</h2>
    ${ro && html`<div class="banner">${t("settings.readonly")}</div>`}


    <section class="learn">
      <h3>${t("settings.models")}</h3>
      <p class="small mut">${t("settings.models_hint")}</p>
      <div class="tablewrap"><table class="settable">
        <tr><th>${t("settings.model")}</th><th>${t("settings.provider")}</th><th>${t("settings.enabled")}</th>
          ${PH.map(k => html`<th class="ph" title=${phTitle(k)}>${k}</th>`)}</tr>
        ${d.catalog.map(m => {
          const dm = draft.models[m.name];
          return html`<tr class=${dm.enabled ? "" : "off"}>
            <td><b>${m.label}</b> ${m.local ? html`<span class="tag local" title=${t("settings.local_warn")}><${Icon} name="warn" /> ${t("settings.local")}</span>` : html`<span class="tag">${t("settings.cloud")}</span>`}</td>
            <td class="small mut">${m.provider}</td>
            <td><input type="checkbox" disabled=${ro} checked=${dm.enabled} aria-label=${t("settings.enabled") + " " + m.label}
              onChange=${e => set(x => (x.models[m.name].enabled = e.target.checked, x))} /></td>
            ${PH.map(k => html`<td class="ph"><input type="checkbox" disabled=${ro || !dm.enabled} checked=${dm.phases.includes(k)}
              aria-label=${m.label + " · " + phTitle(k)}
              onChange=${e => set(x => { const ps = new Set(x.models[m.name].phases); e.target.checked ? ps.add(k) : ps.delete(k);
                x.models[m.name].phases = PH.filter(p => ps.has(p)); return x; })} /></td>`)}
          </tr>`;
        })}
      </table></div>
    </section>
    ${saveBar()}

    <div class="setrow two">
      <section class="learn">
        <h3>${t("settings.active_profile")}</h3>
        <p class="small mut">${t("settings.active_profile_hint")}</p>
        <select disabled=${ro} value=${draft.active_profile} aria-label=${t("settings.active_profile")}
          onChange=${e => set(x => (x.active_profile = e.target.value, x))}>
          ${d.profiles.map(p => html`<option value=${p.name} disabled=${!p.enabled}>${profileName(p.name)}${p.enabled ? "" : t("profile.disabled_suffix")}</option>`)}
        </select>
      </section>
    <section class="learn">
      <h3>${t("settings.defaults")}</h3>
      <p class="small mut">${t("settings.defaults_hint")}</p>
      <label class="small">${t("settings.profile")} <select value=${prof} onChange=${e => setProf(e.target.value)}>
        ${d.profiles.map(p => html`<option value=${p.name}>${profileName(p.name)}</option>`)}</select></label>
      <div class="defaults">
        ${PH.map(k => {
          const cur = pm[k] || "";
          return html`<label class="defrow"><span>${phTitle(k)}</span>
            <select disabled=${ro} value=${cur} onChange=${e => set(x => { (x.profile_models[prof] = x.profile_models[prof] || {})[k] = e.target.value; return x; })}>
              ${!cur && html`<option value="">${t("settings.none")}</option>`}
              ${d.catalog.filter(m => usable(m.name, k) || m.name === cur).map(m =>
                html`<option value=${m.name}>${m.label}${m.local ? " ⚠" : ""}</option>`)}
            </select></label>`;
        })}
      </div>
      ${localPicked && html`<${LocalWarn} />`}
    </section>
    </div>
    ${saveBar()}

    <div class="setrow">
      <${ArchiveCard} d=${d} onDone=${load} />
      <${UploadCard} d=${d} onDone=${load} />
      <section class="learn">
        <h3>${t("settings.lang")}</h3>
        <p class="small mut">${t("settings.lang_hint")}</p>
        <${LangPicker} value=${draft.default_lang} disabled=${ro} label=${t("settings.lang")}
          onPick=${k => set(x => (x.default_lang = k, x))} />
      </section>
    </div>
    ${saveBar()}

    ${d.updated_by && html`<p class="small mut">${t("settings.updated", { who: d.updated_by, when: new Date(d.updated_at * 1000).toLocaleString() })}</p>`}
  </div>`;
}
