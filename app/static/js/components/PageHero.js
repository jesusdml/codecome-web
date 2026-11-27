// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html } from "/static/js/lib.js";
import { t } from "/static/js/i18n.js";

// The Journey look for every page: dark band, eyebrow, big title, a line of context and,
// on the right, a small terminal that says where this data lives on disk. Text only, never markup.
export function PageHero({ eyebrow, title, lede, back = true, actions, term, extraBack, cls = "" }) {
  return html`<section class=${"hero compact " + cls}>
    <div>
      ${back && html`<div class="heroback"><a href="#/">← ${t("report.back")}</a>${extraBack}</div>`}
      ${eyebrow && html`<span class="eyebrow">${eyebrow}</span>`}
      <h1>${title}</h1>
      ${lede && html`<p class="lede">${lede}</p>`}
      ${actions && html`<div class="heroactions">${actions}</div>`}
    </div>
    ${term && html`<${Term} ...${term} />`}
  </section>`;
}

// term = { title: "~/codecome", cmd: "ls itemdb/findings/", lines: [[text, cls], ...] }
export function Term({ title, cmd, lines }) {
  return html`<div class="term-card">
    <div class="bar3"><i class="r"></i><i class="y"></i><i class="g"></i><span>${title || "~/codecome"}</span></div>
    <ol>
      <li><span class="ps">$</span><span class="cmd">${cmd}</span></li>
      ${(lines || []).map(([text, c, right, rc]) => html`<li><span class=${c || "cmd"}>${text}</span>${right != null && html`<span class=${"st " + (rc || "")}>${right}</span>`}</li>`)}
    </ol>
  </div>`;
}
