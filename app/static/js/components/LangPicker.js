// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect, useRef } from "/static/js/lib.js";
import { t, LANGS, lang, setLang } from "/static/js/i18n.js";

// Flags drawn as tiny SVGs: emoji flags render as letters on Windows and there is no standard emoji
// for Catalonia, Galicia or the Basque Country. Simplified on purpose (they are 20 px wide).
const FLAGS = {
  en: html`<svg viewBox="0 0 60 30" class="flag" aria-hidden="true"><rect width="60" height="30" fill="#012169" />
    <path d="M0,0 60,30 M60,0 0,30" stroke="#fff" stroke-width="6" /><path d="M0,0 60,30 M60,0 0,30" stroke="#C8102E" stroke-width="2" />
    <path d="M30,0v30M0,15h60" stroke="#fff" stroke-width="10" /><path d="M30,0v30M0,15h60" stroke="#C8102E" stroke-width="6" /></svg>`,
  es: html`<svg viewBox="0 0 30 20" class="flag" aria-hidden="true"><rect width="30" height="20" fill="#AA151B" /><rect y="5" width="30" height="10" fill="#F1BF00" /></svg>`,
  de: html`<svg viewBox="0 0 30 18" class="flag" aria-hidden="true"><rect width="30" height="6" fill="#000" /><rect y="6" width="30" height="6" fill="#DD0000" /><rect y="12" width="30" height="6" fill="#FFCE00" /></svg>`,
  fr: html`<svg viewBox="0 0 30 20" class="flag" aria-hidden="true"><rect width="10" height="20" fill="#002654" /><rect x="10" width="10" height="20" fill="#fff" /><rect x="20" width="10" height="20" fill="#CE1126" /></svg>`,
  ca: html`<svg viewBox="0 0 27 18" class="flag" aria-hidden="true"><rect width="27" height="18" fill="#FCDD09" />
    ${[2, 6, 10, 14].map(y => html`<rect y=${y} width="27" height="2" fill="#DA121A" />`)}</svg>`,
  gl: html`<svg viewBox="0 0 30 20" class="flag" aria-hidden="true"><rect width="30" height="20" fill="#fff" /><path d="M0,0 L6,0 L30,16 L30,20 L24,20 L0,4 Z" fill="#0099CC" /></svg>`,
  eu: html`<svg viewBox="0 0 28 16" class="flag" aria-hidden="true"><rect width="28" height="16" fill="#D52B1E" />
    <path d="M0,0 28,16 M28,0 0,16" stroke="#009B48" stroke-width="2.6" /><path d="M14,0v16M0,8h28" stroke="#fff" stroke-width="2.4" /></svg>`,
};
export const Flag = ({ code }) => FLAGS[code] || null;

// Without props it switches this browser's language; with `value`/`onPick` it is a plain flag picker
// (e.g. the default language on the Settings page).
export function LangPicker({ value, onPick, disabled, label } = {}) {
  const cur = value || lang;
  const pick = onPick || (k => { if (k !== lang) setLang(k); });
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  useEffect(() => {
    if (!open) return;
    const away = e => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    const esc = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return html`<div class="langpick" ref=${box}>
    <button type="button" class="langbtn" aria-haspopup="listbox" aria-expanded=${open} aria-label=${label || t("ui.language")}
        disabled=${disabled} onClick=${() => setOpen(!open)}>
      <${Flag} code=${cur} /><span>${LANGS[cur]}</span><span class="caret">▾</span></button>
    ${open && html`<ul class="langlist" role="listbox" aria-label=${t("ui.language")}>
      ${Object.entries(LANGS).map(([k, name]) => html`<li role="option" aria-selected=${k === cur}>
        <button type="button" class=${k === cur ? "on" : ""} onClick=${() => { setOpen(false); pick(k); }}>
          <${Flag} code=${k} /><span>${name}</span></button></li>`)}
    </ul>`}
  </div>`;
}
