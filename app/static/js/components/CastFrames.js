// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState, useEffect } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";
import { screen } from "/static/js/components/CastPlayer.js";

// The exploit recording as a grid of printable frames: one frame per step, white paper, black text.
// Each frame shows what that step printed (not the whole scrolled screen), so it stays readable on paper.
const MAX_LINES = 14;
const clock = s => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

function frameText(ev) {
  const lines = screen([ev], 1).split("\n");
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  while (lines.length && !lines[0].trim()) lines.shift();
  return lines.length > MAX_LINES ? [...lines.slice(0, MAX_LINES), "…"].join("\n") : lines.join("\n");
}

export function CastFrames({ id }) {
  const [cast, setCast] = useState(null), [err, setErr] = useState("");
  useEffect(() => { api(`/api/findings/${id}/recording`).then(setCast, e => setErr(e.message)); }, [id]);
  if (err) return html`<p class="c-bad small">${err}</p>`;
  if (!cast) return html`<p class="mut small">…</p>`;
  const frames = cast.events.map((ev, i) => ({ i: i + 1, t: ev[0], text: frameText(ev) })).filter(f => f.text);
  return html`<figure class="frames">
    <figcaption>${t("report.recording", { id })} · ${t("frames.caption", { n: frames.length })}
      <a class="noprint" href=${"#/finding/" + id}> · <${Icon} name="play" /> ${t("frames.watch")}</a></figcaption>
    <div class="framegrid">${frames.map(f => html`<div class="frame">
      <div class="fh"><b>${t("frames.step", { i: f.i })}</b><span>${clock(f.t)}</span></div>
      <pre>${f.text}</pre></div>`)}</div>
  </figure>`;
}
