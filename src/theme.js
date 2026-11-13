// Тема Эйдоса (THEMES.md §4): единственная точка применения.
// Пресеты: system | dark | light (+ акцент Αἰθήρ/Νύξ/…). Слушает settings.changed
// и prefers-color-scheme, кэш в localStorage убирает вспышку не той темы при старте.
import { core } from "./rpc.js";

const LS_KEY = "idos.theme";
export const ACCENTS = {
  nous:   { dark: "#7c8cff", light: "#5b6ee0", label: "Νοῦς · индиго" },
  aither: { dark: "#4fd1ff", light: "#0e7fb8", label: "Αἰθήρ · циан" },
  nyx:    { dark: "#b46cff", light: "#8a46d6", label: "Νύξ · фиолет" },
  physis: { dark: "#35d07f", light: "#1f9d5e", label: "Φύσις · зелёный" },
  helios: { dark: "#ffb547", light: "#b07016", label: "Ἥλιος · оранж" },
};
let lastCfg = { preset: "dark", accent: "" };
let media = null;

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
  const scheme = schemeOf(preset);
  const root = document.documentElement;
  root.dataset.theme = scheme;
  root.dataset.scheme = preset;
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
  core.try("settings.get", {}, 8000).then((cfg) => {
    if (cfg && !cfg.error && cfg.theme) applyTheme(cfg.theme);
  }).catch(() => {});
  core.on("settings.changed", ({ key }) => {
    if (key !== "theme") return;
    core.try("settings.get", {}, 8000).then((c) => { if (c && !c.error) applyTheme(c.theme); }).catch(() => {});
  });
  media = matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener?.("change", () => { if (lastCfg.preset === "system") applyTheme(lastCfg); });
}
