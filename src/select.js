// Выбор области экрана для скриншота, OCR, QR и записи. Окно растянуто на весь монитор.
import { core } from "./rpc.js";
import { invoke, listen, el, sleep } from "./shell.js";

const HINTS = { shot: "Скриншот области", ocr: "Распознать текст", qr: "Сканировать QR-код", record: "Записать область" };

export function mountSelect() {
  document.body.classList.add("view-select");
  let info = { purpose: "shot", x: 0, y: 0, scale: 1 };
  let start = null, rect = null, busy = false, keyboardRect = false;
  const shade = el("div", { class: "sel-shade" });
  const box = el("div", { class: "sel-box" });
  const size = el("div", { class: "sel-size", role: "status", "aria-live": "polite", "aria-atomic": "true" });
  const hint = el("div", { class: "sel-hint", role: "status", "aria-live": "polite" });
  document.body.append(shade, box, size, hint);

  const reset = (i) => {
    info = Object.assign({ x: 0, y: 0, scale: 1, purpose: "shot" }, i || {});
    start = null; rect = null; busy = false; keyboardRect = false;
    box.style.display = "none"; size.style.display = "none";
    shade.style.clipPath = "none";
    hint.textContent = (HINTS[info.purpose] || "Выделите область") + " — мышью или S для клавиатуры · Enter — весь экран · Esc — отмена";
  };
  invoke("select_info").then(reset);
  listen("select-start", reset);

  const draw = () => {
    if (!rect) return;
    const { left, top, width, height } = rect;
    Object.assign(box.style, { display: "block", left: left + "px", top: top + "px", width: width + "px", height: height + "px" });
    shade.style.clipPath = "polygon(0 0, 100% 0, 100% 100%, 0 100%, 0 0, " + left + "px " + top + "px, " + left + "px " + (top + height) + "px, " + (left + width) + "px " + (top + height) + "px, " + (left + width) + "px " + top + "px, " + left + "px " + top + "px)";
    const s = info.scale || devicePixelRatio || 1;
    size.textContent = "X " + Math.round(left * s) + " · Y " + Math.round(top * s) + " · " + Math.round(width * s) + " × " + Math.round(height * s);
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
    const p = info.purpose;
    const res = p === "ocr" ? await core.try("capture.ocr", region)
      : p === "qr" ? await core.try("capture.qr", region)
      : p === "record" ? await core.try("recorder.start", region)
      : await core.try("capture.screenshot", Object.assign({ mode: "area", copy: true }, region));
    if (res && res.error) await core.try("ui.notify", { title: "Не удалось выполнить действие", text: res.error, kind: "error" });
    reset(info);
  };

  shade.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !e.isPrimary) return;
    keyboardRect = false;
    try { shade.setPointerCapture(e.pointerId); } catch (_) { /* capture can be unavailable in preview */ }
    start = { x: e.clientX, y: e.clientY };
    rect = { left: e.clientX, top: e.clientY, width: 0, height: 0 };
    draw();
  });
  document.addEventListener("pointermove", (e) => {
    if (!start) return;
    rect = { left: Math.min(start.x, e.clientX), top: Math.min(start.y, e.clientY), width: Math.abs(e.clientX - start.x), height: Math.abs(e.clientY - start.y) };
    draw();
  });
  document.addEventListener("pointerup", (e) => {
    if (!start) return;
    try { if (shade.hasPointerCapture(e.pointerId)) shade.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    start = null;
    if (rect && rect.width > 4 && rect.height > 4) finish(rect); else { rect = null; box.style.display = "none"; size.style.display = "none"; shade.style.clipPath = "none"; }
  });
  document.addEventListener("pointercancel", () => {
    start = null; rect = null; box.style.display = "none"; size.style.display = "none"; shade.style.clipPath = "none";
  });
  document.addEventListener("contextmenu", (e) => { e.preventDefault(); invoke("close_selector"); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") invoke("close_selector");
    if ((e.key === "s" || e.key === "S") && !keyboardRect) {
      keyboardRect = true;
      start = { x: Math.round(innerWidth / 2), y: Math.round(innerHeight / 2) };
      rect = { left: start.x, top: start.y, width: 0, height: 0 };
      hint.textContent = "Стрелки — переместить · Shift+стрелки — расширить · Alt+Shift+стрелки — сузить · Enter — подтвердить · Esc — отмена";
      draw(); e.preventDefault(); return;
    }
    const delta = e.shiftKey ? 12 : 16;
    const direction = { ArrowLeft: [-delta, 0], ArrowRight: [delta, 0], ArrowUp: [0, -delta], ArrowDown: [0, delta] }[e.key];
    if (keyboardRect && direction && rect) {
      if (e.shiftKey) {
        if (e.altKey) {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") rect.width = Math.max(8, rect.width - delta);
          if (e.key === "ArrowUp" || e.key === "ArrowDown") rect.height = Math.max(8, rect.height - delta);
        } else {
          if (e.key === "ArrowLeft") { const grow = Math.min(delta, rect.left); rect.left -= grow; rect.width += grow; }
          if (e.key === "ArrowRight") rect.width = Math.min(innerWidth - rect.left, rect.width + delta);
          if (e.key === "ArrowUp") { const grow = Math.min(delta, rect.top); rect.top -= grow; rect.height += grow; }
          if (e.key === "ArrowDown") rect.height = Math.min(innerHeight - rect.top, rect.height + delta);
        }
      } else {
        rect.left = Math.max(0, Math.min(innerWidth - rect.width, rect.left + direction[0]));
        rect.top = Math.max(0, Math.min(innerHeight - rect.height, rect.top + direction[1]));
      }
      draw(); e.preventDefault(); return;
    }
    if (e.key === "Enter") finish(rect && rect.width > 4 && rect.height > 4 ? rect : null);
  });
}
