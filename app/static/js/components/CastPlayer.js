// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect, useRef, useMemo } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";

// Player for the exploit recording (asciicast). The server already reduced the terminal output to
// text plus \n \r \b \t and two markers (see app/docs.py); here a tiny terminal replays it. The result
// is rendered as a text node: recorded output is untrusted and is never interpreted as markup.
const ERASE_LINE = "\x01", CLEAR = "\x02";
const MAX_CHARS = 2_000_000;

export function screen(events, n) {
  let lines = [""], row = 0, col = 0, seen = 0;
  for (let i = 0; i < n; i++) {
    for (const c of events[i][1]) {
      if (++seen > MAX_CHARS) return lines.join("\n");
      if (c === CLEAR) { lines = [""]; row = 0; col = 0; }
      else if (c === ERASE_LINE) lines[row] = lines[row].slice(0, col);
      else if (c === "\n") { row++; col = 0; if (lines.length <= row) lines.push(""); }
      else if (c === "\r") col = 0;
      else if (c === "\b") col = Math.max(0, col - 1);
      else if (c === "\t") { const to = (Math.floor(col / 8) + 1) * 8; lines[row] = lines[row].padEnd(to); col = to; }
      else { const l = lines[row].padEnd(col); lines[row] = l.slice(0, col) + c + l.slice(col + 1); col++; }
    }
  }
  return lines.join("\n");
}

const STEP_SECONDS = 5;  // every step lasts the same at x1: the auditor reads, the real timing does not matter
const SPEEDS = [0.25, 0.5, 1, 2, 4];

export function CastPlayer({ id }) {
  const [cast, setCast] = useState(null), [err, setErr] = useState("");
  const [idx, setIdx] = useState(0), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1);
  const pre = useRef(null);
  useEffect(() => {
    api(`/api/findings/${id}/recording`).then(setCast, e => setErr(e.message));
  }, [id]);
  const events = cast ? cast.events : [];
  const n = events.length;
  const text = useMemo(() => screen(events, idx), [events, idx]);
  useEffect(() => {
    if (!playing) return;
    if (idx >= n) { setPlaying(false); return; }
    // The first press shows step 1 at once; then one step every STEP_SECONDS / speed.
    const timer = setTimeout(() => setIdx(i => Math.min(n, i + 1)), idx === 0 ? 0 : (STEP_SECONDS * 1000) / speed);
    return () => clearTimeout(timer);
  }, [playing, idx, speed, n]);
  useEffect(() => { const el = pre.current; if (el) el.scrollTop = el.scrollHeight; }, [idx]);
  if (err) return html`<p class="c-bad small">${err}</p>`;
  if (!cast) return html`<p class="mut small">…</p>`;
  const go = i => { setPlaying(false); setIdx(Math.max(0, Math.min(n, i))); };
  const toggle = () => { if (!playing && idx >= n) setIdx(0); setPlaying(!playing); };
  const keys = e => {
    if (e.key === " ") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(idx + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(idx - 1); }
  };
  const per = STEP_SECONDS / speed;
  return html`<figure class="cast" tabindex="0" onKeyDown=${keys} aria-label=${t("cast.label")}>
    ${cast.title && html`<figcaption class="ctitle"><${Icon} name="play" /> ${cast.title}</figcaption>`}
    <pre class="term" ref=${pre}>${text}</pre>
    <div class="ctrl">
      <button class="btn small" title=${t("cast.start")} onClick=${() => go(0)}><${Icon} name="start" /></button>
      <button class="btn small" title=${t("cast.prev")} onClick=${() => go(idx - 1)}><${Icon} name="prev" /></button>
      <button class="btn small primary" title=${playing ? t("cast.pause") : t("cast.play")} onClick=${toggle}><${Icon} name=${playing ? "pause" : "play"} /></button>
      <button class="btn small" title=${t("cast.next")} onClick=${() => go(idx + 1)}><${Icon} name="next" /></button>
      <input type="range" min="0" max=${n} step="1" value=${idx} aria-label=${t("cast.seek")}
        onInput=${e => go(+e.target.value)} />
      <span class="small mono">${t("cast.step", { i: idx, n })}</span>
      <select class="small" aria-label=${t("cast.speed")} value=${speed} onChange=${e => setSpeed(+e.target.value)}>
        ${SPEEDS.map(s => html`<option value=${s}>×${s}</option>`)}</select>
      <span class="small mut">${t("cast.per_step", { s: per % 1 ? per.toFixed(2) : per })}</span>
    </div>
    <p class="small mut">${t("cast.keys")}</p>
  </figure>`;
}
