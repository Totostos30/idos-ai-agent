// Тема Эйдоса (THEMES.md §4): единственная точка применения.
// Пресеты: system | dark | light (+ акцент Αἰθήρ/Νύξ/…). Слушает settings.changed
// и prefers-color-scheme, кэш в localStorage убирает вспышку не той темы при старте.
import { core } from "./rpc.js";

const LS_KEY = "idos.theme";
export const ACCENTS = {
  nous:     { dark: "#7c8cff", light: "#5b6ee0", label: "Νοῦς · индиго" },
  aither:   { dark: "#4fd1ff", light: "#0e7fb8", label: "Αἰθήρ · циан" },
  nyx:      { dark: "#b46cff", light: "#8a46d6", label: "Νύξ · фиолет" },
  physis:   { dark: "#35d07f", light: "#1f9d5e", label: "Φύσις · зелёный" },
  helios:   { dark: "#ffb547", light: "#b07016", label: "Ἥλιος · оранж" },
  thalassa: { dark: "#2fbdb3", light: "#12857c", label: "Θάλασσα · бирюза" },
  pyr:      { dark: "#ff6d55", light: "#cf4630", label: "Πῦρ · огонь" },
  eros:     { dark: "#ff6fa5", light: "#cf4d80", label: "Ἔρως · розовый" },
  chloros:  { dark: "#a8d94a", light: "#7ba32c", label: "Χλωρός · лайм" },
};
let lastCfg = { preset: "dark", accent: "" };
let media = null;
let customThemes = {}; // slug → {name, base, vars} (THEMES §3)
const CUSTOM_APPLIED = []; // переменные, выставленные последней кастомной темой

// Базы для слияния и контраст-гейта на фронте (дублируют tokens.css)
export const BASE_VARS = {
  dark: { "--surface-0": "#000", "--surface-frame": "#0b0b0d", "--surface-1": "#111114", "--surface-2": "#141417",
    "--surface-3": "#1b1b20", "--bubble-user": "#2a2d3a", "--fg": "#f5f5f7", "--mut": "#9a9aa2",
    "--line": "rgba(255,255,255,0.09)", "--fill-1": "rgba(255,255,255,0.07)", "--fill-2": "rgba(255,255,255,0.13)",
    "--accent": "#7c8cff", "--accent-contrast": "#000", "--ok": "#35d07f", "--warn": "#ffb547", "--err": "#ff5a5f" },
  light: { "--surface-0": "#fbfbfd", "--surface-frame": "#f0f0f3", "--surface-1": "#f4f4f6", "--surface-2": "#f3f3f5",
    "--surface-3": "#ffffff", "--bubble-user": "#e4e7ff", "--fg": "#111114", "--mut": "#6b6b75",
    "--line": "rgba(0,0,0,0.08)", "--fill-1": "rgba(0,0,0,0.05)", "--fill-2": "rgba(0,0,0,0.1)",
    "--accent": "#5b6ee0", "--accent-contrast": "#fff", "--ok": "#1f9d5e", "--warn": "#b07016", "--err": "#e03e43" },
};

export async function loadCustomThemes() {
  try {
    const list = await core.call("themes.list", {}, 8000);
    if (Array.isArray(list)) customThemes = Object.fromEntries(list.map((t) => [t.slug, t]));
  } catch (e) { customThemes = {}; }
  return customThemes;
}
export function customThemesMap() { return customThemes; }

function systemDark() { return matchMedia("(prefers-color-scheme: dark)").matches; }

// старый формат настройки — строка "dark"|"light"|"system"
export function normalizeTheme(cfg) {
  if (!cfg || typeof cfg === "string") return { preset: cfg || "dark", accent: "" };
  return { preset: cfg.preset || "dark", accent: cfg.accent || "" };
}

function schemeOf(preset) {
  if (preset === "light") return "light";
  if (preset === "dark") return "dark";
  return systemDark() ? "dark" : "light";
}

export function applyTheme(cfg) {
  lastCfg = normalizeTheme(cfg);
  const { preset, accent } = lastCfg;
  const root = document.documentElement;
  // сброс переменных предыдущей кастомной темы
  for (const k of CUSTOM_APPLIED.splice(0)) root.style.removeProperty(k);
  let scheme;
  const custom = customThemes[preset];
  if (custom) {
    scheme = custom.base === "light" ? "light" : "dark";
    const merged = Object.assign({}, BASE_VARS[scheme], custom.vars || {});
    for (const [k, v] of Object.entries(merged)) {
      root.style.setProperty(k, v);
      CUSTOM_APPLIED.push(k);
    }
  } else {
    scheme = schemeOf(preset);
  }
  root.dataset.theme = scheme;
  root.dataset.scheme = custom ? "custom:" + preset : preset;
  const a = accent && ACCENTS[accent];
  if (a) root.style.setProperty("--accent", scheme === "light" ? a.light : a.dark);
  else root.style.removeProperty("--accent");
  try { localStorage.setItem(LS_KEY, JSON.stringify(lastCfg)); } catch (e) { /* приватный режим */ }
}

// мгновенное применение из кэша — вызывается из head-скрипта index.html
export function applyCachedTheme() {
  try { applyTheme(JSON.parse(localStorage.getItem(LS_KEY) || "{}")); }
  catch (e) { applyTheme({}); }
}

export function initTheme() {
  applyCachedTheme();
  loadCustomThemes().then(() => applyTheme(lastCfg)).catch(() => {});
  core.try("settings.get", {}, 8000).then((cfg) => {
    if (cfg && !cfg.error && cfg.theme) applyTheme(cfg.theme);
  }).catch(() => {});
  core.on("settings.changed", ({ key }) => {
    if (key !== "theme" && !key.startsWith("themes.")) return;
    if (key.startsWith("themes.")) { loadCustomThemes().then(() => applyTheme(lastCfg)).catch(() => {}); return; }
    core.try("settings.get", {}, 8000).then((c) => { if (c && !c.error) applyTheme(c.theme); }).catch(() => {});
  });
  media = matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener?.("change", () => { if (lastCfg.preset === "system") applyTheme(lastCfg); });
}
