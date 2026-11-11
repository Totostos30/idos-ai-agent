// Мост к оболочке Tauri. В обычном браузере (демо-режим) команды оболочки заменены заглушками,
// а порт/токен ядра берутся из адреса: index.html?view=island&port=9877&token=...
const T = window.__TAURI__;
const query = new URLSearchParams(location.search);

export const inTauri = !!T;
export const win = T ? T.window.getCurrentWindow() : null;
export const view = T ? win.label : (query.get("view") || "island");

const demoListeners = new Map();

export async function invoke(cmd, args = {}) {
  if (T) return T.core.invoke(cmd, args);
  switch (cmd) {
    case "core_info": {
      const port = query.get("port");
      return port ? { ready: true, port: Number(port), token: query.get("token") || "" } : { ready: false, error: "Укажите ?port=&token= для демо" };
    }
    case "register_hotkeys": return { registered: Object.keys(args.map || {}).length, errors: {} };
    case "select_info": return { purpose: query.get("purpose") || "shot", x: 0, y: 0, width: innerWidth, height: innerHeight, scale: 1 };
    case "monitors": return [{ index: 0, name: "Экран 1", x: 0, y: 0, width: screen.width, height: screen.height, scale: devicePixelRatio }];
    case "show_main": if (view !== "app") window.open("?view=app&" + query.toString(), "_blank"); return null;
    default: return null;
  }
}

export function listen(event, fn) {
  if (T) {
    let off = null;
    let cancelled = false;
    T.event.listen(event, (e) => fn(e.payload)).then((u) => { if (cancelled) u(); else off = u; });
    return () => { cancelled = true; if (off) off(); };
  }
  if (!demoListeners.has(event)) demoListeners.set(event, new Set());
  demoListeners.get(event).add(fn);
  return () => demoListeners.get(event).delete(fn);
}

// Локальное событие (только демо): имитация горячей клавиши или события оболочки.
export function emitLocal(event, payload) {
  (demoListeners.get(event) || new Set()).forEach((fn) => fn(payload));
}
window.idosEmit = emitLocal;

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  return h ? h + ":" + mm + ":" + ss : mm + ":" + ss;
}

export function toast(text, kind = "info") {
  let box = document.querySelector(".toasts");
  if (!box) { box = el("div", { class: "toasts" }); document.body.append(box); }
  const t = el("div", { class: "toast " + kind }, text);
  box.append(t);
  setTimeout(() => t.classList.add("out"), 3200);
  setTimeout(() => t.remove(), 3700);
}

// Собственный микрофон Эйдоса: капсула с градиентом фирменных цветов (голубой → синий → фиолетовый),
// блик-«искра» и дужка держателя. Состояния (data-state на кнопке): idle — покой, listening — капсула
// «дышит» под громкость голоса и расходятся волны, transcribing — вокруг бежит градиентное кольцо.
const GMIC_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
  '<defs>' +
  '<linearGradient id="idosMicCap" x1="0" y1="0" x2="0.35" y2="1"><stop offset="0" stop-color="#4fd1ff"/><stop offset="0.55" stop-color="#7c8cff"/><stop offset="1" stop-color="#b46cff"/></linearGradient>' +
  '<linearGradient id="idosMicArc" gradientUnits="userSpaceOnUse" x1="5" y1="12" x2="19" y2="12"><stop offset="0" stop-color="#7c8cff"/><stop offset="1" stop-color="#b46cff"/></linearGradient>' +
  '</defs>' +
  '<path class="g-cap" fill="url(#idosMicCap)" d="M12 2.4a3.6 3.6 0 0 1 3.6 3.6v5.9a3.6 3.6 0 0 1-7.2 0V6A3.6 3.6 0 0 1 12 2.4z"/>' +
  '<path class="g-shine" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="1.3" stroke-linecap="round" d="M10.5 5.6v2.6"/>' +
  '<path class="g-spark" fill="#fff" fill-opacity=".85" d="M13.6 4.3l.32.78.78.32-.78.32-.32.78-.32-.78-.78-.32.78-.32z"/>' +
  '<path class="g-left" fill="none" stroke="url(#idosMicArc)" stroke-width="1.9" stroke-linecap="round" d="M5.6 11.6a6.4 6.4 0 0 0 6.4 6.4"/>' +
  '<path class="g-right" fill="none" stroke="url(#idosMicArc)" stroke-width="1.9" stroke-linecap="round" d="M12 18a6.4 6.4 0 0 0 6.4-6.4"/>' +
  '<path class="g-stem" fill="none" stroke="#b46cff" stroke-width="1.9" stroke-linecap="round" d="M12 18v3.1M9.4 21.3h5.2"/>' +
  '</svg>';

export function micIcon() {
  return el("span", { class: "gmic-wrap" },
    el("span", { class: "gwaves" }, el("i", {}), el("i", {}), el("i", {})),
    el("span", { class: "gspin" }),
    el("span", { class: "gmic", html: GMIC_SVG }));
}

// Состояние микрофона на кнопке: стили читают data-state и переменную --lvl (0..1).
export function setMicState(button, st) {
  if (!button) return;
  const state = st && st.state ? st.state : "idle";
  button.dataset.state = state;
  button.classList.toggle("on", state === "listening" || state === "transcribing");
  button.style.setProperty("--lvl", String(Math.max(0, Math.min(1, Number(st && st.level) || 0)).toFixed(3)));
}
