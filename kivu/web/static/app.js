// kivu web UI. Plain JS, no build step.
// A "draft" is a new chat that has no thread on the server yet; it's created on first send.

const $ = (s) => document.querySelector(s);
const api = async (method, path, body, signal) => {
  const res = await fetch(path, {
    method,
    signal,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
const basename = (p) => p.split("/").filter(Boolean).pop() || p;
const tildify = (p) => (state.home && p.startsWith(state.home) ? "~" + p.slice(state.home.length) : p);

let state = { threads: [], folders: [], models: [], default: {}, home: "" };
let current = null; // full thread from the server
let draft = null; // { folder, provider, model, mode }
let group = localStorage.getItem("kivu.group") || "project";
const closed = new Set(JSON.parse(localStorage.getItem("kivu.closed") || "[]"));
let pollToken = 0;
let rendered = []; // [{key, node}] per chat item, so updates only redraw what changed

// ---------- sidebar ----------

async function loadState() {
  state = await api("GET", "/api/state");
  renderModels();
  renderSidebar();
  renderProfile();
}

function renderSidebar() {
  document.querySelectorAll(".side-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.group === group));
  const nav = $("#threads");
  nav.innerHTML = "";
  if (!state.threads.length) {
    nav.innerHTML = `<div class="side-empty">No chats yet. Start one with New chat.</div>`;
    return;
  }
  if (group === "recent") {
    const g = el("div", "group");
    const rows = el("div", "rows");
    state.threads.forEach((t) => rows.append(threadRow(t, true)));
    g.append(rows);
    nav.append(g);
    return;
  }
  const groups = new Map();
  for (const t of state.threads) {
    if (!groups.has(t.project)) groups.set(t.project, []);
    groups.get(t.project).push(t);
  }
  for (const [project, threads] of groups) {
    const g = el("div", "group" + (closed.has(project) ? " closed" : ""));
    const head = el("div", "group-head");
    const folders = [...new Set(threads.map((t) => t.folder))];
    head.title = folders.map(tildify).join("\n");
    head.innerHTML = `
      <svg class="caret" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>
      <svg viewBox="0 0 16 16"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/></svg>
      <span class="name"></span>
      <button class="add" title="New chat in ${md.esc(project)}"><svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg></button>`;
    head.querySelector(".name").textContent = project;
    const toggle = () => {
      closed.has(project) ? closed.delete(project) : closed.add(project);
      localStorage.setItem("kivu.closed", JSON.stringify([...closed]));
      g.classList.toggle("closed");
    };
    head.querySelector(".name").onclick = toggle;
    head.querySelector(".caret").onclick = toggle;
    head.querySelector(".add").onclick = () => newChat(folders[0]);
    const rows = el("div", "rows");
    threads.forEach((t) => rows.append(threadRow(t, false)));
    g.append(head, rows);
    nav.append(g);
  }
}

function threadRow(t, showProject) {
  const row = el("div", "row" + (current && current.id === t.id ? " active" : ""));
  row.title = tildify(t.folder);
  row.innerHTML = `${t.running ? '<span class="dot"></span>' : ""}<span class="t"></span>${showProject ? '<span class="sub"></span>' : ""}<button class="more" title="More">⋯</button>`;
  row.querySelector(".t").textContent = t.title;
  if (showProject) row.querySelector(".sub").textContent = t.project;
  row.onclick = () => openThread(t.id);
  row.querySelector(".more").onclick = (e) => {
    e.stopPropagation();
    showMenu(e.currentTarget, t);
  };
  return row;
}

function showMenu(anchor, t) {
  const menu = $("#menu");
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${r.left}px`;
  menu.style.top = `${r.bottom + 4}px`;
  menu.hidden = false;
  menu.onclick = async (e) => {
    const act = e.target.dataset.act;
    menu.hidden = true;
    try {
      if (act === "rename") {
        const title = prompt("Rename chat", t.title);
        if (title) await api("PATCH", `/api/threads/${t.id}`, { title });
      } else if (act === "project") {
        const project = prompt("Project for this chat (chats are grouped by it)", t.project);
        if (project) await api("PATCH", `/api/threads/${t.id}`, { project });
      } else if (act === "delete") {
        if (!confirm(`Delete “${t.title}”? This can't be undone.`)) return;
        await api("DELETE", `/api/threads/${t.id}`);
        if (current && current.id === t.id) newChat();
      }
      await loadState();
      if (current && current.id === t.id) openThread(t.id);
    } catch (err) {
      alert(err.message);
    }
  };
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#menu") && !e.target.closest(".more")) $("#menu").hidden = true;
});

// ---------- profile + settings ----------

function renderProfile() {
  const p = state.profile;
  $("#avatar").textContent = (p.name || "?").trim().charAt(0).toUpperCase() || "?";
  $("#profile-name").textContent = p.name || "Set up profile";
  $("#profile-sub").textContent = p.email || state.default.model;
  applyTheme(p.theme || "system");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("kivu.theme", theme); // read before first paint, so no flash
  document.querySelectorAll("#theme-seg button").forEach((b) => b.classList.toggle("on", b.dataset.theme === theme));
}

$("#profile-btn").onclick = () => {
  const f = $("#profile-form");
  f.name.value = state.profile.name;
  f.email.value = state.profile.email;
  fillModelSelect($("#profile-model"));
  $("#profile-model").value = `${state.default.provider}/${state.default.model}`;
  $("#profile-saved").hidden = true;
  renderKeys();
  $("#settings").showModal();
};
$("#settings-close").onclick = () => $("#settings").close();
$("#settings").addEventListener("close", () => draft && newChat(draft.folder)); // pick up a new default model

$("#profile-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const [default_provider, ...rest] = f.default.value.split("/");
  try {
    state.profile = await api("PATCH", "/api/profile", { name: f.name.value, email: f.email.value, default_provider, default_model: rest.join("/") });
    await loadState();
    $("#profile-saved").hidden = false;
  } catch (err) {
    alert(err.message);
  }
};

document.querySelectorAll("#theme-seg button").forEach((b) => {
  b.onclick = async () => {
    applyTheme(b.dataset.theme);
    try {
      state.profile = await api("PATCH", "/api/profile", { theme: b.dataset.theme });
    } catch (err) {
      alert(err.message);
    }
  };
});

function renderKeys() {
  const box = $("#keys");
  box.innerHTML = "";
  for (const p of state.models) {
    const row = el("div", "key");
    const status = p.key ? `<span class="st ok">${md.esc(p.key)}</span>` : `<span class="st off">${p.supported ? "Not set" : "Not set · provider not supported yet"}</span>`;
    row.innerHTML = `<span class="p"></span>${status}<a target="_blank" rel="noopener">Get a key ↗</a>
      <form><input type="password" placeholder="Paste a new ${md.esc(p.provider)} key" autocomplete="off" spellcheck="false">
      <button class="primary">Save</button>${p.key ? '<button type="button" class="ghost rm">Remove</button>' : ""}</form>`;
    row.querySelector(".p").textContent = p.provider;
    row.querySelector("a").href = p.key_url;
    const form = row.querySelector("form");
    form.onsubmit = (e) => {
      e.preventDefault();
      const key = form.querySelector("input").value.trim();
      if (key) saveKey(p.provider, key);
    };
    const rm = row.querySelector(".rm");
    if (rm) rm.onclick = () => confirm(`Remove the ${p.provider} key from keys.json?`) && saveKey(p.provider, "");
    box.append(row);
  }
}

async function saveKey(provider, key) {
  try {
    await api("PATCH", `/api/keys/${provider}`, { key });
    await loadState();
    renderKeys();
  } catch (err) {
    alert(err.message);
  }
}

// ---------- open / new ----------

function newChat(folder) {
  pollToken++;
  if (pollAbort) pollAbort.abort();
  current = null;
  const last = state.threads[0];
  draft = {
    folder: folder || (last && last.folder) || null,
    provider: state.default.provider, // the profile's default model
    model: state.default.model,
    mode: "ask",
  };
  history.replaceState(null, "", "#");
  renderAll();
  $("#input").focus();
}

async function openThread(id) {
  try {
    current = await api("GET", `/api/threads/${id}`);
  } catch {
    return newChat();
  }
  draft = null;
  history.replaceState(null, "", `#${id}`);
  rendered = [];
  $("#messages").innerHTML = "";
  renderAll();
  poll(id, current.version);
}

let pollAbort = null;
async function poll(id, version) {
  const token = ++pollToken;
  if (pollAbort) pollAbort.abort(); // browsers allow ~6 open requests per host
  const abort = (pollAbort = new AbortController());
  while (token === pollToken) {
    try {
      const t = await api("GET", `/api/threads/${id}?after=${version}`, null, abort.signal);
      if (token !== pollToken) return;
      const sidebarChanged = t.running !== current.running || t.title !== current.title || t.project !== current.project;
      current = t;
      version = t.version;
      renderAll();
      if (sidebarChanged) loadState();
    } catch {
      if (abort.signal.aborted) return;
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

// ---------- chat ----------

function renderAll() {
  const t = current || draft;
  $("#head-title").textContent = current ? current.title : "New chat";
  $("#head-folder").textContent = t.folder ? tildify(t.folder) : "";
  renderComposer();
  renderMessages();
  renderSidebar();
}

function renderMessages() {
  const box = $("#messages");
  const items = current ? current.items : [];
  if (!items.length) {
    rendered = [];
    box.innerHTML = `<div class="empty" id="empty"><div class="empty-mark">kivu</div><p>Pick a folder, then ask kivu to read, change or run code in it.</p></div>`;
    return;
  }
  if ($("#empty")) $("#empty").remove();
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  items.forEach((item, i) => {
    const key = JSON.stringify(item);
    if (rendered[i] && rendered[i].key === key) return;
    const node = renderItem(item);
    if (rendered[i]) rendered[i].node.replaceWith(node);
    else box.insertBefore(node, $("#thinking"));
    rendered[i] = { key, node };
  });
  rendered.slice(items.length).forEach((r) => r.node.remove());
  rendered.length = items.length;
  // "working" line while the agent runs and isn't waiting on you
  let thinking = $("#thinking");
  const waiting = items.some((it) => it.answer === null);
  if (current.running && !waiting) {
    if (!thinking) {
      thinking = el("div", "thinking");
      thinking.id = "thinking";
      thinking.innerHTML = `<span class="dot"></span> Working…`;
    }
    box.append(thinking);
  } else if (thinking) thinking.remove();
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function renderItem(item) {
  switch (item.type) {
    case "user": {
      const n = el("div", "msg user");
      n.textContent = item.text;
      return n;
    }
    case "assistant": {
      const n = el("div", "msg assistant");
      n.innerHTML = md.render(item.text);
      return n;
    }
    case "error": {
      const n = el("div", "msg error");
      n.textContent = item.text;
      return n;
    }
    case "tool": {
      const n = el("details", "tool" + (item.error ? " err" : ""));
      const args = Object.entries(item.args || {})
        .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
        .join(" ");
      n.innerHTML = `<summary><span class="bullet"></span><span class="n"></span><span class="a"></span></summary><pre></pre>`;
      n.querySelector(".n").textContent = item.name;
      n.querySelector(".a").textContent = args.length > 140 ? args.slice(0, 137) + "…" : args;
      n.querySelector("pre").textContent = item.output || "(no output)";
      return n;
    }
    case "plan": {
      const n = el("div", "card");
      n.innerHTML = `<div class="h">Plan</div>`;
      const marks = { done: "✓", in_progress: "▸", pending: "○" };
      for (const p of item.items || []) {
        const r = el("div", `plan-item ${p.status}`);
        r.textContent = `${marks[p.status] || "○"} ${p.step}`;
        n.append(r);
      }
      return n;
    }
    case "approval":
      return approvalCard(item);
    case "question":
      return questionCard(item);
  }
  return el("div");
}

function approvalCard(item) {
  const n = el("div", "card" + (item.answer === null ? " pending" : ""));
  const h = el("div", "h");
  h.textContent = item.title;
  n.append(h);
  if (item.detail) {
    const pre = el("pre");
    if (item.detail.startsWith("---")) {
      for (const line of item.detail.split("\n")) {
        const s = el("span", line.startsWith("+") && !line.startsWith("+++") ? "diff-add" : line.startsWith("-") && !line.startsWith("---") ? "diff-del" : "");
        s.textContent = line + "\n";
        pre.append(s);
      }
    } else pre.textContent = item.detail;
    n.append(pre);
  }
  if (item.answer === null) {
    const actions = el("div", "actions");
    for (const [label, answer, cls] of [["Allow", "y", "primary"], ["Always allow this session", "a", "ghost"], ["Deny", "n", "ghost"]]) {
      const b = el("button", cls);
      b.textContent = label;
      b.onclick = () => answerPrompt(item.id, answer);
      actions.append(b);
    }
    n.append(actions);
  } else {
    const d = el("div", "done");
    d.textContent = { y: "Allowed", a: "Allowed for this session", n: "Denied" }[item.answer] || item.answer;
    n.append(d);
  }
  return n;
}

function questionCard(item) {
  const n = el("div", "card" + (item.answer === null ? " pending" : ""));
  const h = el("div", "h");
  h.textContent = item.text;
  n.append(h);
  if (item.answer === null) {
    const f = el("form", "actions");
    f.innerHTML = `<input placeholder="Your answer" autofocus><button class="primary">Reply</button><button type="button" class="ghost">Skip</button>`;
    f.onsubmit = (e) => {
      e.preventDefault();
      answerPrompt(item.id, f.querySelector("input").value);
    };
    f.querySelector(".ghost").onclick = () => answerPrompt(item.id, "");
    n.append(f);
  } else {
    const d = el("div", "done");
    d.textContent = item.answer ? `You: ${item.answer}` : "Skipped";
    n.append(d);
  }
  return n;
}

async function answerPrompt(id, answer) {
  try {
    current = await api("POST", `/api/threads/${current.id}/answer`, { id, answer });
    renderAll();
  } catch (err) {
    alert(err.message);
  }
}

// ---------- composer ----------

function renderModels() {
  const sel = $("#model");
  const value = sel.value;
  fillModelSelect(sel);
  if (value) sel.value = value;
}

function fillModelSelect(sel) {
  sel.innerHTML = "";
  for (const p of state.models) {
    const og = document.createElement("optgroup");
    og.label = p.ready ? p.provider : `${p.provider} (not set up)`;
    for (const m of p.models) {
      const o = new Option(m, `${p.provider}/${m}`);
      o.disabled = !p.ready;
      og.append(o);
    }
    sel.append(og);
  }
}

function renderComposer() {
  const t = current || draft;
  const chip = $("#folder-chip");
  const locked = current && current.items.length > 0;
  $("#folder-label").textContent = t.folder ? basename(t.folder) : "Select folder";
  chip.title = t.folder ? (locked ? `Works in ${t.folder} (fixed once the chat has messages)` : t.folder) : "Pick the folder this chat works in";
  chip.classList.toggle("need", !t.folder);
  chip.classList.toggle("locked", !!locked);
  $("#model").value = `${t.provider}/${t.model}`;
  $("#auto").checked = (current ? current.mode : draft.mode) === "auto";
  const running = current && current.running;
  $("#send").hidden = !!running;
  $("#stop").hidden = !running;
  $("#send").disabled = !t.folder;
  $("#input").placeholder = t.folder ? `Ask kivu about ${basename(t.folder)}…` : "Select a folder first…";
}

$("#composer").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#input");
  const text = input.value.trim();
  const t = current || draft;
  if (!text || !t.folder || (current && current.running)) return;
  try {
    if (!current) {
      const created = await api("POST", "/api/threads", { folder: draft.folder, provider: draft.provider, model: draft.model });
      if (draft.mode === "auto") await api("PATCH", `/api/threads/${created.id}`, { mode: "auto" });
      current = created;
      draft = null;
      history.replaceState(null, "", `#${created.id}`);
      rendered = [];
      $("#messages").innerHTML = "";
    }
    input.value = "";
    autosize();
    current = await api("POST", `/api/threads/${current.id}/messages`, { text });
    renderAll();
    loadState();
    poll(current.id, current.version);
  } catch (err) {
    alert(err.message);
  }
};

$("#stop").onclick = () => current && api("POST", `/api/threads/${current.id}/stop`);

$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
const autosize = () => {
  const i = $("#input");
  i.style.height = "auto";
  i.style.height = Math.min(i.scrollHeight, 220) + "px";
};
$("#input").addEventListener("input", autosize);

$("#model").onchange = async (e) => {
  const [provider, ...rest] = e.target.value.split("/");
  const model = rest.join("/");
  if (current) current = await api("PATCH", `/api/threads/${current.id}`, { provider, model });
  else Object.assign(draft, { provider, model });
  renderAll();
};

$("#auto").onchange = async (e) => {
  const mode = e.target.checked ? "auto" : "ask";
  if (current) current = await api("PATCH", `/api/threads/${current.id}`, { mode });
  else draft.mode = mode;
};

$("#new-chat").onclick = () => newChat();
document.querySelectorAll(".side-tabs button").forEach((b) => {
  b.onclick = () => {
    group = b.dataset.group;
    localStorage.setItem("kivu.group", group);
    renderSidebar();
  };
});

// ---------- folder picker ----------

let browsing = "";
$("#folder-chip").onclick = () => {
  if (current && current.items.length) return;
  const recent = $("#recent-folders");
  recent.innerHTML = "";
  for (const f of state.folders) {
    const b = el("button");
    b.type = "button";
    b.textContent = basename(f);
    b.title = f;
    b.onclick = () => pickFolder(f);
    recent.append(b);
  }
  browse((current || draft).folder || state.folders[0] || state.home);
  $("#folder-dialog").showModal();
};

async function browse(path) {
  try {
    const res = await api("GET", `/api/fs?path=${encodeURIComponent(path)}`);
    browsing = res.path;
    $("#path").value = res.path;
    $("#up").disabled = !res.parent;
    $("#up").onclick = () => res.parent && browse(res.parent);
    const ul = $("#dirs");
    ul.innerHTML = "";
    if (!res.dirs.length) ul.innerHTML = `<li class="none">No folders inside</li>`;
    for (const d of res.dirs) {
      const li = el("li");
      li.innerHTML = `<svg viewBox="0 0 16 16"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/></svg><span></span>`;
      li.querySelector("span").textContent = d;
      li.onclick = () => browse(`${res.path}/${d}`);
      ul.append(li);
    }
  } catch (err) {
    alert(err.message);
  }
}
$("#go").onclick = () => browse($("#path").value);
$("#path").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    browse($("#path").value);
  }
});
$("#pick").onclick = () => pickFolder(browsing);

async function pickFolder(folder) {
  $("#folder-dialog").close();
  try {
    if (current) current = await api("PATCH", `/api/threads/${current.id}`, { folder });
    else draft.folder = folder;
    renderAll();
    if (current) loadState();
    $("#input").focus();
  } catch (err) {
    alert(err.message);
  }
}

// ---------- boot ----------

function el(tag, cls) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  return n;
}

(async () => {
  await loadState();
  const id = location.hash.slice(1);
  if (id && state.threads.some((t) => t.id === id)) openThread(id);
  else newChat();
})();
