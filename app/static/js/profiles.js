// SPDX-License-Identifier: GPL-3.0-or-later OR AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 @jesusdml
import { html, useState } from "/static/js/lib.js";
import { Icon } from "/static/js/components/Icon.js";
import { t } from "/static/js/i18n.js";
import { profileName } from "/static/js/format.js";

// The active profile and the per-phase models come from the server (Settings page). Read only here.
let PROF = null, selected = null, MODELS = [];
export function setProfiles(d) {
  PROF = { default: d.default_profile, list: d.profiles };
  if (d.models) MODELS = d.models;
  selected = PROF.default;  // the active profile is chosen on the Settings page, not here
}
export const hasProfiles = () => PROF !== null;
export const selectedProfile = () => selected;
export const modelLabel = name => (MODELS.find(m => m.name === name) || {}).label || name;
export const isLocal = name => !!(MODELS.find(m => m.name === name) || {}).local;
function profileDefault(phase) {
  const p = PROF && PROF.list.find(x => x.name === selected);
  return p && p.models ? p.models[phase] : null;
}
// What a launch of this phase will use: the default chosen on the Settings page for the selected profile.
// Model choice lives only in Settings (it is an admin decision, not an auditor's).
export const pickedModel = phase => profileDefault(phase);

// The "which AI" square of a phase: read only. While it runs (or after), `used` is the model really used.
export function ModelPick({ phase, used, locked }) {
  if (!PROF) return null;
  const cur = pickedModel(phase);
  const usedTxt = locked && used ? used.split("/").pop() : null;
  const local = !usedTxt && isLocal(cur);
  return html`<span class=${"mpick locked" + (local ? " local" : "")}
      title=${local ? t("settings.local_warn") : (usedTxt ? t("model.used", { model: usedTxt }) : t("model.label"))}>
    <${Icon} name=${local ? "warn" : "bot"} /> ${usedTxt || modelLabel(cur) || t("model.default")}</span>`;
}

