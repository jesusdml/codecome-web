// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect, useRef } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { post } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";

// "Assume" asks WHY: a finding accepted without proof must at least be documented.
// With `mark` it opens in view mode (reason, who, when) and can switch to editing the reason.
export function AssumeDialog({ id, phase, phaseName, mark, onClose }) {
  const [editing, setEditing] = useState(!mark);
  const [text, setText] = useState(mark ? mark.reason || "" : ""), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const area = useRef(null);
  useEffect(() => {
    const esc = e => { if (e.key === "Escape") onClose(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, []);
  useEffect(() => { if (editing && area.current) area.current.focus(); }, [editing]);
  const save = async e => {
    e.preventDefault();
    if (!text.trim()) { setErr(t("error.reason_required")); return; }
    setBusy(true);
    try { await post(`/api/findings/${id}/assume`, { phase, reason: text.trim() }); onClose(true); }
    catch (x) { setErr(x.message); setBusy(false); }
  };
  const meta = mark && t("matrix.assumed_by", { who: mark.by || "?", when: mark.at ? new Date(mark.at * 1000).toLocaleString() : "" });
  return html`<div class="modalbg" onMouseDown=${e => { if (e.target === e.currentTarget) onClose(false); }}>
    <form class="modal" role="dialog" aria-modal="true" aria-labelledby="assume-title" onSubmit=${save}>
      <h3 id="assume-title">${mark ? html`<${Icon} name="warn" /> ${t("report.assumed")}` : t("assume.title")} <span class="mut">· ${id} · ${phaseName}</span></h3>
      ${meta && html`<p class="small mut">${meta}</p>`}
      ${editing ? html`
        ${!mark && html`<p class="small mut">${t("assume.hint", { phase: phaseName })}</p>`}
        <label class="small"><b>${t("assume.reason")}</b>
          <textarea ref=${area} rows="6" maxlength="2000" value=${text} onInput=${e => { setText(e.target.value); setErr(""); }}></textarea></label>
        ${err && html`<p class="c-bad small">${err}</p>`}
        <div class="modalbtns">
          <button type="button" class="btn" onClick=${() => mark ? setEditing(false) : onClose(false)}>${t("assume.cancel")}</button>
          <button class="btn primary" disabled=${busy}>${mark ? t("assume.save") : t("assume.ok")}</button>
        </div>` : html`
        <div class="whybox">${mark.reason}</div>
        <div class="modalbtns">
          <button type="button" class="btn" onClick=${() => setEditing(true)}><${Icon} name="edit" /> ${t("assume.edit")}</button>
          <button type="button" class="btn primary" onClick=${() => onClose(false)}>${t("assume.close")}</button>
        </div>`}
    </form>
  </div>`;
}
