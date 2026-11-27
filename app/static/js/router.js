// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { useState, useEffect } from "/static/js/lib.js";

// Hash router: no reloads, back button works, and the server never sees the route.
const ROUTES = [
  [/^\/?$/, () => ({ name: "home" })],
  [/^\/findings$/, () => ({ name: "findings" })],
  [/^\/finding\/(CC-\d{1,6})$/, m => ({ name: "finding", id: m[1] })],
  [/^\/jobs$/, () => ({ name: "jobs" })],
  [/^\/learn$/, () => ({ name: "learn" })],
  [/^\/report$/, () => ({ name: "report" })],
  [/^\/settings$/, () => ({ name: "settings" })],
  [/^\/archive$/, () => ({ name: "archive" })],
  [/^\/job\/(\d{1,9})$/, m => ({ name: "job", id: +m[1] })],
];

export function parseRoute(hash) {
  const path = decodeURIComponent((hash || "").replace(/^#/, "")) || "/";
  for (const [re, make] of ROUTES) { const m = path.match(re); if (m) return make(m); }
  return { name: "notfound" };
}

export const navigate = path => { location.hash = "#" + path; };

export function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
