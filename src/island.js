// Остров Эйдоса: «живая» капсула сверху экрана, которая перетекает между состояниями:
// покой, музыка, агент думает/действует/говорит, подтверждение, таймер, запись экрана, командная строка,
// раскрытая панель с виджетами. Уведомления (карточки) выплывают под капсулой.
import { core } from "./rpc.js";
import { invoke, listen, el, fmtTime, inTauri, micIcon, setMicState } from "./shell.js";
import { Mic } from "./voice.js";
import { createNous } from "./nous.js";
import { iconNode } from "./icons.js";
import { t, locale } from "./i18n.js";
import { entries, shown, gridRegion } from "./island-layout.js";
import { panelSize } from "./island-panel.js";
import { visualizerPath, smoothBands, timerSeconds } from "./island-motion.js";
import { Fluid } from "./fluid.js";
import { PARTS, TAB_LIST, TOOL_LIST, partOn, listOn, partValues, RESET_VALUES, applyValues } from "./parts.js";

// видна ли часть интерфейса (настраивается в панели кнопкой ✎ или в настройках)
const AUTOMATIC_PARTS = new Set(["music_cover","music_title","music_progress","music_eq","music_controls","lyrics","agent_steps","agent_stop","reply_text","mic_meter"]);
const on = (key) => key!=='battery_percent' && (AUTOMATIC_PARTS.has(key) || partOn(S.cfg.island, key));

const icon = (name, size = 15) => iconNode(name, size);

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
  clipExpanded: null,
  status: {},
  weather: {},
  online: false,
  flash: 0,
  away: false,
  hover: false,
  stick: false,
  near: false,
  musicView: "auto",
  kbd: {},
  privacy: {},
  sysmon: {},
  calendar: {},
  downloads: {},
  ticker: {},
  mixer: {},
  notes: [],
  focus: null,
  sleep: null,
  editMode: "parts",
  capsuleDetail: null,
};

const panelScroll=new Map();
let root, isl, cardsBox, fluid, lip, leaveTimer = null, barTimer = null;
let clipLoadRequest = 0, clipOpenRequest = 0;
try { S.focus = JSON.parse(localStorage.getItem("idos.focus") || "null"); } catch (e) { /* пусто */ }
// автоскрытие: полоска → (навели) капсула → (держишь курсор) большая панель
let quietSince = Date.now(), tuckTimer = null, peeked = false;
const mic = new Mic((st) => { S.mic = st; document.querySelectorAll("button.mic").forEach((b) => setMicState(b, st)); if (st.state === "error") pushCard({ title: t("Микрофон"), text: st.text, kind: "error" }); render(); });

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
  // ПКМ переключает вид капсулы: musicView "normal" показывает обычный островок во время музыки
  if (S.media.active && S.media.title && S.musicView !== "normal") return "music";
  return "idle";
}

const runningTimers = () => [...S.timers.values()].filter((t) => t.state === "running");
const timerLeft = timerSeconds;

// ------------------------------------------------------------------ rendering

// Знак Νοῦς вместо орба-персонажа (гайдбук §12): морфинг-движок, состояние → set().
// 27px — в 1.5 раза крупнее прежних 18: логотип заметнее в капсуле (просьба юзера).
const nous = createNous({ size: 27, getLevel: () => S.mic.level || 0 });
const nousBig = createNous({ size: 22, getLevel: () => S.mic.level || 0 });

function nousState() {
  if (S.mic.state === "transcribing") return "transcribing";
  if (S.mic.state === "listening") return "listen";
  if (S.agent.confirm) return "waiting";
  const a = S.agent.state;
  if (a === "acting" || a === "working") return "working";
  if (["thinking", "searching", "speaking", "idle"].includes(a)) return a;
  return "idle";
}

function orb(extra = "") {
  const big = extra.includes("big");
  const inst = big ? nousBig : nous;
  inst.set(nousState());
  return el("span", { class: "orb nous-wrap " + extra }, inst.el);
}

let waveAnimationStarted=false;
function animateRings() {
  if(waveAnimationStarted)return;
  waveAnimationStarted=true;
  let last=0;
  const frame=now=>{
    requestAnimationFrame(frame);
    if(now-last<16 || document.hidden)return;
    const elapsed=last?(now-last)/1000:1/60;last=now;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.querySelectorAll('.eq-ring').forEach(box=>{
      const target=box._bands||[], previous=box._smooth||target;
      box._smooth=smoothBands(previous,target,elapsed);
      box.querySelectorAll('path').forEach((path,i)=>path.setAttribute('d',visualizerPath(box._smooth,box.dataset.playing==='true',i,reduced?0:now/1000,box.dataset.style)));
    });
  };
  requestAnimationFrame(frame);
}
function eq() {
  const box=el('span',{class:'eq eq-ring',role:'img','aria-label':t('Эквалайзер')});
  box.dataset.style=S.cfg.island.waves?.visualizer||'waves';box._bands=[...S.bands];box.dataset.playing=String(!!S.media.playing);animateRings();
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 128 48');
  for(let i=0;i<(box.dataset.style!=='waves'?1:4);i++){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',visualizerPath(S.bands,S.media.playing,i,matchMedia('(prefers-reduced-motion: reduce)').matches?0:performance.now()/1000,box.dataset.style));svg.append(path);}
  box.append(svg);return box;
}

// A panel can be much wider than the compact pill, so fill its available SVG viewport.
function panelVisualizer(box) {
  box.querySelector('svg')?.setAttribute('preserveAspectRatio','none');
  return box;
}

function syncLyric() {
  const data=S.lyricsData;
  if(!data)return;
  const pos=S.media.position||0;
  const line=(data.lines||[]).filter(l=>l.t<=pos).at(-1);
  S.lyric=line?.text||'';
}
async function loadTrackLyrics(force=false) {
  const key=S.media.track_id||[S.media.app,S.media.artist,S.media.title].join('|');
  if(!S.media.active||!S.media.title)return;
  if(!force&&S.lyricsKey===key)return;
  S.lyricsKey=key;S.lyricsData=null;S.lyric='';
  const data=await core.try('media.lyrics',{force},30000);
  if(S.lyricsKey!==key)return;
  S.lyricsData=data&&!data.error?data:{missing:true};syncLyric();render();
}

function cover(size = "s") {
  const src = S.media.cover;
  return el("span", { class: "cover " + size, style: src ? { backgroundImage: "url(" + JSON.stringify(src) + ")" } : {} },
    src ? null : icon("music"));
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

// Часы «ЧЧ:ММ» — элементом, а не HTML-строкой: el() вставляет строки как текст,
// из-за чего в раскрытой панели виднелась разметка. Пульс двоеточия — .clock .colon.
function clockNode() {
  const d = new Date();
  return el("span", { class: "clock" },
    String(d.getHours()).padStart(2, "0"),
    el("span", { class: "colon" }, ":"),
    String(d.getMinutes()).padStart(2, "0"));
}

// ------------------------------------------------------------------ блоки капсулы и раскладка

// Логотип всегда слева, часы всегда по центру; остальные блоки раскладываются
// редактором по плечам: island.layout = { left: [...], right: [...], width: 0 }.

function batBlock() {
  const b = batInfo();
  if (!b) return el("span",{class:"cblk",title:t("Батарея не обнаружена")},iconNode("battery",14),S.status.battery?.no_battery?t("Сеть"):"—");
  const display=S.cfg.island.battery_display||"icon";
  const label=(b.charging?t("Заряжается"):t("Батарея"))+" · "+b.percent+"%";
  if(display==="text")return el("span",{class:"cblk mono"},label);
  const gauge=el("span", { class: "bat-mini"+(display==="horizontal"?" horizontal":"") + (b.charging ? " charging" : "") + (b.percent < 20 ? " low" : ""),
    title: (b.charging ? t("Заряжается") : t("Батарея")) + " · " + Math.round(b.percent) + "%" },
    el("i", { style: { height: Math.max(8, Math.min(100, b.percent)) + "%" } }));
  return display==="combined"?el("span",{class:"cblk mono",title:label},gauge,b.percent+"%"):gauge;
}



function weatherBlock() {
  const w = S.weather || {};
  const temp = w.temp !== undefined && w.temp !== null
    ? el("span", { class: "temp" }, (w.icon || "") + Math.round(w.temp) + "°") : null;
  const cityAsk = !temp && S.online
    ? el("button", { class: "chip w-ask", title: (S.weather && S.weather.reason) || t("Погоде нужен город — Настройки · Погода"),
      onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "settings" }); } }, t("город?")) : null;
  return temp || cityAsk || el("span",{class:"cblk"},iconNode("sun",13),"—°");
}

function loadBlock() {
  const st = S.sysmon || {};
  if (st.cpu === undefined || st.cpu === null) return el("span",{class:"cblk",title:t("Данные системы пока недоступны")},iconNode("gauge",13),"—%");
  return el("span", { class: "cblk mono", title: t("ЦП {cpu}% · ОЗУ {ram}% · Диск {disk}%",
    { cpu: st.cpu, ram: st.ram ?? "—", disk: st.disk ?? "—" }) },
    iconNode("gauge", 13), el("b", {}, st.cpu + "%"), el("i", { class: "sep" }, "·"), el("b", {}, (st.ram ?? "—") + "%"));
}

function focusBlock() {
  const f = S.focus;
  if (!f) return el("button", {class:"cblk",title:t("Начать фокус-сессию"),onclick:e=>{e.stopPropagation();toggleFocus();}},iconNode("ring",13),"25:00");
  const left = f.endsAt ? Math.max(0, f.endsAt - Date.now() / 1000) : (f.left || 0);
  if (!f.endsAt && !f.left) return null;
  return el("button", { class: "cblk mono focus " + f.phase,
    title: (f.phase === "break" ? t("Перерыв") : t("Фокус-сессия")) + " — " + t("пауза/продолжить"),
    onclick: (e) => { e.stopPropagation(); toggleFocus(); } },
    iconNode("ring", 13), el("b", {}, fmtTime(left)), el("i", { class: "sub-t" }, f.phase === "break" ? t("перерыв") : t("фокус")));
}

function eventBlock() {
  const evs = (S.calendar && S.calendar.events) || [];
  const now = Date.now() / 1000;
  const ev = evs.find((e) => e.end > now);
  if (!ev) return el("span",{class:"cblk",title:t("Ближайших событий нет")},iconNode("calendar",13),t("Нет событий"));
  const time = new Date(ev.start * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  const inMin = Math.round((ev.start - now) / 60);
  const when = inMin > 0 && inMin < 1440 ? t("через {n} мин", { n: inMin }) : "";
  return el("span", { class: "cblk", title: ev.title + (when ? " · " + when : "") },
    iconNode("calendar", 13), el("b", {}, time), el("i", { class: "ev-t" }, ev.title.slice(0, 22)), when ? el("i", { class: "sub-t" }, when) : null);
}

function dlBlock() {
  const act = (S.downloads && S.downloads.active) || [];
  if (!act.length) return el("span",{class:"cblk",title:t("Активных загрузок нет")},iconNode("download",13),"0");
  const d = act[0];
  const pct = d.progress != null ? Math.round(d.progress * 100) : null;
  return el("span", { class: "cblk", title: d.name + (d.speed ? " · " + speedText(d.speed) : "") },
    iconNode("download", 13),
    pct !== null ? el("b", {}, pct + "%") : null,
    el("span", { class: "thin c" }, el("i", { style: { width: Math.round((d.progress || 0) * 100) + "%" } })));
}

function tickerBlock() {
  const rates = (S.ticker && S.ticker.rates) || {};
  const usd = rates.usd, eur = rates.eur;
  if (!usd && !eur) return el("span",{class:"cblk",title:S.ticker.error||t("Курсы пока недоступны")},"$ — · € —");
  const cell = (r, sym) => el("span", { class: "tk mono" }, sym + " " + (r.value < 1000 ? r.value.toFixed(1) : Math.round(r.value))
    + (r.diff > 0 ? " ↑" : r.diff < 0 ? " ↓" : ""));
  return el("span", { class: "cblk", title: t("Курс ЦБ на {d}", { d: new Date((S.ticker.updated || Date.now() / 1000) * 1000).toLocaleDateString(locale()) }) },
    usd ? cell(usd, "$") : null, eur ? cell(eur, "€") : null);
}

function sleepStart() {
  S.sleep = { endsAt: Date.now() / 1000 + 30 * 60 };
  pushCard({ title: t("Таймер сна"), text: t("Компьютер уснёт через 30 минут. Повторный клик по значку отменит."), kind: "info", icon: "hourglass", timeout: 5 });
  render();
}

function sleepBlock() {
  const sl = S.sleep;
  if (!sl || !sl.endsAt) {
    return el("button", { class: "cblk dim", title: t("Таймер сна: уснуть через 30 минут"),
      onclick: (e) => { e.stopPropagation(); sleepStart(); } }, iconNode("hourglass", 13));
  }
  const left = Math.max(0, sl.endsAt - Date.now() / 1000);
  return el("button", { class: "cblk mono", title: t("Отменить сон"),
    onclick: (e) => { e.stopPropagation(); S.sleep = null; render(); } },
    iconNode("hourglass", 13), el("b", {}, fmtTime(left)), el("i", { class: "sub-t" }, t("сон")));
}

function coreinfoBlock() {
  const st = S.status || {};
  const ms = st.model_status || st.model;
  if (ms && Number(ms.progress ?? ms.percent) > 0) {
    const pct = Math.round(Number(ms.progress ?? ms.percent));
    return el("span", { class: "cblk", title: ms.name || t("Модель") },
      iconNode("chipic", 13), el("span", { class: "thin c" }, el("i", { style: { width: pct + "%" } })), el("b", {}, pct + "%"));
  }
  return el("span", {class:"cblk",title:S.router || t("Модель")},iconNode("chipic",13),S.router || "—");
}

// Реестр блоков капсулы. dots() возвращает массив — flatMap раскрывает его.
const CAPS_BLOCKS = {
  dots: () => dots(),
  battery: batBlock,
  weather: weatherBlock,
  core_status: () => el("span", { class: "chip" + (S.online ? "" : " warn"), title:t("Статус ядра") }, S.online ? t("Готов") : t("ядро…")),
  load: loadBlock,
  focus: focusBlock,
  event: eventBlock,
  downloads: dlBlock,
  ticker: tickerBlock,
  sleep: sleepBlock,
  coreinfo: coreinfoBlock,
};
const CAPS_ALL = Object.keys(CAPS_BLOCKS);
const CAPS_DEFAULT = { left: ["dots"], right: ["battery", "weather"] };

function capsLayout() {
  const saved = (S.cfg.island && S.cfg.island.layout) || {};
  const enabled = CAPS_ALL.filter((k) => on(k));
  const clean = (arr) => (Array.isArray(arr) && arr.length ? arr.filter((k) => enabled.includes(k)) : null);
  const left = clean(saved.left) || CAPS_DEFAULT.left.filter((k) => enabled.includes(k));
  let right = clean(saved.right) || CAPS_DEFAULT.right.filter((k) => enabled.includes(k));
  const seen = new Set([...left, ...right]);
  right = right.concat(enabled.filter((k) => !seen.has(k)));
  return { left, right };
}

function speedText(b) {
  if (!b || b <= 0) return "—";
  return b >= 1048576 ? (b / 1048576).toFixed(1) + " " + t("МБ/с") : Math.max(1, Math.round(b / 1024)) + " " + t("КБ/с");
}

function openCapsuleDetail(key) {
  S.capsuleDetail=key;S.expanded=true;S.pinned=true;render();
}

function openExpandedIsland() {
  S.capsuleDetail=null;S.expanded=true;S.stick=false;render();
}

function capsuleDetail() {
  const key=S.capsuleDetail;
  const title=entries("capsule").find(i=>i.key===key)?.label||t("Островок");
  const text=(value)=>el("div",{class:"widget"},el("span",{class:"sub"},value));
  const setting=(label,tab="settings")=>el("button",{class:"btn ghost",onclick:()=>invoke("show_main",{tab})},t(label));
  let content;
  if(key==="weather")content=[panelWeatherWidget(),daysWidget()];
  else if(key==="event")content=[agendaWidget()];
  else if(key==="downloads")content=[dlWidget()];
  else if(key==="load")content=[sysmonWidget()];
  else if(key==="volume_osd")content=[mixerWidget()];
  else if(key==="battery")content=[text(batInfo()?`${batInfo().percent}% · ${t(batInfo().charging?"Заряжается":"Работа от батареи")}`:t(S.status.battery?.no_battery?"Питание от сети · батарея не обнаружена":"Данные питания пока недоступны")),setting("Вид питания","island-settings")];
  else if(key==="privacy")content=[text([S.privacy.mic?t("Микрофон")+": "+(S.privacy.mic_app||t("Занят")):t("Микрофон свободен"),S.privacy.camera?t("Камера")+": "+(S.privacy.camera_app||t("Занята")):t("Камера свободна")].join(" · ")),el("button",{class:"btn ghost",onclick:()=>core.try("status.mic_mute",{})},t("Включить / выключить микрофон"))];
  else if(key==="vpn")content=[text(S.status.vpn?.on?(S.status.vpn.name||"VPN"):t("VPN не подключён"))];
  else if(key==="caps"||key==="layout")content=[text(`${t("Раскладка клавиатуры")}: ${S.kbd.layout||"—"} · Caps Lock: ${t(S.kbd.caps?"Включён":"Выключен")}`)];
  else if(key==="coreinfo")content=[text(S.router||t("Модель пока не выбрана")),setting("Ключи и модели","models")];
  else if(key==="ticker")content=[text(S.ticker.error||t("Курс ЦБ")),tickerBlock(),el("button",{class:"btn ghost",onclick:async e=>{const button=e.currentTarget;button.disabled=true;const r=await core.try("ticker.refresh",{},30000);if(r&&!r.error)S.ticker=r;else toastCard(t("Курс валют"),r?.error||t("Не удалось обновить"));render();}},t("Обновить"))];
  else content=[text(S.online?t("Ядро подключено"):t("Ядро не подключено")),setting("Настройки")];
  return [el("div",{class:"row"},el("b",{class:"grow"},t(title)),el("button",{class:"btn ghost",onclick:()=>{S.capsuleDetail=null;S.pinned=false;S.expanded=false;render();}},t("Назад"))),el("div",{class:"panel-body capsule-detail"},...content)];
}

function visualCapsule() {
  const idleIndicators={
    privacy:()=>el("span",{class:"cblk",title:S.privacy.mic_app||S.privacy.camera_app||t("Микрофон и камера свободны")},iconNode(S.privacy.camera?"eye":"mic",13),S.privacy.mic||S.privacy.camera?t("Занят"):null),
    caps:()=>el("span",{class:"chip caps",title:"Caps Lock"},S.kbd.caps?"⇪":"⇪ —"),
    layout:()=>el("span",{class:"chip lay"},S.kbd.layout||"—"),
    vpn:()=>el("span",{class:"cblk",title:S.status.vpn?.name||t("VPN не подключён")},iconNode("shield",14),S.status.vpn?.on?null:"—"),
    volume_osd:()=>el("span",{class:"cblk",title:t("Микшер громкости")},iconNode("volume",14),String(S.vol&&Date.now()-S.vol.at<1200?S.vol.v:S.mixer.master??"—"))
  };
  return gridRegion(S.cfg.island, "capsule", entries("capsule").filter(item => shown(S.cfg.island, item)).map(({key,label}) => {
    const node=key==="orb"?orb():key==="clock"?clockNode():idleIndicators[key]?.()||CAPS_BLOCKS[key]?.();
    if(!node||["focus","sleep"].includes(key))return [key,node];
    // Existing buttons keep their direct actions; passive blocks open their controls inside the island.
    if(node.tagName==="BUTTON")return [key,node];
    return [key,el("button",{class:"capsule-action",title:t(label),"aria-label":t(label),onclick:e=>{e.stopPropagation();if(key==="orb")invoke("show_main",{tab:"chat"});else if(key==="clock")openExpandedIsland();else openCapsuleDetail(key);}},node)];
  }));
}

const VIEWS = {
  idle: () => {
    const liquid = (S.cfg.island.character || "liquid") === "liquid";
    if (!S.cfg.island.visual_layout?.capsule && on("volume_osd") && S.vol && Date.now() - S.vol.at < 1200) return [el("div", { class: "row pill" }, el("span", { class: "ic" }, S.vol.v ? "🔊" : "🔇"),
      el("span", { class: "vol" }, el("i", { style: { width: S.vol.v + "%" } })), el("span", { class: "mono sub" }, String(S.vol.v)))];
    if (S.cfg.island.visual_layout?.capsule) return [visualCapsule()];
    const lay = capsLayout();
    const cell = (k) => {
      let n = null;
      try { n = CAPS_BLOCKS[k] && CAPS_BLOCKS[k](); } catch (e) { n = null; }
      if (n === null || n === undefined) return [];
      return (Array.isArray(n) ? n : [n]).map(node => el("span", {class:"island-icon-box"}, node));
    };
    // лого — слева, часы — всегда по центру, блоки раскладываются по плечам редактором
    return [el("div", { class: "row pill tri" },
      el("span", { class: "side l" }, on("orb") ? el("span", {class:"island-icon-box"}, orb()) : null, ...lay.left.flatMap(cell)),
      el("span", { class: "mid" }, on("clock") ? el("span", {class:"island-icon-box"}, clockNode()) : null),
      el("span", { class: "side r" }, ...lay.right.flatMap(cell)))];
  },
  music: () => {
    const kind = musicKind();
    const m = S.media;
    const prog = m.duration ? Math.min(100, ((m.position || 0) / m.duration) * 100).toFixed(1) + "%" : "0%";
    const lyricOn = on("lyrics") && S.lyric;
    const cov = (size) => (on("music_cover") ? cover(size) : null);
    const bars = () => (on("music_eq") ? eq(5) : null);
    const thin = () => (on("music_progress") ? el("span", { class: "thin" }, el("i", { style: { width: prog } })) : null);
    if (S.cfg.island.visual_layout?.music) {
      const controls = el("span", {class:"row"},
        el("button", {class:"icon", title:t("Назад"), disabled:!m.can_prev, onclick:e=>{e.stopPropagation();core.try("media.control",{action:"previous"});}},iconNode("prev")),
        el("button", {class:"icon", title:t("Воспроизведение / пауза"), onclick:e=>{e.stopPropagation();core.try("media.control",{action:"play_pause"});}},iconNode(m.playing?"pause":"play")),
        el("button", {class:"icon", title:t("Далее"), disabled:!m.can_next, onclick:e=>{e.stopPropagation();core.try("media.control",{action:"next"});}},iconNode("next")));
      return [gridRegion(S.cfg.island,"music",[["music_cover",cov("s")],["music_title",on("music_title")?el("span",{class:"label"},m.title):null],
        ["music_progress",thin()],["music_eq",bars()],["music_controls",on("music_controls")?controls:null],["lyrics",lyricOn?el("span",{class:"lyric"},S.lyric):null]])];
    }
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
        el("button", { class: "icon", disabled: !m.can_prev, onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "previous" }); } }, iconNode("prev", 14)),
        el("button", { class: "icon", onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "play_pause" }); } }, iconNode(m.playing ? "pause" : "play", 14)),
        el("button", { class: "icon", disabled: !m.can_next, onclick: (e) => { e.stopPropagation(); core.try("media.control", { action: "next" }); } }, iconNode("next", 14))))];
  },
  agent: () => [
    el("div", { class: "row pill" }, orb("big"),
      el("span", { class: "label" }, S.agent.label || stateLabel(S.agent.state)),
      el("span", { class: "grow" }),
      on("agent_stop") ? el("button", { class: "icon stop", title: t("Остановить"), onclick: (e) => { e.stopPropagation(); stopAll(); } }, iconNode("stop", 14)) : null),
    on("agent_steps") ? stepsMini() : null,
  ],
  reply: () => [
    el("div", { class: "row" }, orb("big"), el("span", { class: "label" }, S.agent.state === "speaking" ? t("Говорю…") : t("Ответ")),
      el("span", { class: "grow" }),
      el("button", { class: "icon", title: t("Открыть диалог"), onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "chat" }); } }, "↗"),
      el("button", { class: "icon", title: t("Закрыть"), onclick: (e) => { e.stopPropagation(); S.agent.showReply = false; core.try("voice.stop"); render(); } }, "✕")),
    on("reply_text") ? el("div", { class: "reply-text" }, S.agent.text || S.agent.lastReply) : null,
  ],
  mic: () => [
    el("div", { class: "row pill" }, orb("big"),
      el("span", { class: "label" }, S.mic.state === "transcribing" ? t("Распознаю…") : S.mic.mode === "dictation" ? t("Диктовка…") : t("Слушаю…")),
      el("span", { class: "grow" }), on("mic_meter") ? meter(S.mic.level || 0) : null),
  ],
  rec: () => [
    el("div", { class: "row pill" }, el("span", { class: "rec-dot" }),
      el("span", { class: "label mono" }, fmtTime(S.rec.elapsed || (S.rec.started_at ? Date.now() / 1000 - S.rec.started_at : 0))),
      el("span", { class: "grow" }),
      el("button", { class: "icon stop", title: "Остановить запись", onclick: (e) => { e.stopPropagation(); core.try("recorder.stop"); } }, iconNode("stop", 14))),
  ],
  timer: () => {
    const timer = runningTimers().sort((a, b) => timerLeft(a) - timerLeft(b))[0];
    const left = timerLeft(timer);
    return [el("div", { class: "row pill" }, el("span", { class: "ic" }, icon("timer")),
      el("span", { class: "label mono" }, fmtTime(left)), el("span", { class: "sub" }, timer.label || ""),
      el("span", { class: "grow" }),
      el("button", { class: "icon", title: t("Отменить"), onclick: (e) => { e.stopPropagation(); core.try("island.cancel_timer", { timer_id: timer.id }); } }, "✕"))];
  },
  confirm: () => {
    const c = S.agent.confirm;
    return [
      el("div", { class: "row" }, orb("big"), el("span", { class: "label" }, t("Разрешить действие?"))),
      el("div", { class: "confirm-text" }, c.label || c.tool || ""),
      c.preview ? el("pre", { class: "preview" }, typeof c.preview === "string" ? c.preview : JSON.stringify(c.preview, null, 1)) : null,
      el("div", { class: "row actions" },
        el("button", { class: "btn ghost", onclick: () => answerConfirm(false) }, t("Отклонить")),
        el("button", { class: "btn primary", onclick: () => answerConfirm(true) }, t("Разрешить"))),
    ];
  },
  bar: () => {
    const input = el("input", {
      class: "bar-input", placeholder: t("Спросите Эйдос, найдите приложение, посчитайте…"), value: S.bar.text,
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
  if (on("vpn") && S.status && S.status.vpn && S.status.vpn.on) out.push(el("span", { class: "chip vpn", title: (S.status.vpn.name || "VPN") + " — защищённое соединение" }, iconNode("shield", 14)));
  return out;
}

function stateLabel(s) {
  return ({ thinking: t("Думаю…"), working: t("Работаю…"), acting: t("Действую…"), searching: t("Ищу…"), speaking: t("Говорю…"),
    waiting: t("Жду подтверждения"), stopping: t("Останавливаюсь…") })[s] || t("Готов");
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
  if(S.capsuleDetail)return capsuleDetail();
  const tabs = visibleTabs();
  if (S.tab !== "edit" && !tabs.some(([id]) => id === S.tab)) S.tab = tabs[0][0];
  const editing = S.tab === "edit";
  let head = el("div", { class: "row tabs" }, on("h_orb") ? orb() : null,
    editing ? el("span", { class: "label" }, t("Настройка островка"))
      : tabs.length > 1 ? tabs.map(([id, name]) => el("button", { class: "tab" + (S.tab === id ? " on" : ""), title: t(name), onclick: (e) => { e.stopPropagation(); S.tab = id; if (id === "clip") loadClip(); render(); } }, t(name)))
      : el("span", { class: "label" }, t(tabs[0][1])),
    el("span", { class: "grow" }),
    on("h_edit") || editing ? el("button", { class: "icon" + (editing ? " on" : ""), title: t("Настройка островка"), onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "island-settings" }); } }, editing ? iconNode("check", 13) : iconNode("edit", 20)) : null,
    on("h_pin") && !editing ? el("button", { class: "icon" + (S.pinned ? " on" : ""), title: t("Закрепить"), onclick: (e) => { e.stopPropagation(); S.pinned = !S.pinned; render(); } }, iconNode("pin", 20)) : null,
    on("h_open") && !editing ? el("button", { class: "icon", title: t("Окно Эйдоса"), onclick: (e) => { e.stopPropagation(); invoke("show_main", { tab: "chat" }); } }, iconNode("open", 20)) : null);
  if (S.cfg.island.visual_layout?.header && !editing) {
    const tabNodes = [...head.querySelectorAll("button.tab")];
    const buttons = [...head.querySelectorAll("button.icon")];
    const headerNodes = [["h_orb",on("h_orb")?orb():null], ...tabs.map(([id,name],i)=>["tab_"+id,tabNodes[i] || el("span",{},t(name))])];
    let i=0;for(const key of ["h_edit","h_pin","h_open"]) if(on(key))headerNodes.push([key,buttons[i++]]);
    head=gridRegion(S.cfg.island,"header",headerNodes);
  }
  const body = ({ now: panelNow, tools: panelTools, clip: panelClip, agent: panelAgent, edit: panelEdit })[S.tab]();
  return [head, el("div", { class: "panel-body" + (editing ? " edit" : "") }, body)];
}

async function saveValues(values) {
  applyValues(S.cfg, values);
  render();
  const r = await core.try("settings.update", { values });
  if (r && r.error) toastCard(t("Не удалось сохранить"), r.error);
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

// Режим «✎»: две вкладки — «Элементы» (чипы вкл/выкл) и «Раскладка» (перетаскивание блоков
// капсулы, ширина островка, порядок и ширина виджетов панели по сетке).
function panelEdit() {
  if (!S.editMode) S.editMode = "parts";
  const out = [el("div", { class: "row edit-mode" },
    el("button", { class: "tab" + (S.editMode === "parts" ? " on" : ""), onclick: (e) => { e.stopPropagation(); S.editMode = "parts"; render(); } }, t("Элементы")),
    el("button", { class: "tab" + (S.editMode === "layout" ? " on" : ""), onclick: (e) => { e.stopPropagation(); S.editMode = "layout"; render(); } }, t("Раскладка")))];
  out.push(S.editMode === "layout" ? layoutEditor() : partsEditor());
  return out;
}

function partsEditor() {
  const I = S.cfg.island;
  const groups = new Map();
  for (const [key, label, group] of PARTS) {
    const g = t(group);
    if (!groups.has(g)) groups.set(g, []);
    const shown = partOn(I, key);
    groups.get(g).push(chipT(t(label), shown, () => saveValues(partValues(key, !shown))));
  }
  const out = [el("div", { class: "sub edit-hint" }, t("Нажмите на элемент, чтобы скрыть или показать его. Капсула меняется сразу."))];
  out.push(editGroup(t("Вкладки панели"), TAB_LIST.map(([id, name]) => {
    const shown = listOn(I, "tabs", id);
    return chipT(t(name), shown, () => saveValues(hiddenList("tabs", id, !shown)));
  })));
  for (const [group, chips] of groups) out.push(editGroup(group, chips));
  out.push(editGroup(t("Кнопки на вкладке «Инструменты»"), TOOL_LIST.map(([id, name]) => {
    const shown = listOn(I, "tools", id);
    return chipT(t(name), shown, () => saveValues(hiddenList("tools", id, !shown)));
  })));
  out.push(el("div", { class: "row actions" },
    el("button", { class: "btn ghost", onclick: (e) => { e.stopPropagation(); saveValues(RESET_VALUES); } }, t("Показать всё")),
    el("span", { class: "grow" }),
    el("button", { class: "btn primary", onclick: (e) => { e.stopPropagation(); toggleEdit(); } }, t("Готово"))));
  return out;
}

// ---------- редактор раскладки ----------

function saveLayout(patch) {
  saveValues({ "island.layout": Object.assign({}, (S.cfg.island.layout || {}), patch) });
}

function savePanelLayout(patch) {
  saveValues({ "island.panel_layout": Object.assign({}, (S.cfg.island.panel_layout || {}), patch) });
}

const LAYOUT_LABELS = () => ({
  dots: t("Микрофон · камера · VPN"), battery: t("Значок батареи"),
  weather: t("Погода"), core_status: t("Статус ядра"), load: t("Загрузка ЦП и ОЗУ"), focus: t("Фокус-сессия"),
  event: t("Ближайшее событие"), downloads: t("Скачивания"), ticker: t("Курс валют"), sleep: t("Таймер сна"),
  coreinfo: t("Статус модели"),
});

// Капсула: левое плечо — лого (несъёмное), центр — часы (несъёмные), блоки перетаскиваются
// между плечами и внутри них; ниже — выключенные блоки (клик добавляет) и ширина островка.
function layoutEditor() {
  const lay = capsLayout();
  const labels = LAYOUT_LABELS();
  const drag = { key: null, from: null };

  const chip = (key) => el("div", { class: "lay-chip", draggable: "true" }, labels[key] || key,
    el("button", { class: "lay-x", title: t("Убрать"), onclick: (e) => { e.stopPropagation(); saveValues(partValues(key, false)); } }, "✕"));

  const zone = (side) => {
    const z = el("div", { class: "lay-zone" }, el("span", { class: "lz-t" }, side === "left" ? t("Слева от часов") : t("Справа от часов")));
    const keyAt = (x) => {
      let idx = 0;
      for (const c of z.querySelectorAll(".lay-chip")) {
        const r = c.getBoundingClientRect();
        if (x < r.left + r.width / 2) break;
        idx++;
      }
      return idx;
    };
    z.addEventListener("dragover", (e) => {
      e.preventDefault();
      z.classList.add("over");
      const dragged = z.parentElement.querySelector(".lay-chip.drag");
      if (dragged) {
        const chips = [...z.querySelectorAll(".lay-chip:not(.drag)")];
        const idx = keyAt(e.clientX);
        const ref = chips[Math.min(idx, chips.length - 1)];
        z.insertBefore(dragged, idx >= chips.length ? null : ref);
      }
    });
    z.addEventListener("dragleave", () => z.classList.remove("over"));
    z.addEventListener("drop", (e) => {
      e.preventDefault();
      z.classList.remove("over");
      commit();
    });
    return z;
  };
  const leftZ = zone("left");
  const rightZ = zone("right");
  const commit = () => {
    const read = (z) => [...z.querySelectorAll(".lay-chip")].map((c) => c.firstChild.textContent.trim()
      && Object.keys(labels).find((k) => labels[k] === c.firstChild.textContent.trim()));
    const left = read(leftZ).filter(Boolean);
    const right = read(rightZ).filter(Boolean);
    saveLayout({ left, right });
    render();
  };
  for (const k of lay.left) leftZ.append(chip(k));
  for (const k of lay.right) rightZ.append(chip(k));

  const dragStart = (c, key) => {
    c.addEventListener("dragstart", (e) => { drag.key = key; c.classList.add("drag"); e.dataTransfer.effectAllowed = "move"; });
    c.addEventListener("dragend", () => { drag.key = null; c.classList.remove("drag"); });
  };
  for (const c of [...leftZ.querySelectorAll(".lay-chip"), ...rightZ.querySelectorAll(".lay-chip")]) {
    const label = c.firstChild.textContent.trim();
    dragStart(c, Object.keys(labels).find((k) => labels[k] === label));
  }

  const mid = el("div", { class: "lay-mid" }, iconNode("clock", 13), t("Часы"));
  const capsule = el("div", { class: "lay-capsule" }, leftZ, mid, rightZ);

  // выключенные блоки: клик включает их в правом плече
  const disabled = CAPS_ALL.filter((k) => !on(k) && k !== "core_status");
  const addRow = disabled.length ? editGroup(t("Добавить блок"), disabled.map((k) =>
    chipT("＋ " + (labels[k] || k), false, () => saveValues(partValues(k, true))))) : null;

  // ширина островка: «авто» или фикс в диапазоне 150–520
  const W = Number((S.cfg.island.layout || {}).width) || 0;
  const range = el("input", { type: "range", min: "150", max: "760", step: "2", value: String(W >= 150 ? W : 280) });
  range.addEventListener("input", () => saveLayout({ width: Number(range.value) }));
  range.addEventListener("pointerdown", (e) => e.stopPropagation());
  const widthRow = el("div", { class: "edit-group width-row" },
    el("div", { class: "eg-t" }, t("Длина островка")),
    el("div", { class: "row" },
      chipT(t("Авто"), W < 150, () => saveLayout({ width: 0 })),
      range, el("span", { class: "mono w-val" }, W >= 150 ? W + "px" : t("авто"))));

  // панель: карточки в сетке — перетаскивание меняет порядок, кнопка — ширину (½ или вся)
  const defs = panelOrdered(widgetDescriptors().filter((d) => on(d.key)));
  const partLabel = new Map(PARTS.map(([k, label]) => [k, label]));
  const grid = el("div", { class: "panel-lay" });
  const pdrag = { key: null };
  for (const d of defs) {
    const wide = panelSpan(d.key, d.span) === 2;
    const card = el("div", { class: "pl-card" + (wide ? " wide" : ""), draggable: "true" },
      el("span", { class: "t" }, t(partLabel.get(d.key) || d.key)),
      el("button", { class: "lay-x", title: wide ? t("Половина ширины") : t("Во всю ширину"),
        onclick: () => savePanelLayout({ span: Object.assign({}, (S.cfg.island.panel_layout || {}).span || {},
          { [d.key]: wide ? 1 : 2 }) }) }, wide ? "⇤⇥" : "↔"));
    card.addEventListener("dragstart", () => { pdrag.key = d.key; card.classList.add("drag"); });
    card.addEventListener("dragend", () => {
      if (pdrag.key) {
        const order = [...grid.querySelectorAll(".pl-card")].map((c) =>
          defs.find((x) => (partLabel.get(x.key) || x.key) === c.firstChild.textContent.trim())?.key).filter(Boolean);
        savePanelLayout({ order });
        render();
      }
      pdrag.key = null; card.classList.remove("drag");
    });
    grid.append(card);
  }
  grid.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (!pdrag.key) return;
    const cards = [...grid.querySelectorAll(".pl-card:not(.drag)")];
    const dragged = grid.querySelector(".pl-card.drag");
    if (!dragged) return;
    let before = null;
    for (const c of cards) { const r = c.getBoundingClientRect(); if (e.clientY < r.top + r.height / 2) { before = c; break; } }
    grid.insertBefore(dragged, before);
  });

  return [el("div", { class: "sub edit-hint" }, t("Перетащи блоки капсулы между плечами. Лого всегда слева, часы — по центру.")),
    capsule, addRow, widthRow,
    editGroup(t("Виджеты панели — порядок и ширина"), [grid])];
}

// ---------- виджеты панели «Сейчас»: сетка с раскладкой (порядок и ширина — в редакторе) ----------

const KIND_ICON = { clear: "☀️", partly: "⛅", cloudy: "☁️", fog: "🌫️", rain: "🌧️", sleet: "🌨️", snow: "❄️", thunder: "⛈️" };

function wHead(ic, label) {
  return el("div", { class: "w-t" }, iconNode(ic, 13), t(label));
}

function daysWidget() {
  const days = (S.weather && S.weather.days) || [];
  if (!days.length) {
    return el("button", { class: "widget hint span2", onclick: () => invoke("show_main", { tab: "settings" }) },
      wHead("gauge", "Прогноз на 5 дней"),
      el("div", { class: "sub" }, (S.weather && (S.weather.reason || S.weather.error)) || t("Укажите город в настройках погоды")));
  }
  return el("div", { class: "widget days span2" }, days.slice(0, 5).map((d, i) => {
    const date = new Date(d.date + "T12:00:00");
    return el("div", { class: "day" + (i === 0 ? " today" : "") },
      el("span", { class: "d" }, i === 0 ? t("Сегодня") : date.toLocaleDateString(locale(), { weekday: "short" })),
      el("span", { class: "ic" }, KIND_ICON[d.kind] || "☁️"),
      el("span", { class: "t mono" }, d.min + "° " + d.max + "°"));
  }));
}

function agendaWidget() {
  const cal = S.calendar || {};
  const now = Date.now() / 1000;
  const evs = (cal.events || []).filter((e) => e.end > now).slice(0, 4);
  if (!evs.length) {
    return el("button", { class: "widget hint span2", onclick: () => invoke("show_main", { tab: "settings" }) },
      wHead("calendar", "Повестка дня"),
      el("div", { class: "sub" }, cal.configured === false ? t("Подключите Google Календарь: адрес iCal — Настройки · Интеграции")
        : (cal.error ? cal.error : t("Ближайших событий нет"))));
  }
  return el("div", { class: "widget agenda span2" }, wHead("calendar", "Повестка дня"),
    evs.map((ev) => {
      const inMin = Math.round((ev.start - now) / 60);
      const time = new Date(ev.start * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
      return el("div", { class: "ag-row" }, el("b", { class: "mono" }, time),
        el("span", { class: "t" }, ev.title),
        inMin > 0 ? el("span", { class: "chip" }, t("через {n} мин", { n: inMin })) : null);
    }));
}

function sysmonWidget() {
  const st = S.sysmon || {};
  if (st.cpu === undefined || st.cpu === null) return el("div",{class:"widget"},wHead("gauge","Система"),el("div",{class:"sub"},t("Данные системы пока недоступны")));
  const row = (label, val) => el("div", { class: "mon-row" }, el("span", { class: "lbl" }, label),
    el("span", { class: "bar" }, el("i", { style: { width: Math.min(100, val || 0) + "%" } })),
    el("span", { class: "mono" }, (val ?? "—") + "%"));
  return el("div", { class: "widget span2" }, wHead("gauge", "Система"),
    row(t("ЦП"), st.cpu), row(t("ОЗУ"), st.ram), row(t("Диск"), st.disk));
}

function dlWidget() {
  const act = (S.downloads && S.downloads.active) || [];
  if (!act.length) {
    return el("div", { class: "widget span2" }, wHead("download", "Загрузки"),
      el("div", { class: "sub" }, t("Активных загрузок нет")));
  }
  return el("div", { class: "widget span2 dl" }, wHead("download", "Загрузки"),
    act.slice(0, 3).map((d) => el("div", { class: "dl-row" },
      el("span", { class: "t", title:d.name }, d.name),
      el("span", { class: "bar" }, el("i", { style: { width: Math.round((d.progress || 0) * 100) + "%" } })),
      d.progress != null ? el("span", { class: "mono" }, Math.round(d.progress * 100) + "%") : null,
      el("span", { class: "mono s" }, speedText(d.speed) + (d.eta ? " · " + t("осталось {t}", { t: fmtTime(d.eta) }) : "")))));
}

function mixerWidget() {
  const st = S.mixer || {};
  if (st.error && !st.apps) {
    return el("div", { class: "widget span2" }, wHead("mixer", "Микшер"), el("div", { class: "sub" }, st.error));
  }
  const slider = (val, fn) => {
    const i = el("input", { type: "range", min: "0", max: "100", value: String(Math.max(0, Math.min(100, val ?? 0))) });
    i.addEventListener("input", () => fn(Number(i.value)));
    i.addEventListener("pointerdown", (e) => e.stopPropagation());
    return i;
  };
  const rows = [el("div", { class: "mx-row master" }, iconNode("volume", 14),
    el("b", { class: "t" }, t("Общая громкость")), slider(st.master ?? 0, (v) => core.try("mixer.set", { pid: 0, volume: v })))];
  for (const a of (st.apps || []).slice(0, 6)) {
    rows.push(el("div", { class: "mx-row" }, el("span", { class: "t" }, String(a.name || "").replace(/\.exe$/i, "")),
      slider(a.volume, (v) => core.try("mixer.set", { pid: a.pid, volume: v }))));
  }
  return el("div", { class: "widget span2 mixer" }, wHead("mixer", "Микшер громкости"), ...rows);
}

function noteWidget() {
  const last = (S.notes || [])[0];
  const input = el("input", { class: "bar-input small", 'data-focus-key': 'quick-note', value:S.noteDraft||'', placeholder: t("Быстрая заметка — Enter сохранит…") });
  input.addEventListener("input",()=>{S.noteDraft=input.value;});
  input.addEventListener("focus",()=>invoke("island_focus",{focus:true}));
  input.addEventListener("blur",()=>invoke("island_focus",{focus:false}));
  input.addEventListener("pointerdown", (e) => e.stopPropagation());
  input.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && input.value.trim() && !S.noteSaving) {
      const draft=input.value;S.noteSaving=true;
      const result=await core.try("notes.add", { text: draft.trim() });
      S.noteSaving=false;
      if(!result||result.error){pushCard({title:t("Не удалось сохранить"),text:result?.error||'',kind:'error'});return;}
      if(S.noteDraft===draft){S.noteDraft='';input.value='';}
      pushCard({ title: t("Заметка сохранена"), kind: "ok", icon: "note", timeout: 2 });
    }
    if (e.key === "Escape") input.blur();
  });
  return el("div", { class: "widget span2 note-w" }, wHead("note", "Заметка"),
    el("div", { class: "row" }, input),
    last ? el("div", { class: "sub note-last" }, String(last.text || "")) : null);
}

function healthWidget() {
  const H = (S.cfg.island && S.cfg.island.health) || {};
  const enabled = !!H.enabled;
  const setHealth = (patch) => saveValues({ "island.health": Object.assign({}, H, patch) });
  const left = enabled && S.healthAt
    ? Math.max(0, Math.ceil(((H.interval || 45) * 60000 - (Date.now() - S.healthAt)) / 1000)) : 0;
  return el("div", { class: "widget health" }, wHead("eye", "Перерывы"),
    el("div", { class: "row" },
      el("button", { class: "btn" + (enabled ? " primary" : " ghost"), onclick: () => setHealth({ enabled: !enabled }) },
        enabled ? t("Включены") : t("Выключены")),
      el('select',{class:'field health-interval','aria-label':t('Интервал перерывов'),onchange:e=>setHealth({interval:Number(e.target.value)})},[20,30,45,60].map(m=>el('option',{value:m,selected:(H.interval||45)===m},m+' '+t('мин'))))),
    el("div", { class: "sub" }, enabled
      ? (left > 0 ? t("Перерыв через {t}", { t: fmtTime(left) }) : t("Перерыв идёт…"))
      : t("Островок напомнит встать и размяться")));
}

// Дескрипторы виджетов панели: ключ, ширина по умолчанию (колонки сетки), построитель.
function widgetDescriptors() {
  return [
    { key: "w_weather", span: 1, make: () => panelWeatherWidget() },
    { key: "w_days", span: 2, make: daysWidget },
    { key: "w_agenda", span: 2, make: agendaWidget },
    { key: "w_sysmon", span: 2, make: sysmonWidget },
    { key: "w_downloads", span: 2, make: dlWidget },
    { key: "w_mixer", span: 2, make: mixerWidget },
    { key: "w_note", span: 2, make: noteWidget },
    { key: "w_health", span: 1, make: healthWidget },
    { key: "w_clock", span: 1, make: () => el("div", { class: "widget" }, el("div", { class: "big mono" }, clockNode()),
      el("div", { class: "sub" }, new Date().toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long" }))) },
    { key: "w_battery", span: 1, make: () => batInfo() ? el("div", { class: "widget" },
      el("div", { class: "big" }, (batInfo().charging ? "⚡ " : "") + Math.round(batInfo().percent) + "%"),
      el("div", { class: "sub" }, batInfo().charging ? t("Заряжается") : t("Батарея"))) : el("div",{class:"widget"},wHead("battery","Питание"),el("div",{class:"sub"},t(S.status.battery?.no_battery?"Питание от сети · батарея не обнаружена":"Данные питания пока недоступны"))) },
    { key: "w_timers", span: 1, make: () => {
      const list = [...S.timers.values()].slice(0, 3);
      if (!list.length) return el("button",{class:"widget hint",onclick:()=>openBar("таймер ")},wHead("timer","Таймеры"),el("div",{class:"sub"},t("Создать таймер")));
      return el("div", { class: "widget" }, list.map((tm) => {
        const left = tm.state === "running" ? timerLeft(tm) : 0;
        return el("div", { class: "trow " + tm.state },
          el("div", { class: "big mono" }, tm.state === "done" ? t("Готово") : fmtTime(left)),
          el("div", { class: "sub" }, tm.label || t("Таймер")),
          el("button", { class: "x", onclick: () => { core.try("island.cancel_timer", { timer_id: tm.id }); S.timers.delete(tm.id); render(); } }, "✕"));
      }));
    } },
  ];
}

function panelWeatherWidget() {
  const w = S.weather || {};
  const hasTemp = w.temp !== undefined && w.temp !== null;
  return hasTemp
    ? el("div", { class: "widget weather-summary" }, el("div", { class: "big" }, (w.icon || "") + " " + Math.round(w.temp) + "°"),
      el("div", { class: "sub" }, [w.city, w.text || w.desc].filter(Boolean).join(" · ")))
    : el("button", { class: "widget hint weather-summary", title: "Открыть настройки погоды", onclick: () => invoke("show_main", { tab: "settings" }) },
      el("div", { class: "big" }, "Погода"),
      el("div", { class: "sub" }, w.reason || w.error || "Укажите город в Настройках"));
}

function panelSpan(key, def) {
  const span = ((S.cfg.island.panel_layout || {}).span || {})[key];
  return span === 1 || span === 2 ? span : def;
}

function panelOrdered(defs) {
  const saved = (S.cfg.island.panel_layout || {}).order;
  if (!Array.isArray(saved)) return defs;
  const byKey = new Map(defs.map((d) => [d.key, d]));
  const out = saved.map((k) => byKey.get(k)).filter(Boolean);
  out.push(...defs.filter((d) => !saved.includes(d.key)));
  return out;
}

function panelNow() {
  const blocks = [];
  if ((on("p_player") || S.cfg.island.visual_layout?.now) && S.media.active) {
    const m = S.media;
    const pos = m.position || 0, dur = m.duration || 0;
    blocks.push(el("div", { class: "player", style: m.accent ? { "--accent": m.accent } : {} },
      cover("l"),
      el("div", { class: "meta" },
        el("div", { class: "title" }, m.title || t("Без названия")), el("div", { class: "artist" }, [m.artist, m.app].filter(Boolean).join(" · ")),
        on("p_lyrics") && S.lyric ? el("div", { class: "lyric" }, S.lyric) : null,
        dur ? el("div", { class: "progress", onclick: (e) => { if (!m.can_seek) return; const r = e.currentTarget.getBoundingClientRect(); core.try("media.control", { action: "seek", position: ((e.clientX - r.left) / r.width) * dur }); } },
          el("i", { style: { width: Math.min(100, (pos / dur) * 100).toFixed(1) + "%" } })) : null,
        dur ? el("div", { class: "times mono" }, el("span", {}, fmtTime(pos)), el("span", {}, fmtTime(dur))) : null,
        el("div", { class: "controls" },
          el("button", { class: "icon", disabled: !m.can_prev, onclick: () => core.try("media.control", { action: "previous" }) }, iconNode("prev", 14)),
          el("button", { class: "icon big", onclick: () => core.try("media.control", { action: "play_pause" }) }, iconNode(m.playing ? "pause" : "play", 16)),
          el("button", { class: "icon", disabled: !m.can_next, onclick: () => core.try("media.control", { action: "next" }) }, iconNode("next", 14)),
          on("p_eq") ? eq(9) : null))));
  }
  const defs = panelOrdered(widgetDescriptors().filter((d) => on(d.key)));
  const widgets = el("div", { class: "widgets wgrid" });
  for (const d of defs) {
    let node = null;
    try { node = d.make(); } catch (e) { node = null; }
    if (node) {
      node.classList.add("lay-" + d.key, "span" + panelSpan(d.key, d.span));
      widgets.append(node);
    }
  }
  if (S.cfg.island.visual_layout?.now) {
    const nodes=[];
    if(blocks[0]) {
      const player=blocks[0];
      const visualizer=player.querySelector('.eq');
      if(on('p_lyrics')){
        const data=S.lyricsData;
        const text=data?.plain||(data?.lines||[]).map(l=>l.text).join('\n')||t('Текст песни пока недоступен');
        nodes.push(['p_lyrics',el('div',{class:'lyrics-panel'},el('div',{class:'row'},el('span',{class:'sub grow'},data?.source||'LRCLIB'),el('button',{class:'icon',title:t('Обновить текст песни'),onclick:()=>loadTrackLyrics(true)},iconNode('refresh',14))),el('div',{class:'lyrics-full'+(S.lyricsScroll?' scroll-enabled':''),tabindex:'0',role:'region','aria-label':t('Текст песни'),onclick:e=>{e.stopPropagation();S.lyricsScroll=true;e.currentTarget.classList.add('scroll-enabled');},onkeydown:e=>{if(e.key==='Enter'){S.lyricsScroll=true;e.currentTarget.classList.add('scroll-enabled');}}},text))]);
      }
      if(visualizer)nodes.push(['p_eq',panelVisualizer(visualizer)]);
      if(on("p_player"))nodes.unshift(["p_player",player]);
    }
    for(const d of defs) {const node=widgets.querySelector(".lay-"+d.key);if(node)nodes.push([d.key,node]);}
    const empty={p_player:()=>el("div",{class:"widget"},wHead("music","Плеер"),el("div",{class:"sub"},t("Запустите музыку в любом плеере"))),p_lyrics:()=>el("div",{class:"widget"},wHead("note","Текст песни"),el("div",{class:"sub"},t("Текст песни пока недоступен"))),p_eq:()=>panelVisualizer(eq())};
    for(const [key,make]of Object.entries(empty))if(on(key)&&!nodes.some(([k])=>k===key))nodes.push([key,make()]);
    return [gridRegion(S.cfg.island,"now",nodes)];
  }
  if (widgets.children.length) blocks.push(widgets);
  if (!blocks.length) blocks.push(el("div", { class: "empty" }, t("Здесь всё скрыто — нажмите ✎, чтобы вернуть элементы")));
  return blocks;
}

const TOOL_BUTTONS = [
  ["shot_area", "crop", "Область", () => hotkey("shot_area")],
  ["shot_screen", "monitor", "Экран", () => hotkey("shot_screen")],
  ["ocr", "ocr", "Текст (OCR)", () => hotkey("ocr")],
  ["qr", "qr", "QR-код", () => hotkey("qr")],
  ["color", "pip", "Пипетка", () => hotkey("color")],
  ["record", "rec", "Запись экрана", () => hotkey("record")],
  ["record_area", "shot", "Запись области", () => invoke("open_selector", { purpose: "record" })],
  ["dictation", "keyboard", "Диктовка", () => hotkey("dictation")],
  ["note", "note", "Заметка", () => openBar("заметка: ")],
  ["timer", "timer", "Таймер", () => openBar("таймер ")],
];

function panelTools() {
  const visibleTools = TOOL_BUTTONS.filter(([id]) => listOn(S.cfg.island, "tools", id));
  const grid = el("div", { class: "grid" }, TOOL_BUTTONS.filter(([id]) => listOn(S.cfg.island, "tools", id)).map(([, ic, name, fn]) =>
    el("button", { class: "tile", onclick: (e) => { e.stopPropagation(); S.expanded = false; S.pinned = false; render(); fn(); } }, el("span", { class: "ic" }, iconNode(ic, 18)), el("span", {}, t(name)))));
  const out = [grid];
  if (on("obs") && S.obs && S.obs.connected) {
    out.push(el("div", { class: "row obs" }, el("span", { class: "sub" }, "OBS"),
      el("button", { class: "btn" + (S.obs.recording ? " danger" : ""), onclick: () => core.try("obs.record", { command: "toggle" }) }, S.obs.recording ? t("Остановить запись") : t("Запись экрана")),
      el("button", { class: "btn" + (S.obs.streaming ? " danger" : ""), onclick: () => core.try("obs.stream", { command: "toggle" }) }, t("Эфир")),
      (S.obs.scenes || []).slice(0, 4).map((sc) => el("button", { class: "btn ghost" + (sc === S.obs.scene ? " on" : ""), onclick: () => core.try("obs.set_scene", { name: sc }) }, sc))));
  }
  if(on("obs")&&!out[1])out.push(el("button",{class:"widget hint",onclick:()=>invoke("show_main",{tab:"settings"})},wHead("rec","Управление OBS"),el("span",{class:"sub"},t("Подключите OBS в настройках интеграций"))));
  if(S.cfg.island.visual_layout?.tools) return [gridRegion(S.cfg.island,"tools",[
    ...[...grid.children].map((node,i)=>[visibleTools[i][0],node]),["obs",out[1]]])];
  return out;
}

async function loadClip() {
  const request = ++clipLoadRequest;
  const res = await core.try("clipboard.list", { limit: 20 });
  if (request !== clipLoadRequest) return;
  const items = Array.isArray(res) ? res : res && Array.isArray(res.items) ? res.items : null;
  if (!items) {
    if (res && res.error) toastCard(t("Буфер обмена"), res.error);
    return;
  }
  S.clip = items;
  if (S.clipExpanded && !items.some((item) => item.id === S.clipExpanded.id)) {
    clipOpenRequest++;
    S.clipExpanded = null;
  }
  render();
}

async function toggleClip(itemId) {
  if (S.clipExpanded && S.clipExpanded.id === itemId) {
    clipOpenRequest++;
    S.clipExpanded = null;
    render();
    return;
  }
  const request = ++clipOpenRequest;
  S.clipExpanded = { id: itemId, loading: true, text: "" };
  render();
  const item = await core.try("clipboard.get", { item_id: itemId });
  if (request !== clipOpenRequest || !S.clipExpanded || S.clipExpanded.id !== itemId) return;
  if (!item || item.error || typeof item.text !== "string") {
    S.clipExpanded = null;
    toastCard(t("Буфер обмена"), item && item.error || t("Не удалось загрузить полный текст записи"));
  } else {
    S.clipExpanded = { id: itemId, loading: false, text: item.text };
  }
  render();
}

// «Копировать»: полный текст записи — в системный буфер пользователя (без вставки в активное окно).
async function copyClip(item) {
  const fallback = String(item.preview || item.text || "");
  const res = await core.try("clipboard.get", { item_id: item.id });
  const text = res && typeof res.text === "string" ? res.text : fallback;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    toastCard(t("Буфер обмена"), (e && e.message) || t("Не удалось скопировать запись"));
    return;
  }
  pushCard({ title: t("Скопировано"), text: text.slice(0, 90), kind: "ok", icon: "clip", timeout: 2 });
}

async function setClipPinned(itemId, pinned) {
  const result = await core.try("clipboard.pin", { item_id: itemId, pinned });
  if (!result || result.error || !result.ok) {
    toastCard(t("Буфер обмена"), result && result.error || t("Не удалось изменить закрепление записи"));
    return;
  }
  await loadClip();
}

async function deleteClip(itemId) {
  const result = await core.try("clipboard.delete", { item_id: itemId });
  if (!result || result.error || !result.ok) {
    toastCard(t("Буфер обмена"), result && result.error || t("Не удалось удалить запись"));
    return;
  }
  await loadClip();
}

function panelClip() {
  if (!S.clip.length) return [el("div", { class: "empty" }, t("История буфера пуста"))];
  return [el("div", { class: "clip-list" }, S.clip.map((c) => {
    const expanded = S.clipExpanded && S.clipExpanded.id === c.id;
    const preview = String(c.preview || c.text || "");
    const text = expanded ? S.clipExpanded.loading ? t("Загрузка полного текста…") : S.clipExpanded.text : preview.slice(0, 160);
    return el("div", { class: "clip" + (c.pinned ? " pinned" : "") + (expanded ? " expanded" : "") },
      el("button", { class: "clip-content", type: "button", "aria-expanded": !!expanded,
        "aria-label": expanded ? t("Свернуть запись буфера обмена") : t("Раскрыть запись буфера обмена") + ", " + (c.length || preview.length) + " " + t("симв."),
        title: expanded ? t("Свернуть запись буфера обмена") : t("Показать полностью"), onclick: () => toggleClip(c.id) },
        c.kind === "image" && c.preview && !expanded ? el("img", { src: c.preview, alt: "Предпросмотр изображения" }) : el("span", { class: "t" }, text)),
      el("button", { class: "btn ghost clip-copy", type: "button", title: t("Скопировать в буфер обмена"), onclick: () => copyClip(c) }, t("Копировать")),
      el("button", { class: "icon", type: "button", title: c.pinned ? t("Открепить") : t("Закрепить"), "aria-label": c.pinned ? t("Открепить запись") : t("Закрепить запись"), onclick: () => setClipPinned(c.id, !c.pinned) }, c.pinned ? "★" : "☆"),
      el("button", { class: "icon", type: "button", title: t("Удалить"), "aria-label": t("Удалить запись"), onclick: () => deleteClip(c.id) }, "✕"));
  }))];
}

function panelAgent() {
  const input=el('textarea',{class:'agent-input',rows:3,placeholder:t('Задача для Эйдоса…'),'data-focus-key':'agent-input'},S.agentDraft||'');
  input.addEventListener('input',()=>{S.agentDraft=input.value;});
  input.addEventListener('focus',()=>invoke('island_focus',{focus:true}));
  input.addEventListener('blur',()=>invoke('island_focus',{focus:false}));
  const send=async()=>{
    const text=input.value.trim();if(!text||S.agentSending||BUSY.has(S.agent.state))return;
    S.agentSending=true;S.agentDraft=text;render();
    const r=await core.try('agent.ask',{text,source:'island'});
    S.agentSending=false;
    if(r?.error||!r)toastCard(t('Не получилось'),r?.error||t('Ядро не подключено'));else S.agentDraft='';
    render();
  };
  input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}if(e.key==='Escape')input.blur();});
  const busy=BUSY.has(S.agent.state);
  return [el('section',{class:'agent-panel'},
    el('div',{class:'agent-status'},el('div',{},el('b',{},stateLabel(S.agent.state)),on('pa_model')?el('div',{class:'sub'},S.router||t('Модель пока не выбрана')):null),
      busy?el('button',{class:'btn danger',onclick:stopAll},iconNode('stop',14),t('Стоп')):iconNode('chat',24)),
    on('pa_steps')&&S.agent.steps.length?el('div',{class:'agent-steps'},stepsMini()):null,
    on('pa_reply')?el('div',{class:'agent-reply'},S.agent.lastReply||t('Задайте вопрос или поручите задачу')):null,
    on('pa_input')?el('div',{class:'agent-compose'},input,el('div',{class:'row'},el('span',{class:'sub grow'},'Enter · '+t('Отправить')+' / Shift+Enter · '+t('Новая строка')),micButton(()=>mic.start('ask',micOpts())),el('button',{class:'btn primary',disabled:busy||S.agentSending,onclick:send},t('Отправить')))):null)];
}

function renderCards() {
  const signature=JSON.stringify(S.cards);
  if(cardsBox.dataset.signature===signature)return;
  cardsBox.dataset.signature=signature;
  cardsBox.replaceChildren(...S.cards.map((c) =>
    el("div", { class: "card " + (c.kind || "info") + (c.out ? " out" : "") },
      c.swatch ? el("span", { class: "ic swatch", style: { background: c.swatch } }) : el("span", { class: "ic" }, icon(c.icon || (c.kind === "ok" ? "check" : c.kind === "error" ? "close" : c.kind === "warn" ? "alert" : "info"))),
      el("div", { class: "body" }, el("div", { class: "title" }, c.title || ""), c.text ? el("div", { class: "text" }, c.text) : null,
        c.image ? el("img", { class: "thumb", src: c.image }) : null,
        (c.actions || []).length ? el("div", { class: "row actions" }, c.actions.map((a) =>
          el("button", { class: "btn" + (a.primary ? " primary" : ""), onclick: () => { core.try("island.action", { card_id: c.id, action: a.id, data: a.data }); dropCard(c.id); } }, a.label))) : null),
      el("button", { class: "icon x", onclick: () => dropCard(c.id) }, "✕"))));
}

// Островок не прячется, пока курсор рядом с капсулой (но не на ней) — в пределах 64px от контура.
function updateNear(x, y) {
  if (!isl) return;
  const r = isl.getBoundingClientRect();
  const dx = Math.max(r.left - x, 0, x - r.right);
  const dy = Math.max(r.top - y, 0, y - r.bottom);
  const near = Math.hypot(dx, dy) <= 64;
  if (near !== S.near) { S.near = near; render(); }
}

function tucked(m) {
  const I = S.cfg.island;
  const editing=isl.contains(document.activeElement?.closest('input,textarea,[contenteditable=true]'));
  const quiet=!!I.auto_hide&&!S.hover&&!S.expanded&&!S.pinned&&!editing&&!S.cards.length&&!S.bar.open&&['idle','music'].includes(m);
  if(!quiet){quietSince=Date.now();clearTimeout(tuckTimer);tuckTimer=null;return false;}
  if(S.hideRequested)return true;
  const wait=1000;
  const left = quietSince + wait - Date.now();
  if (left <= 0) { peeked = false; return true; }
  if (!tuckTimer) tuckTimer = setTimeout(() => { tuckTimer = null; render(); }, left + 30);
  return false;
}

function peek() {
  if (!root.classList.contains("hidden")) return;
  S.hideRequested=false;peeked = true; S.hover = true; S.peekAt = Date.now();
  clearTimeout(leaveTimer);
  render();
}

let lastMode = "";
function render() {
  if (!isl||S.resizing) return;
  const scrollNodes=()=>[isl,...isl.querySelectorAll('.panel-body,.island-cell,.lyrics-full,.agent-reply')];
  const oldTab=isl.dataset.panelTab;
  if(isl.dataset.mode==='panel'&&oldTab)panelScroll.set(oldTab,scrollNodes().map(n=>({x:n.scrollLeft,y:n.scrollTop})));
  const focused=document.activeElement?.dataset.focusKey;
  const selection=focused?[document.activeElement.selectionStart,document.activeElement.selectionEnd]:null;
  const m = mode();
  if (m !== lastMode) { isl.dataset.prev = lastMode; lastMode = m; isl.classList.remove("pop", "enter"); void isl.offsetWidth; isl.classList.add("pop", "enter"); clearTimeout(render.enterT); render.enterT = setTimeout(() => isl.classList.remove("enter"), 380); }
  isl.dataset.mode = m;
  isl.dataset.edge = S.cfg.island.eq_edge === false ? "off" : "on";
  isl.dataset.playing = S.media.active && S.media.playing ? "1" : "0";
  // акцент капсулы: цвет трека, если есть, иначе — акцент темы (хардкод #7c8cff
  // перекрывал тему для батареи, щита и всего внутри острова)
  if (S.media.active && (S.cfg.island.waves?.color||S.media.accent)) isl.style.setProperty("--accent", S.cfg.island.waves?.color||S.media.accent);
  else isl.style.removeProperty("--accent");
  isl.style.setProperty("--cover", S.media.active && S.media.cover ? "url(" + JSON.stringify(S.media.cover) + ")" : "none");
  isl.classList.toggle("flash", Date.now() - S.flash < 500);
  isl.classList.toggle("away", S.away);
  isl.dataset.music = musicKind();
  isl.dataset.lyric = m === "music" && musicKind() === "wide" && S.cfg.island.lyrics !== false && S.lyric ? "1" : "0";
  root.classList.toggle("hidden", tucked(m));
  root.dataset.playing = isl.dataset.playing;
  root.style.setProperty("--accent", (S.cfg.island.waves?.color || S.media.active&&S.media.accent || getComputedStyle(document.documentElement).getPropertyValue("--accent")));
  root.style.setProperty("--lip-width", `${Math.max(1, isl.offsetWidth)}px`);
  root.classList.toggle("through", !!S.cfg.island.through_mode && (m === "idle" || m === "music" || m === "timer" || m === "rec"));
  const focusedValue = m === "bar" ? null : undefined;
  let content = (VIEWS[m] || VIEWS.idle)().filter(Boolean);
  if (S.cfg.island.visual_layout?.activity && ["agent","reply","mic"].includes(m)) {
    const nodes=[];
    const stop=content[0]?.querySelector(".stop"), meter=content[0]?.querySelector(".eq.mic");
    if(stop) nodes.push(["agent_stop",stop]);
    if(meter) nodes.push(["mic_meter",meter]);
    if(content[1])nodes.push([m==="reply"?"reply_text":"agent_steps",content[1]]);
    content=[content[0],gridRegion(S.cfg.island,"activity",nodes)];
  }
  isl.replaceChildren(...content);
  isl.classList.toggle("visual-capsule",m==="idle" && !!S.cfg.island.visual_layout?.capsule);
  isl.classList.toggle("visual-music",m==="music" && !!S.cfg.island.visual_layout?.music);
  void focusedValue;
  isl.dataset.panelTab=m==='panel'?S.tab:'';
  if(m==='panel'){
    const size=panelSize(S.cfg.island.panel_size?.[S.tab]);isl.style.width=size.w+'px';isl.style.height='auto';isl.style.minHeight='0';isl.style.maxHeight=Math.max(120,(innerHeight-20)/(Number(S.cfg.island.scale)||1))+'px';
    const saved=panelScroll.get(S.tab)||[];
    scrollNodes().forEach((n,i)=>{if(saved[i]){n.scrollLeft=saved[i].x;n.scrollTop=saved[i].y;}});
    if(focused){const next=isl.querySelector(`[data-focus-key="${focused}"]`);if(next){next.focus({preventScroll:true});if(selection)next.setSelectionRange(...selection);}}
  }else{isl.style.height='';isl.style.minHeight='';isl.style.maxHeight='';}
  const waveColor=(S.cfg.island.waves?.color||S.media.active&&S.media.accent||getComputedStyle(isl).getPropertyValue('--accent')).trim();
  isl.style.setProperty('--music-color',waveColor);
  isl.classList.toggle('music-tinted',m!=='panel'&&!!S.media.active);

  // Мини-островок: капсула сжимается и растягивается под набор включённых элементов,
  // плечи слева и справа делаются равными — часы стоят ровно по центру островка.
  // Волны флюида читают offsetWidth каждый кадр и следуют за шириной сами.
  if (m === "idle" && !S.cfg.island.visual_layout?.capsule) {
    const fixed = Number((S.cfg.island.layout || {}).width) || 0;
    if (fixed >= 150) {
      isl.style.width = fixed + "px";
      root.style.setProperty("--lip-width", fixed + "px");
    } else {
      const row = isl.querySelector(".row.tri");
      if (row && row.children.length >= 3) {
        isl.style.width = "max-content";
        const [l, mid, r] = [...row.children].slice(0, 3).map((c) => c.getBoundingClientRect().width);
        const side = Math.max(l, r);
        // 12px — паддинг капсулы (6+6), 16px — два гэпа сетки по 8
        const total = Math.max(150, Math.min(760, Math.ceil(mid + side * 2 + 16 + 12)));
        isl.style.width = total + "px";
        root.style.setProperty("--lip-width", total + "px");
      } else isl.style.width = "";
    }
  } else if (m!=="panel"&&isl.style.width) isl.style.width = "";
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
    if (root.classList.contains("hidden")) { const lr = lip.getBoundingClientRect(); invoke("set_hit_region", { x: Math.round(lr.left), y: 0, width: Math.round(lr.width), height: 12 }); return; }
    let x1 = r.left, y1 = r.top, x2 = r.right, y2 = r.bottom;
    for (const c of cardsBox.children) {
      const cr = c.getBoundingClientRect();
      x1 = Math.min(x1, cr.left); x2 = Math.max(x2, cr.right); y2 = Math.max(y2, cr.bottom);
    }
    const pad = S.cfg.island.edge_open === false ? 4 : 8;
    const halo=root.querySelector('.island-halo');
    if(halo)Object.assign(halo.style,{left:(r.left-pad)+'px',top:r.top+'px',width:(r.width+pad*2)+'px',height:(r.height+pad)+'px'});
    invoke("set_hit_region", { x: x1 - pad, y: y1, width: x2 - x1 + pad * 2, height: y2 - y1 + pad });
  });
}

// ------------------------------------------------------------------ actions

function micButton(onclick) {
  const b = el("button", { class: "icon mic", title: t("Голосом"), onclick: () => (mic.active ? mic.stop() : onclick()) }, micIcon());
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
  pushCard({ title: t("Остановлено"), text: t("Агент прервал текущую задачу"), kind: "warn", timeout: 3 });
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
  if (res && res.error) pushCard({ title: t("Не получилось"), text: res.error, kind: "error" });
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

// ------------------------------------------------------------------ фокус-сессия, таймер сна, здоровье

const FOCUS_KEY = "idos.focus";
function saveFocus() {
  try { localStorage.setItem(FOCUS_KEY, JSON.stringify(S.focus)); } catch (e) { /* приватный режим */ }
}

function focusStart(phase) {
  const mins = phase === "break" ? (S.focus && S.focus.breakMin) || 5 : (S.focus && S.focus.focusMin) || 25;
  S.focus = Object.assign(S.focus || { focusMin: 25, breakMin: 5 }, { phase, endsAt: Date.now() / 1000 + mins * 60, left: 0 });
  saveFocus();
}

// клик по блоку: нет сессии — старт (25 минут), идёт — пауза/продолжить
function toggleFocus() {
  const f = S.focus;
  if (!f) {
    focusStart("focus");
    pushCard({ title: t("Фокус-сессия"), text: t("25 минут работы — затем перерыв"), kind: "info", icon: "ring", timeout: 4 });
    render();
    return;
  }
  if (f.endsAt) { f.left = Math.max(0, f.endsAt - Date.now() / 1000); f.endsAt = 0; }
  else { f.endsAt = Date.now() / 1000 + (f.left || (f.phase === "break" ? 5 : 25) * 60); f.left = 0; }
  saveFocus();
  render();
}

function focusTick() {
  const f = S.focus;
  if (!f || !f.endsAt || f.endsAt - Date.now() / 1000 > 0) return;
  if (f.phase === "focus") {
    f.phase = "break";
    f.endsAt = Date.now() / 1000 + (f.breakMin || 5) * 60;
    pushCard({ title: t("Перерыв"), text: t("Фокус-сессия завершена — {n} минут перерыва", { n: f.breakMin || 5 }), kind: "ok", icon: "ring", timeout: 6 });
    if (fluid) fluid.bead();
  } else {
    f.phase = "focus";
    f.endsAt = Date.now() / 1000 + (f.focusMin || 25) * 60;
    pushCard({ title: t("Фокус-сессия"), text: t("Перерыв закончен — за работу"), kind: "info", icon: "ring", timeout: 5 });
  }
  saveFocus();
}

function sleepTick() {
  const sl = S.sleep;
  if (!sl || !sl.endsAt || sl.endsAt - Date.now() / 1000 > 0) return;
  S.sleep = null;
  core.try("system.power", { action: "sleep" });
  pushCard({ title: t("Таймер сна"), text: t("Перевожу компьютер в сон"), kind: "info", icon: "hourglass", timeout: 5 });
}

// Здоровье: раз в заданный интервал карточка «перерыв»; островок сам всплывает, если включено.
function healthTick() {
  const H = (S.cfg.island && S.cfg.island.health) || {};
  if (!H.enabled || S.cards.some((c) => c.id === "health-break")) return;
  if (Date.now() - (S.healthAt || 0) >= (H.interval || 45) * 60000) {
    S.healthAt = Date.now();
    pushCard({ id: "health-break", title: t("Пора сделать перерыв"), text: t("Встань и пройдись — глаза отдохнут. 20 секунд смотри вдаль."), kind: "info", icon: "eye", timeout: 30,
      actions: [{ id: "done", label: t("Готово"), primary: true }] });
    if (root && root.classList.contains("hidden")) peek();
    S.stick = true;
    render();
  }
}

async function applyConfig(cfg) {
  S.cfg = Object.assign({ island: {}, hotkeys: {} }, cfg || {});
  S.cfg.island = normalizedIsland(S.cfg.island);
  const isl0 = S.cfg.island;
  const waves = isl0.waves || {};
  const lipPeriod = Math.max(0.5, Math.min(6, Number(waves.lip_breath_period_s ?? 2.4)));
  const reducedMotion = waves.respect_reduced_motion !== false && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.style.setProperty("--wave-lip-animation", waves.lip_breath_enabled === false || reducedMotion ? "none" : `lipbreath ${lipPeriod}s ease-in-out infinite`);
  if (/^#[0-9a-f]{6}$/i.test(waves.color || "")) root.style.setProperty("--wave-accent", waves.color);
  else root.style.removeProperty("--wave-accent");
  document.documentElement.style.setProperty("--scale", String(isl0.scale || 1));
  document.documentElement.style.setProperty("--op-closed", String(isl0.opacity_collapsed ?? 1));
  document.documentElement.style.setProperty("--op-open", String(isl0.opacity_open ?? 1));
  await invoke("island_place", { monitor: Number(isl0.monitor || 0), offsetX: Number(isl0.offset_x || 0) });
  await invoke("set_capture_visible", { visible: !!isl0.show_in_capture });
  const map = {};
  const invalid = [];
  for (const [k, v] of Object.entries(S.cfg.hotkeys || {})) {
    if (!v) continue;
    const parts = String(v).split("+").map((part) => part.trim().toLowerCase());
    if (parts.includes("*") || parts.includes("numpadmultiply") || (parts.includes("shift") && parts.includes("8"))) {
      invalid.push(k + ": " + t("сочетание «{v}» не поддерживается (клавиша * недоступна). Выберите другое сочетание.", { v }));
      continue;
    }
    map[k] = String(v);
  }
  const res = await invoke("register_hotkeys", { map });
  const errors = invalid.concat(res && res.errors ? Object.values(res.errors) : []);
  if (errors.length) pushCard({ title: t("Горячие клавиши"), text: errors.join("\n"), kind: "warn", timeout: 10 });
  render();
}

// ------------------------------------------------------------------ wiring

export function mountIsland() {
  document.body.classList.add("view-island");
  S.healthAt = Date.now();
  root = el("div", { class: "stage" });
  isl = el("div", { class: "island", "data-mode": "idle" });
  cardsBox = el("div", { class: "cards" });
  const cv = el("canvas", { class: "fluid" });
  const wideDrops = el("canvas", { class: "wide-drops" });
  lip = el("div", { class: "lip" });
  root.append(lip, isl, cv, wideDrops, cardsBox);
  fluid = new Fluid(isl, cv, () => {
    const m = mode();
    const cs = getComputedStyle(isl);
    const waves = S.cfg.island.waves || {};
    return {
      hidden: root.classList.contains("hidden"),
      waves,
      liquid: (S.cfg.island.character || "liquid") === "liquid" && !root.classList.contains("hidden"),
      pill: m !== "panel" && m !== "bar" && m !== "confirm" && isl.dataset.lyric !== "1" && musicKind() !== "mini" || m === "idle",
      playing: !!(S.media.active && S.media.playing), edge: S.cfg.island.eq_edge !== false && waves.music_enabled !== false, bands: S.bands,
      accent: (waves.color || cs.getPropertyValue("--accent") || "#7c8cff").trim(), bg: cs.backgroundColor,
      rainColor: "rgba(150,190,255,0.9)", away: S.away, rain: S.weather && ["rain", "sleet", "thunder", "snow"].includes(S.weather.kind),
    };
  }, wideDrops);
  const onCursor = (x, y) => {
    fluid.setCursor(x, y);
    updateNear(x, y);
    const r = isl.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    isl.style.setProperty("--px", Math.max(-1, Math.min(1, (x - cx) / 260)).toFixed(3));
    isl.style.setProperty("--py", Math.max(-1, Math.min(1, (y - cy) / 160)).toFixed(3));
  };
  document.addEventListener("mousemove", (e) => onCursor(e.clientX, e.clientY));
  listen("island-cursor", (p) => { if (Array.isArray(p)) onCursor(p[0], p[1]); else if (p) onCursor(p.x, p.y); });
  lip.addEventListener("mouseenter", peek);
  lip.addEventListener("mousemove", peek);
  // Клик по полоске при скрытом островке: капсула раскрывается и ОСТАЁТСЯ открытой
  // (первый клик — «обычный островок»); второй клик по капсуле откроет панель.
  lip.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); peek(); S.stick = true; });
  const enter=()=>{S.hover=true;S.hideRequested=false;clearTimeout(leaveTimer);};
  const leave=()=>{
    clearTimeout(leaveTimer);
    leaveTimer=setTimeout(()=>{
      if(S.pinned||isl.contains(document.activeElement?.closest('input,textarea,[contenteditable=true]')))return;
      S.hover=false;S.near=false;S.stick=false;S.expanded=false;S.hideRequested=true;render();
    },1000);
  };
  const halo=el('div',{class:'island-halo',onclick:()=>isl.click()});root.prepend(halo);
  for(const node of [isl,halo,lip]){node.addEventListener('mouseenter',enter);node.addEventListener('mouseleave',leave);}
  document.body.append(root);
  new ResizeObserver(scheduleHit).observe(isl);
  new ResizeObserver(scheduleHit).observe(cardsBox);
  isl.addEventListener('transitionend',scheduleHit);
  isl.addEventListener('click',e=>{
    const cell=e.target.closest('.island-cell');
    if(!cell||e.target.closest('button,input,textarea,a'))return;
    const buttons=cell.querySelectorAll('button');
    if(buttons.length===1&&!cell.querySelector('input,textarea')){e.stopPropagation();buttons[0].click();}
  });
  document.addEventListener("pointerdown",e=>{if(!e.target.closest(".lyrics-full")){S.lyricsScroll=false;document.querySelectorAll(".lyrics-full").forEach(n=>n.classList.remove("scroll-enabled"));}},true);
  isl.addEventListener("click", (e) => {
    if (e.target.closest("button,input,.bar-item,.clip,.progress")) return;
    const m = mode();
    if (m === "idle" || m === "music" || m === "timer") openExpandedIsland();
  });
  // Двойной клик открывает окно только на капсуле; в раскрытой панели это обычный жест
  // (двойной клик по записи буфера не должен тянуть за собой окно Эйдоса).
  isl.addEventListener("dblclick", (e) => {
    if (mode() === "panel" || mode() === "bar") return;
    if (!e.target.closest("button,input")) invoke("show_main", { tab: "chat" });
  });
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
  // ПКМ по капсуле: переключение музыкального и обычного вида. Голос по ПКМ убрали —
  // запуск микрофона падал карточкой «Нет доступа к микрофону», голос остался на PageUp.
  isl.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (mode() === "music") S.musicView = "normal";
    else if (S.media.active && S.media.title) S.musicView = "auto";
    render();
  });

  listen("hotkey", (name) => hotkey(typeof name === "string" ? name : name && name.name));
  listen("emergency-stop", stopAll);
  listen("island-hover", (inside) => {
    // Rust releases the strip hit-region only after the 2-second dwell.
    if (inside && root.classList.contains("hidden")) peek();
    if(inside)enter();else leave();
  });
  listen("core-restarted", () => pushCard({ title: t("Ядро перезапущено"), kind: "ok", timeout: 3 }));

  core.on("core.connected", async () => {
    S.online = true;
    const [cfg, media, status, weather, timers, rec, router, calendar, downloads, ticker, mixer, sysmon, notes] = await Promise.all([
      core.try("settings.get"), core.try("media.state"), core.try("status.state"), core.try("weather.state"),
      core.try("island.timers"), core.try("recorder.state"), core.try("router.status"),
      core.try("calendar.state"), core.try("downloads.state"), core.try("ticker.state"),
      core.try("mixer.state"), core.try("sysmon.state"), core.try("notes.list", {})]);
    if (media && !media.error) {S.media=media;loadTrackLyrics();}
    if (status && !status.error) S.status = status;
    if (weather && !weather.error) S.weather = weather;
    if (Array.isArray(timers)) timers.forEach((t) => S.timers.set(t.id, {state:"running",...t}));
    if (rec && !rec.error) S.rec = rec;
    if (router && !router.error) S.router = router.active_label || router.active || "";
    if (calendar && !calendar.error) S.calendar = calendar;
    if (downloads && !downloads.error) S.downloads = downloads;
    if (ticker && !ticker.error) S.ticker = ticker;
    if (mixer && !mixer.error) S.mixer = mixer;
    if (sysmon && !sysmon.error) S.sysmon = sysmon;
    if (Array.isArray(notes)) S.notes = notes;
    S.healthAt = Date.now();
    const indicators=await core.try("status.indicators");if(indicators&&!indicators.error){S.kbd=indicators.keyboard||{};S.privacy=indicators.privacy||{};}
    await applyConfig(cfg && !cfg.error ? cfg : {});
  });
  core.on("core.lost", () => { S.online = false; render(); });
  core.on("core.waiting", (info) => { S.online = false; if (info && info.error && !S.coreErrShown) { S.coreErrShown = true; pushCard({ title: t("Ядро не запущено"), text: info.error, kind: "error", timeout: 0 }); } render(); });

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
  core.on('island.timer',timer=>{
    if(timer.state==='done'||timer.state==='cancelled')S.timers.delete(timer.id);
    else S.timers.set(timer.id,{...S.timers.get(timer.id),...timer});
    // Completion alerts (sticky, with dismiss action) are sent by core as island.card.
    render();
  });
  core.on("media.state", (m) => { const old=S.media.track_id||S.media.title;S.media=m||{active:false};if(!S.media.active){S.musicView="auto";S.lyricsKey=null;S.lyricsData=null;S.lyric="";}else if(old!==(S.media.track_id||S.media.title)){S.lyricsKey=null;loadTrackLyrics();}render(); });
  core.on("media.position", (p) => { if (S.media) { if(p.track_id&&p.track_id!==S.media.track_id)return;S.media.position = p.position;syncLyric(); if (mode() === "panel" && S.tab === "now" && Date.now() - (S.posDrawn || 0) > 900) { S.posDrawn = Date.now(); render(); } } });
  core.on("media.levels", (d) => {
    S.bands = d.bands || S.bands;
    const avg = S.bands.length ? S.bands.reduce((a, b) => a + (b || 0), 0) / S.bands.length : 0;
    const bass = S.bands.length ? Math.max(S.bands[0] || 0, S.bands[1] || 0) : 0;
    isl.style.setProperty("--lvl", (S.media.playing ? Math.min(1, avg * 1.3) : 0).toFixed(3));
    isl.style.setProperty("--bass", (S.media.playing ? Math.min(1, bass) : 0).toFixed(3));
    isl.querySelectorAll('.eq-ring').forEach(box=>{box._bands=S.bands;box.dataset.playing=String(!!S.media.playing);});
  });
  core.on('media.lyrics',d=>{if(d?.track_id&&d.track_id!==S.media.track_id)return;if(d?.lines||d?.plain){S.lyricsData=d;syncLyric();}else S.lyric=d?.line||d?.text||'';if(mode()==='panel'||mode()==='music')render();});
  core.on("recorder.state", (r) => { const was = S.rec.recording; S.rec = r || {}; if (was && !S.rec.recording && S.rec.path) pushCard({ title: t("Запись сохранена"), text: S.rec.path, kind: "ok", icon: "rec" }); render(); });
  core.on("obs.state", (o) => { S.obs = o || {}; render(); });
  core.on("status.state", (s) => { S.status = s || {}; render(); });
  core.on("status.charging", (s) => {
    S.status = S.status || {};
    const prev = S.status.battery && typeof S.status.battery === "object" ? S.status.battery : {};
    S.status.battery = Object.assign({}, prev, s || {}, { charging: true });
    render();
  });
  core.on("weather.state", (w) => { S.weather = w || {}; render(); });
  core.on("sysmon.state", (d) => { S.sysmon = d || {}; if (mode() === "idle" || (mode() === "panel" && S.tab === "now")) render(); });
  core.on("calendar.changed", (d) => { S.calendar = d || {}; render(); });
  core.on("downloads.changed", (d) => { S.downloads = Object.assign({}, S.downloads, d); if (mode() === "idle" || (mode() === "panel" && S.tab === "now")) render(); });
  core.on("ticker.changed", (d) => { S.ticker = d || {}; render(); });
  core.on("mixer.changed", (d) => { S.mixer = d || {}; if (mode() === "panel" && S.tab === "now") render(); });
  core.on("notes.changed", (d) => { if (Array.isArray(d && d.items)) S.notes = d.items; if (mode() === "panel" && S.tab === "now") render(); });
  core.on("router.status", (r) => { S.router = (r && (r.active_label || r.active)) || S.router; render(); });
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
    if (d.state === "armed") pushCard({ id: "color-pick", title: t("Пипетка"), text: t("Наведите курсор на нужный цвет и нажмите Ctrl · Esc — отмена"), kind: "info", icon: "pip", timeout: 0 });
    else if (d.state === "preview") {
      const c = S.cards.find((x) => x.id === "color-pick");
      if (c) { c.title = t("Пипетка") + " · " + d.hex; c.text = t("Нажмите Ctrl, чтобы взять этот цвет · Esc — отмена"); c.swatch = d.hex; renderCards(); }
    } else {
      dropCard("color-pick");
      if (d.state === "cancel") pushCard({ title: t("Пипетка"), text: t("Отменена"), kind: "info", icon: "pip", timeout: 2 });
    }
  });
  core.on("capture.color", (c) => pushCard({ title: t("Цвет ") + (c.hex || ""), text: t("Скопировано в буфер"), kind: "info", icon: "pip", timeout: 4 }));
  core.on("commandbar.open", (d) => openBar((d && d.text) || ""));
  core.on("clipboard.added", () => { if (S.tab === "clip" && mode() === "panel") loadClip(); });
  core.on("voice.stt", (d) => { if (d && d.partial && S.mic.state === "listening") { S.mic.partial = d.partial; } });

  // Часы, таймеры и счётчик записи тикают раз в секунду без лишних перерисовок сложных режимов.
  setInterval(() => {
    const m = mode();
    const ticking = (S.focus && S.focus.endsAt) || (S.sleep && S.sleep.endsAt);
    if (m === "idle" || m === "timer" || m === "rec" || (m === "panel" && S.tab === "now") || ticking) {
      focusTick();
      sleepTick();
      render();
    }
    healthTick();
  }, 1000);
  window.addEventListener("idos.lang", render);
  window.addEventListener("resize",render);
  if (!inTauri) document.body.classList.add("demo");
  render();
}

export { hotkey as islandHotkey, S as islandState, pushCard };

// Редактор использует те же построители DOM, что и настоящее окно, и реальные снимки ядра.
export function normalizedIsland(I = {}) {
  const visual_layout={capsule:{},header:{},now:{},tools:{},agent:{},...I.visual_layout};
  delete visual_layout.music;delete visual_layout.activity;
  return {...I,visual_layout,lyrics:true};
}
export function renderEditorRegion(cfg, region, data = {}) {
  const previous={...S};
  try {
    Object.assign(S,data,{cfg:{...cfg,island:normalizedIsland(cfg.island)},capsuleDetail:null,tab:region==='header'?(data.previewTab||'now'):region,online:core.connected});
    if(S.lyricsData?.track_id&&S.lyricsData.track_id!==S.media.track_id)S.lyricsData=null;
    syncLyric();
    if(region==='capsule')return visualCapsule();
    if(region==='header')return panel()[0];
    if(region==='now')return panelNow()[0];
    if(region==='tools')return panelTools()[0];
    return panelAgent()[0];
  } finally {Object.assign(S,previous);}
}
