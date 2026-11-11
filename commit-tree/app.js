/* Граф истории: коммиты идут слева направо, дорожки (ветки) — сверху вниз, слияния видны как ветвление.
   При наведении или фокусе показывается русское описание коммита. */
const state = { data: null, translations: {}, branch: "all", query: "", scrolled: false, generatedAt: "" };
const $ = (selector) => document.querySelector(selector);

const LANE_H = 54;
const GAP_X = 46;
const PAD_X = 44;
const PAD_TOP = 78;
const PAD_BOTTOM = 78;
const NODE_R = 6;
const MAIN_COLOR = "#3ddc84";
const COLORS = ["#a897ff", "#ffba72", "#7fd1ff", "#f78fb3", "#ffd166", "#c792ea", "#ff8a80", "#8ab4ff"];

function escape(value) {
  return String(value ?? "").replace(/[&<>\"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
}
function list(value) {
  if (Array.isArray(value)) return value;
  if (value == null) return [];
  if (typeof value === "object") return Object.keys(value).length ? Object.values(value) : [];
  return [value];
}
function refsOf(commit) {
  return list(commit && commit.refs);
}
function parentsOf(commit) {
  return list(commit && commit.parents);
}
function subjectOf(commit) {
  return (state.translations && state.translations[commit.short]) || commit.subject;
}
function matches(commit, query) {
  if (!query) return true;
  const haystack = [commit.hash, commit.short, commit.subject, subjectOf(commit), commit.author, ...refsOf(commit)]
    .join(" ").toLowerCase();
  return haystack.includes(query);
}

/* Дорожки: идём от новых коммитов к старым, каждый родитель продолжает дорожку ребёнка.
   Второй родитель слияния получает отдельную дорожку — так ветвление и слияние видны на графике. */
function layout() {
  const commits = state.data.commits;
  const byHash = new Map(commits.map((commit) => [commit.hash, commit]));
  const lanes = [];
  const total = commits.length;
  commits.forEach((commit, index) => {
    let lane = -1;
    for (let i = 0; i < lanes.length; i += 1) {
      if (lanes[i] === commit.hash) {
        if (lane < 0) lane = i;
        else lanes[i] = null;
      }
    }
    if (lane < 0) {
      lane = lanes.indexOf(null);
      if (lane < 0) { lane = lanes.length; lanes.push(null); }
    }
    lanes[lane] = null;
    commit._lane = lane;
    commit._x = PAD_X + (total - 1 - index) * GAP_X;
    const parents = parentsOf(commit).filter((hash) => byHash.has(hash));
    if (parents.length) {
      lanes[lane] = parents[0];
      for (const parent of parents.slice(1)) {
        let other = lanes.indexOf(parent);
        if (other < 0) {
          other = lanes.indexOf(null);
          if (other < 0) { other = lanes.length; lanes.push(null); }
        }
        lanes[other] = parent;
      }
    }
  });
  const laneCount = Math.max(1, lanes.length);
  const mainTip = branchTip("main") || branchTip("origin/main");
  const mainCommit = byHash.get(mainTip);
  const mainLane = mainCommit ? mainCommit._lane : 0;
  /* main — центральная линия. Остальные дорожки по очереди уходят вниз и вверх от неё,
     поэтому каждая новая ветка визуально ответвляется от главной линии. */
  const offsets = new Map([[mainLane, 0]]);
  let branch = 0;
  for (let lane = 0; lane < laneCount; lane += 1) {
    if (lane === mainLane) continue;
    offsets.set(lane, branch % 2 === 0 ? branch / 2 + 1 : -(branch + 1) / 2);
    branch += 1;
  }
  const values = [...offsets.values()];
  const spread = Math.max(1, ...values.map((value) => Math.abs(value)));
  commits.forEach((commit) => {
    commit._visual = spread + offsets.get(commit._lane);
    commit._main = commit._lane === mainLane;
    commit._y = PAD_TOP + commit._visual * LANE_H;
  });
  return {
    byHash,
    width: PAD_X * 2 + Math.max(0, total - 1) * GAP_X,
    height: PAD_TOP + spread * 2 * LANE_H + PAD_BOTTOM,
  };
}

function colorOf(commit) {
  return commit._main ? MAIN_COLOR : COLORS[commit._visual % COLORS.length];
}

function edgePath(child, parent) {
  if (child._y === parent._y) return `M${child._x} ${child._y} L${parent._x} ${parent._y}`;
  const middle = (child._x + parent._x) / 2;
  return `M${child._x} ${child._y} C${middle} ${child._y} ${middle} ${parent._y} ${parent._x} ${parent._y}`;
}

function reachable(tip, byHash) {
  const seen = new Set();
  const stack = tip ? [tip] : [];
  while (stack.length) {
    const hash = stack.pop();
    if (!hash || seen.has(hash)) continue;
    seen.add(hash);
    const commit = byHash.get(hash);
    if (commit) for (const parent of parentsOf(commit)) if (byHash.has(parent)) stack.push(parent);
  }
  return seen;
}

function branchTip(name) {
  const found = list(state.data.branches).find((branch) => branch.name === name);
  return found ? found.tip : "";
}

function renderBranches() {
  const names = ["all", ...list(state.data.branches).map((branch) => branch.name)];
  $("#branches").innerHTML = names.map((name) =>
    `<button class="branch ${state.branch === name ? "active" : ""}" data-branch="${escape(name)}">${name === "all" ? "все ветки" : escape(name)}</button>`
  ).join("");
  $("#branches").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
    state.branch = button.dataset.branch;
    state.scrolled = false;
    render();
  }));
}

function render() {
  if (!state.data) return;
  renderBranches();
  const { byHash, width, height } = layout();
  const commits = state.data.commits;
  const query = state.query.toLowerCase();
  const scope = state.branch === "all" ? null : reachable(branchTip(state.branch), byHash);
  const visible = (commit) => (!scope || scope.has(commit.hash)) && matches(commit, query);
  const shown = commits.filter(visible).length;
  $("#meta").textContent = `${shown} из ${commits.length} коммитов · обновлено ${new Date(state.data.generatedAt).toLocaleString("ru-RU")}`;

  const edges = [];
  const nodes = [];
  commits.forEach((commit, index) => {
    const dim = visible(commit) ? "" : " off";
    const color = colorOf(commit);
    const parents = parentsOf(commit);
    for (const hash of parents) {
      const parent = byHash.get(hash);
      if (!parent) continue;
      edges.push(`<path class="edge${dim}" d="${edgePath(commit, parent)}" stroke="${colorOf(parent)}"></path>`);
    }
    const isMerge = parents.length > 1;
    const refs = refsOf(commit);
    const label = `${subjectOf(commit)}; ${commit.short}`;
    nodes.push(`<g class="node${dim}" data-i="${index}" tabindex="0" role="button" aria-label="${escape(label)}">
      <circle class="halo" cx="${commit._x}" cy="${commit._y}" r="${isMerge ? NODE_R + 5 : NODE_R + 3}" fill="${color}" opacity=".18"></circle>
      ${isMerge ? `<circle class="merge" cx="${commit._x}" cy="${commit._y}" r="${NODE_R + 2.5}"></circle>` : ""}
      <circle class="core" cx="${commit._x}" cy="${commit._y}" r="${NODE_R}" fill="${color}"></circle>
      <text class="hash" x="${commit._x}" y="${commit._y + 19}" text-anchor="middle">${escape(commit.short)}</text>
      ${refs.map((ref, level) => {
        const boxWidth = Math.max(26, [...ref].length * 6.1 + 12);
        return `<g class="ref" transform="translate(${commit._x - boxWidth / 2},${commit._y - 31 - level * 17})">
          <rect width="${boxWidth}" height="15" rx="7.5"></rect>
          <text x="${boxWidth / 2}" y="10.8" text-anchor="middle">${escape(ref)}</text>
        </g>`;
      }).join("")}
    </g>`);
  });

  $("#tree").innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Граф коммитов: слева старые, справа новые">${edges.join("")}${nodes.join("")}</svg>`;
  bindTips(commits);
  if (!state.scrolled) { scrollToTip(commits); state.scrolled = true; }
}

function bindTips(commits) {
  const svg = $("#tree svg");
  if (!svg) return;
  svg.querySelectorAll(".node").forEach((node) => {
    const commit = commits[Number(node.dataset.i)];
    const show = () => showTip(commit, node);
    node.addEventListener("mouseenter", show);
    node.addEventListener("focus", show);
    node.addEventListener("mouseleave", hideTip);
    node.addEventListener("blur", hideTip);
  });
}

function showTip(commit, node) {
  const tip = $("#tooltip");
  const refs = refsOf(commit);
  tip.innerHTML = `<div class="tt-subject">${escape(subjectOf(commit))}</div>
    <div class="tt-meta"><code>${escape(commit.short)}</code><span>${escape(commit.author)}</span><span>${escape(new Date(commit.date).toLocaleString("ru-RU"))}</span></div>
    ${refs.length ? `<div class="tt-refs">${refs.map((ref) => `<span>${escape(ref)}</span>`).join("")}</div>` : ""}
    <div class="tt-hash">${escape(commit.hash)}</div>`;
  tip.hidden = false;
  const box = node.getBoundingClientRect();
  const size = tip.getBoundingClientRect();
  tip.style.left = `${Math.min(Math.max(12, box.left), window.innerWidth - size.width - 12)}px`;
  const below = box.bottom + 10;
  tip.style.top = `${below + size.height > window.innerHeight - 12 ? Math.max(12, box.top - size.height - 10) : below}px`;
}

function hideTip() {
  $("#tooltip").hidden = true;
}

/* При первом открытии показываем свежие коммиты (правый край графа), а не корень истории. */
function scrollToTip(commits) {
  const wrap = $("#tree-wrap");
  if (!wrap) return;
  const target = commits.find((commit) => refsOf(commit).includes("main")) || commits[0];
  if (!target) return;
  wrap.scrollLeft = Math.max(0, target._x - wrap.clientWidth * 0.7);
}

/* Насколько пользователь близок к правому краю (свежие коммиты) — чтобы при появлении новых
   не дёргать прокрутку, если он рассматривает середину истории. */
function pinnedRight() {
  const wrap = $("#tree-wrap");
  return wrap && wrap.scrollWidth - wrap.scrollLeft - wrap.clientWidth < 120;
}

function apply(data) {
  const wrap = $("#tree-wrap");
  const previous = wrap ? wrap.scrollLeft : 0;
  const pin = pinnedRight();
  state.data = data;
  state.generatedAt = data.generatedAt || "";
  render();
  if (state.scrolled) {
    wrap.scrollLeft = pin ? wrap.scrollWidth : previous; // удержать позицию или прилипнуть к новому
  }
}

async function loadJson(name) {
  const response = await fetch(name, { cache: "no-store" });
  if (!response.ok) throw new Error(`${name} не обновлён`);
  return response.json();
}

async function poll() {
  if (!state.generatedAt) return; // ещё не загрузили в первый раз
  try {
    const data = await loadJson("commits.json");
    if ((data.generatedAt || "") !== state.generatedAt) apply(data);
  } catch { /* сервер ещё не поднялся — перезапросим на следующем тике */ }
  try {
    const translations = await loadJson("translations.json");
    state.translations = translations && typeof translations === "object" ? translations : state.translations;
  } catch { /* переводы опциональны */ }
}

loadJson("commits.json")
  .then((data) => { apply(data); state.scrolled = true; })
  .then(() => { scrollToTip(state.data.commits); state.scrolled = true; })
  .catch((error) => {
    $("#tree").innerHTML = `<div class="empty">${escape(error.message)}<br><small>Запустите py -3 commit-tree\\serve.py.</small></div>`;
  });

loadJson("translations.json")
  .then((translations) => {
    if (!translations || Array.isArray(translations) || typeof translations !== "object") return;
    state.translations = translations;
    if (state.data) render();
  })
  .catch(() => {});

setInterval(poll, 2500);

$("#search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  render();
});
