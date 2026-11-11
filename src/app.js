// Главное окно Эйдоса: диалоги, память, ключи и модели, инструменты/скиллы/MCP, настройки.
import { core } from "./rpc.js";
import { invoke, listen, el, toast, win, inTauri, micIcon, setMicState } from "./shell.js";
import { Mic } from "./voice.js";
import { renderScenarios, scenarioEvent } from "./scenarios.js";
import { PARTS, TAB_LIST, TOOL_LIST, partOn, listOn, partValues, RESET_VALUES, applyValues } from "./parts.js";

const TABS = [
  ["chat", "Диалог", "💬"],
  ["memory", "Память", "🧠"],
  ["models", "Ключи и модели", "🔑"],
  ["tools", "Инструменты", "🛠"],
  ["scenarios", "Сценарии", "⛓"],
  ["settings", "Настройки", "⚙"],
];

const A = { tab: "chat", chats: [], chat: null, messages: [], live: "", steps: [], state: "idle", label: "", online: false, cfg: {} };
let main, nav, statusEl;
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
  if (changed) main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, "Загрузка…")));
  Promise.resolve().then(() => pages[tab]()).catch((e) => {
    console.error("tab", tab, e);
    if (A.tok === tok && A.tab === tab)
      main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, "Не удалось открыть вкладку: " + (e && e.message ? e.message : e)),
        el("button", { class: "btn", onclick: () => setTab(tab) }, "Повторить")));
  });
}

function titlebar() {
  const bar = el("div", { class: "titlebar", "data-tauri-drag-region": true },
    el("span", { class: "brand", "data-tauri-drag-region": true }, el("span", { class: "logo" }), "Эйдос"),
    statusEl = el("span", { class: "state", "data-tauri-drag-region": true }, "подключение…"),
    el("span", { class: "grow", "data-tauri-drag-region": true }),
    el("button", { class: "wbtn danger-h", title: "Экстренная остановка (Ctrl+Alt+X)", onclick: stopAgent }, "■"),
    el("button", { class: "wbtn", title: "Свернуть", onclick: () => win && win.minimize() }, "—"),
    el("button", { class: "wbtn", title: "Развернуть", onclick: () => win && win.toggleMaximize() }, "▢"),
    el("button", { class: "wbtn close", title: "Скрыть в трей", onclick: () => win && win.hide() }, "✕"));
  return bar;
}

function stopAgent() { core.try("agent.stop"); core.try("voice.stop"); mic.cancel(); }

function updateStatus() {
  if (!statusEl) return;
  statusEl.className = "state " + (A.online ? A.state : "offline");
  statusEl.textContent = !A.online ? "ядро не подключено" : A.state === "idle" ? "готов" : (A.label || A.state);
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
  if (ms < 1) return "<1 мс";
  if (ms < 1000) return Math.round(ms) + " мс";
  if (ms < 10000) return (ms / 1000).toFixed(1) + " с";
  if (ms < 60000) return Math.round(ms / 1000) + " с";
  return Math.floor(ms / 60000) + " мин " + Math.round((ms % 60000) / 1000) + " с";
}

// Live countdown under «limits exhausted» replies.
export function retryText(at) {
  const left = Number(at) - Date.now();
  if (!isFinite(left)) return "";
  return left > 0 ? "⏳ ближайший ключ освободится через " + fmtMs(Math.ceil(left / 1000) * 1000)
    : "✓ ключ уже должен был освободиться — можно повторить запрос";
}
if (typeof document !== "undefined") {
  setInterval(() => document.querySelectorAll(".retry-live").forEach((n) => { n.textContent = retryText(n.dataset.at); }), 1000);
}

function stepView(s) {
  return el("div", { class: "stepline " + (s.status || "") },
    el("span", { class: "st" }, s.status === "running" ? "◌" : s.status === "ok" ? "✓" : s.status === "denied" ? "✕" : s.status === "error" ? "!" : "·"),
    el("span", { class: "tool" }, s.tool + (s.action ? "." + s.action : "")),
    el("span", { class: "t" }, s.label || ""),
    s.status === "running" ? el("span", { class: "ms live", "data-t0": String(s.t0 || Date.now()) }, fmtMs(Date.now() - (s.t0 || Date.now())))
      : s.ms != null ? el("span", { class: "ms" }, fmtMs(s.ms)) : null,
    s.image ? el("img", { src: s.image }) : null,
    s.preview ? el("details", {}, el("summary", {}, "результат"), el("pre", {}, s.preview)) : null);
}

async function renderChat() {
  await loadChats();
  if (A.tab !== "chat") return;
  const list = el("div", { class: "chatlist" },
    el("button", { class: "btn primary wide", onclick: async () => { const c = await core.try("agent.new_chat"); if (c && c.chat_id) { A.chat = c.chat_id; A.messages = []; } renderChat(); } }, "+ Новый диалог"),
    A.chats.map((c) => el("div", { class: "chatitem" + (c.id === A.chat ? " on" : ""), onclick: async () => { A.chat = c.id; await core.try("agent.select_chat", { chat_id: c.id }); renderChat(); } },
      el("span", { class: "t" }, c.title || "Без названия"),
      el("button", { class: "icon", title: "Переименовать", onclick: async (e) => { e.stopPropagation(); const t = prompt("Название диалога", c.title || ""); if (t !== null) { await core.try("agent.rename_chat", { chat_id: c.id, title: t }); renderChat(); } } }, "✎"),
      el("button", { class: "icon", title: "Удалить", onclick: (e) => { e.stopPropagation(); confirmClick(e.currentTarget, async () => { await core.try("agent.delete_chat", { chat_id: c.id }); if (A.chat === c.id) A.chat = null; renderChat(); }); } }, "✕"))));
  const log = el("div", { class: "log" },
    A.messages.length ? A.messages.filter((m) => m.role === "user" || m.role === "assistant").map(msgView)
      : el("div", { class: "hello" }, el("div", { class: "logo big" }), el("h2", {}, "Привет! Я Эйдос."),
        el("p", {}, "Попросите открыть приложение, найти что-то в интернете, написать письмо или настроить Windows. Можно голосом — PageUp."),
        el("div", { class: "chips" }, ["Какая погода завтра?", "Открой блокнот и напиши список покупок", "Включи тёмную тему", "Переключись на платную модель"].map((t) =>
          el("button", { class: "chip", onclick: () => send(t) }, t)))),
    el("div", { class: "steps-live" }, A.steps.map(stepView)),
    A.live ? el("div", { class: "msg assistant live" }, el("div", { class: "bubble" }, A.live)) : null);
  const box = el("textarea", { rows: 1, placeholder: "Напишите задачу…  Enter — отправить, Shift+Enter — новая строка" });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (box.value.trim()) { send(box.value.trim()); box.value = ""; } } });
  box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = Math.min(200, box.scrollHeight) + "px"; });
  const busy = A.state !== "idle";
  const composer = el("div", { class: "composer" }, modelPicker(), box,
    el("button", { class: "icon mic", title: "Голосом (PageUp)", onclick: () => (mic.active ? mic.stop() : mic.start("ask")) }, micIcon()),
    busy ? el("button", { class: "btn danger", onclick: stopAgent }, "■ Стоп")
      : el("button", { class: "btn primary", onclick: () => { if (box.value.trim()) { send(box.value.trim()); box.value = ""; } } }, "Отправить"));
  main.replaceChildren(el("div", { class: "chatpage" }, list, el("div", { class: "chatmain" }, log, composer)));
  log.scrollTop = log.scrollHeight;
  box.focus();
}

// Выбор модели прямо в чате: Auto — активная модель с автопереключением на резервные,
// любая другая строка — принудительно только эта модель (ключи при этом всё равно чередуются).
function modelPicker() {
  const sel = el("select", { class: "modelpick", title: "Модель для чата" }, el("option", { value: "" }, "Auto"));
  const wrap = el("label", { class: "modelpick-wrap" }, el("span", { class: "dot" }), sel);
  const paint = () => {
    wrap.classList.toggle("forced", !!sel.value);
    const o = sel.selectedOptions[0];
    wrap.title = sel.value ? "Принудительно: " + sel.value + " — без переключения на другие модели"
      : "Auto: основная модель, при лимитах и ошибках — резервные" + (A.modelActive ? " (сейчас " + A.modelActive + ")" : "");
    sel.style.width = Math.max(56, Math.min(230, ((o && o.textContent) || "Auto").length * 7.4 + 30)) + "px";
  };
  const fill = async () => {
    const r = await core.try("router.choices", {}, UI_TIMEOUT);
    if (!r || !Array.isArray(r.choices)) return paint();
    A.modelActive = r.active || "";
    sel.replaceChildren(el("option", { value: "" }, "Auto"),
      ...r.choices.map((c) => el("option", { value: c.spec, disabled: !c.ready },
        c.model.split("/").pop() + " · " + c.provider + (c.ready ? "" : " (нет ключа)"))));
    sel.value = r.force || "";
    paint();
  };
  sel.addEventListener("change", async () => {
    const want = sel.value;
    const r = await core.try("router.force", { spec: want }, UI_TIMEOUT);
    if (!r || r.ok === false) { toast((r && r.error) || "не удалось выбрать модель", "error"); await fill(); return; }
    toast(want ? "Модель закреплена: " + want.split(":").pop() : "Auto — автовыбор модели", "ok");
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
      const btn = busy ? el("button", { class: "btn danger", onclick: stopAgent }, "■ Стоп")
        : el("button", { class: "btn primary", onclick: () => { const b = composer.querySelector("textarea"); if (b.value.trim()) { send(b.value.trim()); b.value = ""; } } }, "Отправить");
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
  const search = el("input", { class: "field", placeholder: "Поиск по памяти…", value: query });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") renderMemory(search.value.trim()); });
  const add = el("input", { class: "field", placeholder: "Запомнить факт (например: «Я живу в Казани»)…" });
  add.addEventListener("keydown", async (e) => { if (e.key === "Enter" && add.value.trim()) { await core.try("memory.save", { text: add.value.trim() }); renderMemory(); } });
  main.replaceChildren(el("div", { class: "page" },
    el("h1", {}, "Память"), el("p", { class: "mut" }, "Локальная долговременная память агента. Эйдос сам сохраняет важное и вспоминает по смыслу."),
    el("div", { class: "row2" }, search, add),
    el("div", { class: "cards-list" }, items.length ? items.map((m) => el("div", { class: "item" },
      el("div", { class: "t" }, m.text || m.content || ""),
      el("div", { class: "meta" }, [m.kind, m.tags && [].concat(m.tags).join(", "), m.created && new Date((m.created > 1e12 ? m.created : m.created * 1000)).toLocaleString("ru-RU")].filter(Boolean).join(" · ")),
      el("button", { class: "icon", title: "Удалить", onclick: async () => { await core.try("memory.delete", { mid: m.id }); renderMemory(query); } }, "✕")))
      : el("div", { class: "empty" }, "Пока ничего не запомнено"))));
}

// ------------------------------------------------------------------ keys & models

function bucketLine(model, b) {
  const lim = b.limit_requests, rem = b.remaining_requests;
  const pct = lim ? Math.round((rem / lim) * 100) : null;
  return el("div", { class: "bucket" }, el("span", { class: "m" }, model),
    pct !== null ? el("span", { class: "bar" }, el("i", { style: { width: pct + "%", background: pct < 15 ? "var(--err)" : pct < 40 ? "var(--warn)" : "var(--ok)" } })) : el("span", { class: "bar empty" }),
    el("span", { class: "n" }, (rem ?? "?") + " / " + (lim ?? "?") + " запр. · сегодня " + (b.used_today || 0)),
    b.cooldown_in ? el("span", { class: "warn" }, "пауза " + b.cooldown_in + "с") : null);
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
    btn.disabled = true; out.textContent = "Проверяю — несколько коротких запросов…";
    const r = await core.try("router.selftest", { spec: pick.value }, 120000);
    btn.disabled = false;
    if (!r || r.error) { out.textContent = (r && r.error) || "нет ответа"; return; }
    out.replaceChildren(el("div", {}, (r.ok ? "✅ " : "⚠️ ") + (r.message || r.spec)),
      ...(r.checks || []).map((c) => el("div", {}, (c.ok ? "✓ " : "✗ ") + c.name + " · " + (c.ms / 1000).toFixed(1) + " с" + (c.detail ? " — " + c.detail : ""))));
  } }, "Проверить модель");
  return el("section", { class: "box" }, el("h3", {}, "Проверка модели"),
    el("p", { class: "mut small" }, "Ответ, вызов инструмента, JSON, для моделей зрения — чтение картинки. Тратит 2–3 запроса."),
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
  const secret = el("textarea", { class: "field mono", rows: 3, placeholder: "Ключи API — по одному на строку (можно вставить сразу много)" });
  const label = el("input", { class: "field", placeholder: "Метка (необязательно)" });
  const active = el("input", { class: "field mono", value: status.active || "", placeholder: "groq:openai/gpt-oss-120b" });
  const accounts = status.accounts || [];
  const provNote = el("div", { class: "mut small" });
  const showNote = () => { const p = providers.find((x) => (x.id || x.name) === provSel.value); provNote.textContent = (p && p.note) || ""; };
  provSel.addEventListener("change", showNote);
  showNote();
  main.replaceChildren(el("div", { class: "page" },
    el("h1", {}, "Ключи и модели"),
    el("p", { class: "mut" }, "Роутер следит за лимитами каждого ключа и заранее переключается на следующий, когда запросы подходят к концу. Модель можно сменить и голосом: «Эйдос, переключись на …»."),
    el("section", { class: "box" }, el("h3", {}, "Активная модель"),
      el("div", { class: "row2" }, active,
        el("button", { class: "btn primary", onclick: async () => { const r = await core.try("router.set_active", { spec: active.value.trim() }); r && r.error ? toast(r.error, "error") : toast(r && r.vision_only ? "Эта модель только для изображений — назначена моделью зрения" : "Модель выбрана", "ok"); renderModels(); } }, "Применить"),
        null),
      status.chain && status.chain.length ? el("div", { class: "mut small" }, "Цепочка резерва: " + status.chain.map((c) => (typeof c === "string" ? c : c.spec || c.model)).join(" → ")) : null,
      status.vision_chain && status.vision_chain.length ? el("div", { class: "mut small" }, "Цепочка зрения: " + [status.vision || ""].concat(status.vision_chain).filter(Boolean).map((c) => (typeof c === "string" ? c : c.spec || c.model)).join(" → ")) : null,
      status.current ? el("div", { class: "mut small" }, "Последний запрос: " + status.current.spec + " · " + (status.current.account || "")) : null),
    selftestBox(status),
    el("section", { class: "box" }, el("h3", {}, "Добавить ключи"),
      el("div", { class: "row2" }, provSel, label),
      provNote,
      secret,
      el("div", { class: "row2 end" }, el("button", { class: "btn primary", onclick: async () => {
        const lines = secret.value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
        if (!lines.length) return;
        const r = lines.length > 1 ? await core.try("keys.add_many", { provider: provSel.value, text: lines.join("\n") })
          : await core.try("keys.add", { provider: provSel.value, secret: lines[0], label: label.value });
        if (r && r.error) toast(r.error, "error"); else { toast("Добавлено ключей: " + lines.length, "ok"); renderModels(); }
      } }, "Добавить"))),
    el("section", { class: "box" }, el("h3", {}, "Ключи (" + accounts.length + ")"),
      accounts.length ? accounts.map((a) => el("div", { class: "account" + (a.current ? " current" : "") + (a.disabled ? " off" : "") },
        el("div", { class: "row2" },
          el("b", {}, a.provider), el("span", { class: "mono" }, a.masked || ""), el("span", { class: "mut" }, a.label || ""),
          a.current ? el("span", { class: "tag ok" }, "активен") : null,
          a.disabled ? el("span", { class: "tag warn" }, a.disabled_reason || "отключён") : null,
          el("span", { class: "grow" }),
          el("button", { class: "btn ghost", title: "Проверить ключ коротким запросом", onclick: async () => { const r = await core.try("router.test", { account_id: a.id }); r && (r.error || r.ok === false) ? toast(r.error || r.message || "Ключ не отвечает", "error") : toast((r && r.message) || "Ключ работает", "ok"); renderModels(); } }, "Проверить"),
          a.source === "env" ? el("span", { class: "tag", title: "Ключ из .env или переменных окружения" }, ".env") : null,
          a.source !== "local" ? [
            el("label", { class: "switch" }, el("input", { type: "checkbox", checked: a.enabled !== false, onchange: async (e) => { await core.try("keys.enable", { key_id: a.id, enabled: e.target.checked }); renderModels(); } }), el("i")),
            el("button", { class: "icon del", title: "Удалить ключ", onclick: (e) => confirmClick(e.currentTarget, async () => {
              const r = await core.try("keys.remove", { key_id: a.id });
              if (r && r.ok) toast(r.env ? "Ключ скрыт. Он записан в .env — уберите его оттуда, чтобы удалить совсем" : "Ключ удалён", "ok");
              else toast((r && r.error) || "Не удалось удалить ключ", "error");
              renderModels();
            }) }, "✕")] : null),
        Object.entries(a.buckets || {}).map(([m, b]) => bucketLine(m, b))))
        : el("div", { class: "empty" }, "Ключей пока нет. Добавьте хотя бы один ключ Groq — console.groq.com/keys")),
    Array.isArray(keys) && keys.length > accounts.length ? el("div", { class: "mut small" }, "В хранилище ключей: " + keys.length) : null));
}

// ------------------------------------------------------------------ tools

async function renderTools() {
  const [tools, skills, mcp] = await Promise.all([core.try("tools.list", {}, UI_TIMEOUT), core.try("skills.list", {}, UI_TIMEOUT), core.try("mcp.servers", {}, UI_TIMEOUT)]);
  if (A.tab !== "tools") return;
  if (tools && tools.error) throw new Error(tools.error);
  main.replaceChildren(el("div", { class: "page" },
    el("h1", {}, "Инструменты"),
    el("p", { class: "mut" }, "Всё, чем агент может пользоваться самостоятельно. Скиллы — инструкции в data/skills, MCP-серверы — в настройках."),
    el("div", { class: "toolgrid" }, (Array.isArray(tools) ? tools : []).map((t) => el("div", { class: "tool" + (t.available ? "" : " off") },
      el("div", { class: "row2" }, el("b", {}, t.name), el("span", { class: "grow" }), el("span", { class: "tag " + (t.available ? "ok" : "warn") }, t.available ? "доступен" : "недоступен")),
      el("div", { class: "mut small" }, t.description || ""),
      el("div", { class: "acts" }, Object.entries(t.actions || {}).map(([n, a]) => el("span", { class: "act " + (a.kind || ""), title: a.label || "" }, n)))))),
    el("h2", {}, "Скиллы"),
    el("div", { class: "cards-list" }, (Array.isArray(skills) && skills.length ? skills : []).map((s) => el("div", { class: "item" },
      el("div", { class: "t" }, el("b", {}, s.name || s), s.description ? " — " + s.description : ""),
      el("button", { class: "icon", title: "Удалить", onclick: (e) => confirmClick(e.currentTarget, async () => { await core.try("skills.delete", { name: s.name || s }); renderTools(); }) }, "✕"))),
      Array.isArray(skills) && skills.length ? null : el("div", { class: "empty" }, "Скиллов пока нет — агент создаёт их сам, когда осваивает новое приложение")),
    el("h2", {}, "MCP-серверы"),
    el("div", { class: "cards-list" }, (Array.isArray(mcp) ? mcp : []).map((m) => el("div", { class: "item" },
      el("div", { class: "t" }, el("b", {}, m.name), " ", el("span", { class: "mono mut" }, m.command ? [m.command].concat(m.args || []).join(" ") : m.url || "")),
      el("span", { class: "tag " + (m.running ? "ok" : "") }, m.running ? "запущен" : "остановлен"))),
      Array.isArray(mcp) && mcp.length ? null : el("div", { class: "empty" }, "Нет подключённых серверов (настройка mcp.servers)"))));
}

// ------------------------------------------------------------------ settings

const SECTION_NAMES = {
  island: "Остров", hotkeys: "Горячие клавиши", voice: "Голос", audio: "Звук и микрофон", agent: "Агент",
  models: "Модели", web: "Интернет", capture: "Скриншоты и запись", integrations: "Интеграции", weather: "Погода",
  clipboard: "Буфер обмена", memory: "Память", mcp: "MCP", obs: "OBS", media: "Музыка", security: "Безопасность",
};

function field(path, value) {
  const save = async (v) => {
    const r = await core.try("settings.set", { key: path, value: v });
    if (r && r.error) toast(r.error, "error"); else toast("Сохранено: " + path, "ok");
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
      inp.placeholder = "нажмите сочетание";
      inp.addEventListener("keydown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "Escape") { inp.blur(); return; }
        if (e.key === "Backspace" || e.key === "Delete") { inp.value = ""; save(""); inp.blur(); return; }
        if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
        if (e.key === "*" || e.code === "NumpadMultiply") { inp.blur(); toast("Клавиша * не поддерживается как глобальное сочетание. Выберите другую клавишу.", "error"); return; }
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
  ta.addEventListener("change", () => { try { save(JSON.parse(ta.value)); } catch (e) { toast("Неверный JSON", "error"); } });
  return ta;
}

async function renderSettings() {
  const cfg = await core.try("settings.get", {}, UI_TIMEOUT);
  if (A.tab !== "settings") return;
  if (!cfg || cfg.error) { main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, "Настройки недоступны: " + ((cfg && cfg.error) || "нет связи")))); return; }
  A.cfg = cfg;
  const filter = el("input", { class: "field", placeholder: "Найти настройку…" });
  const body = el("div", {});
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    body.replaceChildren(...Object.entries(cfg).filter(([, v]) => v && typeof v === "object" && !Array.isArray(v)).map(([sec, vals]) => {
      const rows = Object.entries(vals).filter(([k]) => !(sec === "island" && (PARTS_KEYS.has(k) || k === "waves"))).filter(([k]) => !q || (sec + "." + k).toLowerCase().includes(q)).map(([k, v]) =>
        el("div", { class: "setrow" }, el("span", { class: "k mono" }, k), field(sec + "." + k, v)));
      if (!rows.length) return el("span");
      return el("section", { class: "box" }, el("h3", {}, SECTION_NAMES[sec] || sec, el("span", { class: "mut small" }, "  " + sec)), rows,
        el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: (e) => confirmClick(e.currentTarget, async () => { await core.try("settings.reset", { key: sec }); renderSettings(); }) }, "Сбросить раздел")));
    }));
  };
  filter.addEventListener("input", draw);
  draw();
  main.replaceChildren(el("div", { class: "page" },
    el("h1", {}, "Настройки"),
    el("div", { class: "row2" }, filter,
      el("button", { class: "btn", onclick: async () => { const r = await core.try("core.selftest"); showSelftest(r); } }, "Самопроверка"),
    inTauri ? el("button", { class: "btn ghost", onclick: () => invoke("restart_core") }, "Перезапустить ядро") : null),
    voiceBox(),
    islandPartsBox(cfg),
    waveSettingsBox(cfg),
    body));
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
  const colorInput = el("input", { class: "wave-color", type: "color", value: /^#[0-9a-f]{6}$/i.test(values.color) ? values.color : "#7c8cff", title: "Переопределить цвет волн" });
  colorInput.addEventListener("change", () => save("color", colorInput.value));
  const resetColor = el("button", { class: "btn ghost wave-color-reset", onclick: () => { colorInput.value = "#7c8cff"; save("color", ""); } }, "Цвет обложки");
  box.append(
    el("h3", {}, "Эффекты волн"),
    el("div", { class: "mut small wave-intro" }, "Эти параметры настраивают полноэкранную волну, музыкальные пики и вспомогательные жидкие эффекты. Изменения применяются сразу."),
    subhead("Полноширинная волна при сворачивании"),
    toggle("Включить волну при сворачивании", "full_width_enabled", "Запускать только если в момент сворачивания играет музыка."),
    range("Время распространения", "full_width_duration_s", 0.8, 8, 0.2, " с"),
    range("Высота пиков", "full_width_amplitude", 0.2, 1.8, 0.1, "×"),
    range("Количество пиков", "full_width_density", 8, 64, 1),
    range("Смещение волнового фронта", "full_width_offset_percent", -50, 50, 1, " %", "От центра островка: − влево, + вправо; значение — процент ширины экрана."),
    range("Ширина фронта", "full_width_front_width", 0.5, 3.5, 0.1, "×"),
    range("Пульсация пиков", "full_width_pulse_hz", 0.2, 3, 0.1, " Гц"),
    range("Свечение", "full_width_glow", 0, 24, 1),
    row("Цвет волн", el("div", { class: "wave-color-wrap" }, colorInput, resetColor), "Пустое значение использует цвет обложки."),
    subhead("Музыкальная волна островка"),
    toggle("Включить волну музыкального эквалайзера", "music_enabled"),
    range("Чувствительность музыкальных пиков", "music_gain", 0, 2, 0.1, "×"),
    range("Количество музыкальных пиков", "music_peak_count", 4, 32, 1),
    range("Ширина музыкальных пиков", "music_peak_width", 0.1, 0.8, 0.05, "×"),
    range("Свечение музыкальной волны", "music_glow", 0, 24, 1),
    range("Скорость реакции на усиление", "music_attack", 0.05, 1, 0.05, "×"),
    range("Скорость затухания музыкальных пиков", "music_release", 0.05, 1, 0.05, "×"),
    range("Амплитуда фоновой волны", "ambient_amplitude", 0, 3, 0.1, "×"),
    range("Скорость фонового колебания", "ambient_speed", 0.2, 3, 0.1, "×"),
    subhead("Жидкие реакции"),
    toggle("Реакция на курсор", "cursor_enabled"),
    range("Высота реакции на курсор", "cursor_strength", 0, 24, 1),
    toggle("Провисание, когда пользователя нет", "away_sag_enabled"),
    range("Сила провисания", "away_sag_strength", 0, 18, 1),
    toggle("Дождевые капли", "rain_enabled"),
    range("Средний интервал дождевых капель", "rain_interval_s", 1, 12, 0.5, " с"),
    range("Размер дождевых капель", "rain_drop_size", 0.8, 4, 0.1, " px"),
    range("Размер капли успешного действия", "success_drop_size", 1, 8, 0.1, " px"),
    subhead("Анимация полоски"),
    toggle("Пульсация полоски при музыке", "lip_breath_enabled"),
    range("Период пульсации полоски", "lip_breath_period_s", 0.5, 6, 0.1, " с"),
    toggle("Отключать эффекты при системном «уменьшении движения»", "respect_reduced_motion"),
    el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: async () => { await core.try("settings.reset", { key: "island.waves" }); renderSettings(); } }, "Сбросить параметры волн")));
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
    const groups = new Map([["Вкладки панели", TAB_LIST.map(([id, name]) => row(name, listOn(I, "tabs", id), hidden("tabs", id, !listOn(I, "tabs", id))))]]);
    for (const [key, label, group] of PARTS) {
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(row(label, partOn(I, key), partValues(key, !partOn(I, key))));
    }
    groups.set("Кнопки «Инструменты»", TOOL_LIST.map(([id, name]) => row(name, listOn(I, "tools", id), hidden("tools", id, !listOn(I, "tools", id)))));
    box.replaceChildren(
      el("h3", {}, "Островок: что показывать"),
      el("div", { class: "mut small" }, "Выключите лишнее — островок изменится сразу. То же можно сделать прямо в островке: раскройте его и нажмите ✎."),
      el("div", { class: "parts-grid" }, [...groups].map(([g, rows]) => el("div", { class: "parts-col" }, el("h4", {}, g), rows))),
      el("div", { class: "row2 end" }, el("button", { class: "btn ghost", onclick: () => save(RESET_VALUES) }, "Показать всё")));
  };
  draw();
  return box;
}

const GENDER = { "м": "мужской", "ж": "женский" };

function voiceBox() {
  const box = el("section", { class: "box voicebox" }, el("h3", {}, "Голос Эйдоса"), el("div", { class: "mut small" }, "Загружаю голоса…"));
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
    : [{ id: "", name: "Голос по умолчанию", gender: "", note: "" }];
  const cards = voices.map((voice) => {
    const selected = active && (voice.id === chosen || (!chosen && voices.length === 1));
    const card = el("button", { class: "vcard" + (selected ? " on" : ""), disabled: !group.available, title: voice.id || "" },
      el("b", {}, voice.name),
      el("span", { class: "mut small" }, [GENDER[voice.gender], voice.note].filter(Boolean).join(" · ") || " "));
    card.addEventListener("click", () => pick(group.engine, voice.id, card));
    return card;
  });
  return el("div", { class: "vgroup" + (active ? " active" : "") + (group.available ? "" : " off") },
    el("div", { class: "vhead" }, el("b", {}, group.title), el("span", { class: "mut small" }, group.note), active ? el("span", { class: "tag ok" }, "сейчас") : null),
    group.available ? null : el("div", { class: "vreason small" }, el("span", {}, group.reason || "недоступен"),
      group.installable ? el("button", { class: "btn ghost", onclick: () => install(group) }, "Установить") : null),
    el("div", { class: "vcards" }, cards));
}

function drawVoices(box, info, note) {
  const title = el("h3", {}, "Голос Эйдоса");
  if (!info || info.error || !Array.isArray(info.engines)) {
    box.replaceChildren(title, el("div", { class: "warn" }, "Голоса недоступны: " + ((info && info.error) || "нет связи с ядром")));
    return;
  }
  const cur = info.current || {};
  const status = el("div", { class: "vstatus small" + (note && note.error ? " warn" : " mut") }, (note && (note.error || note.text)) || "Нажмите на голос — он включится и скажет пример фразы.");
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
      engine === "off" ? "Выключаю голос…" : "Включаю голос… В первый раз модель скачивается — это может занять минуту.",
      () => core.try("voice.apply", { engine, voice, sample: true }, 600000),
      (result) => {
        const ok = result && !result.error && result.ok !== false;
        return ok ? { text: engine === "off" ? "Голосовые ответы выключены." : "Готово, голос сохранён." }
          : { error: "Не получилось: " + ((result && result.error) || "нет ответа от ядра") };
      });
  };
  const install = (g) => runVoiceAction(
    "Устанавливаю " + g.title + "… Это занимает 1–3 минуты.",
    () => core.try("voice.install", { engine: g.engine }, 960000),
    (result) => result && result.ok
      ? { text: g.title + " установлен — выберите голос." }
      : { error: "Установка не удалась: " + ((result && (result.error + (result.log ? " — " + result.log.slice(-300) : ""))) || "нет ответа") });
  const rate = Number(info.rate || 1);
  const speed = el("input", { type: "range", min: "0.5", max: "2", step: "0.05", value: String(rate), class: "vspeed" });
  const speedLabel = el("span", { class: "mono small" }, rate.toFixed(2) + "×");
  speed.addEventListener("input", () => { speedLabel.textContent = Number(speed.value).toFixed(2) + "×"; });
  speed.addEventListener("change", async () => {
    await core.try("voice.apply", { rate: Number(speed.value), sample: true, text: "Так я говорю с новой скоростью." }, 600000);
  });
  const groups = info.engines.map((group) => voiceGroup(info, group, cur, pick, install));
  const lexText = el("textarea", { class: "vlex", rows: 5, spellcheck: false, placeholder: "ГПУ = видеокарта\nKubernetes = кубернетес" });
  lexText.value = Object.entries(info.lexicon || {}).map(([k, v]) => k + " = " + v).join("\n");
  const probe = el("input", { class: "vprobe", value: "Давление 760 мм рт. ст., ветер 5 м/с, NVIDIA RTX 4050, т.е. всё ок." });
  const heard = el("div", { class: "mut small vheard" });
  const lexBox = el("div", { class: "vlexbox" },
    el("h4", {}, "Словарь произношения"),
    el("div", { class: "mut small" }, "Каждая строка: слово = как читать. Сокращения, единицы и английские слова уже читаются сами — сюда только то, что звучит неверно."),
    lexText,
    el("div", { class: "row2" },
      el("button", { class: "btn", onclick: async () => {
        const d = parseLexicon(lexText.value);
        const r = await core.try("settings.set", { key: "voice.lexicon", value: d });
        heard.textContent = r === undefined || r === null ? "Не удалось сохранить словарь" : "Словарь сохранён: " + Object.keys(d).length + " зап.";
      } }, "Сохранить словарь")),
    el("div", { class: "row2 vprobe-row" }, probe,
      el("button", { class: "btn ghost", onclick: async () => {
        const r = await core.try("voice.preview", { text: probe.value });
        heard.textContent = r && r.text ? "Прочитает: " + r.text : "—";
      } }, "Как прочитает"),
      el("button", { class: "btn ghost", onclick: () => core.try("voice.say", { text: probe.value }) }, "Прослушать")),
    heard);
  box.replaceChildren(title,
    el("div", { class: "row2 vcontrols" }, el("span", {}, "Скорость речи"), speed, speedLabel,
      el("button", { class: "btn ghost", onclick: () => core.try("voice.say", { text: "Проверка голоса. Раз, два, три." }) }, "Прослушать"),
      el("button", { class: "btn ghost", onclick: () => core.try("voice.stop") }, "Стоп"),
      info.engine === "off" ? el("span", { class: "tag warn" }, "голос выключен") : el("button", { class: "btn ghost", onclick: () => pick("off", "", null) }, "Выключить голос")),
    status, ...groups, lexBox,
    el("div", { class: "mut small" }, "Можно и голосом: «Эйдос, говори мужским голосом», «включи голос Байя», «говори помедленнее», «произноси ГПУ как видеокарта»."));
}

function showSelftest(r) {
  const checks = (r && (r.checks || r.results)) || [];
  const box = el("section", { class: "box selftest" }, el("h3", {}, "Самопроверка"),
    r && r.error ? el("div", { class: "warn" }, r.error) : null,
    (Array.isArray(checks) ? checks : Object.entries(checks).map(([name, v]) => Object.assign({ name }, typeof v === "object" ? v : { ok: !!v }))).map((c) =>
      el("div", { class: "setrow" }, el("span", { class: "tag " + (c.ok ? "ok" : c.warn ? "warn" : "err") }, c.ok ? "OK" : c.warn ? "ВНИМ." : "ОШИБКА"), el("span", { class: "k" }, c.name), el("span", { class: "mut small" }, c.detail || c.message || ""))));
  const page = main.querySelector(".page");
  const old = page.querySelector(".selftest");
  if (old) old.replaceWith(box); else page.children[1].after(box);
}

// ------------------------------------------------------------------ scenario question

function showAsk(d) {
  const old = document.querySelector(".scn-ask");
  if (old) old.remove();
  if (!d || d.state === "done") return;
  const input = el("input", { class: "field", placeholder: "Ваш ответ… (можно и голосом)" });
  let box = null;
  const send = async (text, skip = false) => {
    const r = await core.try("scenarios.answer", { text, skip });
    if (r && r.ok === false) toast("Сценарий уже не ждёт ответа", "error");
    if (box) box.remove();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) send(input.value.trim()); });
  box = el("div", { class: "scn-ask" },
    el("div", { class: "k small" }, "💬 Сценарий спрашивает → {" + (d.var || "answer") + "}"),
    el("div", { class: "q" }, d.question || ""),
    d.choices && d.choices.length ? el("div", { class: "row2 wrap" }, d.choices.map((c) => el("button", { class: "btn", onclick: () => send(c) }, c))) : null,
    el("div", { class: "row2" }, input,
      el("button", { class: "btn primary", onclick: () => { if (input.value.trim()) send(input.value.trim()); } }, "Ответить"),
      el("button", { class: "btn ghost", title: "Взять значение по умолчанию", onclick: () => send("", true) }, "Пропустить"),
      el("button", { class: "btn danger", onclick: () => { core.try("scenarios.stop", {}); box.remove(); } }, "Стоп")));
  document.body.append(box);
  setTimeout(() => input.focus(), 60);
}

// ------------------------------------------------------------------ wiring

export function mountApp() {
  document.body.classList.add("view-app");
  nav = el("nav", { class: "nav" }, TABS.map(([id, name, ic]) => el("button", { "data-tab": id, onclick: () => setTab(id) }, el("span", { class: "ic" }, ic), el("span", {}, name))));
  main = el("main", { class: "main" });
  document.body.append(el("div", { class: "frame" }, titlebar(), el("div", { class: "body" }, nav, main)));
  listen("main-tab", (tab) => setTab(tab || "chat"));
  listen("emergency-stop", stopAgent);
  core.on("core.connected", () => { A.online = true; updateStatus(); setTab(A.tab); });
  core.on("core.lost", () => { A.online = false; updateStatus(); });
  core.on("core.waiting", (info) => {
    A.online = false; updateStatus();
    if (!main.firstChild) main.replaceChildren(el("div", { class: "page" }, el("div", { class: "empty" }, "Жду ядро… " + ((info && info.error) || ""))));
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
  setInterval(() => { for (const n of document.querySelectorAll(".ms.live")) n.textContent = fmtMs(Date.now() - Number(n.dataset.t0)); }, 200);
  updateStatus();
  setTab("chat");
}
