// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
// Texts live in the server locales; the page never hardcodes user-facing sentences.
export const LANGS = { en: "English", es: "Español", de: "Deutsch", fr: "Français", ca: "Català", gl: "Galego", eu: "Euskara" }; // endonyms: never translated
let L = {}, FALLBACK = {};
export let lang = "en";

export function t(key, p) {
  const s = L[key] ?? FALLBACK[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (p && p[k] != null ? p[k] : ""));
}
export const has = key => key in L || key in FALLBACK;

export async function loadLocale() {
  let mine = null;
  try { mine = localStorage.getItem("lang"); } catch (e) { mine = null; }
  // The browser's own choice wins; otherwise the default set on the Settings page.
  if (!mine) { try { mine = (await (await fetch("/api/ui")).json()).default_lang; } catch (e) { mine = null; } }
  lang = mine in LANGS ? mine : "en";
  FALLBACK = await (await fetch("/locales/en.json")).json();
  L = lang === "en" ? FALLBACK : await (await fetch("/locales/" + lang + ".json")).json();
  document.documentElement.lang = lang;
}
export function setLang(l) {
  try { localStorage.setItem("lang", l); } catch (e) { /* private mode: the choice just won't persist */ }
  location.reload();
}

// API errors arrive as {code, params}; unknown codes fall back to a generic text.
export function errText(d) {
  if (d && typeof d === "object" && !Array.isArray(d) && d.code)
    return has("error." + d.code) ? t("error." + d.code, d.params) : t("error.unknown");
  if (Array.isArray(d)) return t("error.validation");
  return d ? String(d) : t("error.unknown");
}
