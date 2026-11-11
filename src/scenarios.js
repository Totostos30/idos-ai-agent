// Конструктор сценариев: блок-схема действий и таблицы входных/runtime-переменных.
import { core } from "./rpc.js";
import { el, toast, win } from "./shell.js";

const ICONS = { start: "▶", launch: "🚀", focus: "🪟", click: "👆", type: "⌨", hotkey: "⌘", wait: "⏱", wait_for: "⏳",
  if_exists: "❓", condition: "⇄", repeat: "🔁", read: "🧪", media: "⏯", open: "📂", say: "🔊", ask: "💬",
  window: "🗗", scenario: "⧉", end: "■" };
const PALETTE = ["launch", "window", "click", "type", "read", "ask", "condition", "repeat", "hotkey", "wait",
  "wait_for", "if_exists", "focus", "media", "open", "say", "scenario", "end"];
const WINDOW_NAMES = { minimize: "Свернуть", maximize: "Развернуть", restore: "Восстановить", close: "Закрыть",
  kill: "Завершить принудительно" };
const ASK_MODES = { any: "Голосом и текстом", voice: "Только голосом", text: "Только текстом (без озвучки)" };
const varName = (v) => String(v || "").trim().replace(/[^\p{L}\p{N}_]+/gu, "_").replace(/^_+|_+$/g, "") || "answer";
const MEDIA_NAMES = { play_pause: "Пуск/пауза", next: "Следующий трек", previous: "Предыдущий трек", stop: "Стоп",
  volume_up: "Громче", volume_down: "Тише", mute: "Без звука" };
const NODE_W = 230;
const SVGNS = "http://www.w3.org/2000/svg";

const S = { list: [], cur: null, sel: null, meta: null, status: {}, runtimeValues: {}, sidebarCollapsed: false,
  scenarioQuery: "", nodeQuery: "", dirty: false, running: false, picking: false };
let page = null, plane = null, svg = null, inspector = null, active = () => false;
let scenarioKeyHandlerInstalled = false;

const clone = (o) => JSON.parse(JSON.stringify(o));
const uid = () => Math.random().toString(16).slice(2, 10);

function summary(n) {
  const p = n.params || {};
  const t = p.target;
  const tl = t && typeof t === "object" ? (t.label || ("«" + (t.name || t.id || "элемент") + "»")) : "элемент не выбран";
  switch (n.type) {
    case "launch": return p.app || "программа не указана";
    case "focus": return p.window || "окно не указано";
    case "click": {
      const targets = Array.isArray(p.targets) && p.targets.length ? p.targets : (t ? [t] : []);
      return targets.length > 1 ? `${targets.length} элементов` : (targets[0] ? (targets[0].label || `«${targets[0].name || targets[0].id || "элемент"}»`) : tl);
    }
    case "wait_for": case "if_exists": case "read": return tl;
    case "condition": return `{${varName(p.var)}} ${p.operator || "equals"} ${p.value || ""}`.trim();
    case "repeat": return `${p.count ?? 3} раз(а)`;
    case "type": return "«" + (p.text || "") + "»" + (p.enter ? " + Enter" : "");
    case "hotkey": return p.keys || "клавиши не указаны";
    case "wait": return (p.seconds ?? 1) + " с";
    case "media": return MEDIA_NAMES[p.action] || p.action || "";
    case "open": return p.path || "";
    case "say": return p.text || "";
    case "ask": return "{" + varName(p.var) + "} ← «" + (p.question || "вопрос не задан") + "»";
    case "window": return (WINDOW_NAMES[p.action] || p.action || "") + ": " + (p.app || "активное окно");
    case "scenario": return p.name || "";
    default: return "";
  }
}

const blockTitle = (type) => (S.meta && S.meta.blocks[type] ? S.meta.blocks[type].title : type);
const outsOf = (type) => (S.meta && S.meta.blocks[type] ? S.meta.blocks[type].outs : ["next"]);
const outLabel = (o) => (S.meta && S.meta.outs[o]) || o;
const nodeById = (id) => (S.cur ? S.cur.nodes.find((n) => n.id === id) : null);

function markDirty() {
  S.dirty = true;
  const b = page && page.querySelector(".scn-save");
  if (b) b.classList.add("primary");
}

function toggleScenarioSidebar() {
  S.sidebarCollapsed = !S.sidebarCollapsed;
  const wrap = page && page.querySelector(".scn-wrap");
  const button = page && page.querySelector(".scn-sidebar-toggle");
  if (wrap) wrap.classList.toggle("scn-sidebar-collapsed", S.sidebarCollapsed);
  if (button) {
    button.textContent = S.sidebarCollapsed ? "›" : "‹";
    button.title = S.sidebarCollapsed ? "Развернуть панель" : "Свернуть панель до иконок";
  }
}

// ------------------------------------------------------------------ public

export async function renderScenarios(main, isActive) {
  active = isActive;
  if (!S.meta) {
    const meta = await core.try("scenarios.blocks", {}, 10000);
    if (meta && meta.error) throw new Error(meta.error);
    S.meta = meta;
  }
  const list = await core.try("scenarios.list", {}, 10000);
  if (!active()) return;
  if (list && list.error) throw new Error(list.error);
  S.list = Array.isArray(list) ? list : [];
  if (S.cur && !S.dirty) {
    const fresh = await core.try("scenarios.get", { sid: S.cur.id }, 10000);
    if (fresh && !fresh.error) S.cur = fresh;
  }
  if (!S.cur && S.list.length) await openScenario(S.list[0].id, false);
  page = el("div", { class: "page scn-page" });
  main.replaceChildren(page);
  draw();
}

export function scenarioEvent(kind, d) {
  if (!d || !S.cur || d.id !== S.cur.id) return;
  if (kind === "run") {
    S.running = d.status === "running";
    if (d.status === "running") { S.status = {}; S.runtimeValues = {}; refreshRuntimeVars(); }
    else if (d.status === "ok") toast("Сценарий выполнен за " + ((d.ms || 0) / 1000).toFixed(1) + " с");
    else if (d.status === "error") toast("Сценарий: " + (d.text || "ошибка"), "error");
    const stop = page && page.querySelector(".scn-stop");
    if (stop) stop.disabled = !S.running;
  } else if (kind === "vars") {
    S.runtimeValues = d.values && typeof d.values === "object" ? d.values : {};
    refreshRuntimeVars();
  } else if (kind === "step") {
    S.status[d.node] = d;
    const node = plane && plane.querySelector(`.scn-node[data-id="${d.node}"]`);
    if (node) paintStatus(node, d);
  }
}

// ------------------------------------------------------------------ data

async function openScenario(id, redraw = true) {
  if (S.dirty && S.cur && !confirm("Есть несохранённые изменения. Открыть другой сценарий без сохранения?")) return;
  const r = await core.try("scenarios.get", { sid: id }, 10000);
  if (r && r.error) { toast(r.error, "error"); return; }
  S.cur = r; S.sel = null; S.status = {}; S.runtimeValues = {}; S.dirty = false;
  if (redraw) draw();
}

function newScenario() {
  if (S.dirty && S.cur && !confirm("Есть несохранённые изменения. Создать новый без сохранения?")) return;
  S.cur = { id: "", name: "Новый сценарий", description: "", triggers: [], variables: [], enabled: true,
    nodes: [{ id: "start", type: "start", x: 60, y: 30, params: {}, links: {} }] };
  S.sel = "start"; S.status = {}; S.runtimeValues = {}; S.dirty = true;
  draw();
}

async function save(quiet = false) {
  if (!S.cur) return null;
  const r = await core.try("scenarios.save", { data: S.cur }, 10000);
  if (r && r.error) { toast("Не сохранено: " + r.error, "error"); return null; }
  S.cur = r; S.dirty = false;
  const list = await core.try("scenarios.list", {}, 10000);
  if (Array.isArray(list)) S.list = list;
  if (!quiet) toast("Сценарий сохранён");
  draw();
  return r;
}

async function runScenario(startAt = "") {
  if (!S.cur) return;
  const saved = await save(true);
  if (!saved) return;
  const variables = Object.fromEntries((saved.variables || []).map((v) =>
    [varName(v.name), String(v.value ?? v.default ?? "").trim()]));
  const missing = (saved.variables || []).filter((v) => v.required !== false && !variables[varName(v.name)]);
  if (missing.length) {
    const names = missing.map((v) => "{" + varName(v.name) + "}").join(", ");
    toast(`Не заполнены переменные: ${names}. Заполните таблицу переменных и нажмите «Запустить» ещё раз.`, "error");
    return;
  }
  S.status = {};
  draw();
  const r = await core.try("scenarios.run", { sid: saved.id, variables, start_at: startAt }, 3600000);
  if (r && r.error) toast(r.error, "error");
}

async function removeScenario() {
  if (!S.cur || !S.cur.id) { S.cur = null; S.dirty = false; draw(); return; }
  if (!confirm(`Удалить сценарий «${S.cur.name}»?`)) return;
  await core.try("scenarios.delete", { sid: S.cur.id });
  S.cur = null; S.dirty = false; S.sel = null;
  const list = await core.try("scenarios.list", {}, 10000);
  S.list = Array.isArray(list) ? list : [];
  if (S.list.length) await openScenario(S.list[0].id, false);
  draw();
}

function addBlock(type, dropPoint = null) {
  if (!S.cur) return;
  const spec = S.meta.blocks[type];
  const from = nodeById(S.sel) || (dropPoint ? null : S.cur.nodes[S.cur.nodes.length - 1]);
  const x = dropPoint ? dropPoint.x - NODE_W / 2 : (from ? from.x : 60);
  const y = dropPoint ? dropPoint.y - 45 : (from ? from.y + 120 : 40);
  const node = { id: uid(), type, params: clone(spec.params), links: {}, x, y };
  if (!dropPoint) while (S.cur.nodes.some((n) => Math.abs(n.x - node.x) < 40 && Math.abs(n.y - node.y) < 60)) node.y += 110;
  if (from && outsOf(from.type).length) {
    const out = outsOf(from.type).find((o) => o !== "fail" && !from.links[o]);
    if (out) from.links[out] = node.id;
  }
  S.cur.nodes.push(node);
  S.sel = node.id;
  markDirty();
  draw();
  if (dropPoint) {
    const view = plane && plane.querySelector(`.scn-node[data-id="${node.id}"]`);
    if (view) {
      const planeRect = plane.getBoundingClientRect();
      const nodeRect = view.getBoundingClientRect();
      const scaleX = planeRect.width ? plane.offsetWidth / planeRect.width : 1;
      const scaleY = planeRect.height ? plane.offsetHeight / planeRect.height : 1;
      node.x += dropPoint.x - (nodeRect.left + nodeRect.width / 2 - planeRect.left) * scaleX;
      node.y += dropPoint.y - (nodeRect.top + nodeRect.height / 2 - planeRect.top) * scaleY;
      draw();
    }
  }
  if (page && page.isConnected) page.focus({ preventScroll: true });
}

function startPaletteDrag(type, row, e) {
  if (e.button !== 0) return;
  e.preventDefault();
  const sx = e.clientX, sy = e.clientY;
  let moved = false;
  const ghost = el("div", { class: "scn-node scn-drag-ghost" },
    el("div", { class: "scn-node-head" }, el("span", { class: "ic" }, ICONS[type] || "•"), el("span", { class: "tt" }, blockTitle(type))));
  const moveGhost = (ev) => {
    ghost.style.left = `${ev.clientX - ghost.offsetWidth / 2}px`;
    ghost.style.top = `${ev.clientY - ghost.offsetHeight / 2}px`;
  };
  document.body.append(ghost);
  moveGhost(e);
  row.classList.add("dragging");
  try { row.setPointerCapture(e.pointerId); } catch (_) { /* fallback below */ }
  const move = (ev) => {
    moveGhost(ev);
    if (!moved && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 5) return;
    moved = true;
    ev.preventDefault();
  };
  const up = (ev) => {
    ghost.remove();
    row.classList.remove("dragging");
    try { row.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    row.removeEventListener("pointermove", move);
    row.removeEventListener("pointerup", up);
    row.removeEventListener("pointercancel", up);
    if (!moved || !plane) return;
    const hit = document.elementFromPoint(ev.clientX, ev.clientY);
    if (hit && hit.closest(".scn-canvas")) {
      const [x, y] = planePoint(ev);
      addBlock(type, { x, y });
    }
  };
  row.addEventListener("pointermove", move);
  row.addEventListener("pointerup", up);
  row.addEventListener("pointercancel", up);
}

function deleteBlock(id) {
  const node = nodeById(id);
  if (!node || node.type === "start") return;
  const next = node.links.next || node.links.yes || "";
  for (const n of S.cur.nodes) for (const [k, v] of Object.entries(n.links)) if (v === id) { if (next && next !== n.id) n.links[k] = next; else delete n.links[k]; }
  S.cur.nodes = S.cur.nodes.filter((n) => n.id !== id);
  S.sel = null;
  markDirty();
  draw();
}

function layout() {
  const all = S.cur.nodes;
  const byId = new Map(all.map((n) => [n.id, n]));
  const order = new Map(all.map((n, i) => [n.id, i]));
  const edges = [];
  for (const n of all) for (const [port, outIndex] of outsOf(n.type).map((name, i) => [name, i])) {
    const to = n.links && n.links[port];
    if (to && byId.has(to)) edges.push({ from: n.id, to, outIndex });
  }

  // Схлопываем циклы в компоненты: обратные связи останутся видны,
  // но не смогут назначить блоку слой раньше его последователей.
  let nextIndex = 0;
  const index = new Map(), low = new Map(), stack = [], onStack = new Set(), components = [];
  const visit = (id) => {
    index.set(id, nextIndex); low.set(id, nextIndex++);
    stack.push(id); onStack.add(id);
    for (const edge of edges) if (edge.from === id) {
      if (!index.has(edge.to)) { visit(edge.to); low.set(id, Math.min(low.get(id), low.get(edge.to))); }
      else if (onStack.has(edge.to)) low.set(id, Math.min(low.get(id), index.get(edge.to)));
    }
    if (low.get(id) === index.get(id)) {
      const component = [];
      let member;
      do { member = stack.pop(); onStack.delete(member); component.push(member); } while (member !== id);
      components.push(component);
    }
  };
  for (const n of all) if (!index.has(n.id)) visit(n.id);

  const componentOf = new Map();
  components.forEach((members, ci) => members.forEach((id) => componentOf.set(id, ci)));
  const outgoing = components.map(() => new Set());
  const incoming = components.map(() => new Set());
  for (const edge of edges) {
    const from = componentOf.get(edge.from), to = componentOf.get(edge.to);
    if (from !== to) { outgoing[from].add(to); incoming[to].add(from); }
  }

  const componentOrder = (ci) => Math.min(...components[ci].map((id) => order.get(id)));
  const ready = components.map((_, ci) => ci).filter((ci) => !incoming[ci].size).sort((a, b) => componentOrder(a) - componentOrder(b));
  const topo = [];
  while (ready.length) {
    const ci = ready.shift(); topo.push(ci);
    for (const to of outgoing[ci]) {
      incoming[to].delete(ci);
      if (!incoming[to].size) {
        ready.push(to);
        ready.sort((a, b) => componentOrder(a) - componentOrder(b));
      }
    }
  }
  // Rebuild incoming links; Kahn's pass above consumes the sets.
  for (const set of incoming) set.clear();
  for (const [from, targets] of outgoing.entries()) for (const to of targets) incoming[to].add(from);

  const start = all.find((n) => n.type === "start");
  const startComponent = start ? componentOf.get(start.id) : undefined;
  const reachable = new Set();
  if (startComponent !== undefined) {
    const queue = [startComponent]; reachable.add(startComponent);
    while (queue.length) for (const to of outgoing[queue.shift()]) if (!reachable.has(to)) { reachable.add(to); queue.push(to); }
  }
  const rank = new Map();
  if (startComponent !== undefined) rank.set(startComponent, 0);
  for (const ci of topo) if (reachable.has(ci) && ci !== startComponent) {
    rank.set(ci, Math.max(0, ...[...incoming[ci]].filter((p) => reachable.has(p)).map((p) => rank.get(p) + 1)));
  }
  const mainDepth = Math.max(0, ...[...rank.values()]);
  for (const ci of topo) if (!reachable.has(ci)) {
    const parents = [...incoming[ci]].filter((p) => !reachable.has(p));
    rank.set(ci, parents.length ? Math.max(...parents.map((p) => rank.get(p) + 1)) : mainDepth + 2);
  }

  const layers = new Map();
  for (const n of all) {
    const depth = rank.get(componentOf.get(n.id)) ?? 0;
    if (!layers.has(depth)) layers.set(depth, []);
    layers.get(depth).push(n);
  }
  const layerOrder = [...layers.keys()].sort((a, b) => a - b);
  const position = new Map();
  for (const nodes of layers.values()) nodes.forEach((n, i) => position.set(n.id, i));
  const reorder = (depth, neighborDepth, forward) => {
    const nodes = layers.get(depth);
    const score = (node) => {
      const related = edges.filter((e) => (forward ? e.to === node.id : e.from === node.id) &&
        rank.get(componentOf.get(forward ? e.from : e.to)) === neighborDepth);
      if (!related.length) return [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
      const ids = related.map((e) => forward ? e.from : e.to);
      return [ids.reduce((sum, id) => sum + position.get(id), 0) / ids.length,
        Math.min(...related.map((e) => e.outIndex))];
    };
    nodes.sort((a, b) => {
      const [av, ao] = score(a), [bv, bo] = score(b);
      return av - bv || ao - bo || order.get(a.id) - order.get(b.id);
    });
    nodes.forEach((n, i) => position.set(n.id, i));
  };
  // Несколько проходов по средним позициям родителей/потомков уменьшают пересечения.
  for (let pass = 0; pass < 3; pass++) {
    for (let i = 1; i < layerOrder.length; i++) reorder(layerOrder[i], layerOrder[i - 1], true);
    for (let i = layerOrder.length - 2; i >= 0; i--) reorder(layerOrder[i], layerOrder[i + 1], false);
  }
  const heights = new Map([...plane.querySelectorAll(".scn-node")].map((view) =>
    [view.dataset.id, view.getBoundingClientRect().height || 0]));
  let y = 35;
  for (const depth of layerOrder) {
    const nodes = layers.get(depth);
    nodes.forEach((n, lane) => { n.x = 60 + lane * 290; n.y = y; });
    const rowHeight = Math.max(110, ...nodes.map((n) => heights.get(n.id) || 0));
    y += rowHeight + 50;
  }
  markDirty();
  draw();
}

// ------------------------------------------------------------------ view

function scenarioVariablesEditor(scenario, collapsed = false) {
  if (!Array.isArray(scenario.variables)) scenario.variables = [];
  const rows = scenario.variables.map((v, index) => {
    if (typeof v === "string") v = scenario.variables[index] = { name: v, description: "", value: "" };
    const name = el("input", { class: "field", value: v.name || "", placeholder: "город" });
    const value = el("input", { class: "field scn-var-value", value: v.value ?? v.default ?? "",
      placeholder: v.required === false ? "Необязательно — заполняется блоком" : "Обязательное значение" });
    name.title = v.description || "Имя переменной";
    name.addEventListener("input", () => { v.name = name.value; markDirty(); });
    value.addEventListener("input", () => { v.value = value.value; v.default = ""; markDirty(); });
    return el("tr", {}, el("td", {}, name), el("td", {}, value),
      el("td", {}, el("button", { class: "icon", title: "Удалить переменную", onclick: () => {
        scenario.variables.splice(index, 1); markDirty(); draw();
      } }, "×")));
  });
  const box = el("details", { class: "scn-vars" });
  box.open = !collapsed;
  box.append(el("summary", {}, `Таблица переменных${scenario.variables.length ? ` · ${scenario.variables.length}` : ""}`),
    el("div", { class: "scn-vars-panel" },
      el("table", { class: "scn-var-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "Переменная"), el("th", {}, "Значение"), el("th", {}))),
        el("tbody", {}, ...rows)),
      el("div", { class: "scn-runtime-vars" }, el("b", {}, "Текущие значения выполнения"),
        el("table", { class: "scn-var-table scn-runtime-table" },
          el("thead", {}, el("tr", {}, el("th", {}, "Переменная"), el("th", {}, "Текущее значение"))),
          el("tbody", { class: "scn-runtime-var-body" }, ...runtimeVariableRows()))),
    el("button", { class: "btn small", onclick: () => {
      scenario.variables.push({ name: "", description: "", value: "" }); S.sel = null; markDirty(); draw();
    } }, "+ Переменная")));
  return box;
}

function runtimeVariableRows() {
  const entries = Object.entries(S.runtimeValues || {}).filter(([, value]) => String(value ?? "").trim())
    .sort(([a], [b]) => a.localeCompare(b));
  return entries.map(([name, value]) => el("tr", {}, el("td", {}, name), el("td", {}, String(value ?? ""))));
}

function refreshRuntimeVars() {
  const body = page && page.querySelector(".scn-runtime-var-body");
  if (body) body.replaceChildren(...runtimeVariableRows());
}

function bindListSearch(input, rows, updateQuery) {
  const filter = () => {
    updateQuery(input.value);
    const query = input.value.trim().toLocaleLowerCase();
    rows.forEach((row) => { row.style.display = !query || row.dataset.search.includes(query) ? "" : "none"; });
  };
  input.addEventListener("input", filter);
  filter();
}

function scenarioSidebar() {
  const scenarioRows = S.list.map((s) => el("button", { class: "scn-item" + (S.cur && S.cur.id === s.id ? " on" : ""), title: s.name,
    "data-search": [s.name || "", ...(s.triggers || [])].join(" ").toLocaleLowerCase(), onclick: () => openScenario(s.id) },
    el("div", { class: "t" }, el("span", { class: "scn-item-icon" }, [...String(s.name || "С")][0].toLocaleUpperCase()),
      el("span", { class: "scn-item-name" }, (s.enabled ? "" : "⏸ ") + s.name))));
  const scenarioSearch = el("input", { class: "field scn-list-search scn-scenario-search", type: "search", placeholder: "Поиск сценария", value: S.scenarioQuery });
  const scenarioList = el("div", { class: "scn-list scn-scenario-list" }, scenarioRows.length ? scenarioRows : el("div", { class: "mut small scn-list-empty" }, "Пока нет сценариев"));
  bindListSearch(scenarioSearch, scenarioRows, (value) => { S.scenarioQuery = value; });

  const nodeSearch = el("input", { class: "field scn-list-search scn-node-search", type: "search", placeholder: "Поиск узла", value: S.nodeQuery });
  const nodeRows = PALETTE.map((t) => {
    const title = blockTitle(t);
    const row = el("button", { class: "scn-node-item", title, "data-search": title.toLocaleLowerCase() },
      el("span", { class: "scn-node-icon" }, ICONS[t]), el("span", { class: "scn-node-label" }, title));
    row.addEventListener("pointerdown", (e) => startPaletteDrag(t, row, e));
    return row;
  });
  const nodeList = el("div", { class: "scn-list scn-node-list" }, nodeRows);
  bindListSearch(nodeSearch, nodeRows, (value) => { S.nodeQuery = value; });

  return el("aside", { class: "scn-side" },
    el("section", { class: "scn-side-panel scn-scenario-panel" },
      el("div", { class: "scn-side-head" },
        el("button", { class: "icon scn-sidebar-toggle", title: S.sidebarCollapsed ? "Развернуть панель" : "Свернуть панель до иконок", onclick: toggleScenarioSidebar }, S.sidebarCollapsed ? "›" : "‹"),
        el("b", {}, "Сценарии"),
        el("button", { class: "btn small scn-new", title: "Новый сценарий", onclick: newScenario }, el("span", { class: "scn-new-icon" }, "+"), el("span", { class: "scn-side-label" }, "Новый"))),
      scenarioSearch, scenarioList,
      el("div", { class: "scn-side-foot" },
        el("button", { class: "btn small scn-import", title: "Импорт JSON", onclick: importDialog }, el("span", {}, "⇪"), el("span", { class: "scn-side-label" }, "Импорт JSON")))),
    el("section", { class: "scn-side-panel scn-node-panel" },
      el("div", { class: "scn-node-head" }, el("b", {}, "Узлы"),
        el("button", { class: "icon scn-layout-btn", title: "Выровнять узлы", onclick: layout }, "⇅")),
      nodeSearch, nodeList));
}

function draw() {
  if (!page || !active()) return;
  const side = scenarioSidebar();

  if (!S.cur) {
    page.replaceChildren(el("div", { class: "scn-wrap" + (S.sidebarCollapsed ? " scn-sidebar-collapsed" : "") }, side, el("div", { class: "scn-empty" },
      el("h1", {}, "Сценарии"), el("button", { class: "btn primary", onclick: newScenario }, "Создать сценарий"))));
    return;
  }
  const c = S.cur;
  const name = el("input", { class: "field scn-name", value: c.name, placeholder: "Название" });
  name.addEventListener("input", () => { c.name = name.value; markDirty(); });
  const triggers = el("textarea", { class: "field scn-triggers", rows: 2, placeholder: "Фразы-триггеры, по одной в строке. «*» — любое слово: «включи трек *» → {arg}" });
  triggers.value = (c.triggers || []).join("\n");
  triggers.addEventListener("input", () => { c.triggers = triggers.value.split("\n").map((t) => t.trim()).filter(Boolean); markDirty(); });
  const enabled = el("input", { type: "checkbox" });
  enabled.checked = c.enabled !== false;
  enabled.addEventListener("change", () => { c.enabled = enabled.checked; markDirty(); });
  const desc = el("input", { class: "field", value: c.description || "", placeholder: "Описание (видит агент)" });
  desc.addEventListener("input", () => { c.description = desc.value; markDirty(); });
  const head = el("div", { class: "scn-head" },
    el("div", { class: "scn-head-row" }, name,
      el("label", { class: "scn-check", title: "Выключенный сценарий не запускается по фразе" }, enabled, "включён"),
      el("button", { class: "btn scn-save" + (S.dirty ? " primary" : ""), onclick: () => save() }, "Сохранить"),
      el("button", { class: "btn primary", onclick: () => runScenario() }, "▶ Запустить"),
      el("button", { class: "btn scn-stop", disabled: !S.running, onclick: () => core.try("scenarios.stop") }, "■ Стоп"),
      el("button", { class: "icon", title: "Скопировать JSON", onclick: exportJson }, "⇪"),
      el("button", { class: "icon", title: "Удалить сценарий", onclick: removeScenario }, "✕")),
    el("div", { class: "scn-head-row" }, triggers, desc));

  plane = el("div", { class: "scn-plane" });
  svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "scn-edges");
  plane.append(svg);
  for (const n of c.nodes) plane.append(nodeView(n));
  const canvas = el("div", { class: "scn-canvas",
    ondragover: (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; canvas.classList.add("drag-over"); },
    ondragleave: (e) => { if (!canvas.contains(e.relatedTarget)) canvas.classList.remove("drag-over"); },
    ondrop: (e) => {
      e.preventDefault(); canvas.classList.remove("drag-over");
      const type = e.dataTransfer.getData("application/x-idos-scenario-block") || e.dataTransfer.getData("text/plain");
      if (!type || !S.meta.blocks[type]) return;
      const rect = plane.getBoundingClientRect();
      addBlock(type, { x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    onpointerdown: (e) => { if (e.target === canvas || e.target === plane || e.target === svg) { S.sel = null; drawInspector(); plane.querySelectorAll(".scn-node.sel").forEach((x) => x.classList.remove("sel")); } } }, plane);
  inspector = el("aside", { class: "scn-inspector" });
  page.replaceChildren(el("div", { class: "scn-wrap" + (S.sidebarCollapsed ? " scn-sidebar-collapsed" : "") }, side,
    el("div", { class: "scn-editor" }, head, el("div", { class: "scn-work" }, canvas, inspector))));
  sizePlane();
  requestAnimationFrame(drawEdges);
  drawInspector();
  page.tabIndex = -1;
  if (!scenarioKeyHandlerInstalled) {
    document.addEventListener("keydown", (e) => {
      if (!active() || !page || !page.isConnected) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
        return;
      }
      const target = e.target;
      const editing = target && (target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(target.tagName));
      if (e.key === "Delete" && S.sel && !editing) {
        e.preventDefault();
        e.stopPropagation();
        deleteBlock(S.sel);
      }
    });
    scenarioKeyHandlerInstalled = true;
  }
}

function sizePlane() {
  let w = 900, h = 600;
  for (const n of S.cur.nodes) { w = Math.max(w, n.x + NODE_W + 300); h = Math.max(h, n.y + 260); }
  plane.style.width = w + "px"; plane.style.height = h + "px";
  svg.setAttribute("width", w); svg.setAttribute("height", h);
}

function paintStatus(view, st) {
  view.classList.remove("run-running", "run-ok", "run-error", "run-stopped");
  if (!st) return;
  view.classList.add("run-" + st.status);
  const s = view.querySelector(".scn-st");
  if (s) s.textContent = st.status === "running" ? "выполняется…" : st.status === "ok" ? "✓ " + ((st.ms || 0) / 1000).toFixed(1) + " с" : st.status === "error" ? "✕ " + (st.text || "") : "остановлено";
}

function nodeView(n) {
  const p = n.params || {};
  const outs = outsOf(n.type);
  const t = p.target && typeof p.target === "object" ? p.target
    : (Array.isArray(p.targets) && p.targets.length ? p.targets[0] : null);
  const view = el("div", { class: "scn-node t-" + n.type + (S.sel === n.id ? " sel" : ""), "data-id": n.id, style: { left: n.x + "px", top: n.y + "px", width: NODE_W + "px" } },
    n.type !== "start" ? el("span", { class: "scn-in", title: "вход" }) : null,
    el("div", { class: "scn-node-head" }, el("span", { class: "ic" }, ICONS[n.type] || "•"), el("span", { class: "tt" }, blockTitle(n.type))),
    n.type !== "start" && n.type !== "end" ? el("div", { class: "scn-sum" }, summary(n)) : null,
    t && t.preview ? el("img", { class: "scn-prev", src: t.preview, alt: "" }) : null,
    n.note ? el("div", { class: "mut small" }, n.note) : null,
    el("div", { class: "scn-st small" }),
    outs.length ? el("div", { class: "scn-outs" }, outs.map((o) => el("span", { class: "scn-port o-" + o + (n.links[o] ? " linked" : ""), "data-out": o, title: "Потяните к следующему блоку" }, outLabel(o)))) : null);
  paintStatus(view, S.status[n.id]);
  view.addEventListener("pointerdown", (e) => {
    const port = e.target.closest(".scn-port");
    if (port) { e.preventDefault(); e.stopPropagation(); startLink(n, port.dataset.out, port, e); return; }
    if (e.button !== 0) return;
    select(n.id);
    startMove(n, view, e);
  });
  view.addEventListener("dblclick", () => { if (n.type !== "start" && n.type !== "end") runScenario(n.id); });
  return view;
}

function select(id) {
  if (S.sel === id) return;
  S.sel = id;
  plane.querySelectorAll(".scn-node").forEach((x) => x.classList.toggle("sel", x.dataset.id === id));
  drawInspector();
}

function startMove(n, view, e) {
  e.preventDefault();
  e.stopPropagation();
  try { view.setPointerCapture(e.pointerId); } catch (_) { /* fallback to window listeners */ }
  const sx = e.clientX, sy = e.clientY, ox = n.x, oy = n.y;
  let moved = false;
  const move = (ev) => {
    const dx = ev.clientX - sx, dy = ev.clientY - sy;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    moved = true;
    n.x = Math.max(0, Math.round((ox + dx) / 10) * 10);
    n.y = Math.max(0, Math.round((oy + dy) / 10) * 10);
    view.style.left = n.x + "px"; view.style.top = n.y + "px";
    drawEdges();
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    try { view.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    if (moved) { markDirty(); sizePlane(); drawEdges(); }
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

function planePoint(ev) {
  const r = plane.getBoundingClientRect();
  return [ev.clientX - r.left, ev.clientY - r.top];
}

function portPoint(n, out) {
  const view = plane.querySelector(`.scn-node[data-id="${n.id}"]`);
  const port = view && view.querySelector(`.scn-port[data-out="${out}"]`);
  if (!view || !port) return [n.x + NODE_W / 2, n.y + 80];
  return [n.x + port.offsetLeft + port.offsetWidth / 2, n.y + view.offsetHeight];
}

function curve(x1, y1, x2, y2) {
  const dy = Math.max(40, Math.abs(y2 - y1) / 2);
  return `M ${x1} ${y1} C ${x1} ${y1 + dy}, ${x2} ${y2 - dy}, ${x2} ${y2}`;
}

function startLink(n, out, port, e) {
  const [x1, y1] = portPoint(n, out);
  const temp = document.createElementNS(SVGNS, "path");
  temp.setAttribute("class", "scn-edge temp");
  svg.append(temp);
  const move = (ev) => { const [x2, y2] = planePoint(ev); temp.setAttribute("d", curve(x1, y1, x2, y2)); };
  move(e);
  const up = (ev) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    temp.remove();
    const hit = document.elementFromPoint(ev.clientX, ev.clientY);
    const target = hit && hit.closest(".scn-node");
    if (target && target.dataset.id !== n.id && nodeById(target.dataset.id).type !== "start") n.links[out] = target.dataset.id;
    else if (!target) delete n.links[out];
    markDirty();
    port.classList.toggle("linked", !!n.links[out]);
    drawEdges();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function drawEdges() {
  if (!svg || !S.cur) return;
  svg.replaceChildren();
  for (const n of S.cur.nodes) for (const [out, to] of Object.entries(n.links || {})) {
    const t = nodeById(to);
    if (!t) continue;
    const [x1, y1] = portPoint(n, out);
    const x2 = t.x + NODE_W / 2, y2 = t.y;
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("class", "scn-edge o-" + out);
    path.setAttribute("d", curve(x1, y1, x2, y2));
    const hit = document.createElementNS(SVGNS, "path");
    hit.setAttribute("class", "scn-edge-hit");
    hit.setAttribute("d", curve(x1, y1, x2, y2));
    const title = document.createElementNS(SVGNS, "title");
    title.textContent = outLabel(out) + " — клик, чтобы удалить связь";
    hit.append(title);
    hit.addEventListener("click", () => { delete n.links[out]; markDirty(); draw(); });
    const arrow = document.createElementNS(SVGNS, "path");
    arrow.setAttribute("class", "scn-arrow o-" + out);
    arrow.setAttribute("d", `M ${x2 - 6} ${y2 - 9} L ${x2} ${y2 - 1} L ${x2 + 6} ${y2 - 9}`);
    svg.append(path, arrow, hit);
  }
}

// ------------------------------------------------------------------ inspector

function field(label, input, hint) {
  return el("label", { class: "scn-f" }, el("span", { class: "k" }, label), input, hint ? el("span", { class: "mut small" }, hint) : null);
}

function setNodeParam(n, key, value) {
  n.params[key] = value;
  markDirty();
  refreshNode(n);
}

function textInput(n, key, placeholder = "", type = "text") {
  const input = el("input", { class: "field", type, value: n.params[key] ?? "", placeholder, step: type === "number" ? "0.1" : null });
  input.addEventListener("input", () => setNodeParam(n, key, type === "number" ? Number(input.value || 0) : input.value));
  return input;
}

function selectInput(n, key, options) {
  const s = el("select", { class: "field" }, Object.entries(options).map(([v, label]) => el("option", { value: v }, label)));
  s.value = n.params[key] ?? Object.keys(options)[0];
  s.addEventListener("change", () => setNodeParam(n, key, s.value));
  return s;
}

function checkInput(n, key, label) {
  const c = el("input", { type: "checkbox" });
  c.checked = !!n.params[key];
  c.addEventListener("change", () => setNodeParam(n, key, c.checked));
  return el("label", { class: "scn-check" }, c, label);
}

function refreshNode(n) {
  const old = plane && plane.querySelector(`.scn-node[data-id="${n.id}"]`);
  if (old) { const fresh = nodeView(n); old.replaceWith(fresh); drawEdges(); }
}

function targetEditor(n, key, optional = false) {
  const t = n.params[key] && typeof n.params[key] === "object" ? n.params[key] : null;
  const box = el("div", { class: "scn-target" });
  const details = t ? [["Программа", t.process], ["Окно", t.title], ["Тип", t.type], ["Имя", t.name], ["AutomationId", t.id],
    ["Ref", t.ref ? (t.ref.twins > 1 ? "№" + t.ref.ordinal + " из " + t.ref.twins + " одинаковых" : "единственный такой") : ""]].filter(([, v]) => v) : [];
  const name = el("input", { class: "field", value: (t && t.name) || "", placeholder: "Имя элемента (как в программе)" });
  name.addEventListener("change", () => { n.params[key] = Object.assign({}, n.params[key] || {}, { name: name.value.trim(), label: "", ref: null, path: [] }); markDirty(); refreshNode(n); drawInspector(); });
  const proc = el("input", { class: "field", value: (t && t.process) || "", placeholder: "Процесс, например Яндекс Музыка.exe" });
  proc.addEventListener("change", () => { n.params[key] = Object.assign({}, n.params[key] || {}, { process: proc.value.trim() }); markDirty(); });
  box.append(
    t && t.preview ? el("img", { class: "scn-prev big", src: t.preview, alt: "" }) : null,
    el("div", { class: "scn-target-label" }, t ? (t.label || "«" + (t.name || t.id || "элемент") + "»") : (optional ? "Без элемента — текст печатается туда, где курсор" : "Элемент не выбран")),
    details.length ? el("div", { class: "mut small" }, details.map(([k, v]) => el("div", {}, k + ": " + v))) : null,
    el("div", { class: "scn-row" },
      el("button", { class: "btn primary", disabled: S.picking, onclick: () => pipette(n, key) }, "🎯 Пипетка"),
      t ? el("button", { class: "btn", onclick: () => probe(t) }, "Проверить") : null,
      t && optional ? el("button", { class: "btn", onclick: () => { n.params[key] = null; markDirty(); refreshNode(n); drawInspector(); } }, "Убрать") : null),
    el("details", { class: "scn-adv" }, el("summary", { class: "mut small" }, "Вручную"), field("Имя", name), field("Процесс", proc)));
  return box;
}

function targetListEditor(n) {
  const targets = Array.isArray(n.params.targets) && n.params.targets.length
    ? n.params.targets : (n.params.target && typeof n.params.target === "object" ? [n.params.target] : []);
  const box = el("div", { class: "scn-target-list" });
  const updateTargets = (next) => {
    n.params.targets = next;
    n.params.target = null;
    markDirty();
    refreshNode(n);
    drawInspector();
  };
  const items = targets.map((target, index) => el("div", { class: "scn-target-item" },
    target.preview ? el("img", { class: "scn-prev big", src: target.preview, alt: "" }) : null,
    el("div", { class: "scn-target-label" }, target.label || `«${target.name || target.id || "элемент"}»`),
    el("div", { class: "mut small" }, [target.process, target.type, target.id].filter(Boolean).join(" · ")),
    el("div", { class: "scn-row" },
      el("button", { class: "btn small", onclick: () => probe(target) }, "Проверить"),
      el("button", { class: "btn small", disabled: index === 0, title: "Переместить вверх", onclick: () => {
        const next = [...targets]; [next[index - 1], next[index]] = [next[index], next[index - 1]];
        updateTargets(next);
      } }, "↑"),
      el("button", { class: "btn small", disabled: index === targets.length - 1, title: "Переместить вниз", onclick: () => {
        const next = [...targets]; [next[index + 1], next[index]] = [next[index], next[index + 1]];
        updateTargets(next);
      } }, "↓"),
      el("button", { class: "btn small danger", title: "Убрать элемент", onclick: () => {
        updateTargets(targets.filter((_, i) => i !== index));
      } }, "Убрать"))));
  box.append(
    ...(targets.length ? items : [el("div", { class: "mut small" }, "Элементы ещё не выбраны.")]),
    el("button", { class: "btn primary", disabled: S.picking, onclick: () => pickTarget(n, (target) => {
      n.params.targets = [...targets, target]; n.params.target = null; markDirty();
    }) }, "🎯 Добавить элемент пипеткой"));
  return box;
}

async function pipette(n, key) {
  return pickTarget(n, (target) => { n.params[key] = target; markDirty(); });
}

async function pickTarget(n, setTarget) {
  if (S.picking) return;
  S.picking = true;
  drawInspector();
  toast("Наведите курсор на нужную кнопку и нажмите Ctrl. Esc — отмена.");
  try {
    if (win) { try { await win.minimize(); } catch (e) { /* ignore */ } }
    await new Promise((r) => setTimeout(r, 450));
    const r = await core.call("scenarios.pick", { timeout: 60 }, null, 75000);
    if (r && r.target) {
      setTarget(r.target);
      toast("Выбрано: " + (r.target.label || r.target.name || "элемент"));
    } else toast("Пипетка отменена");
  } catch (e) {
    toast("Пипетка: " + (e.message || e), "error");
  } finally {
    S.picking = false;
    if (win) { try { await win.unminimize(); await win.show(); await win.setFocus(); } catch (e) { /* ignore */ } }
    if (active()) { refreshNode(n); drawInspector(); }
  }
}

async function probe(target) {
  if (win) { try { await win.minimize(); } catch (e) { /* ignore */ } }
  await new Promise((r) => setTimeout(r, 400));
  const r = await core.try("scenarios.probe", { target }, 30000);
  if (win) { try { await win.unminimize(); await win.setFocus(); } catch (e) { /* ignore */ } }
  toast(r && r.text ? r.text : (r && r.error) || "нет ответа", r && r.found ? "info" : "error");
}

function drawInspector() {
  if (!inspector) return;
  const n = nodeById(S.sel);
  if (!n) {
    inspector.replaceChildren(el("h3", {}, "Переменные"),
      el("p", { class: "mut small" }, "Заполните значения до запуска. Пустые входы блокируют выполнение."),
      scenarioVariablesEditor(S.cur));
    return;
  }
  const p = n.params;
  const parts = [];
  switch (n.type) {
    case "launch": parts.push(field("Программа", textInput(n, "app", "Яндекс Музыка"), "Как в меню Пуск, путь к .exe или ссылка"), field("Подождать после запуска, с", textInput(n, "wait", "", "number"))); break;
    case "focus": parts.push(field("Окно", textInput(n, "window", "часть заголовка или программа"))); break;
    case "click": parts.push(el("div", { class: "scn-f" }, el("span", { class: "k" }, "Элементы (по порядку)"),
      targetListEditor(n), el("span", { class: "mut small" }, "Выберите один или несколько элементов пипеткой.")),
      field("Кнопка мыши", selectInput(n, "button", { left: "Левая", right: "Правая", double: "Двойной клик" })),
      field("Способ", selectInput(n, "method", { mouse: "Мышью (как человек)", invoke: "Без мыши (UI Automation)" }), "«Без мыши» работает даже в фоне, но не во всех программах"),
      field("Ждать появления, с", textInput(n, "timeout", "", "number"))); break;
    case "type": parts.push(field("Текст", textInput(n, "text", "можно {arg}")), checkInput(n, "enter", "Нажать Enter после ввода"),
      el("div", { class: "k small" }, "Поле ввода (необязательно)"), targetEditor(n, "target", true), field("Ждать поле, с", textInput(n, "timeout", "", "number"))); break;
    case "hotkey": parts.push(field("Клавиши", textInput(n, "keys", "ctrl+l, alt+tab, win+d, space, f5"))); break;
    case "wait": parts.push(field("Секунд", textInput(n, "seconds", "", "number"))); break;
    case "wait_for": parts.push(targetEditor(n, "target"), field("Ждать не дольше, с", textInput(n, "timeout", "", "number"))); break;
    case "if_exists": parts.push(targetEditor(n, "target"), field("Ждать, с", textInput(n, "timeout", "", "number"), "0 — проверить сразу")); break;
    case "read": parts.push(targetEditor(n, "target"),
      field("Сохранить в переменную", textInput(n, "var", "значение")),
      field("Что прочитать", selectInput(n, "property", { value: "Значение / Value", text: "Текст", name: "Имя элемента", state: "Состояние переключателя", id: "AutomationId" })),
      field("Ждать появления, с", textInput(n, "timeout", "", "number")),
      el("p", { class: "mut small" }, "Переменная доступна в следующих блоках как {имя}.")); break;
    case "condition": parts.push(
      field("Переменная", textInput(n, "var", "город")),
      field("Условие", selectInput(n, "operator", {
        equals: "равно", not_equals: "не равно", contains: "содержит", not_contains: "не содержит",
        greater: "больше (число)", less: "меньше (число)", is_set: "задано", is_empty: "пусто",
      })),
      field("Сравнить со значением", textInput(n, "value", "Москва"), "Для «задано» и «пусто» значение не используется.")); break;
    case "repeat": parts.push(
      field("Количество повторов", textInput(n, "count", "", "number"), "От 1 до 100. Выход «повтор» соедините с телом цикла, его конец — обратно с этим блоком."),
      field("Переменная итерации", textInput(n, "var", "iteration"), "Внутри цикла принимает значения от 1 до N.")); break;
    case "media": parts.push(field("Действие", selectInput(n, "action", MEDIA_NAMES))); break;
    case "open": parts.push(field("Файл, папка или ссылка", textInput(n, "path", "https://music.yandex.ru или C:\\…"))); break;
    case "say": parts.push(
      field("Фраза", textInput(n, "text", "Включаю вашу музыку"), "Можно подставлять ответы: «Включаю {исполнитель}»"),
      checkInput(n, "wait", "Ждать окончания речи")); break;
    case "ask": parts.push(
      field("Вопрос", textInput(n, "question", "Какого исполнителя включить?")),
      field("Имя переменной", textInput(n, "var", "исполнитель"), "Дальше пишите {" + varName(p.var) + "} в любом текстовом поле: «Ввести текст», «Сказать», «Открыть», «Запустить»…"),
      field("Как спрашивать", selectInput(n, "mode", ASK_MODES)),
      field("Варианты ответа", textInput(n, "choices", "необязательно: рок, джаз, классика"), "Через запятую. Поймёт «второй», «давай джаз». Не подошло — выход «если ошибка»"),
      field("Значение по умолчанию", textInput(n, "default", "если не ответили или нажали «Пропустить»"), "Пусто — без ответа сценарий идёт по выходу «если ошибка»"),
      field("Ждать ответа, с", textInput(n, "timeout", "", "number")),
      field("Уточнить ответ моделью", textInput(n, "extract", "например: время в формате ЧЧ:ММ"), "Пусто — ответ берётся как есть (0 запросов). Заполнено — один короткий запрос: «поставь на семь утра» → 07:00"),
      checkInput(n, "skip_known", "Не спрашивать, если значение уже известно (агент взял его из вашей фразы)")); break;
    case "window": parts.push(
      field("Программа или окно", textInput(n, "app", "Яндекс Музыка"), "Пусто — активное окно. Можно {переменную}"),
      field("Действие", selectInput(n, "action", WINDOW_NAMES), "«Закрыть» — как крестик (программа может спросить о сохранении). «Завершить» — сразу, несохранённое пропадёт")); break;
    case "scenario": parts.push(field("Сценарий", selectInput(n, "name", Object.fromEntries([["", "— выберите —"]].concat(S.list.filter((s) => !S.cur || s.id !== S.cur.id).map((s) => [s.name, s.name])))))); break;
    default: break;
  }
  const note = el("input", { class: "field", value: n.note || "", placeholder: "Заметка к блоку" });
  note.addEventListener("input", () => { n.note = note.value; markDirty(); refreshNode(n); });
  inspector.replaceChildren(
    el("h3", {}, (ICONS[n.type] || "") + " " + blockTitle(n.type)),
    ...parts,
    scenarioVariablesEditor(S.cur, true),
    n.type !== "start" ? field("Заметка", note) : el("p", { class: "mut small" }, "Отсюда начинается сценарий. Потяните «далее» к первому действию."),
    el("div", { class: "scn-row" },
      n.type !== "start" && n.type !== "end" ? el("button", { class: "btn", onclick: () => runScenario(n.id) }, "▶ Отсюда") : null,
      n.type !== "start" ? el("button", { class: "btn danger", onclick: () => deleteBlock(n.id) }, "Удалить блок") : null));
  void p;
}

// ------------------------------------------------------------------ import / export

async function exportJson() {
  const data = clone(S.cur);
  for (const n of data.nodes) if (n.params && n.params.target) delete n.params.target.preview;
  const text = JSON.stringify(data, null, 1);
  try { await navigator.clipboard.writeText(text); toast("JSON сценария скопирован в буфер"); }
  catch (e) { toast("Не удалось скопировать: " + e.message, "error"); }
}

function importDialog() {
  const area = el("textarea", { class: "field", rows: 10, placeholder: "Вставьте JSON сценария" });
  const box = el("div", { class: "scn-modal" }, el("div", { class: "scn-modal-card" }, el("h3", {}, "Импорт сценария"), area,
    el("div", { class: "scn-row" },
      el("button", { class: "btn primary", onclick: async () => {
        try {
          const data = JSON.parse(area.value);
          data.id = "";
          S.cur = data; S.dirty = true; S.sel = null; box.remove();
          await save();
        } catch (e) { toast("Неверный JSON: " + e.message, "error"); }
      } }, "Импортировать"),
      el("button", { class: "btn", onclick: () => box.remove() }, "Отмена"))));
  document.body.append(box);
  area.focus();
}
