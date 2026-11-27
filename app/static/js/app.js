// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, render, useState, useEffect, useRef } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { api, post } from "/static/js/api.js";
import "/static/js/theme.js";  // applies the stored night-mode choice before the first paint
import { t, loadLocale } from "/static/js/i18n.js";
import { isDark, toggleTheme } from "/static/js/theme.js";
import { LangPicker } from "/static/js/components/LangPicker.js";
import { useRoute } from "/static/js/router.js";
import { Activity } from "/static/js/components/Activity.js";
import { Findings, Finding } from "/static/js/views/Findings.js";
import { Jobs, Job } from "/static/js/views/Jobs.js";
import { Journey } from "/static/js/views/Journey.js";
import { Learn } from "/static/js/views/Learn.js";
import { Report } from "/static/js/views/Report.js";
import { Settings } from "/static/js/views/Settings.js";
import { Archive } from "/static/js/views/Archive.js";

// Each typed character shows a RANDOM glyph, never derived from the key: it reveals only the length,
// exactly like asterisks would. The real value stays in the password input.
const GLYPHS = "#$%&@*+=?!§¤◆●■▲◇○□△✦✧";
const glyph = () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)];

// Login as a Unix console in a window: "codecome login:" then "Password:" echoed as random symbols.
function Login({ onDone }) {
  const [msg, setMsg] = useState(""), [busy, setBusy] = useState(false), [mask, setMask] = useState("");
  const onPass = e => {
    const n = e.target.value.length;
    setMask(m => m.length >= n ? m.slice(0, n) : m + Array.from({ length: n - m.length }, glyph).join(""));
  };
  const user = useRef(null), pass = useRef(null);
  useEffect(() => { user.current && user.current.focus(); }, []);
  const submit = async e => {
    e.preventDefault();
    if (!user.current.value) { user.current.focus(); return; }
    if (!pass.current.value) { pass.current.focus(); return; }
    setBusy(true);
    try { await post("/api/login", { username: user.current.value, password: pass.current.value }); onDone(); }
    catch (err) { setMsg(err.message); pass.current.value = ""; setMask(""); pass.current.focus(); }
    finally { setBusy(false); }
  };
  const host = location.hostname || "localhost";
  return html`<div class="loginpage">
    <div class="loginlang"><${ThemeButton} /><${LangPicker} /></div>
    <form class="term-card console" onSubmit=${submit} autocomplete="on">
      <div class="bar3"><i class="r"></i><i class="y"></i><i class="g"></i><span>codecome@${host} — login</span></div>
      <div class="screen">
        <div class="cmt"># CodeCome Web · ${t("login.banner")}</div>
        <div class="dim">${host} · tty1</div>
        <div class="gap"></div>
        <label class="line"><span class="ps">codecome login:</span>
          <input ref=${user} name="u" autocomplete="username" spellcheck="false" aria-label=${t("login.username")}
            onKeyDown=${e => { if (e.key === "Enter" && user.current.value) { e.preventDefault(); pass.current.focus(); } }} /></label>
        <label class="line"><span class="ps">Password:</span>
          <span class="pwbox"><input ref=${pass} name="p" type="password" class="noecho" autocomplete="current-password"
            aria-label=${t("login.password")} onInput=${onPass} /><span class="pwmask" aria-hidden="true">${mask}<i class="cur"></i></span></span></label>
        ${msg && html`<div class="err">${msg}</div>`}
        ${busy && html`<div class="dim">…</div>`}
        <div class="gap"></div>
        <div class="cmt"># ${t("login.hint")}</div>
        <button class="srbtn">${t("login.submit")}</button>
      </div>
    </form>
  </div>`;
}

function ThemeButton() {
  const [dark, setDark] = useState(isDark());
  return html`<button class="themebtn" aria-pressed=${dark} title=${dark ? t("ui.theme_light") : t("ui.theme_dark")}
    aria-label=${dark ? t("ui.theme_light") : t("ui.theme_dark")} onClick=${() => setDark(toggleTheme() === "dark")}><${Icon} name=${dark ? "sun" : "moon"} /></button>`;
}

function View({ route }) {
  switch (route.name) {
    case "home": return html`<${Journey} />`;
    case "findings": return html`<${Findings} />`;
    case "finding": return html`<${Finding} id=${route.id} key=${route.id} />`;
    case "jobs": return html`<${Jobs} />`;
    case "learn": return html`<${Learn} />`;
    case "report": return html`<${Report} />`;
    case "settings": return html`<${Settings} />`;
    case "archive": return html`<${Archive} />`;
    case "job": return html`<${Job} id=${route.id} key=${route.id} />`;
    default: return html`<p class="mut">${t("error.unknown")}</p>`;
  }
}

function Shell({ user, onOut }) {
  const route = useRoute();
  const tab = route.name === "home" ? "" : ["learn", "report", "settings", "archive"].includes(route.name) ? route.name : route.name.startsWith("job") ? "jobs" : "findings";
  return html`
    <header class="top">
      <h1>CodeCome</h1><span class="mut small">· ${user}</span><span class="grow"></span>
      <a class=${"toplink" + (route.name === "learn" ? " on" : "")} href="#/learn"><${Icon} name="book" /> ${t("tab.learn")}</a>
      <a class=${"toplink" + (route.name === "archive" ? " on" : "")} href="#/archive"><${Icon} name="box" /> ${t("tab.archive")}</a>
      <a class=${"toplink" + (route.name === "settings" ? " on" : "")} href="#/settings"><${Icon} name="gear" /> ${t("tab.settings")}</a>
      <${ThemeButton} />
      <${LangPicker} />
      <a href="#" class="small toplink" onClick=${e => { e.preventDefault(); onOut(); }}>${t("ui.sign_out")}</a>
    </header>
    <nav class="tabs">
      ${[["", "tab.journey"], ["findings", "tab.findings"], ["report", "tab.report"], ["jobs", "tab.jobs"]].map(([k, l]) =>
        html`<a class="plain" href=${"#/" + k}><button class=${k === tab ? "on" : ""}>${t(l)}</button></a>`)}
    </nav>
    <${Activity} />
    <${View} route=${route} />`;
}

function App() {
  const [user, setUser] = useState(undefined); // undefined = still asking, null = signed out
  const start = () => api("/api/me").then(m => setUser(m.user)).catch(() => setUser(null));
  useEffect(() => {
    start();
    const lost = () => setUser(null);
    window.addEventListener("auth-lost", lost);
    return () => window.removeEventListener("auth-lost", lost);
  }, []);
  if (user === undefined) return null;
  if (user === null) return html`<${Login} onDone=${start} />`;
  return html`<${Shell} user=${user} onOut=${async () => { await post("/api/logout"); setUser(null); }} />`;
}

loadLocale()
  .then(() => render(html`<${App} />`, document.getElementById("app")))
  .catch(() => { document.getElementById("app").textContent = "Could not load the interface texts / No se pudieron cargar los textos."; });
