// Микрофон: запись PCM16, автоостановка по тишине, распознавание через ядро (GigaSTT → Groq Whisper).
// После распознавания текст сразу уходит агенту (не нужно нажимать «Отправить») или печатается (диктовка).
import { core } from "./rpc.js";

function toPcm16(chunks) {
  const total = chunks.reduce((size, chunk) => size + chunk.length, 0);
  const pcm = new Int16Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++) pcm[offset++] = Math.max(-32768, Math.min(32767, chunk[i] * 32767));
  }
  return { pcm, total };
}

export class Mic {
  constructor(onState) {
    this.onState = onState || (() => {});
    this.active = false;
    this.mode = "ask";
    this.chunks = [];
    this.level = 0;
  }

  async start(mode = "ask", opts = {}) {
    if (this.active) return this.stop();
    this.mode = mode;
    this.opts = Object.assign({ silenceMs: 1300, maxMs: 60000, minMs: 500, deviceId: "" }, opts);
    try {
      const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
      if (this.opts.deviceId) audio.deviceId = { exact: this.opts.deviceId };
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (e) {
      this.onState({ state: "error", text: "Нет доступа к микрофону: " + (e.message || e) });
      return;
    }
    this.ctx = new AudioContext();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.chunks = [];
    this.active = true;
    this.started = performance.now();
    this.lastVoice = this.started;
    this.heard = false;
    let noise = 0.008;
    this.node.onaudioprocess = (ev) => {
      if (!this.active) return;
      const data = ev.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(data));
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length);
      this.level = Math.min(1, rms * 9);
      const now = performance.now();
      if (!this.heard) noise = noise * 0.95 + rms * 0.05;
      if (rms > Math.max(0.015, noise * 2.6)) { this.lastVoice = now; this.heard = true; }
      this.onState({ state: "listening", level: this.level, mode: this.mode });
      const elapsed = now - this.started;
      if (elapsed > this.opts.maxMs) this.stop();
      else if (this.heard && elapsed > this.opts.minMs && now - this.lastVoice > this.opts.silenceMs) this.stop();
      else if (!this.heard && elapsed > 8000) this.cancel();
    };
    src.connect(this.node);
    this.node.connect(this.ctx.destination);
    core.try("voice.stop");
    this.onState({ state: "listening", level: 0, mode: this.mode });
  }

  release() {
    this.active = false;
    try { this.node && this.node.disconnect(); } catch (e) { /* */ }
    try { this.stream && this.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* */ }
    const rate = this.ctx ? this.ctx.sampleRate : 48000;
    try { this.ctx && this.ctx.close(); } catch (e) { /* */ }
    return rate;
  }

  cancel() {
    if (!this.active) return;
    this.release();
    this.chunks = [];
    this.onState({ state: "idle" });
  }

  async stop() {
    if (!this.active) return;
    const rate = this.release();
    const { pcm, total } = toPcm16(this.chunks);
    this.chunks = [];
    if (total < rate * 0.3) { this.onState({ state: "idle" }); return; }
    this.onState({ state: "transcribing" });
    try {
      const res = await core.call("voice.transcribe", { sample_rate: rate, language: "ru" }, new Uint8Array(pcm.buffer), 90000);
      const text = ((res && res.text) || "").trim();
      if (!text) { this.onState({ state: "idle", text: "" }); return; }
      if (this.mode === "dictation") await core.call("dictation.type", { text });
      else await core.call("agent.ask", { text, source: "voice" });
      this.onState({ state: "idle", text });
    } catch (e) {
      this.onState({ state: "error", text: e.message || String(e) });
    }
  }
}
