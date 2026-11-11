const state = { data: null, branch: "all", query: "" };
const $ = (selector) => document.querySelector(selector);

function escape(value) {
  return String(value ?? "").replace(/[&<>\"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[char]));
}
function branchClass(refs) {
  const text = refs.join(" ");
  return text.includes("main") ? "main" : text.includes("experiment") ? "experiment" : "other";
}
function matches(commit) {
  const haystack = [commit.hash, commit.short, commit.subject, commit.author, ...(commit.refs || [])].join(" ").toLowerCase();
  return !state.query || haystack.includes(state.query.toLowerCase());
}
function renderBranches() {
  const names = ["all", ...state.data.branches.map((branch) => branch.name)];
  $("#branches").innerHTML = names.map((name) => `<button class="branch ${state.branch === name ? "active" : ""}" data-branch="${escape(name)}">${name === "all" ? "Все ветки" : escape(name)}</button>`).join("");
  $("#branches").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { state.branch = button.dataset.branch; render(); }));
}
function render() {
  if (!state.data) return;
  renderBranches();
  const commits = state.data.commits.filter((commit) => (state.branch === "all" || (commit.refs || []).includes(state.branch)) && matches(commit));
  $("#meta").textContent = `${commits.length} из ${state.data.commits.length} коммитов · обновлено ${new Date(state.data.generatedAt).toLocaleString("ru-RU")}`;
  if (!commits.length) { $("#tree").innerHTML = '<div class="empty">Ничего не найдено</div>'; return; }
  const visible = new Set(commits.map((commit) => commit.hash));
  $("#tree").innerHTML = commits.map((commit) => {
    const refs = (commit.refs || []).map(escape).join(", ");
    const parents = (commit.parents || []).filter((parent) => visible.has(parent));
    const lines = parents.map(() => '<span class="line"></span>').join("");
    return `<article class="commit ${branchClass(commit.refs || [])}" tabindex="0" title="${escape(commit.subject)}">
      <div class="graph"><span class="node"></span>${lines}</div>
      <div class="commit-body"><div class="subject">${escape(commit.subject)}</div><div class="details"><code>${escape(commit.short)}</code><span>${escape(commit.author)}</span><time datetime="${escape(commit.date)}">${escape(new Date(commit.date).toLocaleString("ru-RU"))}</time>${refs ? `<span class="refs">${refs}</span>` : ""}</div></div>
      <div class="tooltip"><strong>${escape(commit.subject)}</strong><span>${escape(commit.hash)}</span><span>${escape(commit.author)} · ${escape(new Date(commit.date).toLocaleString("ru-RU"))}</span>${refs ? `<span>Ветки: ${refs}</span>` : ""}</div>
    </article>`;
  }).join("");
}

fetch("commits.json", { cache: "no-store" }).then((response) => { if (!response.ok) throw new Error("commits.json недоступен"); return response.json(); }).then((data) => { state.data = data; render(); }).catch((error) => { $("#tree").innerHTML = `<div class="empty">${escape(error.message)}<br><small>Запустите scripts\\Update-CommitTree.ps1.</small></div>`; });
$("#search").addEventListener("input", (event) => { state.query = event.target.value.trim(); render(); });
