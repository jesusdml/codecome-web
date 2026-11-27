// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, h } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t, errText } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";
import { CastFrames } from "/static/js/components/CastFrames.js";
import { PageHero } from "/static/js/components/PageHero.js";

// The server sends a tree of allowed tags with text leaves (see app/docs.py); it is turned into
// elements here with h(), so text is always text. No raw-HTML injection anywhere.
const TAGS = new Set(["p", "ul", "ol", "li", "blockquote", "table", "thead", "tbody", "tr", "th", "td",
  "strong", "em", "s", "code", "pre", "br", "hr", "a", "h1", "h2", "h3", "h4", "h5", "h6"]);
const ID_RE = /\bCC-\d{1,6}\b/;

const textOf = n => typeof n === "string" ? n : (n.c || []).map(textOf).join("");

let ASSUMED = {};  // finding id -> mark, for the report being rendered

function node(n, i, recs, casts) {
  if (typeof n === "string") return n;
  if (!TAGS.has(n.t)) return (n.c || []).map((c, j) => node(c, j, recs, casts));
  const kids = (n.c || []).map((c, j) => node(c, j, recs, casts));
  const props = { key: i };
  if (n.t === "a") { props.href = n.href; props.rel = "noopener noreferrer nofollow"; if (/^https?:/.test(n.href)) props.target = "_blank"; }
  if ((n.t === "th" || n.t === "td") && n.align) props.class = "al-" + n.align;
  if (n.t === "table") return h("div", { key: i, class: "tablewrap" }, h("table", props, kids));
  const el = h(n.t, props, kids);
  // An exploited finding with a recording gets its GIF right under its heading.
  if (n.t === "h2") {
    const m = textOf(n).match(ID_RE);
    const a = m && ASSUMED[m[0]];
    // Web-side decision (not in CodeCome's markdown): accepted without proof, with the user's reason.
    const note = a ? html`<div class="assumebox" key=${"a" + i}><b><${Icon} name="warn" /> ${t("report.assumed")}</b> · ${t(`phase.${a.phase}.title`)} ·
      <span class="mut">${t("matrix.assumed_by", { who: a.by, when: new Date(a.at * 1000).toLocaleString() })}</span>
      <div class="why">${a.reason}</div></div>` : null;
    if (note) return [el, note];
    // Printable frames when the text recording exists; the GIF only as a fallback.
    if (m && casts && casts.includes(m[0])) return [el, html`<${CastFrames} id=${m[0]} key=${"f" + i} />`];
    if (m && recs.includes(m[0])) return [el, html`<figure class="rec" key=${"r" + i}>
      <img src=${`/api/findings/${m[0]}/recording.gif`} alt=${t("report.recording", { id: m[0] })} loading="lazy" />
      <figcaption>${t("report.recording", { id: m[0] })} · <a class="noprint" href=${"#/finding/" + m[0]}>${t("cast.open")} ›</a></figcaption></figure>`];
  }
  return el;
}

// Shared by the report and the finding detail.
export function Markdown({ tree, recs, casts }) {
  return html`<div class="md">${tree.map((n, i) => node(n, i, recs || [], casts || []))}</div>`;
}

const kb = n => n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B";

export function Report() {
  const { data: d, error } = usePoll(() => api("/api/report"), 15000);
  if (error && !d) return html`<p class="c-bad">${errText(error.detail) || error.message}</p>
    <p><a href="#/">${t("report.back")}</a></p>`;
  if (!d) return html`<p class="mut">…</p>`;
  const when = new Date(d.mtime * 1000).toLocaleString();
  return html`<article class="report">
    <${PageHero} cls="noprint" eyebrow=${t("report.title")} title=${d.project || t("report.title")}
      lede=${html`${t("report.generated", { when, size: kb(d.size) })}<br /><span class="small">${t("report.untrusted")}</span>`}
      actions=${html`<button class="btn primary" onClick=${() => window.print()}><${Icon} name="download" /> ${t("report.pdf")}</button>
        <a class="btn ondark" href="/api/report.md" download><${Icon} name="download" /> ${t("report.md")}</a>`}
      term=${{ title: "~/codecome — reports", cmd: "cat itemdb/reports/report.md",
        lines: [["# CodeCome Report", "tc-dim"], [kb(d.size), "cmd"], [`recordings: ${(d.casts || d.recordings || []).length}`, "tc-blue"],
                [`assumed: ${Object.keys(d.assumed || {}).length}`, "tc-yellow"]] }} />
    ${(ASSUMED = d.assumed || {}, null)}
    <${Markdown} tree=${d.tree} recs=${d.recordings} casts=${d.casts} />
  </article>`;
}
