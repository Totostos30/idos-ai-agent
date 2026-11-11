// WebSocket-клиент ядра Эйдоса: запросы {id, method, params}, ответы {id, result|error}, события {event, data}.
// Двоичные кадры: 4 байта длины заголовка (big-endian) + JSON-заголовок + данные.
import { invoke, sleep, view } from "./shell.js";

function decodeFrame(raw) {
  if (typeof raw === "string") {
    try { return { message: JSON.parse(raw), binary: null }; } catch (e) { return null; }
  }
  const view8 = new DataView(raw);
  const headerSize = view8.getUint32(0, false);
  return {
    message: JSON.parse(new TextDecoder().decode(new Uint8Array(raw, 4, headerSize))),
    binary: new Uint8Array(raw, 4 + headerSize),
  };
}

class Core {
  constructor() {
    this.ws = null;
    this.seq = 0;
    this.pending = new Map();
    this.handlers = new Map();
    this.connected = false;
    this.info = null;
    this.queue = [];
    this.loop();
  }

  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event).add(fn);
    return () => this.handlers.get(event).delete(fn);
  }

  fire(event, data, binary) {
    for (const key of [event, "*"]) {
      const set = this.handlers.get(key);
      if (!set) continue;
      for (const fn of set) {
        try { key === "*" ? fn(event, data, binary) : fn(data, binary); } catch (e) { console.error(event, e); }
      }
    }
  }

  async loop() {
    let delay = 400;
    for (;;) {
      let info = null;
      try { info = await invoke("core_info"); } catch (e) { info = { ready: false, error: String(e) }; }
      if (info && !info.ready && info.exited && view === "island") {
        const now = Date.now();
        this.restarts = (this.restarts || []).filter((t) => now - t < 180000);
        if (this.restarts.length < 5) {
          this.restarts.push(now);
          console.warn("core exited (" + info.exited + "), restarting");
          try { await invoke("restart_core"); } catch (e) { console.error(e); }
          await sleep(1500);
          continue;
        }
      }
      if (!info || !info.ready) {
        this.fire("core.waiting", info || {});
        await sleep(delay);
        delay = Math.min(delay * 1.5, 3000);
        continue;
      }
      this.info = info;
      const ok = await this.open(info);
      this.connected = false;
      this.fire("core.lost", { ok });
      for (const [, p] of this.pending) p.reject(new Error("Связь с ядром потеряна"));
      this.pending.clear();
      await sleep(ok ? 300 : delay);
      delay = ok ? 400 : Math.min(delay * 1.5, 3000);
    }
  }

  open(info) {
    return new Promise((resolve) => {
      let opened = false;
      const url = "ws://127.0.0.1:" + info.port + "/ws?token=" + encodeURIComponent(info.token || "");
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      ws.onopen = async () => {
        opened = true;
        this.connected = true;
        try { await this.call("session.hello", { window: view }); } catch (e) { /* ignore */ }
        const queued = this.queue.splice(0);
        queued.forEach((q) => q());
        this.fire("core.connected", info);
      };
      ws.onmessage = (ev) => this.receive(ev.data);
      ws.onclose = () => resolve(opened);
      ws.onerror = () => { /* закрытие обработает onclose */ };
    });
  }

  receive(raw) {
    const frame = decodeFrame(raw);
    if (!frame) return;
    const { message: msg, binary } = frame;
    if (msg.id !== undefined && msg.id !== null && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message || "Ошибка ядра"));
      else p.resolve(msg.result);
      return;
    }
    if (msg.event) this.fire(msg.event, msg.data, binary);
  }

  call(method, params = {}, binary = null, timeout = 120000) {
    const send = () => new Promise((resolve, reject) => {
      const id = ++this.seq;
      const message = { id, method, params };
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error("Ядро не ответило: " + method));
      }, timeout);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      if (binary) {
        const head = new TextEncoder().encode(JSON.stringify(message));
        const body = binary instanceof Uint8Array ? binary : new Uint8Array(binary.buffer || binary);
        const frame = new Uint8Array(4 + head.length + body.length);
        new DataView(frame.buffer).setUint32(0, head.length, false);
        frame.set(head, 4);
        frame.set(body, 4 + head.length);
        this.ws.send(frame);
      } else {
        this.ws.send(JSON.stringify(message));
      }
    });
    if (this.connected && this.ws && this.ws.readyState === 1) return send();
    if (method === "session.hello" && this.ws && this.ws.readyState === 1) return send();
    return new Promise((resolve, reject) => this.queue.push(() => send().then(resolve, reject)));
  }

  // Безопасный вызов: ошибка не роняет интерфейс, а возвращается как { error }.
  async try(method, params = {}, timeout = 120000) {
    try { return await this.call(method, params, null, timeout); } catch (e) { return { error: e.message || String(e) }; }
  }
}

export const core = new Core();
