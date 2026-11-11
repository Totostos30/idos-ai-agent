// Выбор области экрана для скриншота, OCR, QR и записи. Окно растянуто на весь монитор.
import { core } from "./rpc.js";
import { invoke, listen, el, sleep } from "./shell.js";

const HINTS = { shot: "Скриншот области", ocr: "Распознать текст", qr: "Сканировать QR-код", record: "Записать область" };

function captureSelection(purpose, region) {
  if (purpose === "ocr") return core.try("capture.ocr", region);
  if (purpose === "qr") return core.try("capture.qr", region);
  if (purpose === "record") return core.try("recorder.start", region);
  return core.try("capture.screenshot", Object.assign({ mode: "area", copy: true }, region));
}

export function mountSelect() {
  document.body.classList.add("view-select");
  let info = { purpose: "shot", x: 0, y: 0, scale: 1 };
  let start = null, rect = null, busy = false;
  const shade = el("div", { class: "sel-shade" });
  const box = el("div", { class: "sel-box" });
  const size = el("div", { class: "sel-size" });
  const hint = el("div", { class: "sel-hint" });
  document.body.append(shade, box, size, hint);

  const reset = (i) => {
    info = Object.assign({ x: 0, y: 0, scale: 1, purpose: "shot" }, i || {});
    start = null; rect = null; busy = false;
    box.style.display = "none"; size.style.display = "none";
    shade.style.clipPath = "none";
    hint.textContent = (HINTS[info.purpose] || "Выделите область") + " — выделите мышью · Enter — весь экран · Esc — отмена";
  };
  invoke("select_info").then(reset);
  listen("select-start", reset);

  const draw = () => {
    if (!rect) return;
    const { left, top, width, height } = rect;
    Object.assign(box.style, { display: "block", left: left + "px", top: top + "px", width: width + "px", height: height + "px" });
    shade.style.clipPath = "polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, " + left + "px " + top + "px, " + left + "px " + (top + height) + "px, " + (left + width) + "px " + (top + height) + "px, " + (left + width) + "px " + top + "px, " + left + "px " + top + "px)";
    const s = info.scale || devicePixelRatio || 1;
    size.textContent = Math.round(width * s) + " × " + Math.round(height * s);
    Object.assign(size.style, { display: "block", left: left + "px", top: Math.max(0, top - 26) + "px" });
  };

  const finish = async (r) => {
    if (busy) return;
    busy = true;
    const s = info.scale || devicePixelRatio || 1;
    const region = r ? { x: Math.round(info.x + r.left * s), y: Math.round(info.y + r.top * s), width: Math.round(r.width * s), height: Math.round(r.height * s) }
      : { x: info.x, y: info.y, width: Math.round(innerWidth * s), height: Math.round(innerHeight * s) };
    await invoke("close_selector");
    await sleep(120); // дать окну исчезнуть, чтобы оно не попало в кадр
    const res = await captureSelection(info.purpose, region);
    if (res && res.error) console.warn("select:", res.error);
    reset(info);
  };

  document.addEventListener("mousedown", (e) => { if (e.button !== 0) return; start = { x: e.clientX, y: e.clientY }; rect = { left: e.clientX, top: e.clientY, width: 0, height: 0 }; draw(); });
  document.addEventListener("mousemove", (e) => {
    if (!start) return;
    rect = { left: Math.min(start.x, e.clientX), top: Math.min(start.y, e.clientY), width: Math.abs(e.clientX - start.x), height: Math.abs(e.clientY - start.y) };
    draw();
  });
  document.addEventListener("mouseup", () => {
    if (!start) return;
    start = null;
    if (rect && rect.width > 4 && rect.height > 4) finish(rect); else { rect = null; box.style.display = "none"; size.style.display = "none"; shade.style.clipPath = "none"; }
  });
  document.addEventListener("contextmenu", (e) => { e.preventDefault(); invoke("close_selector"); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") invoke("close_selector");
    if (e.key === "Enter") finish(rect && rect.width > 4 ? rect : null);
  });
}
