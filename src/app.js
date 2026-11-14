// Главное окно Эйдоса: диалоги, память, ключи и модели, инструменты/скиллы/MCP, настройки.
import { core } from "./rpc.js";
import { invoke, listen, el, toast, win, inTauri, micIcon, setMicState } from "./shell.js";
import { Mic } from "./voice.js";
import { t, lang, setLang, locale } from "./i18n.js";
import { icon, iconNode } from "./icons.js";
import { createNous } from "./nous.js";
import { applyTheme, ACCENTS, BASE_VARS, normalizeTheme } from "./theme.js";
import { renderScenarios, scenarioEvent } from "./scenarios.js";
import { PARTS, TAB_LIST, TOOL_LIST, partOn, listOn, partValues, RESET_VALUES, applyValues } from "./parts.js";

// Названия вкладок — русские строки-ключи: при отрисовке оборачиваются в t().
const TABS = [
  { id: "chat", name: "Диалог", icon: "chat", gr: "Λόγος", myth: "Логос у Гераклита — слово и разум" },
  { id: "memory", name: "Память", icon: "memory", gr: "Μνήμη", myth: "Мнемозина — титанида памяти" },
  { id: "models", name: "Ключи и модели", icon: "key", gr: "Δύναμις", myth: "Аристотель — сила и возможность" },
  { id: "tools", name: "Инструменты", icon: "tools", gr: "Ὄργανον", myth: "«Органон» Аристотеля" },
  { id: "scenarios", name: "Сценарии", icon: "scenario", gr: "Μίμησις", myth: "Мимесис — воспроизведение" },
  { id: "settings", name: "Настройки", icon: "settings", gr: "Θέμις", myth: "Фемида — богиня порядка" },
];
// Греческий eyebrow страницы (Γλῶтта, гайдбук §11): один на область, с расшифровкой в title.
const grEyebrow = (meta) => el("p", { class: "gr-eyebrow", title: t(meta.myth) }, meta.gr + " · " + t(meta.name).toLowerCase());

// Русские подписи всех ключей настроек (по конфигу ядра); на английский переводит t().
const SETTING_LABELS = {
  "scenarios.fast_path": "Быстрый запуск сценариев",
  "agent.autonomy": "Автономность", "agent.max_steps": "Максимум шагов", "agent.max_minutes": "Максимум минут, мин",
  "agent.tools_share": "Доля текста инструментов", "agent.auto_send_voice": "Отправлять голос автоматически",
  "agent.history_messages": "Сообщений в истории", "agent.tool_result_chars": "Символов в ответе инструмента",
  "agent.failsafe_corner": "Стоп в углу экрана", "agent.auto_memory_context": "Память в контексте",
  "agent.narrate_actions": "Озвучивать действия",
  "models.active": "Активная модель", "models.chain": "Цепочка резерва", "models.fast": "Быстрая модель",
  "models.force": "Закреплённая модель", "models.vision": "Модель зрения", "models.vision_only": "Только для зрения",
  "models.vision_chain": "Цепочка зрения", "models.stt": "Модель распознавания речи",
  "models.max_output_tokens": "Максимум токенов ответа", "models.max_input_tokens": "Максимум токенов ввода",
  "models.context_mode": "Режим контекста", "models.max_input_tokens_full": "Максимум токенов (полный)",
  "models.temperature": "Температура", "models.switch_before": "Переключаться заранее",
  "models.max_wait_seconds": "Максимум ожидания, с", "models.params": "Параметры моделей",
  "models.custom_providers": "Свои провайдеры", "models.paid_default": "Платные по умолчанию",
  "voice.tts_engine": "Движок озвучки", "voice.speak_replies": "Когда говорить", "voice.silero_model": "Модель Silero",
  "voice.silero_speaker": "Голос Silero", "voice.sample_rate": "Частота сэмплов", "voice.device": "Устройство (cpu/gpu)",
  "voice.rate": "Скорость речи", "voice.volume": "Громкость речи", "voice.stt": "Распознавание речи",
  "voice.stt_url": "Адрес STT-сервера", "voice.silence_ms": "Пауза конца речи, мс",
  "voice.max_speech_chars": "Максимум символов речи", "voice.lexicon": "Словарь произношения",
  "island.theme": "Тема островка", "island.scale": "Масштаб", "island.opacity_collapsed": "Прозрачность в покое",
  "island.opacity_open": "Прозрачность раскрытым", "island.character": "Характер жидкости",
  "island.auto_hide": "Скрываться в покое", "island.auto_hide_delay": "Задержка скрытия, с",
  "island.auto_hide_music": "Скрывать музыку (устарело)", "island.peek_delay_ms": "Задержка появления, мс",
  "island.expand_delay_ms": "Задержка раскрытия, мс", "island.hide_for_apps": "Прятать над приложениями",
  "island.hide_in_fullscreen": "Прятать в полноэкранном", "island.through_mode": "Сквозной режим",
  "island.show_in_capture": "Показываться в скриншотах", "island.monitor": "Монитор", "island.offset_x": "Смещение по X",
  "island.music_pill": "Вид музыкальной капсулы", "island.lyrics": "Строка песни", "island.eq_edge": "Эквалайзер на краю",
  "island.no_activate": "Не перехватывать фокус", "island.open_on_hover": "Раскрывать при наведении",
  "island.hover_delay_ms": "Задержка наведения, мс", "island.edge_open": "Открывать у края",
  "island.show_battery": "Батарея в капсуле", "island.show_weather": "Погода в капсуле", "island.sounds": "Звуки островка",
  "hotkeys.toggle": "Показать/скрыть островок", "hotkeys.voice": "Голосовой вопрос", "hotkeys.command_bar": "Командная строка",
  "hotkeys.chat": "Открыть диалог", "hotkeys.dictation": "Диктовка", "hotkeys.clipboard": "Буфер обмена",
  "hotkeys.shot_area": "Скриншот области", "hotkeys.shot_screen": "Скриншот экрана", "hotkeys.ocr": "Текст (OCR)",
  "hotkeys.qr": "QR-код", "hotkeys.color": "Пипетка цвета", "hotkeys.record": "Запись экрана",
  "hotkeys.paste_plain": "Вставить без форматирования", "hotkeys.mic_mute": "Заглушить микрофон",
  "hotkeys.stop_agent": "Остановить агента", "hotkeys.through_mode": "Сквозной режим (клавиша)", "hotkeys.vpn": "VPN (клавиша)",
  "clipboard.enabled": "История буфера включена", "clipboard.max_items": "Максимум записей",
  "clipboard.skip_apps": "Не следить в приложениях", "clipboard.paste_on_click": "Вставлять по клику",
  "weather.enabled": "Погода включена", "weather.city": "Город", "weather.lat": "Широта", "weather.lon": "Долгота",
  "weather.rain_warning": "Предупреждать о дожде", "weather.provider": "Источник погоды", "weather.yandex_key": "Ключ Яндекса",
  "capture.folder": "Папка скриншотов", "capture.record_folder": "Папка записей", "capture.record_fps": "Кадров в секунду",
  "capture.record_quality": "Качество записи", "capture.record_format": "Формат записи",
  "capture.record_mic": "Записывать микрофон", "capture.imgbb_key": "Ключ imgbb",
  "notifications.mirror": "Зеркалить уведомления", "notifications.skip_apps": "Пропускать приложения",
  "integrations.claude_code": "Claude Code", "integrations.codex": "Codex", "integrations.obs": "OBS (настройка)",
  "integrations.vpn_ip_lookup": "Показывать IP VPN", "integrations.downloads": "Следить за загрузками",
  "integrations.calendar_url": "Адрес iCal (Google Календарь)", "integrations.downloads_folder": "Папка загрузок",
  "web.search_provider": "Поисковик", "web.searxng_url": "Адрес SearXNG", "web.region": "Регион поиска",
  "web.tavily_key": "Ключ Tavily", "web.brave_key": "Ключ Brave",
  "browser.channel": "Канал браузера", "browser.cdp_url": "Адрес CDP", "browser.headless": "Без окна",
  "browser.executable": "Путь к браузеру", "browser.search_url": "Адрес поиска",
  "browser.user_data_dir": "Каталог профилей", "browser.profile_directory": "Каталог профиля",
  "alice.enabled": "Алиса включена", "alice.url": "Адрес Алисы", "alice.home_address": "Домашний адрес",
  "alice.input_selector": "Селектор поля ввода", "alice.answer_selector": "Селектор ответа",
  "dictation.type_into_window": "Печатать в окно",
  "audio.output_device": "Устройство вывода", "audio.input_device": "Устройство ввода",
  "mcp.servers": "MCP-серверы",
  "theme.preset": "Пресет темы", "theme.accent": "Акцент",
};

const A = { tab: "chat", chats: [], chat: null, messages: [], live: "", steps: [], state: "idle", label: "", online: false, cfg: {} };
let main, nav, statusEl;
// Знак Νοῦς для титлбара и hello-экрана (гайдбук §12.1)
const titleMark = createNous({ size: 20 });
titleMark.set("idle");
const helloMark = createNous({ size: 76 });
helloMark.set("idle");
const mic = new Mic((st) => {
  const b = document.querySelector(".composer .mic");
  setMicState(b, st);
  if (st.state === "error") toast(st.text, "error");
});

const UI_TIMEOUT = 10000;

function setTab(tab) {
  const pages = { chat: renderChat, memory: renderMemory, models: renderModels, tools: renderTools, scenarios: () => renderScenarios(main, () => A.tab === "scenarios"), settings: renderSettings };
  if (!pages[tab]) tab = "chat";
  const changed = A.tab !== tab || !main.firstChild;
  A.tab = tab;
  const tok = (A.tok = (A.tok || 0) + 1);
  nav.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  // Сразу показываем, что вкладка переключилась, даже если ядро отвечает долго.
  if (changed) main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, t("Загрузка…"))));
  Promise.resolve().then(() => pages[tab]()).catch((e) => {
    console.error("tab", tab, e);
    if (A.tok === tok && A.tab === tab)
      main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, t("Не удалось открыть вкладку: ") + (e && e.message ? e.message : e)),
        el("button", { class: "btn", onclick: () => setTab(tab) }, t("Повторить"))));
  });
}

function titlebar() {
  const bar = el("div", { class: "titlebar", "data-tauri-drag-region": true },
    el("span", { class: "brand", "data-tauri-drag-region": true }, el("span", { class: "logo nous-logo", title: t("Νοῦς — двигатель состояний") }, titleMark.el), t("Эйдос")),
    statusEl = el("span", { class: "state", "data-tauri-drag-region": true }, t("подключение…")),
    el("span", { class: "grow", "data-tauri-drag-region": true }),
    el("button", { class: "wbtn danger-h", title: t("Экстренная остановка (Ctrl+Alt+X)"), onclick: stopAgent }, iconNode("stop", 17)),
    el("button", { class: "wbtn", title: t("Свернуть"), onclick: () => win && win.minimize() }, iconNode("minus", 14)),
    el("button", { class: "wbtn", title: t("Развернуть"), onclick: () => win && win.toggleMaximize() }, iconNode("expand", 13)),
    el("button", { class: "wbtn close", title: t("Скрыть в трей"), onclick: () => win && win.hide() }, "✕"));
  return bar;
}

function stopAgent() { core.try("agent.stop"); core.try("voice.stop"); mic.cancel(); }

function updateStatus() {
  if (!statusEl) return;
  statusEl.className = "state " + (A.online ? A.state : "offline");
  statusEl.textContent = !A.online ? t("ядро не подключено") : A.state === "idle" ? t("готов") : (A.label || A.state);
  // статус без чтения: знак в титлбаре краснеет и дышит, пока ядро недоступно (ревью U4)
  titleMark.set(A.online ? "idle" : "error");
}

// ------------------------------------------------------------------ chat

async function loadChats() {
  const chats = await core.try("agent.chats", {}, UI_TIMEOUT);
  if (Array.isArray(chats)) A.chats = chats;
  if (!A.chat && A.chats.length) A.chat = A.chats[0].id;
  if (A.chat) {
    const msgs = await core.try("agent.messages", { chat_id: A.chat }, UI_TIMEOUT);
    if (Array.isArray(msgs)) A.messages = msgs;
  } else A.messages = [];
}

function msgView(m) {
  const meta = m.meta || {};
  return el("div", { class: "msg " + m.role + (m.ok === false ? " fail" : "") },
    el("div", { class: "bubble" }, m.text || m.content || "",
      meta.model ? el("div", { class: "meta" }, meta.model + (meta.ms ? " · " + fmtMs(meta.ms) : "")) : null,
      meta.retry_at ? el("div", { class: "meta retry-live", "data-at": String(meta.retry_at * 1000) }, retryText(meta.retry_at * 1000)) : null));
}

// 0.4 → "<1 мс", 37 → "37 мс", 1234 → "1.2 с", 75000 → "1 мин 15 с"
export function fmtMs(ms) {
  ms = Number(ms);
  if (!isFinite(ms) || ms < 0) return "";
  if (ms < 1) return t("<1 мс");
  if (ms < 1000) return Math.round(ms) + t(" мс");
  if (ms < 10000) return (ms / 1000).toFixed(1) + t(" с");
  if (ms < 60000) return Math.round(ms / 1000) + t(" с");
  return Math.floor(ms / 60000) + t(" мин ") + Math.round((ms % 60000) / 1000) + t(" с");
}

// Live countdown under «limits exhausted» replies.
export function retryText(at) {
  const left = Number(at) - Date.now();
  if (!isFinite(left)) return "";
  return left > 0 ? t("⏳ ближайший ключ освободится через ") + fmtMs(Math.ceil(left / 1000) * 1000)
    : t("✓ ключ уже должен был освободиться — можно повторить запрос");
}
if (typeof document !== "undefined") {
  setInterval(() => document.querySelectorAll(".retry-live").forEach((n) => { n.textContent = retryText(n.dataset.at); }), 1000);
}

function stepView(s) {
  const stIcon = s.status === "running" ? iconNode("spinner", 13)
    : s.status === "ok" ? iconNode("check", 13)
    : s.status === "denied" ? iconNode("close", 13)
    : s.status === "error" ? iconNode("alert", 13)
    : iconNode("minus", 13);
  return el("div", { class: "stepline " + (s.status || "") },
    el("span", { class: "st" }, stIcon),
    el("span", { class: "tool" }, s.tool + (s.action ? "." + s.action : "")),
    el("span", { class: "t" }, s.label || ""),
    s.status === "running" ? el("span", { class: "ms live", "data-t0": String(s.t0 || Date.now()) }, fmtMs(Date.now() - (s.t0 || Date.now())))
      : s.ms != null ? el("span", { class: "ms" }, fmtMs(s.ms)) : null,
    s.image ? el("img", { src: s.image }) : null,
    s.preview ? el("details", {}, el("summary", {}, t("результат")), el("pre", {}, s.preview)) : null);
}

async function renderChat() {
  await loadChats();
  if (A.tab !== "chat") return;
  const list = el("div", { class: "chatlist" },
    el("button", { class: "btn primary wide", onclick: async () => { const c = await core.try("agent.new_chat"); if (c && c.chat_id) { A.chat = c.chat_id; A.messages = []; } renderChat(); } }, t("+ Новый диалог")),
    A.chats.map((c) => el("div", { class: "chatitem" + (c.id === A.chat ? " on" : ""), onclick: async () => { A.chat = c.id; await core.try("agent.select_chat", { chat_id: c.id }); renderChat(); } },
      el("span", { class: "t" }, c.title || t("Без названия")),
      el("button", { class: "icon", title: t("Переименовать"), onclick: async (e) => { e.stopPropagation(); const nm = prompt(t("Название диалога"), c.title || ""); if (nm !== null) { await core.try("agent.rename_chat", { chat_id: c.id, title: nm }); renderChat(); } } }, iconNode("edit", 14)),
      el("button", { class: "icon", title: t("Удалить"), onclick: (e) => { e.stopPropagation(); confirmClick(e.currentTarget, async () => { await core.try("agent.delete_chat", { chat_id: c.id }); if (A.chat === c.id) A.chat = null; renderChat(); }); } }, iconNode("close", 14)))));
  const log = el("div", { class: "log" },
    A.messages.length ? A.messages.filter((m) => m.role === "user" || m.role === "assistant").map(msgView)
      : el("div", { class: "hello" }, helloMark.el, el("h2", {}, t("Привет! Я Эйдос.")),
        el("p", {}, t("Попросите открыть приложение, найти что-то в интернете, написать письмо или настроить Windows. Можно голосом — PageUp.")),
        el("div", { class: "chips" }, ["Собери сводку по моим заметкам", "Найди свежее про курс рубля и перескажи", "Сделай скриншот и объясни, что на экране", "Открой блокнот и составь список покупок"].map((s) =>
          el("button", { class: "chip", onclick: () => send(s) }, t(s))))),
    el("div", { class: "steps-live" }, A.steps.map(stepView)),
    A.live ? el("div", { class: "msg assistant live" }, el("div", { class: "bubble" }, A.live)) : null);
  const box = el("textarea", { rows: 1, placeholder: t("Напишите задачу…  Enter — отправить, Shift+Enter — новая строка") });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (box.value.trim()) { send(box.value.trim()); box.value = ""; } } });
  box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = Math.min(200, box.scrollHeight) + "px"; });
  const busy = A.state !== "idle";
  const composer = el("div", { class: "composer" }, modelPicker(), box,
    el("button", { class: "icon mic", title: t("Голосом (PageUp)"), onclick: () => (mic.active ? mic.stop() : mic.start("ask")) }, micIcon()),
    busy ? el("button", { class: "btn danger", onclick: stopAgent }, t("■ Стоп"))
      : el("button", { class: "btn primary", onclick: () => { if (box.value.trim()) { send(box.value.trim()); box.value = ""; } } }, t("Отправить")));
  main.replaceChildren(el("div", { class: "chatpage" }, list, el("div", { class: "chatmain" }, log, composer)));
  log.scrollTop = log.scrollHeight;
  box.focus();
}

// Выбор модели прямо в чате: Auto — активная модель с автопереключением на резервные,
// любая другая строка — принудительно только эта модель (ключи при этом всё равно чередуются).
function modelPicker() {
  const sel = el("select", { class: "modelpick", title: t("Модель для чата") }, el("option", { value: "" }, "Auto"));
  const wrap = el("span", { class: "modelpick-wrap" }, el("span", { class: "dot" }), sel); // не label: label пересылает клик скрытому select
  const paint = () => {
    wrap.classList.toggle("forced", !!sel.value);
    const o = sel.selectedOptions[0];
    wrap.title = sel.value ? t("Принудительно: ") + sel.value + t(" — без переключения на другие модели")
      : t("Auto: основная модель, при лимитах и ошибках — резервные") + (A.modelActive ? " (сейчас " + A.modelActive + ")" : "");
    sel.style.width = Math.max(56, Math.min(230, ((o && o.textContent) || "Auto").length * 7.4 + 30)) + "px";
  };
  const fill = async () => {
    const r = await core.try("router.choices", {}, UI_TIMEOUT);
    if (!r || !Array.isArray(r.choices)) return paint();
    A.modelActive = r.active || "";
    sel.replaceChildren(el("option", { value: "" }, "Auto"),
      ...r.choices.map((c) => el("option", { value: c.spec, disabled: !c.ready },
        c.model.split("/").pop() + " · " + c.provider + (c.ready ? "" : t(" (нет ключа)")))));
    sel.value = r.force || "";
    paint();
  };
  sel.addEventListener("change", async () => {
    const want = sel.value;
    const r = await core.try("router.force", { spec: want }, UI_TIMEOUT);
    if (!r || r.ok === false) { toast((r && r.error) || t("не удалось выбрать модель"), "error"); await fill(); return; }
    toast(want ? t("Модель закреплена: ") + want.split(":").pop() : t("Auto — автовыбор модели"), "ok");
    paint();
  });
  A.modelPickerFill = fill;
  fill();
  return wrap;
}

async function send(text) {
  A.messages.push({ role: "user", text });
  A.steps = [];
  A.live = "";
  if (A.tab === "chat") renderChatSoft();
  const r = await core.try("agent.ask", { text, chat_id: A.chat, source: "text" });
  if (r && r.error) toast(r.error, "error");
  else if (r && r.chat_id) A.chat = r.chat_id;
}

// Перерисовать только ленту, не трогая введённый текст.
function renderChatSoft() {
  const log = main.querySelector(".log");
  if (!log) return;
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.replaceChildren(...A.messages.filter((m) => m.role === "user" || m.role === "assistant").map(msgView),
    el("div", { class: "steps-live" }, A.steps.map(stepView)),
    ...(A.live ? [el("div", { class: "msg assistant live" }, el("div", { class: "bubble" }, A.live))] : []));
  if (stick) log.scrollTop = log.scrollHeight;
  const composer = main.querySelector(".composer");
  if (composer) {
    const last = composer.lastElementChild;
    const busy = A.state !== "idle";
    if (busy !== last.classList.contains("danger")) {
      const btn = busy ? el("button", { class: "btn danger", onclick: stopAgent }, t("■ Стоп"))
        : el("button", { class: "btn primary", onclick: () => { const b = composer.querySelector("textarea"); if (b.value.trim()) { send(b.value.trim()); b.value = ""; } } }, t("Отправить"));
      last.replaceWith(btn);
    }
  }
}

// ------------------------------------------------------------------ memory

async function renderMemory(query = "") {
  const res = query ? await core.try("memory.search", { query, limit: 50 }, UI_TIMEOUT) : await core.try("memory.list", { limit: 200 }, UI_TIMEOUT);
  if (A.tab !== "memory") return;
  if (res && res.error) throw new Error(res.error);
  const items = Array.isArray(res) ? res : (res && res.items) || [];
  const search = el("input", { class: "field", placeholder: t("Поиск по памяти…"), value: query });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") renderMemory(search.value.trim()); });
  const add = el("input", { class: "field", placeholder: t("Запомнить факт (например: «Я живу в Казани»)…") });
  add.addEventListener("keydown", async (e) => { if (e.key === "Enter" && add.value.trim()) { await core.try("memory.save", { text: add.value.trim() }); renderMemory(); } });
  main.replaceChildren(el("div", { class: "page" },
    grEyebrow(TABS[1]), el("h1", {}, t("Память")), el("p", { class: "mut" }, t("Локальная долговременная память агента. Эйдос сам сохраняет важное и вспоминает по смыслу.")),
    el("div", { class: "row2" }, search, add),
    el("div", { class: "cards-list" }, items.length ? items.map((m) => el("div", { class: "item" },
      el("div", { class: "t" }, m.text || m.content || ""),
      el("div", { class: "meta" },
        m.kind ? el("span", { class: "tag" }, m.kind) : null,
        [].concat(m.tags || []).filter(Boolean).map((t) => el("span", { class: "tag ok" }, t)),
        m.created ? el("span", {}, new Date((m.created > 1e12 ? m.created : m.created * 1000)).toLocaleString(locale())) : null),
      el("button", { class: "icon", title: t("Удалить"), onclick: async () => { await core.try("memory.delete", { mid: m.id }); renderMemory(query); } }, iconNode("close", 14))))
      : el("div", { class: "empty" }, t("Пока ничего не запомнено")))));
}

// ------------------------------------------------------------------ keys & models

function bucketLine(model, b) {
  const lim = Number(b.limit_requests) || 0, rem = Number(b.remaining_requests);
  const known = lim > 0 && Number.isFinite(rem);
  const pct = known ? Math.max(0, Math.min(100, Math.round((rem / lim) * 100))) : null;
  // Лимит неизвестен — полосы нет вовсе (класс .empty на полосе ловил глобальный
  // padding из body.view-app .empty и раздувал её до огромного блока, фото 1).
  // Текст встаёт на место полосы и приглушается: .bucket.no-lim .n в app.css.
  return el("div", { class: "bucket" + (known ? "" : " no-lim") }, el("span", { class: "m" }, model),
    known ? el("span", { class: "bar" }, el("i", { style: { width: pct + "%", background: pct < 15 ? "var(--err)" : pct < 40 ? "var(--warn)" : "var(--ok)" } })) : null,
    el("span", { class: "n" }, known ? rem + " / " + lim + t(" запр. · сегодня ") + (b.used_today || 0) : t("лимит неизвестен · сегодня ") + (b.used_today || 0)),
    b.cooldown_in ? el("span", { class: "warn" }, t("пауза ") + b.cooldown_in + "с") : null);
}

function selftestBox(status) {
  const specs = [...new Set([status.active, ...(status.chain || []), status.vision, ...(status.vision_chain || [])]
    .map((c) => (c && typeof c === "object" ? c.spec || c.model : c)).filter(Boolean))];
  if (!specs.length) return null;
  const pick = el("select", { class: "field mono" }, specs.map((s) => el("option", { value: s }, s)));
  const ultra = specs.find((s) => s.includes("nemotron-3-ultra"));
  if (ultra) pick.value = ultra;
  const out = el("div", { class: "mut small" });
  const btn = el("button", { class: "btn", onclick: async () => {
    btn.disabled = true; out.textContent = t("Проверяю — несколько коротких запросов…");
    const r = await core.try("router.selftest", { spec: pick.value }, 120000);
    btn.disabled = false;
    if (!r || r.error) { out.textContent = (r && r.error) || t("нет ответа"); return; }
    out.replaceChildren(el("div", {}, (r.ok ? "✅ " : "⚠️ ") + (r.message || r.spec)),
      ...(r.checks || []).map((c) => el("div", {}, (c.ok ? "✓ " : "✗ ") + c.name + " · " + (c.ms / 1000).toFixed(1) + " с" + (c.detail ? " — " + c.detail : ""))));
  } }, t("Проверить модель"));
  return el("section", { class: "box" }, el("h3", {}, t("Проверка модели")),
    el("p", { class: "mut small" }, t("Ответ, вызов инструмента, JSON, для моделей зрения — чтение картинки. Тратит 2–3 запроса.")),
    el("div", { class: "row2" }, pick, btn), out);
}

/* Двойной клик вместо window.confirm: в окне Tauri системный диалог может не появиться и вернуть false. */
function confirmClick(btn, action) {
  if (btn.dataset.armed) { delete btn.dataset.armed; clearTimeout(btn._t); btn.textContent = btn.dataset.label || "✕"; action(); return; }
  btn.dataset.armed = "1"; btn.dataset.label = btn.textContent; btn.textContent = "Удалить?"; btn.classList.add("armed");
  btn._t = setTimeout(() => { delete btn.dataset.armed; btn.textContent = btn.dataset.label; btn.classList.remove("armed"); }, 3000);
}

async function renderModels() {
  const [st, keys] = await Promise.all([core.try("router.status", {}, UI_TIMEOUT), core.try("keys.list", {}, UI_TIMEOUT)]);
  if (A.tab !== "models") return;
  const status = st && !st.error ? st : { accounts: [], providers: [], chain: [] };
  const pv = status.providers || [];
  const providers = (Array.isArray(pv) ? pv.map((p) => (typeof p === "string" ? { id: p, name: p } : p)) : Object.entries(pv).map(([id, p]) => Object.assign({ id }, p))).filter((p) => !p.no_key);
  const provSel = el("select", { class: "field" }, (providers.length ? providers : [{ id: "groq", name: "Groq" }]).map((p) => el("option", { value: p.id || p.name }, p.name || p.id)));
  const secret = el("textarea", { class: "field mono", rows: 3, placeholder: t("Ключи API — по одному на строку (можно вставить сразу много)") });
  const label = el("input", { class: "field", placeholder: t("Метка (необязательно)") });
  const active = el("input", { class: "field mono", value: status.active || "", placeholder: "groq:openai/gpt-oss-120b" });
  const accounts = status.accounts || [];
  const provNote = el("div", { class: "mut small" });
  const showNote = () => { const p = providers.find((x) => (x.id || x.name) === provSel.value); provNote.textContent = (p && p.note) || ""; };
  provSel.addEventListener("change", showNote);
  showNote();
  main.replaceChildren(el("div", { class: "page" },
    grEyebrow(TABS[2]), el("h1", {}, t("Ключи и модели")),
    el("p", { class: "mut" }, t("Роутер следит за лимитами каждого ключа и заранее переключается на следующий, когда запросы подходят к концу. Модель можно сменить и голосом: «Эйдос, переключись на …».")),
    el("section", { class: "box" }, el("h3", {}, t("Активная модель")),
      el("div", { class: "row2" }, active,
        el("button", { class: "btn primary", onclick: async () => { const r = await core.try("router.set_active", { spec: active.value.trim() }); r && r.error ? toast(r.error, "error") : toast(r && r.vision_only ? t("Эта модель только для изображений — назначена моделью зрения") : t("Модель выбрана"), "ok"); renderModels(); } }, "Применить"),
        null),
      status.chain && status.chain.length ? el("div", { class: "mut small" }, t("Цепочка резерва: ") + status.chain.map((c) => (typeof c === "string" ? c : c.spec || c.model)).join(" → ")) : null,
      status.vision_chain && status.vision_chain.length ? el("div", { class: "mut small" }, t("Цепочка зрения: ") + [status.vision || ""].concat(status.vision_chain).filter(Boolean).map((c) => (typeof c === "string" ? c : c.spec || c.model)).join(" → ")) : null,
      status.current ? el("div", { class: "mut small" }, t("Последний запрос: ") + status.current.spec + " · " + (status.current.account || "")) : null),
    selftestBox(status),
    el("section", { class: "box" }, el("h3", {}, t("Добавить ключи")),
      el("div", { class: "row2" }, provSel, label),
      provNote,
      secret,
      el("div", { class: "row2 end" }, el("button", { class: "btn primary", onclick: async () => {
        const lines = secret.value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
        if (!lines.length) return;
        const r = lines.length > 1 ? await core.try("keys.add_many", { provider: provSel.value, text: lines.join("\n") })
          : await core.try("keys.add", { provider: provSel.value, secret: lines[0], label: label.value });
        if (r && r.error) toast(r.error, "error"); else { toast(t("Добавлено ключей: ") + lines.length, "ok"); renderModels(); }
      } }, "Добавить"))),
    el("section", { class: "box" }, el("h3", {}, t("Ключи (") + accounts.length + ")"),
      accounts.length ? accounts.map((a) => el("div", { class: "account" + (a.current ? " current" : "") + (a.disabled ? " off" : "") },
        el("div", { class: "row2" },
          el("b", {}, a.provider), el("span", { class: "mono" }, a.masked || ""), el("span", { class: "mut" }, a.label || ""),
          a.current ? el("span", { class: "tag ok" }, t("активен")) : null,
          a.disabled ? el("span", { class: "tag warn" }, a.disabled_reason || t("отключён")) : null,
          el("span", { class: "grow" }),
          el("button", { class: "btn ghost", title: t("Проверить ключ коротким запросом"), onclick: async () => { const r = await core.try("router.test", { account_id: a.id }); r && (r.error || r.ok === false) ? toast(r.error || r.message || t("Ключ не отвечает"), "error") : toast((r && r.message) || t("Ключ работает"), "ok"); renderModels(); } }, "Проверить"),
          a.source === "env" ? el("span", { class: "tag", title: t("Ключ из .env или переменных окружения") }, ".env") : null,
          a.source !== "local" ? [
            el("label", { class: "switch" }, el("input", { type: "checkbox", checked: a.enabled !== false, onchange: async (e) => { await core.try("keys.enable", { key_id: a.id, enabled: e.target.checked }); renderModels(); } }), el("i")),
            el("button", { class: "icon del", title: t("Удалить ключ"), onclick: (e) => confirmClick(e.currentTarget, async () => {
              const r = await core.try("keys.remove", { key_id: a.id });
              if (r && r.ok) toast(r.env ? t("Ключ скрыт. Он записан в .env — уберите его оттуда, чтобы удалить совсем") : t("Ключ удалён"), "ok");
              else toast((r && r.error) || t("Не удалось удалить ключ"), "error");
              renderModels();
            }) }, iconNode("close", 14))] : null),
        Object.entries(a.buckets || {}).map(([m, b]) => bucketLine(m, b))))
        : el("div", { class: "empty" }, iconNode("key", 15), t("Ключей пока нет. Добавьте хотя бы один ключ Groq — console.groq.com/keys"))),
    Array.isArray(keys) && keys.length > accounts.length ? el("div", { class: "mut small" }, t("В хранилище ключей: ") + keys.length) : null));
}

// ------------------------------------------------------------------ tools

const TOOL_ICONS = {
  browser: "globe", computer: "app", weather: "sun", alice: "spark",
  files: "file", notes: "note", screen: "shot", clipboard: "clip",
};
async function renderTools() {
  const [tools, skills, mcp] = await Promise.all([core.try("tools.list", {}, UI_TIMEOUT), core.try("skills.list", {}, UI_TIMEOUT), core.try("mcp.servers", {}, UI_TIMEOUT)]);
  if (A.tab !== "tools") return;
  if (tools && tools.error) throw new Error(tools.error);
  main.replaceChildren(el("div", { class: "page" },
    grEyebrow(TABS[3]), el("h1", {}, t("Инструменты")),
    el("p", { class: "mut" }, t("Всё, чем агент может пользоваться самостоятельно. Скиллы — инструкции в data/skills, MCP-серверы — в настройках.")),
    el("div", { class: "toolgrid" }, (Array.isArray(tools) ? tools : []).map((tl) => el("div", { class: "tool" + (tl.available ? "" : " off") },
      el("div", { class: "row2" }, el("span", { class: "tool-ic" }, iconNode(TOOL_ICONS[tl.name] || "spark", 17)), el("b", {}, tl.name), el("span", { class: "grow" }), el("span", { class: "tag " + (tl.available ? "ok" : "warn") }, tl.available ? t("доступен") : t("недоступен"))),
      el("div", { class: "mut small" }, tl.description || ""),
      el("div", { class: "acts" }, Object.entries(tl.actions || {}).map(([n, a]) => el("span", { class: "act " + (a.kind || ""), title: a.label || "" }, n)))))),
    el("h2", {}, t("Скиллы")),
    el("div", { class: "cards-list" }, (Array.isArray(skills) && skills.length ? skills : []).map((s) => el("div", { class: "item" },
      el("div", { class: "t" }, el("b", {}, s.name || s), s.description ? " — " + s.description : ""),
      el("button", { class: "icon", title: t("Удалить"), onclick: (e) => confirmClick(e.currentTarget, async () => { await core.try("skills.delete", { name: s.name || s }); renderTools(); }) }, "✕"))),
      Array.isArray(skills) && skills.length ? null : el("div", { class: "empty" }, iconNode("spark", 15), t("Скиллов пока нет — агент создаёт их сам, когда осваивает новое приложение"))),
    el("h2", {}, t("MCP-серверы")),
    el("div", { class: "cards-list" }, (Array.isArray(mcp) ? mcp : []).map((m) => el("div", { class: "item" },
      el("div", { class: "t" }, el("b", {}, m.name), " ", el("span", { class: "mono mut" }, m.command ? [m.command].concat(m.args || []).join(" ") : m.url || "")),
      el("span", { class: "tag " + (m.running ? "ok" : "") }, m.running ? t("запущен") : t("остановлен")))),
      Array.isArray(mcp) && mcp.length ? null : el("div", { class: "empty" }, t("Нет подключённых серверов (настройка mcp.servers)")))));
}

// ------------------------------------------------------------------ settings

const SECTION_NAMES = {
  island: "Остров", hotkeys: "Горячие клавиши", voice: "Голос", audio: "Звук и микрофон", agent: "Агент",
  models: "Модели", web: "Интернет", capture: "Скриншоты и запись", integrations: "Интеграции", weather: "Погода",
  clipboard: "Буфер обмена", memory: "Память", mcp: "MCP", obs: "OBS", media: "Музыка", security: "Безопасность",
  browser: "Браузер", alice: "Алиса (Яндекс)",
};

function field(path, value) {
  const save = async (v) => {
    const r = await core.try("settings.set", { key: path, value: v });
    if (r && r.error) toast(r.error, "error"); else toast(t("Сохранено: ") + path, "ok");
  };
  if (typeof value === "boolean")
    return el("label", { class: "switch" }, el("input", { type: "checkbox", checked: value, onchange: (e) => save(e.target.checked) }), el("i"));
  if (typeof value === "number") {
    const inp = el("input", { class: "field small", type: "number", step: "any", value: String(value) });
    inp.addEventListener("change", () => save(Number(inp.value)));
    return inp;
  }
  if (value === null || typeof value === "string") {
    const inp = el("input", { class: "field" + (path.startsWith("hotkeys.") ? " mono hk" : ""), value: value || "" });
    if (path.startsWith("hotkeys.")) {
      inp.readOnly = true;
      inp.placeholder = t("нажмите сочетание");
      inp.addEventListener("keydown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "Escape") { inp.blur(); return; }
        if (e.key === "Backspace" || e.key === "Delete") { inp.value = ""; save(""); inp.blur(); return; }
        if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
        if (e.key === "*" || e.code === "NumpadMultiply") { inp.blur(); toast(t("Клавиша * не поддерживается как глобальное сочетание. Выберите другую клавишу."), "error"); return; }
        const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Super"].filter(Boolean);
        let k = e.code.replace(/^Key/, "").replace(/^Digit/, "");
        if (k === " " || e.code === "Space") k = "Space";
        inp.value = mods.concat(k).join("+");
        save(inp.value);
        inp.blur();
      });
    } else inp.addEventListener("change", () => save(inp.value));
    return inp;
  }
  const ta = el("textarea", { class: "field mono", rows: 3 }, JSON.stringify(value, null, 2));
  ta.addEventListener("change", () => { try { save(JSON.parse(ta.value)); } catch (e) { toast(t("Неверный JSON"), "error"); } });
  return ta;
}

// Погода: город задаётся здесь — ядро само геокодирует и включает обновление (weather.set_city).
function weatherCityBox() {
  const input = el("input", { class: "field", placeholder: t("Город — например, Казань"), "aria-label": t("Город погоды") });
  const btn = el("button", { class: "btn primary", onclick: async () => {
    const city = input.value.trim();
    if (!city || btn.disabled) return;
    btn.disabled = true; const oldLabel = btn.textContent; btn.textContent = t("Ищу город…");
    // геокод до ~30 с (Open-Meteo → Nominatim) + первый прогноз до ~45 с: рычаг длинный,
    // чтобы ядро успело ответить целиком (короткий таймаут давал «Ядро не ответило»)
    const r = await core.try("weather.set_city", { city }, 90000);
    btn.disabled = false; btn.textContent = oldLabel;
    if (!r || r.error) { toast((r && r.error) || t("Не удалось определить город"), "error"); return; }
    // город с координатами сохраняется, даже если прогноз снять не удалось, — говорим об этом честно
    if (r.ok === false || r.temp === undefined || r.temp === null) {
      toast(t("Город «{city}» сохранён, но прогноз недоступен: {err}", { city: r.city || city, err: r.error || r.reason || "" }), "error");
      renderSettings();
      return;
    }
    toast(t("Погода: ") + (r.city || city) + ", " + Math.round(r.temp) + "°", "ok");
    renderSettings();
  } }, t("Сохранить"));
  return el("section", { class: "box" }, el("h3", {}, t("Погода")),
    el("p", { class: "mut small" }, t("Город показывает температуру в острове — справа в капсуле и виджетом в панели «Сейчас». Ядро само найдёт координаты и включит обновление.")),
    el("div", { class: "row2" }, input, btn));
}

// Выбор языка интерфейса: применяется ко всем окнам сразу (событие settings.changed от ядра).
function langBox() {
  const seg = el("div", { class: "theme-seg" }, [["ru", "Русский"], ["en", "English"]].map(([id, name]) =>
    el("button", { class: "seg-btn" + (lang() === id ? " on" : ""), "data-lang": id, onclick: () => {
      if (lang() === id) return;
      setLang(id); // localStorage + <html lang> + settings.set — оба окна перерисуются сами
      window.dispatchEvent(new CustomEvent("idos.lang"));
    } }, name)));
  return el("section", { class: "box" }, el("h3", {}, t("Язык интерфейса")), seg);
}

async function renderSettings() {
  const cfg = await core.try("settings.get", {}, UI_TIMEOUT);
  if (A.tab !== "settings") return;
  if (!cfg || cfg.error) { main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, t("Настройки недоступны: ") + ((cfg && cfg.error) || t("нет связи"))))); return; }
  A.cfg = cfg;
  const filter = el("input", { class: "field", placeholder: t("Найти настройку…") });
  const body = el("div", {});
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    body.replaceChildren(...Object.entries(cfg).filter(([, v]) => v && typeof v === "object" && !Array.isArray(v)).map(([sec, vals]) => {
      const rows = Object.entries(vals).filter(([k]) => !(sec === "island" && (PARTS_KEYS.has(k) || k === "waves"))).filter(([k]) => !q || (sec + "." + k).toLowerCase().includes(q) || (SETTING_LABELS[sec + "." + k] || k).toLowerCase().includes(q)).map(([k, v]) =>
        el("div", { class: "setrow" + (q ? " hl" : ""), title: sec + "." + k }, el("span", { class: "k" + (SETTING_LABELS[sec + "." + k] ? "" : " mono") }, t(SETTING_LABELS[sec + "." + k] || k)), field(sec + "." + k, v)));
      if (!rows.length) return el("span");
      return el("section", { class: "box" }, el("h3", {}, t(SECTION_NAMES[sec] || sec), el("span", { class: "mut small" }, "  " + sec)), rows,
        el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: (e) => confirmClick(e.currentTarget, async () => { await core.try("settings.reset", { key: sec }); renderSettings(); }) }, t("Сбросить раздел"))));
    }));
  };
  filter.addEventListener("input", draw);
  draw();
  main.replaceChildren(el("div", { class: "page" },
    grEyebrow(TABS[5]), el("h1", {}, t("Настройки")),
    langBox(),
    themeBox(cfg),
    el("div", { class: "row2" }, filter,
      el("button", { class: "btn", onclick: async () => { const r = await core.try("core.selftest"); showSelftest(r); } }, t("Самопроверка")),
    inTauri ? el("button", { class: "btn ghost", onclick: () => invoke("restart_core") }, t("Перезапустить ядро")) : null),
    voiceBox(),
    islandPartsBox(cfg),
    waveSettingsBox(cfg),
    weatherCityBox(),
    body));
}

// «Внешний вид»: пресет темы + акцент (гайдбук §9, THEMES §5). Имена пресетов — по Γλῶτте.
function themeBox(cfg) {
  const cur = normalizeTheme(cfg.theme);
  const box = el("section", { class: "box themebox" },
    el("h3", {}, el("span", { class: "gr" }, "Χρώμα ·"), t(" Внешний вид")),
    el("div", { class: "mut small" }, t("Тема применяется во всех окнах сразу. «Системная» следует за настройками Windows.")));
  const presets = [["system", "Системная", "auto"], ["dark", "Тёмная", "moon"], ["light", "Светлая", "sun"]];
  const seg = el("div", { class: "theme-seg" }, presets.map(([id, label, ic]) =>
    el("button", { class: "seg-btn" + (cur.preset === id ? " on" : ""), "data-preset": id, title: label,
      onclick: async (e) => {
        seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.currentTarget));
        applyTheme({ preset: id, accent: cur.accent });
        const r = await core.try("settings.set", { key: "theme", value: { preset: id, accent: cur.accent } });
        if (r && r.error) toast(r.error, "error");
      } }, iconNode(ic, 14), t(label))));
  const swatches = el("div", { class: "swatches" },
    [["", "Авто"], ...Object.entries(ACCENTS)].map(([id, a]) => {
      const hex = id ? (document.documentElement.dataset.theme === "light" ? a.light : a.dark) : "";
      const sw = el("button", {
        class: "swatch" + ((cur.accent || "") === id ? " on" : "") + (id ? "" : " auto"),
        title: id ? t(a.label) : t("Акцент темы"), "aria-label": id ? t(a.label) : t("Акцент темы"), style: id ? { background: hex } : {},
        onclick: async () => {
          swatches.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("on", b === sw));
          applyTheme({ preset: cur.preset, accent: id });
          const r = await core.try("settings.set", { key: "theme", value: { preset: cur.preset, accent: id } });
          if (r && r.error) toast(r.error, "error");
        } }, id ? "" : "A");
      return sw;
    }));
  box.append(el("div", { class: "setrow" }, el("span", { class: "k" }, t("Тема")), seg),
    el("div", { class: "setrow" }, el("span", { class: "k" }, t("Акцент")), swatches),
    customThemesUI(cur));
  return box;
}

// Кастомные темы (THEMES §3): выбор, JSON-редактор, сохранение с контраст-гейтом в ядре.
function customThemesUI(cur) {
  const select = el("select", { class: "field small" }, el("option", { value: "" }, t("— нет —")));
  const editor = el("textarea", { class: "field mono", rows: 6, spellcheck: "false" });
  // редактор сразу с шаблоном: пикеры-буквы пишут в него с первого клика, без пустого JSON
  editor.value = JSON.stringify({ name: "Моя тема", base: "dark", vars: {} }, null, 2);
  const status = el("span", { class: "mut small" }, "");
  let themesMap = {};
  const fillEditor = (slug) => {
    const th = slug && themesMap[slug];
    editor.value = JSON.stringify(th ? { name: th.name, base: th.base, vars: th.vars }
      : { name: t("Моя тема"), base: "dark", vars: { "--surface-0": "", "--accent": "" } }, null, 2);
  };
  const refresh = async () => {
    try {
      const list = await core.call("themes.list", {}, 8000);
      themesMap = Object.fromEntries((Array.isArray(list) ? list : []).map((t) => [t.slug, t]));
      select.replaceChildren(el("option", { value: "" }, "— нет —"),
        ...Object.entries(themesMap).map(([slug, t]) => el("option", { value: slug }, t.name)));
      select.value = cur.preset.startsWith("custom:") ? cur.preset.slice(7) : "";
      if (select.value && themesMap[select.value]) fillEditor(select.value);
    } catch (e) { status.textContent = t("Список тем недоступен"); }
  };
  select.addEventListener("change", async () => {
    fillEditor(select.value);
    await core.try("settings.set", { key: "theme", value: { preset: select.value ? "custom:" + select.value : "dark", accent: cur.accent } });
    const c = await core.try("settings.get", {}, 8000);
    if (c && !c.error) applyTheme(c.theme);
  });
  const saveBtn = el("button", { class: "btn", onclick: async () => {
    let payload;
    try { payload = JSON.parse(editor.value); } catch (e) { toast(t("Неверный JSON в теме"), "error"); return; }
    // полное слияние поверх базы: ядро проверяет контраст на итоговых значениях
    const base = payload.base === "light" ? "light" : "dark";
    const merged = Object.assign({}, BASE_VARS[base], payload.vars || {});
    const r = await core.try("themes.save", { name: payload.name || "Тема", base, vars: merged }, 8000);
    if (r && r.error) { toast(r.error, "error"); return; }
    toast(t("Тема сохранена"), "ok");
    await core.try("settings.set", { key: "theme", value: { preset: "custom:" + r.slug, accent: cur.accent } });
    await refresh();
    const c = await core.try("settings.get", {}, 8000);
    if (c && !c.error) applyTheme(c.theme);
  } }, "Сохранить");
  const delBtn = el("button", { class: "btn ghost", onclick: async () => {
    if (!select.value) return;
    await core.try("themes.delete", { name: select.value }, 8000);
    await core.try("settings.set", { key: "theme", value: { preset: "dark", accent: cur.accent } });
    applyTheme({ preset: "dark", accent: cur.accent });
    await refresh();
  } }, "Удалить");
  refresh();
  // простой режим: интерактивные греческие буквы — каждая окрашена в текущее значение
  // переменной; клик по букве открывает выбор цвета, после выбора буква перекрашивается
  const PICKS = [
    ["--surface-0", "Α", "Фон"], ["--surface-1", "Β", "Карточки"], ["--surface-2", "Γ", "Панели"], ["--surface-3", "Δ", "Тосты"],
    ["--fg", "Ε", "Текст"], ["--mut", "Ζ", "Вторичный"], ["--accent", "Η", "Акцент"], ["--accent-contrast", "Θ", "На акценте"],
    ["--ok", "Ι", "Успех"], ["--warn", "Κ", "Внимание"], ["--err", "Λ", "Ошибка"],
  ];
  const setVar = (key, value) => {
    let payload;
    try { payload = JSON.parse(editor.value); } catch (e) { return; }
    payload.vars = payload.vars || {};
    if (value) payload.vars[key] = value; else delete payload.vars[key];
    editor.value = JSON.stringify(payload, null, 2);
  };
  const pickers = el("div", { class: "var-pickers" }, PICKS.map(([key, letter, label]) => {
    const input = el("input", { class: "var-picker", type: "color", title: t(label), "aria-label": t(label), tabindex: "-1" });
    const letterEl = el("span", { class: "var-letter", title: t(label) + " · " + key }, letter);
    const paint = () => {
      let vars = {};
      try { vars = (JSON.parse(editor.value) || {}).vars || {}; } catch (e) { /* неверный JSON — красим из темы */ }
      input.value = vars[key] || getComputedStyle(document.documentElement).getPropertyValue(key).trim() || "#000000";
      letterEl.style.color = vars[key] || input.value;
    };
    input.addEventListener("input", () => { setVar(key, input.value); paint(); });
    editor.addEventListener("input", paint);
    paint();
    return el("label", { class: "var-pick" }, letterEl, input, el("span", {}, t(label)));
  }));
  return el("div", { class: "custom-themes" },
    el("div", { class: "setrow" }, el("span", { class: "k" }, t("Своя тема")), select, saveBtn, delBtn),
    el("div", { class: "setrow" }, pickers),
    el("div", { class: "setrow" }, editor),
    status,
    el("div", { class: "mut small" }, t("Пикеры пишут в JSON ниже; можно править и вручную (name, base, vars — только семантические переменные). Ядро не сохранит тему с контрастом текста ниже 4.5:1.")));
}

function waveSettingsBox(cfg) {
  const values = Object.assign({
    full_width_enabled: true, full_width_duration_s: 3, full_width_amplitude: 1,
    full_width_density: 32, full_width_offset_percent: -3, full_width_front_width: 1.8, full_width_pulse_hz: 1,
    full_width_glow: 10, color: "", music_enabled: true, music_gain: 1, music_glow: 10,
    music_peak_count: 32, music_peak_width: 0.3, music_attack: 0.55, music_release: 0.18,
    ambient_amplitude: 1, ambient_speed: 1,
    cursor_enabled: true, cursor_strength: 11, away_sag_enabled: true,
    away_sag_strength: 7, rain_enabled: true, rain_interval_s: 4, rain_drop_size: 1.9, success_drop_size: 3.4,
    lip_breath_enabled: true, lip_breath_period_s: 2.4, respect_reduced_motion: false,
  }, cfg.island && cfg.island.waves || {});
  const box = el("section", { class: "box wave-settings" });
  const save = async (key, value) => {
    values[key] = value;
    if (!cfg.island) cfg.island = {};
    cfg.island.waves = values;
    const r = await core.try("settings.set", { key: "island.waves." + key, value });
    if (r && r.error) toast(r.error, "error");
  };
  const row = (label, control, hint = "") => el("div", { class: "setrow wave-setting" },
    el("span", { class: "k" }, label), control,
    hint ? el("span", { class: "mut small wave-hint" }, hint) : null);
  const toggle = (label, key, hint) => row(label,
    el("label", { class: "switch" }, el("input", { type: "checkbox", checked: !!values[key], onchange: (e) => save(key, e.target.checked) }), el("i")), hint);
  const range = (label, key, min, max, step, unit = "", hint = "") => {
    const input = el("input", { class: "wave-range", type: "range", min: String(min), max: String(max), step: String(step), value: String(values[key]) });
    const digits = (String(step).split(".")[1] || "").length;
    const output = el("span", { class: "mono small wave-value" }, Number(values[key]).toFixed(digits) + unit);
    input.addEventListener("input", () => { output.textContent = Number(input.value).toFixed(digits) + unit; });
    input.addEventListener("change", () => save(key, Number(input.value)));
    return row(label, el("div", { class: "wave-range-wrap" }, input, output), hint);
  };
  const subhead = (text) => el("h4", { class: "wave-subhead" }, text);
  const colorInput = el("input", { class: "wave-color", type: "color", value: /^#[0-9a-f]{6}$/i.test(values.color) ? values.color : "#7c8cff", title: t("Переопределить цвет волн") });
  colorInput.addEventListener("change", () => save("color", colorInput.value));
  const resetColor = el("button", { class: "btn ghost wave-color-reset", onclick: () => { colorInput.value = "#7c8cff"; save("color", ""); } }, t("Цвет обложки"));
  box.append(
    el("h3", {}, t("Эффекты волн")),
    el("div", { class: "mut small wave-intro" }, t("Эти параметры настраивают полноэкранную волну, музыкальные пики и вспомогательные жидкие эффекты. Изменения применяются сразу.")),
    subhead(t("Полноширинная волна при сворачивании")),
    toggle(t("Включить волну при сворачивании"), "full_width_enabled", t("Запускать только если в момент сворачивания играет музыка.")),
    range(t("Время распространения"), "full_width_duration_s", 0.8, 8, 0.2, " с"),
    range(t("Высота пиков"), "full_width_amplitude", 0.2, 1.8, 0.1, "×"),
    range(t("Количество пиков"), "full_width_density", 8, 64, 1),
    range(t("Смещение волнового фронта"), "full_width_offset_percent", -50, 50, 1, " %", t("От центра островка: − влево, + вправо; значение — процент ширины экрана.")),
    range(t("Ширина фронта"), "full_width_front_width", 0.5, 3.5, 0.1, "×"),
    range(t("Пульсация пиков"), "full_width_pulse_hz", 0.2, 3, 0.1, " Гц"),
    range(t("Свечение"), "full_width_glow", 0, 24, 1),
    row(t("Цвет волн"), el("div", { class: "wave-color-wrap" }, colorInput, resetColor), t("Пустое значение использует цвет обложки.")),
    subhead(t("Музыкальная волна островка")),
    toggle(t("Включить волну музыкального эквалайзера"), "music_enabled"),
    range(t("Чувствительность музыкальных пиков"), "music_gain", 0, 2, 0.1, "×"),
    range(t("Количество музыкальных пиков"), "music_peak_count", 4, 32, 1),
    range(t("Ширина музыкальных пиков"), "music_peak_width", 0.1, 0.8, 0.05, "×"),
    range(t("Свечение музыкальной волны"), "music_glow", 0, 24, 1),
    range(t("Скорость реакции на усиление"), "music_attack", 0.05, 1, 0.05, "×"),
    range(t("Скорость затухания музыкальных пиков"), "music_release", 0.05, 1, 0.05, "×"),
    range(t("Амплитуда фоновой волны"), "ambient_amplitude", 0, 3, 0.1, "×"),
    range(t("Скорость фонового колебания"), "ambient_speed", 0.2, 3, 0.1, "×"),
    subhead(t("Жидкие реакции")),
    toggle(t("Реакция на курсор"), "cursor_enabled"),
    range(t("Высота реакции на курсор"), "cursor_strength", 0, 24, 1),
    toggle(t("Провисание, когда пользователя нет"), "away_sag_enabled"),
    range(t("Сила провисания"), "away_sag_strength", 0, 18, 1),
    toggle(t("Дождевые капли"), "rain_enabled"),
    range(t("Средний интервал дождевых капель"), "rain_interval_s", 1, 12, 0.5, " с"),
    range(t("Размер дождевых капель"), "rain_drop_size", 0.8, 4, 0.1, " px"),
    range(t("Размер капли успешного действия"), "success_drop_size", 1, 8, 0.1, " px"),
    subhead(t("Анимация полоски")),
    toggle(t("Пульсация полоски при музыке"), "lip_breath_enabled"),
    range(t("Период пульсации полоски"), "lip_breath_period_s", 0.5, 6, 0.1, " с"),
    toggle(t("Отключать эффекты при системном «уменьшении движения»"), "respect_reduced_motion"),
    el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: async () => { await core.try("settings.reset", { key: "island.waves" }); renderSettings(); } }, t("Сбросить параметры волн"))));
  return box;
}

// «Островок: что показывать» — те же переключатели, что и в режиме ✎ в самом островке.
const PARTS_KEYS = new Set(["parts", "hidden_tabs", "hidden_tools"]);

function islandPartsBox(cfg) {
  const box = el("section", { class: "box parts-box" });
  const save = async (values) => {
    applyValues(cfg, values);
    draw();
    const r = await core.try("settings.update", { values });
    if (r && r.error) toast(r.error, "error");
  };
  const sw = (shown, values) => el("label", { class: "switch" }, el("input", { type: "checkbox", checked: shown, onchange: () => save(values) }), el("i"));
  const row = (label, shown, values) => el("div", { class: "setrow" }, el("span", { class: "k" }, label), sw(shown, values));
  const hidden = (kind, id, show) => {
    const cur = new Set((cfg.island || {})["hidden_" + kind] || []);
    if (show) cur.delete(id); else cur.add(id);
    return { ["island.hidden_" + kind]: [...cur] };
  };
  const draw = () => {
    const I = cfg.island || {};
    const groups = new Map([[t("Вкладки панели"), TAB_LIST.map(([id, name]) => row(t(name), listOn(I, "tabs", id), hidden("tabs", id, !listOn(I, "tabs", id))))]]);
    for (const [key, label, group] of PARTS) {
      const g = t(group);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(row(t(label), partOn(I, key), partValues(key, !partOn(I, key))));
    }
    groups.set(t("Кнопки «Инструменты»"), TOOL_LIST.map(([id, name]) => row(t(name), listOn(I, "tools", id), hidden("tools", id, !listOn(I, "tools", id)))));
    box.replaceChildren(
      el("h3", {}, t("Островок: что показывать")),
      el("div", { class: "mut small" }, t("Выключите лишнее — островок изменится сразу. То же можно сделать прямо в островке: раскройте его и нажмите ✎.")),
      el("div", { class: "parts-grid" }, [...groups].map(([g, rows]) => el("div", { class: "parts-col" }, el("h4", {}, g), rows))),
      el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: () => save(RESET_VALUES) }, t("Показать всё"))));
  };
  draw();
  return box;
}

const GENDER = { "м": "мужской", "ж": "женский" };

function voiceBox() {
  const box = el("section", { class: "box voicebox" }, el("h3", {}, el("span", { class: "gr", title: "Музы — богини голоса и песни" }, "Μοῦσα ·"), t(" Голос Эйдоса")), el("div", { class: "mut small" }, t("Загружаю голоса…")));
  refreshVoices(box);
  return box;
}

async function refreshVoices(box, note) {
  const info = await core.try("voice.voices", {}, UI_TIMEOUT);
  if (!box.isConnected && A.tab !== "settings") return;
  drawVoices(box, info, note);
}

function parseLexicon(text) {
  const entries = {};
  text.split("\n").forEach((line) => {
    const separator = line.indexOf("=");
    if (separator <= 0) return;
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (key && value) entries[key] = value;
  });
  return entries;
}

function voiceGroup(info, group, current, pick, install) {
  const active = info.engine === group.engine;
  const chosen = current[String(group.setting || "").split(".")[1]] || "";
  const voices = group.voices && group.voices.length
    ? group.voices
    : [{ id: "", name: t("Голос по умолчанию"), gender: "", note: "" }];
  const cards = voices.map((voice) => {
    const selected = active && (voice.id === chosen || (!chosen && voices.length === 1));
    const card = el("button", { class: "vcard" + (selected ? " on" : ""), disabled: !group.available, title: voice.id || "" },
      el("b", {}, voice.name),
      el("span", { class: "mut small" }, [GENDER[voice.gender] && t(GENDER[voice.gender]), voice.note].filter(Boolean).join(" · ") || " "));
    card.addEventListener("click", () => pick(group.engine, voice.id, card));
    return card;
  });
  return el("div", { class: "vgroup" + (active ? " active" : "") + (group.available ? "" : " off") },
    el("div", { class: "vhead" }, el("b", {}, group.title), el("span", { class: "mut small" }, group.note), active ? el("span", { class: "tag ok" }, t("сейчас")) : null),
    group.available ? null : el("div", { class: "vreason small" }, el("span", {}, group.reason || "недоступен"),
      group.installable ? el("button", { class: "btn ghost", onclick: () => install(group) }, t("Установить")) : null),
    el("div", { class: "vcards" }, cards));
}

function drawVoices(box, info, note) {
  const title = el("h3", {}, el("span", { class: "gr", title: "Музы — богини голоса и песни" }, "Μοῦσα ·"), t(" Голос Эйдоса"));
  if (!info || info.error || !Array.isArray(info.engines)) {
    box.replaceChildren(title, el("div", { class: "warn" }, t("Голоса недоступны: ") + ((info && info.error) || t("нет связи с ядром"))));
    return;
  }
  const cur = info.current || {};
  const status = el("div", { class: "vstatus small" + (note && note.error ? " warn" : " mut") }, (note && (note.error || note.text)) || t("Нажмите на голос — он включится и скажет пример фразы."));
  let busy = false;
  const setStatus = (text, bad) => { status.textContent = text; status.className = "vstatus small " + (bad ? "warn" : "mut"); };
  const runVoiceAction = async (message, action, resultNote) => {
    if (busy) return;
    busy = true;
    setStatus(message);
    try {
      const result = await action();
      refreshVoices(box, resultNote(result));
    } finally {
      busy = false;
    }
  };
  const pick = async (engine, voice, card) => {
    if (busy) return;
    box.querySelectorAll(".vcard").forEach((n) => n.classList.toggle("busy", n === card));
    return runVoiceAction(
      engine === "off" ? t("Выключаю голос…") : t("Включаю голос… В первый раз модель скачивается — это может занять минуту."),
      () => core.try("voice.apply", { engine, voice, sample: true }, 600000),
      (result) => {
        const ok = result && !result.error && result.ok !== false;
        return ok ? { text: engine === "off" ? t("Голосовые ответы выключены.") : t("Готово, голос сохранён.") }
          : { error: t("Не получилось: ") + ((result && result.error) || t("нет ответа от ядра")) };
      });
  };
  const install = (g) => runVoiceAction(
    t("Устанавливаю ") + g.title + t("… Это занимает 1–3 минуты."),
    () => core.try("voice.install", { engine: g.engine }, 960000),
    (result) => result && result.ok
      ? { text: g.title + t(" установлен — выберите голос.") }
      : { error: t("Установка не удалась: ") + ((result && (result.error + (result.log ? " — " + result.log.slice(-300) : ""))) || t("нет ответа")) });
  const rate = Number(info.rate || 1);
  const speed = el("input", { type: "range", min: "0.5", max: "2", step: "0.05", value: String(rate), class: "vspeed" });
  const speedLabel = el("span", { class: "mono small" }, rate.toFixed(2) + "×");
  speed.addEventListener("input", () => { speedLabel.textContent = Number(speed.value).toFixed(2) + "×"; });
  speed.addEventListener("change", async () => {
    await core.try("voice.apply", { rate: Number(speed.value), sample: true, text: t("Так я говорю с новой скоростью.") }, 600000);
  });
  const groups = info.engines.map((group) => voiceGroup(info, group, cur, pick, install));
  const lexText = el("textarea", { class: "vlex", rows: 5, spellcheck: false, placeholder: t("ГПУ = видеокарта\nKubernetes = кубернетес") });
  lexText.value = Object.entries(info.lexicon || {}).map(([k, v]) => k + " = " + v).join("\n");
  const probe = el("input", { class: "vprobe", value: t("Давление 760 мм рт. ст., ветер 5 м/с, NVIDIA RTX 4050, т.е. всё ок.") });
  const heard = el("div", { class: "mut small vheard" });
  const lexBox = el("div", { class: "vlexbox" },
    el("h4", {}, t("Словарь произношения")),
    el("div", { class: "mut small" }, t("Каждая строка: слово = как читать. Сокращения, единицы и английские слова уже читаются сами — сюда только то, что звучит неверно.")),
    lexText,
    el("div", { class: "row2" },
      el("button", { class: "btn", onclick: async () => {
        const d = parseLexicon(lexText.value);
        const r = await core.try("settings.set", { key: "voice.lexicon", value: d });
        heard.textContent = r === undefined || r === null ? t("Не удалось сохранить словарь") : t("Словарь сохранён: ") + Object.keys(d).length + t(" зап.");
      } }, t("Сохранить словарь"))),
    el("div", { class: "row2 vprobe-row" }, probe,
      el("button", { class: "btn ghost", onclick: async () => {
        const r = await core.try("voice.preview", { text: probe.value });
        heard.textContent = r && r.text ? t("Прочитает: ") + r.text : "—";
      } }, t("Как прочитает")),
      el("button", { class: "btn ghost", onclick: () => core.try("voice.say", { text: probe.value }) }, t("Прослушать"))),
    heard);
  box.replaceChildren(title,
    el("div", { class: "row2 vcontrols" }, el("span", {}, t("Скорость речи")), speed, speedLabel,
      el("button", { class: "btn ghost", onclick: () => core.try("voice.say", { text: t("Проверка голоса. Раз, два, три.") }) }, t("Прослушать")),
      el("button", { class: "btn ghost", onclick: () => core.try("voice.stop") }, t("Стоп")),
      info.engine === "off" ? el("span", { class: "tag warn" }, t("голос выключен")) : el("button", { class: "btn ghost", onclick: () => pick("off", "", null) }, t("Выключить голос"))),
    status, ...groups, lexBox,
    el("div", { class: "mut small" }, t("Можно и голосом: «Эйдос, говори мужским голосом», «включи голос Байя», «говори помедленнее», «произноси ГПУ как видеокарта».")));
}

function showSelftest(r) {
  const checks = (r && (r.checks || r.results)) || [];
  const box = el("section", { class: "box selftest" }, el("h3", {}, t("Самопроверка")),
    r && r.error ? el("div", { class: "warn" }, r.error) : null,
    (Array.isArray(checks) ? checks : Object.entries(checks).map(([name, v]) => Object.assign({ name }, typeof v === "object" ? v : { ok: !!v }))).map((c) =>
      el("div", { class: "setrow" }, el("span", { class: "tag " + (c.ok ? "ok" : c.warn ? "warn" : "err") }, c.ok ? "OK" : c.warn ? t("ВНИМ.") : t("ОШИБКА")), el("span", { class: "k" }, c.name), el("span", { class: "mut small" }, c.detail || c.message || ""))));
  const page = main.querySelector(".page");
  const old = page.querySelector(".selftest");
  if (old) old.replaceWith(box); else page.children[1].after(box);
}

// ------------------------------------------------------------------ scenario question

function showAsk(d) {
  const old = document.querySelector(".scn-ask");
  if (old) old.remove();
  if (!d || d.state === "done") return;
  const input = el("input", { class: "field", placeholder: t("Ваш ответ… (можно и голосом)") });
  let box = null;
  const send = async (text, skip = false) => {
    const r = await core.try("scenarios.answer", { text, skip });
    if (r && r.ok === false) toast(t("Сценарий уже не ждёт ответа"), "error");
    if (box) box.remove();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) send(input.value.trim()); });
  box = el("div", { class: "scn-ask" },
    el("div", { class: "k small" }, t("💬 Сценарий спрашивает → ") + "{" + (d.var || "answer") + "}"),
    el("div", { class: "q" }, d.question || ""),
    d.choices && d.choices.length ? el("div", { class: "row2 wrap" }, d.choices.map((c) => el("button", { class: "btn", onclick: () => send(c) }, c))) : null,
    el("div", { class: "row2" }, input,
      el("button", { class: "btn primary", onclick: () => { if (input.value.trim()) send(input.value.trim()); } }, t("Ответить")),
      el("button", { class: "btn ghost", title: t("Взять значение по умолчанию"), onclick: () => send("", true) }, t("Пропустить")),
      el("button", { class: "btn danger", onclick: () => { core.try("scenarios.stop", {}); box.remove(); } }, "Стоп")));
  document.body.append(box);
  setTimeout(() => input.focus(), 60);
}

// ------------------------------------------------------------------ wiring

// Фон Γλῶτта: бегущие греческие слова мифов и философов в цвете темы
// (design/bg-preview.html — превью). Дорожки не пересекаются, размеры 0.5×–3×.
const BG_WORDS = ["εἶδος", "Νοῦς", "Λόγος", "Ἀλήθεια", "Μνήμη", "Κόσμος", "Ἀρετή", "Φύσις", "Τύχη", "Μοῖρα",
  "Ὕπνος", "Ἔρως", "Θέμις", "Μοῦσα", "Χάος", "Σοφία", "Γνῶσις", "Δύναμις", "Μίμησις",
  "Θάλασσα", "Πῦρ", "Ἀκάδημα", "Λύκειον", "Στοά", "Προμηθεύς", "Ὠκεανός", "Ἀθήνη", "Ἀπόλλων"];

function buildWordField() {
  const field = el("div", { class: "word-field", "aria-hidden": "true" });
  const laneCount = Math.max(5, Math.min(9, Math.round((window.innerHeight || 900) / 125)));
  const laneH = 100 / laneCount;
  const pool = BG_WORDS.slice().sort(() => Math.random() - 0.5);
  for (let r = 0; r < laneCount; r++) {
    const row = el("div", { class: "row" });
    row.style.top = (r * laneH) + "%";
    row.style.height = laneH + "%";
    let half = "", i = r, width = 0;
    const target = Math.max(1400, (window.innerWidth || 1200) * 1.25);
    while (width < target && i < 500) {
      const w = pool[i % BG_WORDS.length];
      i += 3;
      const sc = 0.5 + Math.random() * 2.5;
      const op = (0.06 + Math.random() * 0.06).toFixed(2);
      const gap = Math.round(70 + Math.random() * 220);
      half += `<span style="font-size:${Math.round(18 * sc)}px;opacity:${op};margin-right:${gap}px">${w}</span>`;
      width += 18 * sc * w.length * 0.62 + gap;
    }
    row.innerHTML = half + half; // дубль половины → бесшовный translateX(-50%)
    row.style.animationDuration = Math.round(80 + Math.random() * 70) + "s";
    row.style.animationDelay = (-Math.random() * 120).toFixed(0) + "s";
    field.append(row);
  }
  return field;
}

// --- выпадающие списки в стиле интерфейса: нативный select скрыт внутри обёртки
// и продолжает держать значение/события change, попап рисуем сами (стеклянная карточка,
// клавиатура, клик вне закрывает). Улучшаются select.field и select.modelpick.
let cselOpen = null; // { sel, wrap, pop, opts, hl }

function cselSyncLabel(sel) {
  const wrap = sel.closest(".csel-wrap");
  if (wrap) wrap.querySelector(".csel-label").textContent = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : "";
}

function cselClose() {
  if (!cselOpen) return;
  if (cselOpen.cleanup) cselOpen.cleanup();
  cselOpen.pop.remove();
  cselOpen.wrap.classList.remove("open");
  cselOpen = null;
}

function cselToggle(sel, wrap) {
  if (cselOpen && cselOpen.sel === sel) { cselClose(); return; }
  cselClose();
  const opts = [...sel.options];
  let hl = Math.max(0, opts.findIndex((o) => o.selected));
  const pop = el("div", { class: "csel-pop", role: "listbox" });
  const paint = () => pop.replaceChildren(...opts.map((o, idx) => {
    const b = el("div", { class: "csel-opt" + (idx === hl ? " hl" : "") + (o.selected ? " on" : "") + (o.disabled ? " dis" : ""),
      role: "option", "aria-selected": o.selected ? "true" : "false" }, o.textContent,
      o.selected ? el("span", { class: "csel-ck" }, "✓") : null);
    if (!o.disabled) b.addEventListener("click", () => {
      if (sel.value !== o.value) { sel.value = o.value; sel.dispatchEvent(new Event("change", { bubbles: true })); }
      cselClose();
      wrap.focus();
    });
    return b;
  }));
  paint();
  document.body.append(pop);
  cselOpen = { sel, wrap, pop, opts, hl };
  wrap.classList.add("open");
  const place = () => {
    const r = wrap.getBoundingClientRect();
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    // попап центрируется на триггере (узкие пилюли не «уезжают» вбок) и прижимается к краям экрана
    let left = r.left + r.width / 2 - pw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - pw - 8));
    let top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
    pop.style.minWidth = Math.max(160, r.width) + "px";
  };
  place();
  const cleanup = () => { window.removeEventListener("scroll", cselClose, true); window.removeEventListener("resize", cselClose); };
  cselOpen.cleanup = cleanup;
}

function enhanceSelect(sel) {
  const wrap = document.createElement("span");
  wrap.className = ("csel-wrap " + sel.className).trim();
  wrap.tabIndex = 0;
  sel.className = "csel-host";
  sel.before(wrap);
  wrap.append(sel);
  const label = el("span", { class: "csel-label" });
  wrap.append(label, el("i", { class: "csel-chev" }));
  const d = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  Object.defineProperty(sel, "value", {
    get() { return d.get.call(sel); },
    set(v) { d.set.call(sel, v); cselSyncLabel(sel); },
  });
  cselSyncLabel(sel);
  // пересланный label-активацией клик по скрытому select гасим: preventDefault не даёт
  // открыться нативному попапу, а триггер игнорирует это событие — иначе toggle дважды
  sel.addEventListener("click", (e) => e.preventDefault());
  // триггер: сама обёртка, а для пикера модели — пилюля вокруг него; stopPropagation
  // не даёт глобальному «клик вне — закрыть» закрыть только что открытый попап
  const trigger = sel.closest(".modelpick-wrap") || wrap;
  trigger.addEventListener("click", (e) => {
    if (e.target.classList && e.target.classList.contains("csel-host")) return;
    e.stopPropagation();
    cselToggle(sel, wrap);
  });
  wrap.addEventListener("keydown", (e) => {
    if (cselOpen && cselOpen.sel === sel) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const dir = e.key === "ArrowDown" ? 1 : -1;
        do { cselOpen.hl = (cselOpen.hl + dir + cselOpen.opts.length) % cselOpen.opts.length; }
        while (cselOpen.opts[cselOpen.hl].disabled);
        const pop = cselOpen.pop;
        [...pop.children].forEach((c, idx) => c.classList.toggle("hl", idx === cselOpen.hl));
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const o = cselOpen.opts[cselOpen.hl];
        if (!o.disabled) { if (sel.value !== o.value) { sel.value = o.value; sel.dispatchEvent(new Event("change", { bubbles: true })); } }
        cselClose();
        return;
      }
    }
    if (e.key === "Enter" || e.key === " " || (e.key === "ArrowDown" && !cselOpen)) { e.preventDefault(); cselToggle(sel, wrap); }
    if (e.key === "Escape") cselClose();
  });
}

function enhanceSelects(root = document) {
  for (const sel of root.querySelectorAll("select.field, select.modelpick")) {
    if (sel.dataset.csel) continue;
    sel.dataset.csel = "1";
    enhanceSelect(sel);
  }
}

export function mountApp() {
  document.body.classList.add("view-app");
  nav = el("nav", { class: "nav" }, TABS.map((tab) => el("button", { "data-tab": tab.id, title: t(tab.myth), onclick: () => setTab(tab.id) }, el("span", { class: "ic" }, iconNode(tab.icon, 18)), el("span", {}, t(tab.name)))));
  main = el("main", { class: "main" });
  document.body.append(el("div", { class: "frame" }, buildWordField(), titlebar(), el("div", { class: "body" }, nav, main)));
  // списки в стиле интерфейса: следим за перерисовками страниц и улучшаем новые select-ы
  enhanceSelects();
  new MutationObserver(() => enhanceSelects()).observe(document.body, { childList: true, subtree: true });
  document.addEventListener("click", (e) => { if (cselOpen && !e.target.closest(".csel-wrap, .csel-pop")) cselClose(); });
  listen("main-tab", (tab) => setTab(tab || "chat"));
  listen("emergency-stop", stopAgent);
  core.on("core.connected", () => { A.online = true; updateStatus(); setTab(A.tab); });
  core.on("core.lost", () => { A.online = false; updateStatus(); });
  core.on("core.waiting", (info) => {
    A.online = false; updateStatus();
    if (!main.firstChild) main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, t("Жду ядро… ") + ((info && info.error) || ""))));
  });
  core.on("agent.state", (d) => { A.state = d.state || "idle"; A.label = d.label || ""; updateStatus(); if (A.tab === "chat") renderChatSoft(); });
  core.on("agent.delta", (d) => { A.live += d.text || ""; if (A.tab === "chat") renderChatSoft(); });
  core.on("agent.delta_reset", () => { A.live = ""; });
  core.on("agent.step", (d) => {
    const i = A.steps.findIndex((s) => s.id === d.id);
    if (i >= 0) {
      const prev = A.steps[i];
      if (d.status !== "running" && d.ms == null && prev.t0) d.ms = Date.now() - prev.t0;
      A.steps[i] = Object.assign(prev, d);
    } else A.steps.push(Object.assign({ t0: Date.now() }, d));
    if (A.tab === "chat") renderChatSoft();
  });
  core.on("agent.message", (d) => {
    if (d.chat_id && A.chat && d.chat_id !== A.chat) return;
    if (d.chat_id) A.chat = d.chat_id;
    if (d.role === "user" && A.messages.length && A.messages[A.messages.length - 1].role === "user" && A.messages[A.messages.length - 1].text === d.text) return;
    A.messages.push(d);
    if (d.role === "assistant") A.live = "";
    if (A.tab === "chat") renderChatSoft();
  });
  core.on("agent.chats", () => { if (A.tab === "chat") renderChat(); });
  core.on("router.status", () => { if (A.tab === "models") renderModels(); else if (A.tab === "chat" && A.modelPickerFill && document.querySelector(".modelpick") && document.activeElement?.className !== "modelpick") A.modelPickerFill(); });
  core.on("scenario.run", (d) => { if (A.tab === "scenarios") scenarioEvent("run", d); });
  core.on("scenario.step", (d) => { if (A.tab === "scenarios") scenarioEvent("step", d); });
  core.on("scenario.vars", (d) => { if (A.tab === "scenarios") scenarioEvent("vars", d); });
  core.on("scenario.ask", showAsk);
  core.on("core.connected", async () => { const r = await core.try("scenarios.question", {}); if (r && r.question) showAsk(r.question); });
  core.on("island.card", (c) => { if (document.hasFocus() && c.kind === "error") toast(c.title + (c.text ? ": " + c.text : ""), "error"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && A.state !== "idle") stopAgent(); });
  window.addEventListener("idos.lang", () => { updateStatus(); setTab(A.tab); });
  setInterval(() => { for (const n of document.querySelectorAll(".ms.live")) n.textContent = fmtMs(Date.now() - Number(n.dataset.t0)); }, 200);
  updateStatus();
  setTab("chat");
}
