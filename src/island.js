// Остров Эйдоса: «живая» капсула сверху экрана, которая перетекает между состояниями:
// покой, музыка, агент думает/действует/говорит, подтверждение, таймер, запись экрана, командная строка,
// раскрытая панель с виджетами. Уведомления (карточки) выплывают под капсулой.
import { core } from "./rpc.js";
import { invoke, listen, el, fmtTime, inTauri, micIcon, gDots, setMicState } from "./shell.js";
import { Mic } from "./voice.js";
import { Fluid } from "./fluid.js";
import { PARTS, TAB_LIST, TOOL_LIST, partOn, listOn, partValues, RESET_VALUES, applyValues } from "./parts.js";

// видна ли часть интерфейса (настраивается в панели кнопкой ✎ или в настройках)
const on = (key) => partOn(S.cfg.island, key);

const ICONS = {
  spark: "✦", app: "▣", globe: "◎", link: "↗", calc: "=", convert: "⇄", timer: "⏱", bell: "⚑",
  settings: "⚙", system: "⏻", history: "↺", file: "▤", clip: "⎘", note: "✎", info: "i", ok: "✓",
  error: "!", warn: "!", shot: "▢", rec: "●", music: "♪", download: "↓",
};
const icon = (name) => ICONS[name] || "✦";

const S = {
  cfg: { island: {}, hotkeys: {} },
  agent: { state: "idle", label: "", text: "", steps: [], run: null, confirm: null, lastReply: "" },
  media: { active: false },
  bands: new Array(12).fill(0),
  lyric: "",
  timers: new Map(),
  rec: { recording: false },
  obs: {},
  cards: [],
  mic: { state: "idle", level: 0 },
  bar: { open: false, text: "", items: [], sel: 0 },
  expanded: false,
  pinned: false,
  tab: "now",
  clip: [],
  status: {},
  weather: {},
  online: false,
  flash: 0,
  away: false,
  hover: false,
  near: false,
  kbd: {},
  privacy: {},
};

let root, isl, cardsBox, fluid, lip, leaveTimer = null, barTimer = null;
// автоскрытие: полоска → (навели) капсула → (держишь курсор) большая панель
let quietSince = Date.now(), tuckTimer = null, peeked = false, lastSig = "";
const mic = new Mic((st) => { S.mic = st; document.querySelectorAll("button.mic").forEach((b) => setMicState(b, st)); if (st.state === "error") pushCard({ title: "Микрофон", text: st.text, kind: "error" }); render(); });

// ------------------------------------------------------------------ mode selection

const BUSY = new Set(["thinking", "working", "acting", "speaking", "searching", "waiting", "stopping"]);

function mode() {
  if (S.bar.open) return "bar";
  if (S.agent.confirm) return "confirm";
  if (S.expanded || S.pinned) return "panel";
  if (S.mic.state === "listening" || S.mic.state === "transcribing") return "mic";
  if (BUSY.has(S.agent.state)) return S.agent.text && S.agent.state === "speaking" ? "reply" : "agent";
  if (S.agent.showReply) return "reply";
  if (S.rec.recording) return "rec";
  if (runningTimers().length) return "timer";
  if (S.media.active && S.media.title) return "music";
  return "idle";
}

const runningTimers = () => [...S.timers.values()].filter((t) => t.state === "running");
function timerLeft(t) {
  const rawEnd = Number(t.ends_at ?? t.ends);
  if (Number.isFinite(rawEnd) && rawEnd > 0) {
    const endSeconds = rawEnd > 100_000_000_000 ? rawEnd / 1000 : rawEnd;
    return endSeconds - Date.now() / 1000;
  }
  return Number(t.seconds) || 0;
}

// ------------------------------------------------------------------ rendering

function orb(extra = "") {
  const ch = (S.cfg.island.character || "liquid");
  if (ch === "eyes") return el("span", { class: "eyes " + extra + " s-" + S.agent.state },
    el("span", { class: "eye" }, el("i")), el("span", { class: "eye" }, el("i")));
  return el("span", { class: "orb " + ch + " " + extra + " s-" + (S.mic.state === "listening" ? "listening" : S.agent.state) },
    el("i"), el("i"), el("i"));
}

function eq(n = 5) {
  const box = el("span", { class: "eq" });
  const bands = S.bands;
  for (let i = 0; i < n; i++) {
    const v = bands.length ? bands[Math.floor((i / n) * bands.length)] || 0 : 0;
    const h = S.media.playing ? Math.max(0.15, Math.min(1, v)) : 0.15;
    box.append(el("b", { style: { transform: "scaleY(" + h.toFixed(3) + ")" } }));
  }
  return box;
}

function cover(size = "s") {
  const src = S.media.cover;
  return el("span", { class: "cover " + size, style: src ? { backgroundImage: "url(" + JSON.stringify(src) + ")" } : {} },
    src ? null : icon("music"));
}

function pillRight() {
  const parts = [];
  if (S.cfg.island.show_weather !== false && S.weather && S.weather.temp !== undefined)
    parts.push(el("span", { class: "chip" }, (S.weather.icon || "") + " " + Math.round(S.weather.temp) + "°"));
  if (S.cfg.island.show_battery !== false && batInfo())
    parts.push(el("span", { class: "chip bat" + (batInfo().charging ? " charging" : "") + (batInfo().percent < 20 ? " low" : "") },
      (batInfo().charging ? "⚡" : "") + Math.round(batInfo().percent) + "%"));
  return parts;
}

// Ядро присылает батарею объектом { percent, charging, saver, no_battery }; старые версии — числом.
function batInfo() {
  const st = S.status || {};
  let b = st.battery;
  if (b === undefined || b === null) return null;
  if (typeof b !== "object") b = { percent: b, charging: st.charging };
  if (b.no_battery || b.percent === undefined || b.percent === null) return null;
  const p = Number(b.percent);
  if (!Number.isFinite(p)) return null;
  return { percent: Math.max(0, Math.min(100, Math.round(p))), charging: !!b.charging, saver: !!b.saver };
}

function clock() {
  const d = new Date();
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

const VIEWS = {
  idle: () => {
    const liquid = (S.cfg.island.character || "liquid") === "liquid";
    const bat = on("battery") && batInfo()
      ? el("span", { class: "bat-mini" + (batInfo().charging ? " charging" : "") + (batInfo().percent < 20 ? " low" : "") },
        el("i", { style: { width: Math.max(8, Math.min(100, batInfo().percent)) + "%" } }), el("b", {}, Math.round(batInfo().percent))) : null;
    const temp = on("weather") && S.weather && S.weather.temp !== undefined && S.weather.temp !== null
      ? el("span", { class: "temp" }, (S.weather.icon || "") + Math.round(S.weather.temp) + "°") : null;
    if (on("volume_osd") && S.vol && Date.now() - S.vol.at < 1200) return [el("div", { class: "row pill" }, el("span", { class: "ic" }, S.vol.v ? "🔊" : "🔇"),
      el("span", { class: "vol" }, el("i", { style: { width: S.vol.v + "%" } })), el("span", { class: "mono sub" }, String(S.vol.v)))];
    return [el("div", { class: "row pill tri" },
      el("span", { class: "side l" }, liquid || !on("orb") ? null : orb(), ...dots()),
      el("span", { class: "mid" }, on("clock") ? el("span", { class: "clock" }, clock()) : null, bat),
      el("span", { class: "side r" }, !S.online && on("core_status") ? el("span", { class: "chip warn" }, "ядро…") : temp))];
  },
  music: () => {
    const kind = musicKind();
    const m = S.media;
    const prog = m.duration ? Math.min(100, ((m.position || 0) / m.duration) * 100).toFixed(1) + "%" : "0%";
    const lyricOn = on("lyrics") && S.lyric;
    const cov = (size) => (on("music_cover") ? cover(size) : null);
    const bars = () => (on("music_eq") ? eq(5) : null);
    const thin = () => (on("music_progress") ? el("span", { class: "thin" }, el("i", { style: { width: prog } })) : null);
    if (kind === "normal") return [el("div", { class: "row pill" }, cov("s"), el("span", { class: "grow" }), bars())];
    if (kind === "wide") return [el("div", { class: "row pill" }, cov("s"),
      on("music_title") || on("music_progress") ? el("span", { class: "wide-meta" },
        on("music_title") ? el("span", { class: "marquee" }, el("span", {}, (m.title || "") + (m.artist ? " — " + m.artist : ""))) : null,
        thin()) : el("span", { class: "grow" }),
      bars()), lyricOn ? lyricPill() : null];
    return [el("div", { class: "row mini" }, cov("m"),
      el("span", { class: "mini-meta" },
        on("music_title") ? el("span", { class: "t" }, m.title || "Без названия") : null,
        lyricOn ? el("span", { class: "sung", key: S.lyric }, S.lyric) : on("music_title") ? el("span", { class: "a" }, m.artist || m.app || "") : null,
        thin()),
      !on("music_controls") ? null : el("span", { class: "mini-ctl" },
        el("button", { class: "icon", disabled: !m.can_prev, onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "previous" }); } }, "⏮"),
        el("button", { class: "icon", onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "play_pause" }); } }, m.playing ? "⏸" : "▶"),
        el("button", { class: "icon", disabled: !m.can_next, onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "next" }); } }, "⏭")))];
  },
  agent: () => [
    el("div", { class: "row pill" }, orb("big"),
      el("span", { class: "label" }, S.agent.label || stateLabel(S.agent.state)),
      el("span", { class: "grow" }),
      on("agent_stop") ? el("button", { class: "icon stop", title: "Остановить", onclick: (e) => { e.stopPropagation(); stopAll(); } }, "■") : null),
    on("agent_steps") ? stepsMini() : null,
  ],
  reply: () => [
    el("div", { class: "row" }, orb("big"), el("span", { class: "label" }, S.agent.state === "speaking" ? "Говорю…" : "Ответ"),
      el("span", { class: "grow" }),
      el("button", { class: "icon", title: "Открыть диалог", onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "chat" }); } }, "↗"),
      el("button", { class: "icon", title: "Закрыть", onclick: (e) => { e.stopPropagation(); S.agent.showReply = false; core.try("voice.stop"); render(); } }, "✕")),
    on("reply_text") ? el("div", { class: "reply-text" }, S.agent.text || S.agent.lastReply) : null,
  ],
  mic: () => [
    el("div", { class: "row pill" }, orb("big"),
      el("span", { class: "label" }, S.mic.state === "transcribing" ? "Распознаю…" : S.mic.mode === "dictation" ? "Диктовка…" : "Слушаю…"),
      el("span", { class: "grow" }), on("mic_meter") ? meter(S.mic.level || 0) : null),
  ],
  rec: () => [
    el("div", { class: "row pill" }, el("span", { class: "rec-dot" }),
      el("span", { class: "label mono" }, fmtTime(S.rec.elapsed || (S.rec.started_at ? Date.now() / 1000 - S.rec.started_at : 0))),
      el("span", { class: "grow" }),
      el("button", { class: "icon stop", title: "Остановить запись", onclick: (e) => { e.stopPropagation(); core.try("recorder.stop"); } }, "■")),
  ],
  timer: () => {
    const t = runningTimers().sort((a, b) => timerLeft(a) - timerLeft(b))[0];
    const left = timerLeft(t);
    return [el("div", { class: "row pill" }, el("span", { class: "ic" }, icon("timer")),
      el("span", { class: "label mono" }, fmtTime(left)), el("span", { class: "sub" }, t.label || ""),
      el("span", { class: "grow" }),
      el("button", { class: "icon", title: "Отменить", onclick: (e) => { e.stopPropagation(); core.try("island.cancel_timer", { timer_id: t.id }); } }, "✕"))];
  },
  confirm: () => {
    const c = S.agent.confirm;
    return [
      el("div", { class: "row" }, orb("big"), el("span", { class: "label" }, "Разрешить действие?")),
      el("div", { class: "confirm-text" }, c.label || c.tool || ""),
      c.preview ? el("pre", { class: "preview" }, typeof c.preview === "string" ? c.preview : JSON.stringify(c.preview, null, 1)) : null,
      el("div", { class: "row actions" },
        el("button", { class: "btn ghost", onclick: () => answerConfirm(false) }, "Отклонить"),
        el("button", { class: "btn primary", onclick: () => answerConfirm(true) }, "Разрешить")),
    ];
  },
  bar: () => {
    const input = el("input", {
      class: "bar-input", placeholder: "Спросите Эйдос, найдите приложение, посчитайте…", value: S.bar.text,
      spellcheck: "false", autocomplete: "off",
    });
    input.addEventListener("input", () => { S.bar.text = input.value; S.bar.sel = 0; queryBar(); });
    input.addEventListener("keydown", barKey);
    const list = el("div", { class: "bar-list" }, S.bar.items.slice(0, 8).map((it, i) =>
      el("div", { class: "bar-item" + (i === S.bar.sel ? " sel" : ""), onmousemove: () => { if (S.bar.sel !== i) { S.bar.sel = i; render(); } }, onclick: () => runBar(i) },
        el("span", { class: "ic" }, icon(it.icon)), el("span", { class: "t" }, it.title), el("span", { class: "s" }, it.subtitle || ""))));
    setTimeout(() => { const i = isl.querySelector(".bar-input"); if (i && document.activeElement !== i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 0);
    return [el("div", { class: "row" }, orb(), input,
      micButton(() => { closeBar(); mic.start("ask", micOpts()); })), list];
  },
  panel: () => panel(),
};

function musicKind() {
  const k = S.cfg.island.music_pill || "wide";
  return k === "compact" ? "normal" : k === "mini" || k === "normal" ? k : "wide";
}

// Строка песни в своей пилюле: пропетая строка подсвечивается слева направо (караоке).
function lyricPill() {
  const dur = Math.max(1.2, Math.min(8, S.lyricDur || 3));
  return el("div", { class: "lyric-pill" }, el("span", { class: "sung", key: S.lyric, style: { "--dur": dur + "s" } }, S.lyric));
}

// Точки микрофона и камеры (клик по микрофону — выключить звук), раскладка, Caps Lock, VPN.
function dots() {
  const out = [];
  const p = S.privacy || {};
  if (on("privacy") && p.mic) out.push(el("button", { class: "dot mic" + (p.mic_muted ? " muted" : ""), title: p.mic_app ? "Микрофон: " + p.mic_app : "Микрофон включён",
    onclick: (e) => { e.stopPropagation(); core.try("status.mic_mute", {}); } }));
  if (on("privacy") && p.camera) out.push(el("span", { class: "dot cam", title: p.camera_app ? "Камера: " + p.camera_app : "Камера включена" }));
  if (on("caps") && S.kbd.caps) out.push(el("span", { class: "chip caps" }, "⇪"));
  if (on("layout") && S.kbd.layout && S.kbdShow) out.push(el("span", { class: "chip lay" }, S.kbd.layout));
  if (on("vpn") && S.status && S.status.vpn && S.status.vpn.on) out.push(el("span", { class: "chip vpn", title: S.status.vpn.name || "VPN" }, "VPN"));
  return out;
}

function stateLabel(s) {
  return ({ thinking: "Думаю…", working: "Работаю…", acting: "Действую…", searching: "Ищу…", speaking: "Говорю…",
    waiting: "Жду подтверждения", stopping: "Останавливаюсь…" })[s] || "Готов";
}

function meter(level) {
  const box = el("span", { class: "eq mic" });
  for (let i = 0; i < 5; i++) {
    const k = [0.55, 0.8, 1, 0.8, 0.55][i];
    box.append(el("b", { style: { transform: "scaleY(" + Math.max(0.15, Math.min(1, level * k * 1.4)).toFixed(3) + ")" } }));
  }
  return box;
}

function stepsMini() {
  const steps = S.agent.steps.slice(-3);
  if (!steps.length) return null;
  return el("div", { class: "steps" }, steps.map((s) =>
    el("div", { class: "step " + (s.status || "") }, el("span", { class: "st" }, s.status === "running" ? "◌" : s.status === "ok" ? "✓" : s.status === "error" ? "!" : s.status === "denied" ? "✕" : "·"),
      el("span", { class: "t" }, s.label || s.tool), s.ms ? el("span", { class: "ms" }, (s.ms / 1000).toFixed(1) + "с") : null)));
}

function visibleTabs() {
  const tabs = TAB_LIST.filter(([id]) => listOn(S.cfg.island, "tabs", id));
  return tabs.length ? tabs : [TAB_LIST[0]];
}

function toggleEdit() {
  if (S.tab === "edit") { S.tab = S.tabBeforeEdit || visibleTabs()[0][0]; S.pinned = !!S.pinBeforeEdit; }
  else { S.tabBeforeEdit = S.tab; S.pinBeforeEdit = S.pinned; S.tab = "edit"; S.pinned = true; }
  render();
}

function panel() {
  const tabs = visibleTabs();
  if (S.tab !== "edit" && !tabs.some(([id]) => id === S.tab)) S.tab = tabs[0][0];
  const editing = S.tab === "edit";
  const head = el("div", { class: "row tabs" }, on("h_orb") ? orb() : null,
    editing ? el("span", { class: "label" }, "Настройка островка")
      : tabs.length > 1 ? tabs.map(([id, name]) => el("button", { class: "tab" + (S.tab === id ? " on" : ""), onclick: (e) => { e.stopPropagation(); S.tab = id; if (id === "clip") loadClip(); render(); } }, name))
      : el("span", { class: "label" }, tabs[0][1]),
    el("span", { class: "grow" }),
    on("h_edit") || editing ? el("button", { class: "icon" + (editing ? " on" : ""), title: editing ? "Готово" : "Настроить: что показывать", onclick: (e) => { e.stopPropagation(); toggleEdit(); } }, editing ? "✓" : "✎") : null,
    on("h_pin") && !editing ? el("button", { class: "icon" + (S.pinned ? " on" : ""), title: "Закрепить", onclick: (e) => { e.stopPropagation(); S.pinned = !S.pinned; render(); } }, "⌂") : null,
    on("h_open") && !editing ? el("button", { class: "icon", title: "Окно Эйдоса", onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "chat" }); } }, "↗") : null);
  const body = ({ now: panelNow, tools: panelTools, clip: panelClip, agent: panelAgent, edit: panelEdit })[S.tab]();
  return [head, el("div", { class: "panel-body" + (editing ? " edit" : "") }, body)];
}

async function saveValues(values) {
  applyValues(S.cfg, values);
  render();
  const r = await core.try("settings.update", { values });
  if (r && r.error) toastCard("Не удалось сохранить", r.error);
}

function toastCard(title, text) {
  S.cards.push({ id: "edit-" + Date.now(), kind: "error", title, text, at: Date.now() });
  renderCards();
}

function hiddenList(kind, id, show) {
  const cur = new Set(S.cfg.island["hidden_" + kind] || []);
  if (show) cur.delete(id); else cur.add(id);
  return { ["island.hidden_" + kind]: [...cur] };
}

function chipT(label, shown, fn) {
  return el("button", { class: "chip-t" + (shown ? " on" : ""), title: shown ? "Скрыть" : "Показать", onclick: (e) => { e.stopPropagation(); fn(); } }, label);
}

function editGroup(title, chips) {
  return el("div", { class: "edit-group" }, el("div", { class: "eg-t" }, title), el("div", { class: "chips" }, chips));
}

// Режим «✎»: каждый элемент капсулы и панели — переключатель-чип, изменения видны сразу.
function panelEdit() {
  const I = S.cfg.island;
  const groups = new Map();
  for (const [key, label, group] of PARTS) {
    if (!groups.has(group)) groups.set(group, []);
    const shown = partOn(I, key);
    groups.get(group).push(chipT(label, shown, () => saveValues(partValues(key, !shown))));
  }
  const out = [el("div", { class: "sub edit-hint" }, "Нажмите на элемент, чтобы скрыть или показать его. Капсула меняется сразу.")];
  out.push(editGroup("Вкладки панели", TAB_LIST.map(([id, name]) => {
    const shown = listOn(I, "tabs", id);
    return chipT(name, shown, () => saveValues(hiddenList("tabs", id, !shown)));
  })));
  for (const [group, chips] of groups) out.push(editGroup(group, chips));
  out.push(editGroup("Кнопки на вкладке «Инструменты»", TOOL_LIST.map(([id, name]) => {
    const shown = listOn(I, "tools", id);
    return chipT(name, shown, () => saveValues(hiddenList("tools", id, !shown)));
  })));
  out.push(el("div", { class: "row actions" },
    el("button", { class: "btn ghost", onclick: (e) => { e.stopPropagation(); saveValues(RESET_VALUES); } }, "Показать всё"),
    el("span", { class: "grow" }),
    el("button", { class: "btn primary", onclick: (e) => { e.stopPropagation(); toggleEdit(); } }, "Готово")));
  return out;
}

function panelNow() {
  const blocks = [];
  if (on("p_player") && S.media.active) {
    const m = S.media;
    const pos = m.position || 0, dur = m.duration || 0;
    blocks.push(el("div", { class: "player", style: m.accent ? { "--accent": m.accent } : {} },
      cover("l"),
      el("div", { class: "meta" },
        el("div", { class: "title" }, m.title || "Без названия"), el("div", { class: "artist" }, [m.artist, m.app].filter(Boolean).join(" · ")),
        on("p_lyrics") && S.lyric ? el("div", { class: "lyric" }, S.lyric) : null,
        dur && on("p_progress") ? el("div", { class: "progress", onclick: (e) => { if (!m.can_seek) return; const r = e.currentTarget.getBoundingClientRect(); core.try("media.control", { action: "seek", position: ((e.clientX - r.left) / r.width) * dur }); } },
          el("i", { style: { width: Math.min(100, (pos / dur) * 100).toFixed(1) + "%" } })) : null,
        dur && on("p_progress") ? el("div", { class: "times mono" }, el("span", {}, fmtTime(pos)), el("span", {}, fmtTime(dur))) : null,
        el("div", { class: "controls" },
          el("button", { class: "icon", disabled: !m.can_prev, onclick: () => core.try("media.control", { action: "previous" }) }, "⏮"),
          el("button", { class: "icon big", onclick: () => core.try("media.control", { action: "play_pause" }) }, m.playing ? "⏸" : "▶"),
          el("button", { class: "icon", disabled: !m.can_next, onclick: () => core.try("media.control", { action: "next" }) }, "⏭"),
          on("p_eq") ? eq(9) : null))));
  }
  const widgets = el("div", { class: "widgets" });
  if (on("w_weather") && S.weather && S.weather.temp !== undefined)
    widgets.append(el("div", { class: "widget" }, el("div", { class: "big" }, (S.weather.icon || "") + " " + Math.round(S.weather.temp) + "°"),
      el("div", { class: "sub" }, [S.weather.city, S.weather.text].filter(Boolean).join(" · "))));
  if (on("w_clock")) widgets.append(el("div", { class: "widget" }, el("div", { class: "big mono" }, clock()),
    el("div", { class: "sub" }, new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" }))));
  if (on("w_battery") && batInfo())
    widgets.append(el("div", { class: "widget" }, el("div", { class: "big" }, (batInfo().charging ? "⚡ " : "") + Math.round(batInfo().percent) + "%"),
      el("div", { class: "sub" }, batInfo().charging ? "Заряжается" : "Батарея")));
  for (const t of on("w_timers") ? S.timers.values() : []) {
    const left = t.state === "running" && t.ends_at ? t.ends_at - Date.now() / 1000 : 0;
    widgets.append(el("div", { class: "widget timer " + t.state },
      el("div", { class: "big mono" }, t.state === "done" ? "Готово" : fmtTime(left)),
      el("div", { class: "sub" }, t.label || "Таймер"),
      el("button", { class: "x", onclick: () => { core.try("island.cancel_timer", { timer_id: t.id }); S.timers.delete(t.id); render(); } }, "✕")));
  }
  if (widgets.children.length) blocks.push(widgets);
  if (!blocks.length) blocks.push(el("div", { class: "empty" }, "Здесь всё скрыто — нажмите ✎, чтобы вернуть элементы"));
  return blocks;
}

const TOOL_BUTTONS = [
  ["shot_area", "▢", "Область", () => hotkey("shot_area")],
  ["shot_screen", "▭", "Экран", () => hotkey("shot_screen")],
  ["ocr", "T", "Текст (OCR)", () => hotkey("ocr")],
  ["qr", "▦", "QR-код", () => hotkey("qr")],
  ["color", "◉", "Пипетка", () => hotkey("color")],
  ["record", "●", "Запись экрана", () => hotkey("record")],
  ["record_area", "⬚", "Запись области", () => invoke("open_selector", { purpose: "record" })],
  ["dictation", "⌨", "Диктовка", () => hotkey("dictation")],
  ["note", "✎", "Заметка", () => openBar("заметка: ")],
  ["timer", "⏱", "Таймер", () => openBar("таймер ")],
];

function panelTools() {
  const grid = el("div", { class: "grid" }, TOOL_BUTTONS.filter(([id]) => listOn(S.cfg.island, "tools", id)).map(([, ic, name, fn]) =>
    el("button", { class: "tile", onclick: (e) => { e.stopPropagation(); S.expanded = false; S.pinned = false; render(); fn(); } }, el("span", { class: "ic" }, ic), el("span", {}, name))));
  const out = [grid];
  if (on("obs") && S.obs && S.obs.connected) {
    out.push(el("div", { class: "row obs" }, el("span", { class: "sub" }, "OBS"),
      el("button", { class: "btn" + (S.obs.recording ? " danger" : ""), onclick: () => core.try("obs.record", { command: "toggle" }) }, S.obs.recording ? "■ Запись" : "● Запись"),
      el("button", { class: "btn" + (S.obs.streaming ? " danger" : ""), onclick: () => core.try("obs.stream", { command: "toggle" }) }, S.obs.streaming ? "■ Эфир" : "▶ Эфир"),
      (S.obs.scenes || []).slice(0, 4).map((sc) => el("button", { class: "btn ghost" + (sc === S.obs.scene ? " on" : ""), onclick: () => core.try("obs.set_scene", { name: sc }) }, sc))));
  }
  return out;
}

async function loadClip() {
  const res = await core.try("clipboard.list", { limit: 20 });
  if (Array.isArray(res)) { S.clip = res; render(); }
  else if (res && Array.isArray(res.items)) { S.clip = res.items; render(); }
}

function panelClip() {
  if (!S.clip.length) return [el("div", { class: "empty" }, "История буфера пуста")];
  return [el("div", { class: "clip-list" }, S.clip.map((c) =>
    el("div", { class: "clip" + (c.pinned ? " pinned" : ""), title: "Вставить", onclick: () => core.try("clipboard.paste", { item_id: c.id }) },
      c.kind === "image" && c.preview ? el("img", { src: c.preview }) : el("span", { class: "t" }, (c.text || c.preview || "").slice(0, 160)),
      el("span", { class: "grow" }),
      el("button", { class: "icon", title: c.pinned ? "Открепить" : "Закрепить", onclick: async (e) => { e.stopPropagation(); await core.try("clipboard.pin", { item_id: c.id, pinned: !c.pinned }); loadClip(); } }, c.pinned ? "★" : "☆"),
      el("button", { class: "icon", title: "Удалить", onclick: async (e) => { e.stopPropagation(); await core.try("clipboard.delete", { item_id: c.id }); loadClip(); } }, "✕"))))];
}

function panelAgent() {
  const input = el("input", { class: "bar-input small", placeholder: "Задача для Эйдоса…" });
  input.addEventListener("focus", () => invoke("island_focus", { focus: true }));
  input.addEventListener("blur", () => invoke("island_focus", { focus: false }));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) { core.try("agent.ask", { text: input.value.trim(), source: "island" }); input.value = ""; input.blur(); }
    if (e.key === "Escape") input.blur();
  });
  return [
    el("div", { class: "row" }, el("span", { class: "label" }, stateLabel(S.agent.state)), el("span", { class: "grow" }),
      on("pa_model") ? el("span", { class: "chip" }, S.router || "") : null,
      BUSY.has(S.agent.state) ? el("button", { class: "btn danger", onclick: stopAll }, "■ Стоп") : null),
    on("pa_steps") ? stepsMini() : null,
    on("pa_reply") && S.agent.lastReply ? el("div", { class: "reply-text" }, S.agent.lastReply) : null,
    on("pa_input") ? el("div", { class: "row" }, input, micButton(() => mic.start("ask", micOpts()))) : null,
  ];
}

function renderCards() {
  cardsBox.replaceChildren(...S.cards.map((c) =>
    el("div", { class: "card " + (c.kind || "info") + (c.out ? " out" : "") },
      c.swatch ? el("span", { class: "ic swatch", style: { background: c.swatch } }) : el("span", { class: "ic" }, c.icon && c.icon.length <= 2 ? c.icon : icon(c.icon || c.kind)),
      el("div", { class: "body" }, el("div", { class: "title" }, c.title || ""), c.text ? el("div", { class: "text" }, c.text) : null,
        c.image ? el("img", { class: "thumb", src: c.image }) : null,
        (c.actions || []).length ? el("div", { class: "row actions" }, c.actions.map((a) =>
          el("button", { class: "btn" + (a.primary ? " primary" : ""), onclick: () => { core.try("island.action", { card_id: c.id, action: a.id, data: a.data }); dropCard(c.id); } }, a.label))) : null),
      el("button", { class: "icon x", onclick: () => dropCard(c.id) }, "✕"))));
}

function tucked(m) {
  const I = S.cfg.island;
  // Смена режима, трека или состояния воспроизведения запускает новый короткий показ.
  const sig = m + "|" + (S.media.track_id || S.media.title || "") + "|" + (S.media.playing ? 1 : 0);
  if (sig !== lastSig) { lastSig = sig; quietSince = Date.now(); }
  // Обычные задачи не скрываем. Автоскрытие действует только для покоя и музыки.
  const transient = m === "idle" || m === "music";
  const quiet = !!I.auto_hide && !S.hover && !S.near && !S.expanded && !S.pinned && transient && !S.cards.length && !S.bar.open;
  if (!quiet) { quietSince = Date.now(); clearTimeout(tuckTimer); tuckTimer = null; return false; }
  const wait = m === "music" ? 3000 : 1000;
  const left = quietSince + wait - Date.now();
  if (left <= 0) { peeked = false; return true; }
  if (!tuckTimer) tuckTimer = setTimeout(() => { tuckTimer = null; render(); }, left + 30);
  return false;
}

function peek() {
  if (!root.classList.contains("hidden")) return;
  peeked = true; S.hover = true; S.peekAt = Date.now();
  clearTimeout(leaveTimer);
  render();
}

let lastMode = "";
function render() {
  if (!isl) return;
  const m = mode();
  if (m !== lastMode) { isl.dataset.prev = lastMode; lastMode = m; isl.classList.remove("pop", "enter"); void isl.offsetWidth; isl.classList.add("pop", "enter"); clearTimeout(render.enterT); render.enterT = setTimeout(() => isl.classList.remove("enter"), 380); }
  isl.dataset.mode = m;
  isl.dataset.edge = S.cfg.island.eq_edge === false ? "off" : "on";
  isl.dataset.playing = S.media.active && S.media.playing ? "1" : "0";
  isl.style.setProperty("--accent", S.media.active && S.media.accent ? S.media.accent : "#7c8cff");
  isl.style.setProperty("--cover", S.media.active && S.media.cover ? "url(" + JSON.stringify(S.media.cover) + ")" : "none");
  isl.classList.toggle("flash", Date.now() - S.flash < 500);
  isl.classList.toggle("away", S.away);
  isl.dataset.music = musicKind();
  isl.dataset.lyric = m === "music" && musicKind() === "wide" && S.cfg.island.lyrics !== false && S.lyric ? "1" : "0";
  root.classList.toggle("hidden", tucked(m));
  root.dataset.playing = isl.dataset.playing;
  root.style.setProperty("--accent", isl.style.getPropertyValue("--accent"));
  root.classList.toggle("through", !!S.cfg.island.through_mode && (m === "idle" || m === "music" || m === "timer" || m === "rec"));
  const focusedValue = m === "bar" ? null : undefined;
  isl.replaceChildren(...(VIEWS[m] || VIEWS.idle)().filter(Boolean));
  void focusedValue;
  renderCards();
  scheduleHit();
}

// ------------------------------------------------------------------ hit region (клики сквозь пустое место)

let hitQueued = false;
function scheduleHit() {
  if (hitQueued) return;
  hitQueued = true;
  requestAnimationFrame(() => {
    hitQueued = false;
    const r = isl.getBoundingClientRect();
    if (root.classList.contains("through")) { invoke("set_hit_region", { x: 0, y: 0, width: 0, height: 0 }); return; }
    if (root.classList.contains("hidden")) { const cx = r.left + r.width / 2; invoke("set_hit_region", { x: Math.round(cx - 110), y: 0, width: 220, height: 12 }); return; }
    let x1 = r.left, y1 = 0, x2 = r.right, y2 = r.bottom;
    for (const c of cardsBox.children) {
      const cr = c.getBoundingClientRect();
      x1 = Math.min(x1, cr.left); x2 = Math.max(x2, cr.right); y2 = Math.max(y2, cr.bottom);
    }
    const pad = S.cfg.island.edge_open === false ? 4 : 8;
    invoke("set_hit_region", { x: x1 - pad, y: y1, width: x2 - x1 + pad * 2, height: y2 - y1 + pad });
  });
}

// ------------------------------------------------------------------ actions

function micButton(onclick) {
  const b = el("button", { class: "icon mic", title: "Голосом", onclick: () => (mic.active ? mic.stop() : onclick()) }, micIcon());
  setMicState(b, S.mic);
  return b;
}

function micOpts() {
  const a = S.cfg.audio || {};
  return { deviceId: a.input_device || "", silenceMs: a.silence_ms || 1300 };
}

function stopAll() {
  mic.cancel();
  core.try("agent.stop");
  core.try("voice.stop");
  pushCard({ title: "Остановлено", text: "Агент прервал текущую задачу", kind: "warn", timeout: 3 });
}

function answerConfirm(approve) {
  const c = S.agent.confirm;
  if (!c) return;
  S.agent.confirm = null;
  core.try("agent.confirm", { step_id: c.id, approve });
  render();
}

function pushCard(card) {
  const c = Object.assign({ id: "local-" + Math.random().toString(36).slice(2), timeout: 6 }, card);
  S.cards = S.cards.filter((x) => x.id !== c.id).concat(c).slice(-4);
  if (c.timeout) setTimeout(() => dropCard(c.id), c.timeout * 1000);
  if (fluid && (c.kind === "ok" || c.kind === "success")) fluid.bead();
  render();
}

function dropCard(id) {
  const c = S.cards.find((x) => x.id === id);
  if (!c || c.out) return;
  c.out = true;
  renderCards();
  setTimeout(() => { S.cards = S.cards.filter((x) => x.id !== id); render(); }, 260);
}

async function openBar(text = "") {
  S.bar = { open: true, text, items: [], sel: 0 };
  S.expanded = false;
  await invoke("island_focus", { focus: true });
  render();
  queryBar();
}

function closeBar() {
  if (!S.bar.open) return;
  S.bar.open = false;
  invoke("island_focus", { focus: false });
  render();
}

function queryBar() {
  clearTimeout(barTimer);
  barTimer = setTimeout(async () => {
    const text = S.bar.text;
    const items = await core.try("commandbar.query", { text });
    if (S.bar.open && S.bar.text === text && Array.isArray(items)) { S.bar.items = items; render(); }
  }, 90);
}

async function runBar(i) {
  const it = S.bar.items[i];
  if (!it) {
    if (S.bar.text.trim()) { core.try("agent.ask", { text: S.bar.text.trim(), source: "commandbar" }); closeBar(); }
    return;
  }
  closeBar();
  const res = await core.try("commandbar.run", { action: it.action, key: it.key });
  if (res && res.error) pushCard({ title: "Не получилось", text: res.error, kind: "error" });
}

function barKey(e) {
  const n = Math.min(8, S.bar.items.length);
  if (e.key === "Escape") { e.preventDefault(); closeBar(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); S.bar.sel = n ? (S.bar.sel + 1) % n : 0; render(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); S.bar.sel = n ? (S.bar.sel - 1 + n) % n : 0; render(); }
  else if (e.key === "Enter") { e.preventDefault(); runBar(S.bar.sel); }
  else if (e.key === "Tab") { e.preventDefault(); const it = S.bar.items[S.bar.sel]; if (it) { S.bar.text = it.title.replace(/^.*?:\s*/, ""); render(); queryBar(); } }
}

async function hotkey(name) {
  switch (name) {
    case "toggle": S.pinned = !S.pinned; S.expanded = S.pinned; render(); break;
    case "voice": if (mic.active) mic.stop(); else mic.start("ask", micOpts()); break;
    case "dictation": if (mic.active) mic.stop(); else mic.start("dictation", micOpts()); break;
    case "command_bar": S.bar.open ? closeBar() : openBar(); break;
    case "clipboard": S.pinned = true; S.tab = "clip"; await loadClip(); render(); break;
    case "shot_area": invoke("open_selector", { purpose: "shot" }); break;
    case "ocr": invoke("open_selector", { purpose: "ocr" }); break;
    case "qr": invoke("open_selector", { purpose: "qr" }); break;
    case "shot_screen": await reportResult("Скриншот", core.call("capture.screenshot", { mode: "screen" })); break;
    case "color": S.pinned = false; S.expanded = false; render(); await reportResult("Пипетка", core.call("capture.color", { wait: true, timeout: 30 }, 40000), true); break;
    case "record": await reportResult("Запись экрана", core.call("recorder.toggle", {}), true); break;
    case "paste_plain": core.try("clipboard.paste_plain"); break;
    case "mic_mute": core.try("voice.stop"); mic.cancel(); break;
    case "stop_agent": stopAll(); break;
    case "chat": invoke("show_main", { tab: "chat" }); break;
    default: break;
  }
}

async function reportResult(title, promise, quiet = false) {
  try { await promise; } catch (e) { pushCard({ title, text: e.message || String(e), kind: "error" }); return; }
  if (!quiet) S.flash = Date.now();
  render();
}

async function applyConfig(cfg) {
  S.cfg = Object.assign({ island: {}, hotkeys: {} }, cfg || {});
  const isl0 = S.cfg.island;
  document.documentElement.dataset.theme = isl0.theme || "dark";
  document.documentElement.style.setProperty("--scale", String(isl0.scale || 1));
  document.documentElement.style.setProperty("--op-closed", String(isl0.opacity_collapsed ?? 1));
  document.documentElement.style.setProperty("--op-open", String(isl0.opacity_open ?? 1));
  await invoke("island_place", { monitor: Number(isl0.monitor || 0), offsetX: Number(isl0.offset_x || 0) });
  await invoke("set_capture_visible", { visible: !!isl0.show_in_capture });
  const map = {};
  for (const [k, v] of Object.entries(S.cfg.hotkeys || {})) if (v) map[k] = v;
  const res = await invoke("register_hotkeys", { map });
  if (res && res.errors && Object.keys(res.errors).length)
    pushCard({ title: "Горячие клавиши", text: Object.values(res.errors).join("\n"), kind: "warn", timeout: 10 });
  render();
}

// ------------------------------------------------------------------ wiring

export function mountIsland() {
  document.body.classList.add("view-island");
  root = el("div", { class: "stage" });
  isl = el("div", { class: "island", "data-mode": "idle" });
  cardsBox = el("div", { class: "cards" });
  const cv = el("canvas", { class: "fluid" });
  lip = el("div", { class: "lip" });
  root.append(lip, isl, cv, cardsBox);
  fluid = new Fluid(isl, cv, () => {
    const m = mode();
    const cs = getComputedStyle(isl);
    return {
      liquid: (S.cfg.island.character || "liquid") === "liquid" && !root.classList.contains("hidden"),
      pill: m !== "panel" && m !== "bar" && m !== "confirm" && isl.dataset.lyric !== "1" && musicKind() !== "mini" || m === "idle",
      playing: !!(S.media.active && S.media.playing), edge: S.cfg.island.eq_edge !== false, bands: S.bands,
      accent: (cs.getPropertyValue("--accent") || "#7c8cff").trim(), bg: (cs.getPropertyValue("--bg") || "#000").trim(),
      rainColor: "rgba(150,190,255,0.9)", away: S.away, rain: S.weather && ["rain", "sleet", "thunder", "snow"].includes(S.weather.kind),
    };
  });
  const onCursor = (x, y) => {
    fluid.setCursor(x, y);
    const r = isl.getBoundingClientRect();
    const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
    const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
    const near = !root.classList.contains("hidden") && dx <= 80 && dy <= 90;
    if (near !== S.near) { S.near = near; render(); }
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    isl.style.setProperty("--px", Math.max(-1, Math.min(1, (x - cx) / 260)).toFixed(3));
    isl.style.setProperty("--py", Math.max(-1, Math.min(1, (y - cy) / 160)).toFixed(3));
  };
  document.addEventListener("mousemove", (e) => onCursor(e.clientX, e.clientY));
  listen("island-cursor", (p) => { if (Array.isArray(p)) onCursor(p[0], p[1]); else if (p) onCursor(p.x, p.y); });
  lip.addEventListener("mouseenter", peek);
  lip.addEventListener("mousemove", peek);
  root.addEventListener("mouseleave", () => {
    if (!S.hover) return;
    setTimeout(() => { if (!isl.matches(":hover") && !lip.matches(":hover")) { S.hover = false; render(); } }, 500);
  });
  document.body.append(root);
  new ResizeObserver(scheduleHit).observe(isl);
  new ResizeObserver(scheduleHit).observe(cardsBox);
  isl.addEventListener("transitionend", scheduleHit);

  isl.addEventListener("mouseenter", () => {
    S.hover = true;
    clearTimeout(leaveTimer);
  });
  isl.addEventListener("mouseleave", () => {
    leaveTimer = setTimeout(() => { if (S.expanded && !S.pinned) { S.expanded = false; render(); } }, 420);
  });
  isl.addEventListener("click", (e) => {
    if (e.target.closest("button,input,.bar-item,.clip,.progress")) return;
    const m = mode();
    if (m === "idle" || m === "music" || m === "timer") { S.expanded = true; render(); }
  });
  isl.addEventListener("dblclick", (e) => { if (!e.target.closest("button,input")) invoke("show_main", { tab: "chat" }); });
  isl.addEventListener("wheel", (e) => {
    const m = mode();
    if (m === "panel" || m === "bar" || m === "reply" || m === "confirm") return;
    e.preventDefault();
    clearTimeout(S.wheelT);
    S.wheelAcc = (S.wheelAcc || 0) + (e.deltaY < 0 ? 2 : -2);
    S.wheelT = setTimeout(async () => {
      const d = S.wheelAcc; S.wheelAcc = 0;
      const r = await core.try("status.volume", { delta: d });
      if (r && typeof r.volume === "number") { S.vol = { v: r.volume, at: Date.now() }; render(); setTimeout(render, 1300); }
    }, 60);
  }, { passive: false });
  isl.addEventListener("contextmenu", (e) => { e.preventDefault(); hotkey("voice"); });

  listen("hotkey", (name) => hotkey(typeof name === "string" ? name : name && name.name));
  listen("emergency-stop", stopAll);
  listen("island-hover", (inside) => {
    // Rust releases the strip hit-region only after the 2-second dwell.
    if (inside && root.classList.contains("hidden")) peek();
    if (!inside) {
      S.hover = false;
      if (S.cfg.island.auto_hide) render();
    }
    if (!inside && S.expanded && !S.pinned) { leaveTimer = setTimeout(() => { S.expanded = false; render(); }, 420); }
  });
  listen("core-restarted", () => pushCard({ title: "Ядро перезапущено", kind: "ok", timeout: 3 }));

  core.on("core.connected", async () => {
    S.online = true;
    const [cfg, media, status, weather, timers, rec, router] = await Promise.all([
      core.try("settings.get"), core.try("media.state"), core.try("status.state"), core.try("weather.state"),
      core.try("island.timers"), core.try("recorder.state"), core.try("router.status")]);
    if (media && !media.error) S.media = media;
    if (status && !status.error) S.status = status;
    if (weather && !weather.error) S.weather = weather;
    if (Array.isArray(timers)) timers.forEach((t) => S.timers.set(t.id, t));
    if (rec && !rec.error) S.rec = rec;
    if (router && !router.error) S.router = router.active_label || router.active || "";
    await applyConfig(cfg && !cfg.error ? cfg : {});
  });
  core.on("core.lost", () => { S.online = false; render(); });
  core.on("core.waiting", (info) => { S.online = false; if (info && info.error && !S.coreErrShown) { S.coreErrShown = true; pushCard({ title: "Ядро не запущено", text: info.error, kind: "error", timeout: 0 }); } render(); });

  core.on("settings.changed", async ({ key }) => { if (/^(island|hotkeys|audio)/.test(key || "")) applyConfig(await core.try("settings.get")); });
  core.on("agent.state", (d) => {
    const was = S.agent.state;
    S.agent.state = d.state || "idle";
    S.agent.label = d.label || "";
    if (d.run && d.run !== S.agent.run) { S.agent.run = d.run; S.agent.steps = []; S.agent.text = ""; }
    if (S.agent.state === "idle" && was !== "idle" && S.agent.lastReply) {
      S.agent.showReply = true;
      if (fluid) fluid.bead();
      clearTimeout(S.replyTimer);
      S.replyTimer = setTimeout(() => { S.agent.showReply = false; render(); }, Math.min(20000, 5000 + S.agent.lastReply.length * 40));
    }
    invoke("set_tray_status", { label: S.agent.state === "idle" ? "" : stateLabel(S.agent.state) });
    render();
  });
  core.on("agent.delta", (d) => { S.agent.text += d.text || ""; if (mode() === "reply") render(); });
  core.on("agent.delta_reset", () => { S.agent.text = ""; });
  core.on("agent.message", (d) => { if (d.role === "assistant") { S.agent.lastReply = d.text || ""; S.agent.text = ""; render(); } });
  core.on("agent.step", (d) => {
    const i = S.agent.steps.findIndex((s) => s.id === d.id);
    if (i >= 0) S.agent.steps[i] = Object.assign(S.agent.steps[i], d); else S.agent.steps.push(d);
    S.agent.steps = S.agent.steps.slice(-12);
    render();
  });
  core.on("agent.confirm", (d) => { S.agent.confirm = d; S.expanded = false; render(); });
  core.on("scenario.ask", (d) => {
    if (!d || d.state === "done") { dropCard("scn-ask"); return; }
    pushCard({ id: "scn-ask", title: "💬 Сценарий спрашивает", timeout: 0,
      text: (d.question || "") + (d.choices && d.choices.length ? " — " + d.choices.join(" / ") : "") });
    if (d.listen && !mic.active) mic.start("ask", micOpts());
    else if (d.mode === "text") openBar("");
  });
  core.on("agent.confirm_done", (d) => { if (S.agent.confirm && S.agent.confirm.id === d.id) { S.agent.confirm = null; render(); } });
  core.on("island.card", (c) => pushCard(c));
  core.on("island.clear", () => { S.cards = []; render(); });
  core.on("island.timer", (t) => {
    S.timers.set(t.id, t);
    if (t.state === "done") { pushCard({ title: "Время вышло", text: t.label || "Таймер", kind: "ok", icon: "timer", timeout: 12 }); setTimeout(() => { S.timers.delete(t.id); render(); }, 15000); }
    if (t.state === "cancelled") S.timers.delete(t.id);
    render();
  });
  core.on("media.state", (m) => { S.media = m || { active: false }; render(); });
  core.on("media.position", (p) => { if (S.media) { S.media.position = p.position; if (mode() === "panel" && S.tab === "now" && Date.now() - (S.posDrawn || 0) > 900) { S.posDrawn = Date.now(); render(); } } });
  core.on("media.levels", (d) => {
    S.bands = d.bands || S.bands;
    const avg = S.bands.length ? S.bands.reduce((a, b) => a + (b || 0), 0) / S.bands.length : 0;
    const bass = S.bands.length ? Math.max(S.bands[0] || 0, S.bands[1] || 0) : 0;
    isl.style.setProperty("--lvl", (S.media.playing ? Math.min(1, avg * 1.3) : 0).toFixed(3));
    isl.style.setProperty("--bass", (S.media.playing ? Math.min(1, bass) : 0).toFixed(3));
    isl.querySelectorAll(".eq:not(.mic)").forEach((box) => {
      const bars = box.children;
      for (let i = 0; i < bars.length; i++) {
        const v = S.bands[Math.floor((i / bars.length) * S.bands.length)] || 0;
        bars[i].style.transform = "scaleY(" + (S.media.playing ? Math.max(0.15, Math.min(1, v)) : 0.15).toFixed(3) + ")";
      }
    });
  });
  core.on("media.lyrics", (d) => {
    const now = Date.now();
    if (S.lyricAt) S.lyricDur = Math.min(8, (now - S.lyricAt) / 1000);
    S.lyricAt = now;
    if (d && d.duration) S.lyricDur = d.duration;
    S.lyric = (d && (d.line || d.text)) || ""; if (mode() === "panel" || mode() === "music") render(); });
  core.on("recorder.state", (r) => { const was = S.rec.recording; S.rec = r || {}; if (was && !S.rec.recording && S.rec.path) pushCard({ title: "Запись сохранена", text: S.rec.path, kind: "ok", icon: "rec" }); render(); });
  core.on("obs.state", (o) => { S.obs = o || {}; render(); });
  core.on("status.state", (s) => { S.status = s || {}; render(); });
  core.on("status.charging", (s) => {
    S.status = S.status || {};
    const prev = S.status.battery && typeof S.status.battery === "object" ? S.status.battery : {};
    S.status.battery = Object.assign({}, prev, s || {}, { charging: true });
    render();
  });
  core.on("weather.state", (w) => { S.weather = w || {}; render(); });
  core.on("router.status", (r) => { S.router = (r && (r.active_label || r.active)) || S.router; });
  core.on("status.keyboard", (k) => {
    const changed = S.kbd.layout && k && k.layout && k.layout !== S.kbd.layout;
    S.kbd = k || {};
    if (changed) { S.kbdShow = true; clearTimeout(S.kbdT); S.kbdT = setTimeout(() => { S.kbdShow = false; render(); }, 1600); }
    render();
  });
  core.on("status.privacy", (p) => { S.privacy = p || {}; render(); });
  core.on("status.away", (d) => { S.away = !!(d && d.away); render(); });
  core.on("capture.flash", () => { if (fluid) fluid.bead(0.8); S.flash = Date.now(); render(); setTimeout(render, 520); });
  core.on("capture.color_pick", (d) => {
    if (d.state === "armed") pushCard({ id: "color-pick", title: "Пипетка", text: "Наведите курсор на нужный цвет и нажмите Ctrl · Esc — отмена", kind: "info", icon: "◉", timeout: 0 });
    else if (d.state === "preview") {
      const c = S.cards.find((x) => x.id === "color-pick");
      if (c) { c.title = "Пипетка · " + d.hex; c.text = "Нажмите Ctrl, чтобы взять этот цвет · Esc — отмена"; c.swatch = d.hex; renderCards(); }
    } else {
      dropCard("color-pick");
      if (d.state === "cancel") pushCard({ title: "Пипетка", text: "Отменена", kind: "info", icon: "◉", timeout: 2 });
    }
  });
  core.on("capture.color", (c) => pushCard({ title: "Цвет " + (c.hex || ""), text: "Скопировано в буфер", kind: "info", icon: "●", timeout: 4 }));
  core.on("commandbar.open", (d) => openBar((d && d.text) || ""));
  core.on("clipboard.added", () => { if (S.tab === "clip" && mode() === "panel") loadClip(); });
  core.on("voice.stt", (d) => { if (d && d.partial && S.mic.state === "listening") { S.mic.partial = d.partial; } });

  // Часы, таймеры и счётчик записи тикают раз в секунду без лишних перерисовок сложных режимов.
  setInterval(() => { const m = mode(); if (m === "idle" || m === "timer" || m === "rec" || (m === "panel" && S.tab === "now")) render(); }, 1000);
  if (!inTauri) document.body.classList.add("demo");
  render();
}

export { hotkey as islandHotkey, S as islandState, pushCard };
