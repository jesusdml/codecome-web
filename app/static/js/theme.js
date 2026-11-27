// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
// Night mode. No choice stored = follow the operating system (the CSS media query does it).
// The choice lives in this browser only; reading/writing storage may fail in private mode.
const KEY = "theme";
const root = document.documentElement;
let stored = null;
try { stored = localStorage.getItem(KEY); } catch (e) { stored = null; }
if (stored === "dark" || stored === "light") root.dataset.theme = stored;

export function isDark() {
  if (root.dataset.theme) return root.dataset.theme === "dark";
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
export function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem(KEY, next); } catch (e) { /* the choice just won't persist */ }
  return next;
}
