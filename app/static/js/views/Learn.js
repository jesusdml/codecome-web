// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api } from "/static/js/api.js";
import { t } from "/static/js/i18n.js";
import { usePoll } from "/static/js/poll.js";

// "How it works": three hand-drawn SVG diagrams. Animation is SMIL/CSS only (no script timers),
// and every diagram is backed by plain text, so nothing depends on seeing the motion.
const PHASES = [
  { k: "1", agent: "recon", icon: "compass" },
  { k: "2", agent: "auditor", icon: "search" },
  { k: "3", agent: "reviewer", icon: "eye" },
  { k: "4", agent: "validator", icon: "flask", per: true },
  { k: "5", agent: "exploiter", icon: "bolt", per: true },
  { k: "6", agent: "reporter", icon: "file" },
];

function Arrows() {
  return html`<defs>
    <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path class="ahead" d="M0,0 L10,5 L0,10 z" /></marker>
  </defs>`;
}

function Pipeline() {
  const [sel, setSel] = useState("1");
  const W = 960, bw = 132, bh = 112, gap = (W - 40 - 6 * bw) / 5, y = 34;
  const cx = i => 20 + i * (bw + gap) + bw / 2;
  const flow = `M${cx(0)},${y + bh / 2} L${cx(5)},${y + bh / 2}`;
  const p = PHASES.find(x => x.k === sel);
  return html`<section class="learn">
    <h2>${t("learn.s1.title")}</h2>
    <p class="lead">${t("learn.s1.lead")}</p>
    <svg class="diagram" viewBox=${`0 0 ${W} 250`} role="img" aria-label=${t("learn.s1.title")}>
      <${Arrows} />
      <text class="cap" x="20" y="18">src/ → ${t("learn.s1.in")}</text>
      <text class="cap" x=${W - 20} y="18" text-anchor="end">${t("learn.s1.out")} → itemdb/reports/report.md</text>
      <path id="flow" class="flowline" d=${flow} />
      ${[0, 1, 2].map(i => html`<circle class="flowdot" r="6">
        <animateMotion dur="7s" begin=${`${i * 2.33}s`} repeatCount="indefinite"><mpath href="#flow" /></animateMotion></circle>`)}
      ${PHASES.map((ph, i) => {
        const x = 20 + i * (bw + gap), on = ph.k === sel;
        return html`<g class=${"pnode" + (on ? " on" : "")} tabindex="0" role="button" aria-pressed=${on}
            onClick=${() => setSel(ph.k)} onKeyDown=${e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setSel(ph.k))}>
          ${ph.per && html`<rect class="stack" x=${x + 8} y=${y - 8} width=${bw} height=${bh} rx="12" />
            <rect class="stack" x=${x + 4} y=${y - 4} width=${bw} height=${bh} rx="12" />`}
          <rect class="box" x=${x} y=${y} width=${bw} height=${bh} rx="12" />
          <g class="picon"><${Icon} name=${ph.icon} x=${x + bw / 2 - 14} y=${y + 14} size="28" /></g>
          <text class="pnum" x=${x + bw / 2} y=${y + 68} text-anchor="middle">${t("learn.phase")} ${ph.k}</text>
          <text class="ptitle" x=${x + bw / 2} y=${y + 90} text-anchor="middle">${t(`learn.short.${ph.k}`)}</text>
          <text class="agent" x=${x + bw / 2} y=${y + bh + 24} text-anchor="middle">${ph.agent}</text>
          ${ph.per && html`<text class="per" x=${x + bw / 2} y=${y + bh + 44} text-anchor="middle">× ${t("learn.per_finding")}</text>`}
        </g>`;
      })}
    </svg>
    <div class="pdetail">
      <h3><${Icon} name=${p.icon} /> ${t("learn.phase")} ${p.k} · ${t(`phase.${p.k}.title`)} <span class="mut small">· ${t("learn.agent")} <code>${p.agent}</code></span></h3>
      <div class="field"><div class="fk">${t("card.does")}</div><div class="fv">${t(`phase.${p.k}.does`)}</div></div>
      <div class="field"><div class="fk">${t("card.needs")}</div><div class="fv">${t(`phase.${p.k}.needs`)}</div></div>
      <div class="field"><div class="fk">${t("card.produces")}</div><div class="fv">${t(`phase.${p.k}.produces`)}</div></div>
      <p class="small mut">${t("learn.s1.click")}</p>
    </div>
  </section>`;
}

function InsidePhase() {
  const B = (x, y, w, h, cls, title, sub) => html`<g class=${"ibox " + cls}>
    <rect x=${x} y=${y} width=${w} height=${h} rx="12" />
    <text class="ititle" x=${x + w / 2} y=${y + h / 2 - 3} text-anchor="middle">${title}</text>
    <text class="isub" x=${x + w / 2} y=${y + h / 2 + 17} text-anchor="middle">${sub}</text></g>`;
  return html`<section class="learn">
    <h2>${t("learn.s2.title")}</h2>
    <p class="lead">${t("learn.s2.lead")}</p>
    <svg class="diagram" viewBox="0 0 960 300" role="img" aria-label=${t("learn.s2.title")}>
      <${Arrows} />
      ${B(20, 30, 230, 80, "c1", t("learn.s2.prompt"), t("learn.s2.prompt_sub"))}
      ${B(355, 30, 250, 80, "c2", t("learn.s2.agent"), t("learn.s2.agent_sub"))}
      ${B(710, 30, 230, 80, "c3", t("learn.s2.tools"), "read · grep · bash · write")}
      ${B(355, 190, 250, 80, "c4", "itemdb/", t("learn.s2.disk_sub"))}
      ${B(710, 190, 230, 80, "c5", t("learn.s2.gate"), "tools/gate-check.py")}
      <path class="arrow" d="M250,70 L350,70" marker-end="url(#ah)" />
      <path id="toTools" class="arrow" d="M605,55 L705,55" marker-end="url(#ah)" />
      <path id="fromTools" class="arrow" d="M710,88 L610,88" marker-end="url(#ah)" />
      <circle class="flowdot" r="5"><animateMotion dur="1.6s" repeatCount="indefinite"><mpath href="#toTools" /></animateMotion></circle>
      <circle class="flowdot alt" r="5"><animateMotion dur="1.6s" begin="0.8s" repeatCount="indefinite"><mpath href="#fromTools" /></animateMotion></circle>
      <text class="cap" x="657" y="128" text-anchor="middle">${t("learn.s2.loop")}</text>
      <path class="arrow" d="M480,110 L480,185" marker-end="url(#ah)" />
      <text class="cap" x="490" y="152">${t("learn.s2.writes")}</text>
      <path class="arrow" d="M605,230 L705,230" marker-end="url(#ah)" />
      <text class="ok" x="825" y="292" text-anchor="middle">✓ ${t("learn.s2.pass")}   ✗ ${t("learn.s2.fail")}</text>
    </svg>
    <ul class="points">
      <li>${t("learn.s2.p1")}</li><li>${t("learn.s2.p2")}</li><li>${t("learn.s2.p3")}</li>
    </ul>
  </section>`;
}

const STATES = [
  { s: "PENDING", x: 20, y: 120, cls: "st-p" },
  { s: "DUPLICATE", x: 360, y: 14, cls: "st-x" },
  { s: "CONFIRMED", x: 360, y: 120, cls: "st-c" },
  { s: "REJECTED", x: 360, y: 226, cls: "st-x" },
  { s: "EXPLOITED", x: 720, y: 120, cls: "st-e" },
];

function Lifecycle() {
  const { data } = usePoll(() => api("/api/audit"), 10000);
  const count = s => data ? data.matrix.filter(r => r.status === s).length : null;
  const w = 220, h = 64;
  const E = (d, label, lx, ly) => html`<path class="arrow" d=${d} marker-end="url(#ah)" />
    <text class="cap" x=${lx} y=${ly} text-anchor="middle">${label}</text>`;
  return html`<section class="learn">
    <h2>${t("learn.s3.title")}</h2>
    <p class="lead">${t("learn.s3.lead")}</p>
    <svg class="diagram" viewBox="0 0 960 310" role="img" aria-label=${t("learn.s3.title")}>
      <${Arrows} />
      ${E("M240,140 C300,140 300,46 355,46", t("learn.s3.e_dup"), 290, 72)}
      ${E("M240,152 L355,152", t("learn.s3.e_val"), 298, 144)}
      ${E("M240,164 C300,164 300,258 355,258", t("learn.s3.e_rej"), 290, 238)}
      ${E("M580,152 L715,152", t("learn.s3.e_exp"), 648, 144)}
      ${STATES.map(st => html`<g class=${"state " + st.cls}>
        <rect x=${st.x} y=${st.y} width=${w} height=${h} rx="32" />
        <text class="sname" x=${st.x + w / 2} y=${st.y + 28} text-anchor="middle">${t("fstatus." + st.s)}</text>
        <text class="isub" x=${st.x + w / 2} y=${st.y + 49} text-anchor="middle">${count(st.s) != null ? t("learn.s3.now", { n: count(st.s) }) : ""}</text>
      </g>`)}
    </svg>
    <ul class="points">
      <li>${t("learn.s3.p1")}</li><li>${t("learn.s3.p2")}</li><li>${t("learn.s3.p3")}</li>
    </ul>
  </section>`;
}

export function Learn() {
  return html`<div class="learnpage">
    <p><a href="#/">← ${t("report.back")}</a></p>
    <p class="hero">${t("learn.hero")}</p>
    <${Pipeline} />
    <${InsidePhase} />
    <${Lifecycle} />
  </div>`;
}
