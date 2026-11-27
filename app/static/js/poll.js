// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { useState, useEffect } from "/static/js/lib.js";
import { AuthError } from "/static/js/api.js";

// Calls `fn` now and every `ms`; stops when the component goes away or `deps` change.
export function usePoll(fn, ms, deps = []) {
  const [state, set] = useState({ data: null, error: null });
  useEffect(() => {
    let alive = true, timer = null;
    const tick = async () => {
      try { const data = await fn(); if (alive) set({ data, error: null }); }
      catch (e) { if (alive && !(e instanceof AuthError)) set(s => ({ ...s, error: e })); }
      if (alive) timer = setTimeout(tick, ms);
    };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, deps);
  return state;
}
