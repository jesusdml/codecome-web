// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html } from "/static/js/lib.js";

// Our own line icons (24x24, stroke = currentColor), drawn here like the flags: they look the same on
// every OS and never depend on an emoji font being installed. Decorative: aria-hidden.
const P = {
  book: html`<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5z" /><path d="M4 5.5v16" /><path d="M8 7.5h8" />`,
  gear: html`<circle cx="12" cy="12" r="3" /><circle cx="12" cy="12" r="7" /><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8" />`,
  moon: html`<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />`,
  sun: html`<circle cx="12" cy="12" r="4" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />`,
  bot: html`<rect x="4" y="8" width="16" height="11" rx="3" /><path d="M12 4.5V8M2 12.5v3M22 12.5v3M9 16h6" /><circle cx="12" cy="3.5" r="1" />
    <circle cx="9" cy="12.5" r="1.1" fill="currentColor" stroke="none" /><circle cx="15" cy="12.5" r="1.1" fill="currentColor" stroke="none" />`,
  lock: html`<rect x="5" y="11" width="14" height="9.5" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" />`,
  file: html`<path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 12h6M9 16h6" />`,
  wrench: html`<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z" />`,
  clock: html`<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />`,
  save: html`<path d="M5 3h11l3 3v15H5z" /><path d="M8 3v5h7V3" /><rect x="8" y="13" width="8" height="6" rx="1" />`,
  warn: html`<path d="M12 3.5 22 20.5H2z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.5" r=".9" fill="currentColor" stroke="none" />`,
  edit: html`<path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M13.5 6.5l4 4" />`,
  compass: html`<circle cx="12" cy="12" r="9" /><path d="M15.5 8.5 13 13l-4.5 2.5L11 11z" />`,
  search: html`<circle cx="10.5" cy="10.5" r="6" /><path d="M15 15l5.5 5.5" />`,
  eye: html`<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />`,
  flask: html`<path d="M9 3h6M10 3v6L4.6 18.5A1.7 1.7 0 0 0 6.1 21h11.8a1.7 1.7 0 0 0 1.5-2.5L14 9V3M6.8 15h10.4" />`,
  bolt: html`<path d="M13 2 4 14h7l-1 8 9-12h-7z" />`,
  play: html`<path d="M7 4.5v15l12-7.5z" fill="currentColor" />`,
  pause: html`<rect x="6.5" y="5" width="3.5" height="14" rx="1" fill="currentColor" /><rect x="14" y="5" width="3.5" height="14" rx="1" fill="currentColor" />`,
  start: html`<path d="M6 5v14" /><path d="M18 5 9 12l9 7z" fill="currentColor" />`,
  prev: html`<path d="M16 5 7 12l9 7z" fill="currentColor" />`,
  next: html`<path d="M8 5l9 7-9 7z" fill="currentColor" /><path d="M19 5v14" />`,
  download: html`<path d="M12 3v12M7 10l5 5 5-5M4 20h16" />`,
  upload: html`<path d="M12 21V9M7 14l5-5 5 5M4 4h16" />`,
  box: html`<rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />`,
  reset: html`<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.6" /><path d="M4 3.5v5.1h5.1" />`,
};

export function Icon({ name, x, y, size, cls }) {
  const nested = x != null;  // inside another SVG (the diagrams)
  return html`<svg viewBox="0 0 24 24" class=${"ico" + (cls ? " " + cls : "")} aria-hidden="true" focusable="false"
    x=${nested ? x : undefined} y=${nested ? y : undefined} width=${size || undefined} height=${size || undefined}>${P[name] || null}</svg>`;
}
