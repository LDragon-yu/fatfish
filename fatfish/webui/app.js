const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const ADMIN_HEADER = { "X-Fatfish-Admin": "1" };
const ROUTES = ["chat", "overview", "users", "memory", "settings", "prompts", "voices"];
const appState = { botName: "茉莉" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmtBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast"; }, 2600);
}

async function api(path, options = {}) {
  const method = options.method || (options.form ? "POST" : "GET");
  const headers = {};
  if (!options.form) headers["Content-Type"] = "application/json";
  if (method !== "GET") Object.assign(headers, ADMIN_HEADER);
  const body = options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined);
  const response = await fetch(path, { method, headers, body });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : (typeof data === "string" ? data : response.statusText);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function fillUserSelect(select, preferredId) {
  const data = await api("/api/admin/users");
  const items = data.items || [];
  select.innerHTML = items
    .map((user) => {
      const name = user.display_name || "QQ " + user.platform_id;
      const role = user.role === "owner" ? " · owner" : "";
      const bot = user.bot_name ? ` · ${user.bot_name}` : "";
      return `<option value="${user.id}">${esc(name)}${esc(bot)}（${esc(user.status)}${role}）</option>`;
    })
    .join("");
  const chosen =
    items.find((user) => user.id === preferredId) ||
    items.find((user) => user.role === "owner") ||
    items[0] ||
    null;
  if (!chosen) return null;
  select.value = String(chosen.id);
  return chosen.id;
}

function openModal(html) {
  $("#modalBody").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modalClose").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (event) => {
  if (event.target === $("#modal")) closeModal();
});

/* ---------------- navigation ---------------- */

function navigate() {
  const raw = location.hash.replace(/^#\/?/, "") || "chat";
  const page = ROUTES.includes(raw) ? raw : "chat";
  $$(".page").forEach((node) => node.classList.toggle("active", node.id === `page-${page}`));
  $$("#nav a").forEach((node) => node.classList.toggle("active", node.dataset.page === page));
  if (page === "overview") loadOverview();
  if (page === "users") loadUsersPage();
  if (page === "memory") loadMemoryPage();
  if (page === "settings") loadSettings();
  if (page === "prompts") loadPrompts();
  if (page === "voices") loadVoices();
}
window.addEventListener("hashchange", navigate);

/* ---------------- health badge ---------------- */

async function updateHealthBadge() {
  try {
    const health = await api("/api/health");
    const badge = $("#modelBadge");
    badge.textContent = health.mock ? "mock 模型" : health.model;
    badge.className = `badge ${health.mock ? "warn" : "ok"}`;
    if (health.bot_name) {
      appState.botName = health.bot_name;
      const brand = $("#brandName");
      if (brand) brand.textContent = health.bot_name;
      document.title = `${health.bot_name} · 私聊陪伴`;
    }
  } catch {
    const badge = $("#modelBadge");
    badge.textContent = "core 未连接";
    badge.className = "badge danger";
  }
}

/* ---------------- chat ---------------- */

const chat = {
  ws: null,
  timer: null,
  userId: null,
  botNames: {},
  init() {
    this.messages = $("#chatMessages");
    this.input = $("#chatInput");
    this.form = $("#chatForm");
    this.status = $("#chatStatus");
    this.userSelect = $("#chatUser");
    this.userSelect.addEventListener("change", () => {
      const value = Number(this.userSelect.value);
      if (!value || value === this.userId) return;
      this.userId = value;
      this.messages.innerHTML = "";
      this.connect();
    });
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.send();
    });
    this.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this.send();
      }
    });
    $("#stateBtn").addEventListener("click", () => this.showState());
    this.loadUsers();
  },
  async loadUsers() {
    try {
      const data = await api("/api/admin/users");
      const active = (data.items || []).filter((user) => user.status === "active");
      this.botNames = Object.fromEntries(
        active.map((user) => [user.id, user.bot_name || ""])
      );
      this.userSelect.innerHTML = active
        .map((user) => {
          const label =
            (user.display_name || user.platform_id) +
            (user.role === "owner" ? "（owner）" : "");
          return `<option value="${user.id}">${esc(label)}</option>`;
        })
        .join("");
      const current = active.find((user) => user.id === this.userId);
      const fallback =
        active.find((user) => user.role === "owner") || active[0] || null;
      const chosen = current || fallback;
      if (chosen) {
        this.userId = chosen.id;
        this.userSelect.value = String(chosen.id);
      }
    } catch (error) {
      toast(error.message, true);
    }
    this.connect();
  },
  connect() {
    if (this.ws) {
      try { this.ws.close(); } catch { /* ignore */ }
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = this.userId ? `?user_id=${this.userId}` : "";
    this.ws = new WebSocket(`${proto}://${location.host}/ws${query}`);
    this.ws.addEventListener("open", () => this.setStatus(true));
    this.ws.addEventListener("close", () => {
      this.setStatus(false);
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.connect(), 3000);
    });
    this.ws.addEventListener("message", (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.type === "history") {
        this.messages.innerHTML = "";
        data.messages.forEach((row) => this.add(row.role, row.text, row.channel, row.ts, row.audio_url));
      } else if (data.type === "assistant") {
        const msg = data.message || {};
        this.add("assistant", msg.text || "", msg.kind === "voice" ? "voice" : "text", Date.now() / 1000, msg.audio_url);
      } else if (data.type === "notice") {
        this.notice(data.message || "");
      } else if (data.type === "error") {
        this.notice(data.message || "出错了", true);
      }
    });
  },
  async refreshNames() {
    try {
      const data = await api("/api/admin/users");
      this.botNames = Object.fromEntries(
        (data.items || [])
          .filter((user) => user.status === "active")
          .map((user) => [user.id, user.bot_name || ""])
      );
    } catch { /* names are cosmetic */ }
  },
  setStatus(ok) {
    this.status.className = `status-dot ${ok ? "ok" : "bad"}`;
    this.status.innerHTML = `<i></i>${ok ? "已连接" : "已断开"}`;
  },
  send() {
    const text = this.input.value.trim();
    if (!text || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "message", text, id: crypto.randomUUID?.() || String(Date.now()) }));
    this.input.value = "";
  },
  add(role, text, channel, ts, audioUrl) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}` + (channel === "voice" ? " voice" : "");
    const meta = document.createElement("span");
    meta.className = "meta";
    const who = role === "user" ? "你" : (this.botNames[this.userId] || appState.botName || "她");
    meta.textContent = who + (ts ? " · " + new Date(ts * 1000).toLocaleTimeString() : "");
    wrap.appendChild(meta);
    if (channel === "voice") {
      const tag = document.createElement("span");
      tag.className = "voice-tag";
      tag.textContent = "🎙 语音";
      wrap.appendChild(tag);
    }
    const body = document.createElement("span");
    body.textContent = text;
    wrap.appendChild(body);
    if (channel === "voice" && audioUrl) {
      const player = document.createElement("audio");
      player.className = "voice-player";
      player.controls = true;
      player.preload = "none";
      player.src = audioUrl;
      wrap.appendChild(player);
    }
    this.messages.appendChild(wrap);
    this.messages.scrollTop = this.messages.scrollHeight;
  },
  notice(text, isError = false) {
    const node = document.createElement("div");
    node.className = isError ? "error" : "notice";
    node.textContent = text;
    this.messages.appendChild(node);
    this.messages.scrollTop = this.messages.scrollHeight;
  },
  async showState() {
    try {
      const query = this.userId ? `?user_id=${this.userId}` : "";
      const data = await api(`/api/state${query}`);
      openModal(`<h2 style="margin-top:0">当前状态</h2><pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
    } catch (error) {
      toast(error.message, true);
    }
  },
};

/* ---------------- overview ---------------- */

async function loadOverview() {
  const root = $("#overviewContent");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    const data = await api("/api/admin/overview");
    const services = data.services;
    const onebot = services.onebot || {};
    const voice = services.tts_voice;
    root.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card">
          <h3>服务</h3>
          <div class="stat-line"><span>聊天模型</span><span>${esc(data.settings.model)}${data.settings.mock ? "（mock）" : ""}</span></div>
          <div class="stat-line"><span>视觉模型</span><span>${esc(data.settings.vision_model)}</span></div>
          <div class="stat-line"><span>TTS</span><span>${services.tts ? "可用" : "未配置"}</span></div>
          <div class="stat-line"><span>当前音色</span><span>${voice ? esc(`${voice.name}（${voice.provider}/${voice.mode}）`) : "无"}</span></div>
          <div class="stat-line"><span>ASR</span><span>${services.asr ? "可用" : "未配置"}</span></div>
        </div>
        <div class="stat-card">
          <h3>QQ / OneBot</h3>
          <div class="stat-line"><span>启用</span><span>${onebot.enabled ? "是" : "否"}</span></div>
          <div class="stat-line"><span>连接</span><span>${onebot.connected ? "已连接" : "未连接"}</span></div>
          <div class="stat-line"><span>登录小号</span><span>${esc(onebot.self_id || "—")}</span></div>
          <div class="stat-line"><span>主人 QQ</span><span>${esc(onebot.owner_qq || "—")}</span></div>
          <div class="stat-line"><span>活跃用户</span><span>${data.users ? data.users.active : "—"}</span></div>
          <div class="stat-line"><span>待批准</span><span>${data.users ? data.users.pending : "—"}</span></div>
        </div>
        <div class="stat-card">
          <h3>数据</h3>
          <div class="stat-line"><span>消息总数</span><span>${data.db.messages}</span></div>
          <div class="stat-line"><span>今日消息</span><span>${data.db.today}</span></div>
          <div class="stat-line"><span>数据库</span><span>${fmtBytes(data.db.bytes)}</span></div>
          <div class="stat-line"><span>媒体文件</span><span>${fmtBytes(data.media.bytes)}</span></div>
          <div class="stat-line"><span>待触发思考</span><span>${data.scheduler.pending_thoughts}</span></div>
        </div>
        <div class="stat-card">
          <h3>提示词与错误</h3>
          <div class="stat-line"><span>主动消息</span><span>${data.scheduler.proactive ? "开启" : "关闭"}</span></div>
          <div class="stat-line"><span>已自定义提示词</span><span>${data.prompts.customized.length} 项</span></div>
          <div class="stat-line"><span>最近错误</span><span>${esc(data.last_error || "无")}</span></div>
        </div>
      </div>
      <div class="card">
        <div class="row" style="justify-content:space-between;margin-bottom:10px">
          <h2 style="margin:0">最近日志</h2>
          <button class="btn small" id="overviewLogRefresh" type="button">刷新日志</button>
        </div>
        <div class="log-box" id="overviewLogs">${esc((data.logs || []).join("\n") || "（暂无日志）")}</div>
      </div>`;
    $("#overviewLogRefresh").addEventListener("click", async () => {
      try {
        const logs = await api("/api/admin/logs?lines=120");
        $("#overviewLogs").textContent = logs.text || "（暂无日志）";
      } catch (error) { toast(error.message, true); }
    });
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

/* ---------------- users ---------------- */

const USER_STATUS_LABEL = {
  active: ["已启用", "ok"],
  pending: ["待批准", "warn"],
  blocked: ["已禁用", "danger"],
};

async function loadUsersPage() {
  const root = $("#usersContent");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    const data = await api("/api/admin/users");
    renderUsersPage(data.items || []);
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

function renderUsersPage(items) {
  const root = $("#usersContent");
  const list = items.length
    ? items.map((user) => {
        const [statusLabel, statusClass] = USER_STATUS_LABEL[user.status] || [user.status, ""];
        const last = user.last_contact_at
          ? new Date(user.last_contact_at * 1000).toLocaleString()
          : "—";
        const badges = [
          `<span class="badge ${statusClass}">${statusLabel}</span>`,
          user.role === "owner" ? `<span class="badge accent">owner</span>` : "",
          `<span class="badge">QQ ${esc(user.platform_id)}</span>`,
        ].join("");
        const actions = [];
        if (user.status !== "active") {
          actions.push(`<button class="btn small primary" data-status="active" data-id="${user.id}" type="button">批准/启用</button>`);
        }
        if (user.status !== "blocked" && user.role !== "owner") {
          actions.push(`<button class="btn small danger" data-status="blocked" data-id="${user.id}" type="button">禁用</button>`);
        }
        if (user.status === "blocked" && user.role !== "owner") {
          actions.push(`<button class="btn small" data-status="pending" data-id="${user.id}" type="button">转为待批准</button>`);
        }
        return `
          <section class="card">
            <div class="voice-head">
              <div>
                <h2>${esc(user.display_name || "QQ " + user.platform_id)}</h2>
                <div class="voice-meta">${badges}</div>
                <p class="hint" style="margin-top:6px">
                  消息 ${user.message_count} 条 · 待触发 ${user.pending_thoughts} 条 · 最近联系 ${esc(last)}
                </p>
              </div>
              <div class="voice-actions">${actions.join("")}</div>
            </div>
          </section>`;
      }).join("")
    : `<div class="card"><p class="hint">还没有用户。未知 QQ 发来私聊后会自动出现在这里（pending）。</p></div>`;

  root.innerHTML = `
    <section class="card">
      <h2>添加用户</h2>
      <p class="hint">只有启用状态的 QQ 私聊才会被回复；未知 QQ 会先进入 pending，不会自动回复。</p>
      <form id="addUserForm">
        <div class="create-grid">
          <label class="field"><span>QQ 号</span><input name="platform_id" inputmode="numeric" placeholder="纯数字"></label>
          <label class="field"><span>备注</span><input name="display_name" placeholder="可选"></label>
        </div>
        <div class="row" style="margin-top:14px">
          <button class="btn primary" type="submit">添加并启用</button>
        </div>
      </form>
    </section>
    <div class="stack">${list}</div>
  `;

  $("#addUserForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const platformId = String(formData.get("platform_id") || "").trim();
    if (!/^\d+$/.test(platformId)) {
      toast("QQ 号必须是纯数字", true);
      return;
    }
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: {
          platform_id: platformId,
          display_name: String(formData.get("display_name") || ""),
          status: "active",
        },
      });
      toast("用户已添加");
      await loadUsersPage();
      await chat.loadUsers();
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  root.onclick = async (event) => {
    const button = event.target.closest("button[data-status]");
    if (!button) return;
    button.disabled = true;
    try {
      await api(`/api/admin/users/${button.dataset.id}/status`, {
        method: "POST",
        body: { status: button.dataset.status },
      });
      toast("已更新");
      await loadUsersPage();
      await chat.loadUsers();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  };
}

/* ---------------- settings ---------------- */

let settingsData = null;

async function loadSettings() {
  const root = $("#settingsContent");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    settingsData = await api("/api/admin/settings");
    renderSettings();
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

function inputForField(field) {
  const value = field.value;
  const data = `data-key="${esc(field.key)}" data-type="${esc(field.type)}"`;
  if (field.type === "secret") {
    const placeholder = field.has_value ? `已设置：${field.value}（留空不修改）` : "未设置（留空不修改）";
    return `<input type="password" autocomplete="new-password" ${data} placeholder="${esc(placeholder)}">`;
  }
  if (field.type === "bool") {
    return `<label class="switch"><input type="checkbox" ${data} ${value ? "checked" : ""} data-original="${value ? "1" : "0"}"><span>${esc(field.label)}</span></label>`;
  }
  if (field.type === "select") {
    const options = (field.options || []).map((opt) =>
      `<option value="${esc(opt)}" ${String(value) === String(opt) ? "selected" : ""}>${esc(opt)}</option>`
    ).join("");
    return `<select ${data} data-original="${esc(value)}">${options}</select>`;
  }
  if (field.type === "int" || field.type === "float") {
    const step = field.step ?? (field.type === "int" ? 1 : 0.05);
    return `<input type="number" ${data} value="${esc(value)}" min="${field.min ?? ""}" max="${field.max ?? ""}" step="${step}" data-original="${esc(value)}">`;
  }
  const listId = field.suggestions ? `list-${esc(field.key)}` : "";
  const list = field.suggestions
    ? `<datalist id="${listId}">${field.suggestions.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>`
    : "";
  return `<input type="text" ${data} value="${esc(value)}" ${listId ? `list="${listId}"` : ""} data-original="${esc(value)}">${list}`;
}

function renderSettings() {
  const root = $("#settingsContent");
  root.innerHTML = "";
  for (const group of settingsData.groups) {
    const card = document.createElement("section");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = group.name;
    card.appendChild(title);
    const grid = document.createElement("div");
    grid.className = "settings-grid";
    for (const field of group.fields) {
      const wrap = document.createElement("div");
      wrap.className = `field${field.from_overlay ? " is-overlay" : ""}`;
      if (field.type !== "bool") {
        const label = document.createElement("span");
        label.textContent = field.label + (field.restart ? "（重启生效）" : "");
        wrap.appendChild(label);
      }
      const holder = document.createElement("div");
      holder.innerHTML = inputForField(field);
      wrap.appendChild(holder.firstElementChild);
      if (field.help) {
        const help = document.createElement("div");
        help.className = "field-help";
        help.textContent = field.help;
        wrap.appendChild(help);
      }
      grid.appendChild(wrap);
    }
    card.appendChild(grid);
    root.appendChild(card);
  }
}

async function saveSettings() {
  const values = {};
  $$("[data-key]", $("#settingsContent")).forEach((input) => {
    const key = input.dataset.key;
    const type = input.dataset.type;
    if (type === "secret") {
      const value = input.value.trim();
      if (value) values[key] = value;
      return;
    }
    if (type === "bool") {
      const value = input.checked ? "1" : "0";
      if (value !== input.dataset.original) values[key] = value;
      return;
    }
    if (input.value !== input.dataset.original) values[key] = input.value;
  });
  if (!Object.keys(values).length) {
    toast("没有修改");
    return;
  }
  try {
    await api("/api/admin/settings", { method: "PUT", body: { values } });
    toast("已保存并生效");
    await loadSettings();
    updateHealthBadge();
  } catch (error) {
    toast(error.message, true);
  }
}
$("#settingsSave").addEventListener("click", saveSettings);

/* ---------------- prompts ---------------- */

let promptData = null;
let currentPrompt = "persona";
let promptUserId = null;

async function loadPrompts() {
  try {
    promptUserId = await fillUserSelect($("#promptUser"), promptUserId);
    if (!promptUserId) {
      toast("还没有用户", true);
      return;
    }
    promptData = await api(`/api/admin/prompts?user_id=${promptUserId}`);
    $("#promptBotName").value = promptData.bot_name || "";
    renderPromptList();
    const exists = promptData.items.some((item) => item.name === currentPrompt);
    selectPrompt(exists ? currentPrompt : promptData.items[0]?.name);
  } catch (error) {
    toast(error.message, true);
  }
}

$("#promptUser").addEventListener("change", () => {
  promptUserId = Number($("#promptUser").value) || null;
  loadPrompts();
});

$("#promptBotNameSave").addEventListener("click", async () => {
  if (!promptUserId) return;
  const botName = $("#promptBotName").value.trim();
  try {
    await api(`/api/admin/users/${promptUserId}/profile`, {
      method: "PUT",
      body: { bot_name: botName },
    });
    toast("角色名已保存");
    await chat.refreshNames();
    await loadPrompts();
  } catch (error) {
    toast(error.message, true);
  }
});

function renderPromptList() {
  const list = $("#promptList");
  list.innerHTML = "";
  for (const item of promptData.items) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.name = item.name;
    button.innerHTML = `${item.customized ? '<span class="dot"></span>' : ""}${esc(item.label)}`;
    if (item.name === currentPrompt) button.classList.add("active");
    button.addEventListener("click", () => selectPrompt(item.name));
    list.appendChild(button);
  }
}

function selectPrompt(name) {
  if (!promptData) return;
  const item = promptData.items.find((entry) => entry.name === name);
  if (!item) return;
  currentPrompt = name;
  $$("#promptList button").forEach((button) => button.classList.toggle("active", button.dataset.name === name));
  $("#promptTitle").textContent = item.label;
  $("#promptBadge").textContent = item.customized ? "已自定义" : "使用默认";
  $("#promptBadge").className = `badge ${item.customized ? "accent" : ""}`;
  const placeholders = item.placeholders || [];
  $("#promptPlaceholders").textContent = placeholders.length
    ? `可用占位符：${placeholders.join(" ")}`
    : "该提示词没有占位符";
  $("#promptText").value = item.text;
}

$("#promptSave").addEventListener("click", async () => {
  const text = $("#promptText").value;
  if (!text.trim()) { toast("提示词不能为空", true); return; }
  try {
    await api(`/api/admin/prompts/${currentPrompt}?user_id=${promptUserId}`, { method: "PUT", body: { text } });
    toast("提示词已保存，下一轮生效");
    await loadPrompts();
  } catch (error) { toast(error.message, true); }
});

$("#promptReset").addEventListener("click", async () => {
  if (!confirm("恢复为默认提示词？当前内容会存入历史版本。")) return;
  try {
    await api(`/api/admin/prompts/${currentPrompt}/reset?user_id=${promptUserId}`, { method: "POST", body: {} });
    toast("已恢复默认");
    await loadPrompts();
  } catch (error) { toast(error.message, true); }
});

$("#promptHistoryBtn").addEventListener("click", async () => {
  try {
    const data = await api(`/api/admin/prompts/${currentPrompt}/history?user_id=${promptUserId}`);
    if (!data.items.length) { openModal(`<p class="hint">暂无历史版本</p>`); return; }
    const rows = data.items.map((item) => `
      <div class="history-item">
        <div><strong>${esc(item.modified)}</strong><div class="hint">${esc(item.preview || "（空）")}</div></div>
        <button class="btn small" data-file="${esc(item.file)}">恢复</button>
      </div>`).join("");
    openModal(`<h2 style="margin-top:0">历史版本</h2>${rows}`);
    $$("#modalBody button[data-file]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/admin/prompts/${currentPrompt}/restore?user_id=${promptUserId}`, {
            method: "POST", body: { file: button.dataset.file },
          });
          closeModal();
          toast("已恢复历史版本");
          await loadPrompts();
        } catch (error) { toast(error.message, true); }
      });
    });
  } catch (error) { toast(error.message, true); }
});

$("#promptPreview").addEventListener("click", async () => {
  try {
    const data = await api(`/api/admin/system-prompt?user_id=${promptUserId}`);
    openModal(`<h2 style="margin-top:0">当前 system prompt</h2><pre>${esc(data.text)}</pre>`);
  } catch (error) { toast(error.message, true); }
});

/* ---------------- memory ---------------- */

const MOOD_LABELS = {
  joy: "高兴", anger: "生气", sad: "委屈", shy: "害羞", lonely: "孤独",
  bored: "无聊", jealous: "吃醋", proud: "得意", worry: "担心",
  annoyed: "烦躁", arousal: "兴致", energy: "精力",
};
const REL_LABELS = {
  intimacy: "亲密", trust: "信任", familiar: "熟悉",
  interest: "兴趣", reliance: "依赖", grudge: "不满",
};
const FACT_KIND_LABELS = {
  fact: "事实", preference: "偏好", event: "事件", impression: "印象",
};
const TOPIC_STATUS_LABELS = { open: "进行中", waiting: "等待", closed: "已结束" };
const THOUGHT_STATUS_LABELS = {
  pending: "待触发", processing: "处理中", done: "已完成", cancelled: "已取消",
};

let memoryCache = { userId: 0, facts: [], tags: [], topics: [], thoughts: [] };

function memoryCurrentUserId() {
  return Number($("#memoryUser").value || 0);
}

function formatTs(ts) {
  return ts ? new Date(Number(ts) * 1000).toLocaleString() : "—";
}

function datetimeLocalValue(ts) {
  if (!ts) return "";
  const date = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function loadMemoryPage() {
  const root = $("#memoryContent");
  const select = $("#memoryUser");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    const data = await api("/api/admin/users");
    const usersList = data.items || [];
    if (!usersList.length) {
      root.innerHTML = `<div class="card"><p class="hint">还没有用户</p></div>`;
      return;
    }
    const previous = Number(select.value || 0);
    select.innerHTML = usersList
      .map((user) => `<option value="${user.id}">${esc(user.display_name || user.platform_id)} · ${esc(user.status)}</option>`)
      .join("");
    const chosen =
      usersList.find((user) => user.id === previous) ||
      usersList.find((user) => user.role === "owner") ||
      usersList[0];
    select.value = String(chosen.id);
    select.onchange = () => loadMemoryData(Number(select.value));
    await loadMemoryData(chosen.id);
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

async function loadMemoryData(userId) {
  const root = $("#memoryContent");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    const data = await api(`/api/admin/users/${userId}/memory`);
    memoryCache = {
      userId,
      facts: data.facts || [],
      tags: data.tags || [],
      topics: data.topics || [],
      thoughts: data.thoughts || [],
    };
    renderMemory(userId, data);
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

function stateInputs(values, labels, kind) {
  return Object.entries(labels)
    .map(([key, label]) => `
      <label class="field">
        <span>${esc(label)}</span>
        <input type="number" min="0" max="100" step="1"
          data-${kind}="${key}" value="${Math.round(Number(values[key] ?? 0))}">
      </label>`)
    .join("");
}

function renderMemory(userId, data) {
  const root = $("#memoryContent");
  const facts = data.facts || [];
  const tags = data.tags || [];
  const topics = data.topics || [];
  const thoughts = data.thoughts || [];
  const behavior = data.behavior || {};

  const factsHtml = facts.length
    ? facts.map((fact) => `
      <div class="memory-item">
        <div class="memory-item-head">
          <div>
            <span class="badge accent">${esc(FACT_KIND_LABELS[fact.kind] || fact.kind)}</span>
            ${fact.topic ? `<span class="badge">${esc(fact.topic)}</span>` : ""}
            <span class="badge">重要度 ${fact.importance}</span>
            ${fact.active ? "" : `<span class="badge danger">已停用</span>`}
          </div>
          <div class="memory-actions">
            <button class="btn small" data-fact-edit="${fact.id}" type="button">编辑</button>
            <button class="btn small" data-fact-toggle="${fact.id}" type="button">${fact.active ? "停用" : "启用"}</button>
            <button class="btn small danger" data-fact-delete="${fact.id}" type="button">删除</button>
          </div>
        </div>
        <div class="memory-text">${esc(fact.content)}</div>
      </div>`).join("")
    : `<p class="hint">还没有长期事实</p>`;

  const tagsHtml = tags.length
    ? tags.map((tag) => `
      <div class="memory-item">
        <div class="memory-item-head">
          <div>
            <span class="badge accent">${esc(tag.tag)}</span>
            <span class="badge">${esc(tag.value)}</span>
            <span class="badge">置信度 ${Number(tag.confidence).toFixed(2)}</span>
          </div>
          <div class="memory-actions">
            <button class="btn small" data-tag-edit="${esc(tag.tag)}" type="button">编辑</button>
            <button class="btn small danger" data-tag-delete="${esc(tag.tag)}" type="button">删除</button>
          </div>
        </div>
        ${tag.evidence ? `<div class="hint">证据：${esc(tag.evidence)}</div>` : ""}
      </div>`).join("")
    : `<p class="hint">还没有标签</p>`;

  const topicsHtml = topics.length
    ? topics.map((topic) => `
      <div class="memory-item">
        <div class="memory-item-head">
          <div>
            <span class="badge accent">${esc(TOPIC_STATUS_LABELS[topic.status] || topic.status)}</span>
            <span class="badge">${esc(topic.name)}</span>
            <span class="badge">重要度 ${topic.importance}</span>
            ${topic.next_check_at ? `<span class="badge">下次 ${esc(formatTs(topic.next_check_at))}</span>` : ""}
          </div>
          <div class="memory-actions">
            <button class="btn small" data-topic-edit="${esc(topic.name)}" type="button">编辑</button>
            <button class="btn small danger" data-topic-delete="${esc(topic.name)}" type="button">删除</button>
          </div>
        </div>
      </div>`).join("")
    : `<p class="hint">还没有话题</p>`;

  const thoughtsHtml = thoughts.length
    ? thoughts.map((thought) => `
      <div class="memory-item">
        <div class="memory-item-head">
          <div>
            <span class="badge ${thought.status === "pending" ? "accent" : ""}">${esc(THOUGHT_STATUS_LABELS[thought.status] || thought.status)}</span>
            <span class="badge">${esc(formatTs(thought.due_at))}</span>
            ${thought.topic ? `<span class="badge">${esc(thought.topic)}</span>` : ""}
          </div>
          <div class="memory-actions">
            ${thought.status === "pending" ? `<button class="btn small" data-thought-delay="${thought.id}" type="button">延后 30 分</button>` : ""}
            ${thought.status === "pending" ? `<button class="btn small" data-thought-cancel="${thought.id}" type="button">取消</button>` : ""}
            <button class="btn small danger" data-thought-delete="${thought.id}" type="button">删除</button>
          </div>
        </div>
        <div class="memory-text">${esc(thought.reason)}</div>
      </div>`).join("")
    : `<p class="hint">没有待触发</p>`;

  root.innerHTML = `
    <section class="card">
      <div class="row" style="justify-content:space-between">
        <div>
          <h2>关系与情绪</h2>
          <p class="hint">用户 {{name}} · 消息 {{messages}} · 助手轮次 {{turns}} · 主动 {{proactive}}</p>
        </div>
        <div class="row">
          <button class="btn small" id="memoryResetState" type="button">重置</button>
          <button class="btn small primary" id="memorySaveState" type="button">保存状态</button>
        </div>
      </div>
      <div class="memory-grid">
        ${stateInputs(data.mood || {}, MOOD_LABELS, "mood")}
        ${stateInputs(data.relationship || {}, REL_LABELS, "rel")}
      </div>
    </section>

    <section class="card">
      <div class="row" style="justify-content:space-between">
        <h2>长期事实</h2>
        <button class="btn small" id="memoryAddFact" type="button">添加事实</button>
      </div>
      <div class="memory-list">${factsHtml}</div>
    </section>

    <section class="card">
      <div class="row" style="justify-content:space-between">
        <h2>标签</h2>
        <button class="btn small" id="memoryAddTag" type="button">添加标签</button>
      </div>
      <div class="memory-list">${tagsHtml}</div>
    </section>

    <section class="card">
      <div class="row" style="justify-content:space-between">
        <h2>话题</h2>
        <button class="btn small" id="memoryAddTopic" type="button">添加话题</button>
      </div>
      <div class="memory-list">${topicsHtml}</div>
    </section>

    <section class="card">
      <div class="row" style="justify-content:space-between">
        <h2>待触发</h2>
        <button class="btn small" id="memoryAddThought" type="button">添加待触发</button>
      </div>
      <div class="memory-list">${thoughtsHtml}</div>
    </section>

    <section class="card">
      <div class="row" style="justify-content:space-between">
        <h2>聊天记录</h2>
        <span class="hint">删除不可恢复</span>
      </div>
      <div class="search-row">
        <input type="text" id="memoryMessageSearch" placeholder="搜索关键词，留空看最近 50 条">
        <button class="btn small" id="memoryMessageSearchBtn" type="button">搜索</button>
      </div>
      <div id="memoryMessages" class="memory-list"></div>
    </section>
  `;

  const user = data.user || {};
  root.querySelector("section .hint").textContent =
    `${user.display_name || user.platform_id || "用户"} · 消息 ${behavior.user_messages || 0} · 助手轮次 ${behavior.assistant_turns || 0} · 主动 ${behavior.proactive_turns || 0}`;

  $("#memorySaveState").addEventListener("click", () => saveMemoryState(userId));
  $("#memoryResetState").addEventListener("click", async () => {
    if (!confirm("重置这个用户的情绪和关系？")) return;
    try {
      await api(`/api/admin/users/${userId}/state/reset`, { method: "POST", body: {} });
      toast("已重置");
      await loadMemoryData(userId);
    } catch (error) { toast(error.message, true); }
  });

  $("#memoryAddFact").addEventListener("click", () => openFactModal(userId, null));
  $("#memoryAddTag").addEventListener("click", () => openTagModal(userId, null));
  $("#memoryAddTopic").addEventListener("click", () => openTopicModal(userId, null));
  $("#memoryAddThought").addEventListener("click", () => openThoughtModal(userId));

  $("#memoryMessageSearchBtn").addEventListener("click", () =>
    loadMemoryMessages(userId, $("#memoryMessageSearch").value.trim())
  );
  $("#memoryMessageSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadMemoryMessages(userId, $("#memoryMessageSearch").value.trim());
    }
  });

  renderMemoryMessages(userId, data.messages || []);
  bindMemoryActions(userId);
}

function renderMemoryMessages(userId, rows) {
  const box = $("#memoryMessages");
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = `<p class="hint">没有消息</p>`;
    return;
  }
  box.innerHTML = rows.map((row) => `
    <div class="message-row">
      <span class="message-role">${row.role === "assistant" ? esc(appState.botName || "她") : "用户"}</span>
      <div class="message-body">
        <div class="hint">${esc(formatTs(row.ts))} · ${esc(row.channel || "text")}</div>
        ${esc(row.text)}
      </div>
      <button class="btn small danger" data-message-delete="${row.id}" type="button">删除</button>
    </div>`).join("");
}

async function loadMemoryMessages(userId, query) {
  try {
    const suffix = query ? `&query=${encodeURIComponent(query)}` : "";
    const data = await api(`/api/admin/users/${userId}/messages?limit=50${suffix}`);
    renderMemoryMessages(userId, data.items || []);
  } catch (error) { toast(error.message, true); }
}

async function saveMemoryState(userId) {
  const mood = {};
  const relationship = {};
  $$("[data-mood]", $("#memoryContent")).forEach((input) => {
    mood[input.dataset.mood] = Number(input.value);
  });
  $$("[data-rel]", $("#memoryContent")).forEach((input) => {
    relationship[input.dataset.rel] = Number(input.value);
  });
  try {
    await api(`/api/admin/users/${userId}/state`, {
      method: "PUT", body: { mood, relationship },
    });
    toast("状态已保存");
    await loadMemoryData(userId);
  } catch (error) { toast(error.message, true); }
}

function bindMemoryActions(userId) {
  const root = $("#memoryContent");
  root.onclick = async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.factEdit) {
      const fact = memoryCache.facts.find((item) => String(item.id) === button.dataset.factEdit);
      if (fact) openFactModal(userId, fact);
      return;
    }
    if (button.dataset.factToggle) {
      const fact = memoryCache.facts.find((item) => String(item.id) === button.dataset.factToggle);
      if (!fact) return;
      try {
        await api(`/api/admin/users/${userId}/facts/${fact.id}`, {
          method: "PUT", body: { active: !fact.active },
        });
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.factDelete) {
      if (!confirm("删除这条事实？")) return;
      try {
        await api(`/api/admin/users/${userId}/facts/${button.dataset.factDelete}`, { method: "DELETE" });
        toast("已删除");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.tagEdit) {
      const tag = memoryCache.tags.find((item) => item.tag === button.dataset.tagEdit);
      if (tag) openTagModal(userId, tag);
      return;
    }
    if (button.dataset.tagDelete) {
      if (!confirm("删除这个标签？")) return;
      try {
        await api(`/api/admin/users/${userId}/tags?tag=${encodeURIComponent(button.dataset.tagDelete)}`, { method: "DELETE" });
        toast("已删除");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.topicEdit) {
      const topic = memoryCache.topics.find((item) => item.name === button.dataset.topicEdit);
      if (topic) openTopicModal(userId, topic);
      return;
    }
    if (button.dataset.topicDelete) {
      if (!confirm("删除这个话题？")) return;
      try {
        await api(`/api/admin/users/${userId}/topics?name=${encodeURIComponent(button.dataset.topicDelete)}`, { method: "DELETE" });
        toast("已删除");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.thoughtDelay) {
      try {
        await api(`/api/admin/users/${userId}/thoughts/${button.dataset.thoughtDelay}`, {
          method: "PUT", body: { in_minutes: 30 },
        });
        toast("已延后 30 分钟");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.thoughtCancel) {
      try {
        await api(`/api/admin/users/${userId}/thoughts/${button.dataset.thoughtCancel}`, {
          method: "PUT", body: { status: "cancelled" },
        });
        toast("已取消");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.thoughtDelete) {
      if (!confirm("删除这条待触发？")) return;
      try {
        await api(`/api/admin/users/${userId}/thoughts/${button.dataset.thoughtDelete}`, { method: "DELETE" });
        toast("已删除");
        await loadMemoryData(userId);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (button.dataset.messageDelete) {
      if (!confirm("删除这条聊天记录？不可恢复。")) return;
      try {
        await api(`/api/admin/users/${userId}/messages/${button.dataset.messageDelete}`, { method: "DELETE" });
        toast("已删除");
        await loadMemoryMessages(userId, $("#memoryMessageSearch").value.trim());
      } catch (error) { toast(error.message, true); }
      return;
    }
  };
}

function openFactModal(userId, fact) {
  const editing = Boolean(fact);
  openModal(`
    <h2 style="margin-top:0">${editing ? "编辑事实" : "添加事实"}</h2>
    <form id="memoryFactForm">
      <div class="create-grid">
        <label class="field"><span>类型</span>
          <select name="kind">${Object.entries(FACT_KIND_LABELS).map(([key, label]) => `<option value="${key}" ${fact && fact.kind === key ? "selected" : ""}>${label}</option>`).join("")}</select>
        </label>
        <label class="field"><span>重要度</span>
          <input type="number" name="importance" min="1" max="5" value="${fact ? fact.importance : 3}">
        </label>
        <label class="field full"><span>话题</span>
          <input type="text" name="topic" value="${fact ? esc(fact.topic) : ""}" placeholder="可选">
        </label>
        <label class="field full"><span>内容</span>
          <textarea name="content" rows="4">${fact ? esc(fact.content) : ""}</textarea>
        </label>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">保存</button></div>
    </form>
  `);
  $("#memoryFactForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = {
      kind: form.get("kind"),
      importance: Number(form.get("importance") || 3),
      topic: form.get("topic") || "",
      content: form.get("content") || "",
    };
    try {
      if (editing) {
        await api(`/api/admin/users/${userId}/facts/${fact.id}`, { method: "PUT", body });
      } else {
        await api(`/api/admin/users/${userId}/facts`, { method: "POST", body });
      }
      closeModal();
      toast("已保存");
      await loadMemoryData(userId);
    } catch (error) { toast(error.message, true); }
  });
}

function openTagModal(userId, tag) {
  const editing = Boolean(tag);
  openModal(`
    <h2 style="margin-top:0">${editing ? "编辑标签" : "添加标签"}</h2>
    <form id="memoryTagForm">
      <div class="create-grid">
        <label class="field"><span>标签</span>
          <input type="text" name="tag" value="${tag ? esc(tag.tag) : ""}" ${editing ? "readonly" : ""}>
        </label>
        <label class="field"><span>值</span>
          <input type="text" name="value" value="${tag ? esc(tag.value) : ""}">
        </label>
        <label class="field"><span>置信度 0–1</span>
          <input type="number" name="confidence" min="0" max="1" step="0.05" value="${tag ? tag.confidence : 0.6}">
        </label>
        <label class="field full"><span>证据</span>
          <input type="text" name="evidence" value="${tag ? esc(tag.evidence) : ""}">
        </label>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">保存</button></div>
    </form>
  `);
  $("#memoryTagForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/api/admin/users/${userId}/tags`, {
        method: "PUT",
        body: {
          tag: form.get("tag"),
          value: form.get("value"),
          confidence: Number(form.get("confidence") || 0.5),
          evidence: form.get("evidence") || "",
        },
      });
      closeModal();
      toast("已保存");
      await loadMemoryData(userId);
    } catch (error) { toast(error.message, true); }
  });
}

function openTopicModal(userId, topic) {
  const editing = Boolean(topic);
  openModal(`
    <h2 style="margin-top:0">${editing ? "编辑话题" : "添加话题"}</h2>
    <form id="memoryTopicForm">
      <div class="create-grid">
        <label class="field"><span>话题名</span>
          <input type="text" name="name" value="${topic ? esc(topic.name) : ""}" ${editing ? "readonly" : ""}>
        </label>
        <label class="field"><span>状态</span>
          <select name="status">${Object.entries(TOPIC_STATUS_LABELS).map(([key, label]) => `<option value="${key}" ${topic && topic.status === key ? "selected" : ""}>${label}</option>`).join("")}</select>
        </label>
        <label class="field"><span>重要度</span>
          <input type="number" name="importance" min="1" max="5" value="${topic ? topic.importance : 3}">
        </label>
        <label class="field"><span>下次检查（可选）</span>
          <input type="datetime-local" name="next_check" value="${topic ? datetimeLocalValue(topic.next_check_at) : ""}">
        </label>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">保存</button></div>
    </form>
  `);
  $("#memoryTopicForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = String(form.get("next_check") || "");
    const body = {
      name: form.get("name"),
      status: form.get("status"),
      importance: Number(form.get("importance") || 3),
      next_check_at: next ? new Date(next).getTime() / 1000 : null,
    };
    try {
      if (editing) {
        await api(`/api/admin/users/${userId}/topics`, { method: "PUT", body });
      } else {
        await api(`/api/admin/users/${userId}/topics`, { method: "PUT", body });
      }
      closeModal();
      toast("已保存");
      await loadMemoryData(userId);
    } catch (error) { toast(error.message, true); }
  });
}

function openThoughtModal(userId) {
  openModal(`
    <h2 style="margin-top:0">添加待触发</h2>
    <form id="memoryThoughtForm">
      <div class="create-grid">
        <label class="field"><span>多少分钟后</span>
          <input type="number" name="in_minutes" min="1" value="30">
        </label>
        <label class="field"><span>话题</span>
          <input type="text" name="topic" placeholder="可选">
        </label>
        <label class="field full"><span>原因</span>
          <input type="text" name="reason" value="主动想想">
        </label>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">添加</button></div>
    </form>
  `);
  $("#memoryThoughtForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/api/admin/users/${userId}/thoughts`, {
        method: "POST",
        body: {
          in_minutes: Number(form.get("in_minutes") || 30),
          topic: form.get("topic") || "",
          reason: form.get("reason") || "主动想想",
        },
      });
      closeModal();
      toast("已添加");
      await loadMemoryData(userId);
    } catch (error) { toast(error.message, true); }
  });
}

/* ---------------- voices ---------------- */

let voiceData = null;
let voiceUserId = null;

async function loadVoices() {
  const root = $("#voicesContent");
  root.innerHTML = `<div class="card"><p class="hint">加载中…</p></div>`;
  try {
    voiceUserId = await fillUserSelect($("#voiceUser"), voiceUserId);
    if (!voiceUserId) {
      root.innerHTML = `<div class="card"><p class="hint">还没有用户</p></div>`;
      return;
    }
    voiceData = await api(`/api/admin/voices?user_id=${voiceUserId}`);
    renderVoices();
  } catch (error) {
    root.innerHTML = `<div class="card"><p class="error">${esc(error.message)}</p></div>`;
  }
}

$("#voiceUser").addEventListener("change", () => {
  voiceUserId = Number($("#voiceUser").value) || null;
  loadVoices();
});

function renderVoices() {
  const root = $("#voicesContent");
  const activeId = voiceData.active_id;
  const defaults = voiceData.defaults || {};
  root.innerHTML = `
    <section class="card">
      <h2>新建音色档案</h2>
      <p class="hint">SiliconFlow 支持 references（参考音频 + 逐字稿）和 upload（先上传拿 voice URI）。MiMo 为预设音色，Breeze 会先克隆并保存 voice_id。</p>
      <form id="voiceForm">
        <div class="create-grid">
          <label class="field"><span>名称</span><input type="text" name="name" placeholder="例如：简单音色"></label>
          <label class="field"><span>供应商</span>
            <select name="provider" id="voiceProvider">
              <option value="siliconflow">SiliconFlow</option>
              <option value="breeze">Breeze Blue</option>
              <option value="mimo">MiMo</option>
            </select>
          </label>
          <label class="field" data-sf><span>模式</span>
            <select name="mode" id="voiceMode">
              <option value="references">references（带参考音频）</option>
              <option value="upload">upload（上传音色 URI）</option>
            </select>
          </label>
          <label class="field" data-sf><span>模型</span><input type="text" name="model" value="${esc(defaults.siliconflow_model || "")}"></label>
          <label class="field" data-sf><span>语速</span><input type="number" name="speed" value="0.8" min="0.5" max="1.5" step="0.05"></label>
          <label class="field" data-mimo style="display:none"><span>MiMo 音色名</span><input type="text" name="voice_name" value="${esc(defaults.mimo_voice || "茉莉")}"></label>
          <p class="field-help full" data-breeze style="display:none">Breeze Blue 需要先在设置页填 API key；参考音频 3 秒以上、不超过 5 MB，会立即克隆并保存成一个音色档案。</p>
          <label class="field full" data-audio><span>参考音频（mp3/wav/m4a/flac/ogg；SiliconFlow ≤30 MB，Breeze ≤5 MB）</span><input type="file" name="audio" accept="audio/*"></label>
          <label class="field full" data-sf><span>参考音频逐字稿</span><textarea name="transcript" rows="3" placeholder="references 模式必填；upload 模式上传到 SiliconFlow 时也需要"></textarea></label>
        </div>
        <div class="row" style="margin-top:14px">
          <button class="btn primary" type="submit">创建音色</button>
        </div>
      </form>
    </section>
    <div class="stack" id="voiceList"></div>
  `;
  const providerSelect = $("#voiceProvider");
  const toggle = () => {
    const provider = providerSelect.value;
    const isMimo = provider === "mimo";
    const isBreeze = provider === "breeze";
    $$("[data-sf]", root).forEach((node) => { node.style.display = isMimo || isBreeze ? "none" : ""; });
    $$("[data-mimo]", root).forEach((node) => { node.style.display = isMimo ? "" : "none"; });
    $$("[data-breeze]", root).forEach((node) => { node.style.display = isBreeze ? "" : "none"; });
    $$("[data-audio]", root).forEach((node) => { node.style.display = isMimo ? "none" : ""; });
  };
  providerSelect.addEventListener("change", toggle);
  toggle();
  $("#voiceForm").addEventListener("submit", createVoice);

  const list = $("#voiceList");
  if (!voiceData.items.length) {
    list.innerHTML = `<div class="card"><p class="hint">还没有音色档案</p></div>`;
    return;
  }
  for (const profile of voiceData.items) {
    const card = document.createElement("section");
    card.className = "card voice-card";
    const tags = [];
    if (profile.id === activeId) tags.push(`<span class="badge ok">当前音色</span>`);
    if (profile.archived) tags.push(`<span class="badge">已归档</span>`);
    tags.push(`<span class="badge accent">${esc(profile.provider)} / ${esc(profile.mode)}</span>`);
    card.innerHTML = `
      <div class="voice-head">
        <div>
          <h2>${esc(profile.name)}</h2>
          <div class="voice-meta">
            ${tags.join("")}
            <span class="hint">模型：${esc(profile.model || "—")} · 语速：${esc(profile.speed)}</span>
          </div>
        </div>
      </div>
      <div class="voice-preview">
        <input type="text" value="你好呀，今天过得怎么样？" data-preview-text="${profile.id}">
        <button class="btn small" data-preview="${profile.id}" type="button">试听</button>
        ${profile.has_reference ? `<button class="btn small ghost" data-ref="${profile.id}" type="button">参考音频</button>` : ""}
      </div>
      <div class="voice-actions">
        ${!profile.archived && profile.id !== activeId ? `<button class="btn small primary" data-activate="${profile.id}" type="button">设为当前</button>` : ""}
        ${profile.provider === "siliconflow" && profile.has_reference && !profile.archived ? `<button class="btn small" data-upload="${profile.id}" type="button">上传到 SiliconFlow</button>` : ""}
        ${!profile.archived ? `<button class="btn small" data-archive="${profile.id}" type="button">归档</button>` : ""}
        ${profile.id !== activeId ? `<button class="btn small danger" data-delete="${profile.id}" type="button">删除</button>` : ""}
      </div>
      <div class="voice-meta">
        ${profile.voice_uri ? `<span class="hint">voice URI：${esc(profile.voice_uri.slice(0, 26))}…</span>` : ""}
        ${profile.reference_text ? `<span class="hint">逐字稿：${esc(profile.reference_text.slice(0, 40))}…</span>` : ""}
      </div>
      <audio class="voice-player" data-player="${profile.id}" controls preload="none" style="display:none"></audio>
    `;
    list.appendChild(card);
  }

  list.addEventListener("click", async (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    try {
      if (target.dataset.preview) {
        const id = target.dataset.preview;
        const text = $(`[data-preview-text="${id}"]`).value;
        target.disabled = true;
        const data = await api(`/api/admin/voices/${id}/preview?user_id=${voiceUserId}`, { method: "POST", body: { text } });
        const player = $(`[data-player="${id}"]`);
        player.style.display = "block";
        player.src = data.audio_url;
        await player.play();
      } else if (target.dataset.ref) {
        const id = target.dataset.ref;
        const player = $(`[data-player="${id}"]`);
        player.style.display = "block";
        player.src = `/api/admin/voices/${id}/reference?user_id=${voiceUserId}`;
        await player.play();
      } else if (target.dataset.activate) {
        await api(`/api/admin/voices/${target.dataset.activate}/activate?user_id=${voiceUserId}`, { method: "POST", body: {} });
        toast("已切换当前音色");
        await loadVoices();
      } else if (target.dataset.archive) {
        if (!confirm("归档这个音色？文件会保留，可随时重新启用。")) return;
        await api(`/api/admin/voices/${target.dataset.archive}/archive?user_id=${voiceUserId}`, { method: "POST", body: {} });
        toast("已归档");
        await loadVoices();
      } else if (target.dataset.delete) {
        if (!confirm("删除这个音色档案？参考音频文件会保留，但档案无法恢复。")) return;
        await api(`/api/admin/voices/${target.dataset.delete}?user_id=${voiceUserId}`, { method: "DELETE" });
        toast("已删除");
        await loadVoices();
      } else if (target.dataset.upload) {
        target.disabled = true;
        toast("正在上传到 SiliconFlow…");
        await api(`/api/admin/voices/${target.dataset.upload}/upload-provider?user_id=${voiceUserId}`, { method: "POST", body: {} });
        toast("已上传并切换为 upload 模式");
        await loadVoices();
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      target.disabled = false;
    }
  });
}

async function createVoice(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  formData.delete("audio");
  if (voiceUserId) formData.set("user_id", String(voiceUserId));
  const fileInput = form.querySelector('input[type="file"]');
  if (fileInput && fileInput.files[0]) formData.append("audio", fileInput.files[0]);
  if ((formData.get("provider") || "siliconflow") === "mimo") {
    formData.delete("audio");
    formData.delete("transcript");
  } else if (formData.get("provider") === "breeze") {
    formData.delete("transcript");
  }
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await api("/api/admin/voices", { form: formData });
    toast("音色档案已创建");
    await loadVoices();
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

/* ---------------- boot ---------------- */

$("#overviewRefresh").addEventListener("click", loadOverview);
$("#usersRefresh").addEventListener("click", loadUsersPage);
$("#memoryRefresh").addEventListener("click", loadMemoryPage);
navigate();
updateHealthBadge();
setInterval(updateHealthBadge, 30000);
chat.init();
