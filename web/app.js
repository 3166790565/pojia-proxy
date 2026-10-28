/* 破甲路由中转站 前端 */
"use strict";

const POSITIONS = ["prepend", "append", "replace"];
const POSITION_LABEL = { prepend: "前置", append: "后置", replace: "替换" };

const state = {
  view: "settings",
  authed: false,
  presets: [],
  families: [],
  categories: [],
  expanded: {},
  matrixMode: false,
  page: 1,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 401) { showLogin(); throw new Error("未登录"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || j.error?.message || msg; } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showLogin() {
  state.authed = false;
  $("#login-view").classList.remove("hidden");
  $("#main-view").classList.add("hidden");
}

async function showMain() {
  state.authed = true;
  $("#login-view").classList.add("hidden");
  $("#main-view").classList.remove("hidden");
  await refreshBadge();
  switchView(state.view);
}

async function refreshBadge() {
  try {
    const s = await api("/admin/settings");
    const on = s.inject_enabled === "1";
    const badge = $("#inject-badge");
    badge.textContent = on ? "注入开" : "注入关";
    badge.className = "badge " + (on ? "on" : "off");
  } catch {}
}

/* ---------------- 视图切换 ---------------- */
const TITLES = {
  settings: "设置",
  packs: "破甲词包",
  bindings: "模型绑定",
  presets: "精细破甲词（模型 × 类别）",
  rules: "模型分类规则",
  keywords: "关键词 → 类别",
  logs: "请求日志",
};

async function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $("#view-title").textContent = TITLES[view] || view;
  $("#topbar-actions").innerHTML = "";
  const body = $("#view-body");
  body.innerHTML = `<div class="loading">加载中…</div>`;
  try {
    if (view === "settings") await renderSettings(body);
    else if (view === "packs") await renderPacks(body);
    else if (view === "bindings") await renderBindings(body);
    else if (view === "rules") await renderRules(body);
    else if (view === "keywords") await renderKeywords(body);
    else if (view === "presets") await renderPresets(body);
    else if (view === "logs") await renderLogs(body);
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${e.message}</div>`;
  }
}

/* ---------------- 设置 ---------------- */
async function renderSettings(body) {
  const s = await api("/admin/settings");
  body.innerHTML = `
    <div class="status-line" id="status-line"></div>
    <div class="form-grid">
      <label>上游地址</label><input id="s-upstream" value="${esc(s.upstream_base)}" placeholder="https://api.openai.com">
      <label>注入总开关</label><select id="s-inject"><option value="1" ${s.inject_enabled==="1"?"selected":""}>开启</option><option value="0" ${s.inject_enabled==="0"?"selected":""}>关闭</option></select>
      <label>路由模式</label>
      <div>
        <select id="s-routemode">
          <option value="fine" ${s.route_mode==="fine"?"selected":""}>精细路由（模型分类 + 关键词）</option>
          <option value="pack" ${s.route_mode==="pack"?"selected":""}>破甲词包（按模型绑定）</option>
        </select>
        <p class="hint">精细路由：按模型分类与消息关键词选 28 格预设。破甲词包：按模型绑定独立破甲词包（可叠加多个）。</p>
      </div>
      <label>注入位置</label>
      <div>
        <select id="s-position">${POSITIONS.map(p=>`<option value="${p}" ${s.inject_position===p?"selected":""}>${POSITION_LABEL[p]}</option>`).join("")}</select>
        <p class="hint">前置：破甲词在下游 system 之前；后置：之后；替换：只留破甲词。</p>
      </div>
      <label>请求超时(s)</label><input id="s-timeout" type="number" value="${esc(s.request_timeout)}">
      <label>请求体上限(MB)</label><input id="s-maxbody" type="number" value="${Math.round(parseInt(s.max_body_bytes||0)/1048576)}">
      <label>日志保留(天)</label><input id="s-retention" type="number" value="${esc(s.log_retention_days)}">
      <label>CORS 白名单</label><input id="s-cors" value="${esc(s.cors_allow)}" placeholder="逗号分隔 origin，留空=同源">
    </div>
    <div class="form-actions"><button class="btn primary" id="s-save">保存</button></div>
  `;
  $("#s-save").onclick = async () => {
    await api("/admin/settings", { method: "PUT", body: { values: {
      upstream_base: $("#s-upstream").value.trim(),
      inject_enabled: $("#s-inject").value,
      inject_position: $("#s-position").value,
      route_mode: $("#s-routemode").value,
      request_timeout: parseFloat($("#s-timeout").value) || 300,
      max_body_bytes: (parseInt($("#s-maxbody").value)||4)*1048576,
      log_retention_days: parseInt($("#s-retention").value)||7,
      cors_allow: $("#s-cors").value.trim(),
    }}});
    await refreshBadge();
    flash("已保存");
  };
}

/* ---------------- 模型规则 ---------------- */
async function renderRules(body) {
  const rules = await api("/admin/model_rules");
  body.innerHTML = `
    <div class="filter-bar">
      <input id="r-search" placeholder="搜索 family / pattern">
      <button class="btn small" id="r-add">新增规则</button>
    </div>
    <table><thead><tr><th>family</th><th>pattern</th><th>priority</th><th>enabled</th><th>操作</th></tr></thead>
    <tbody id="r-tbody">${rules.map(r=>rowRule(r)).join("")}</tbody></table>
  `;
  $("#r-search").oninput = e => {
    const q = e.target.value.toLowerCase();
    $$("#r-tbody tr").forEach(tr => tr.style.display = tr.textContent.toLowerCase().includes(q) ? "" : "none");
  };
  $("#r-add").onclick = () => openRuleDrawer(null);
  $$("#r-tbody .edit").forEach(b => b.onclick = () => openRuleDrawer(Number(b.dataset.id)));
  $$("#r-tbody .del").forEach(b => b.onclick = async () => {
    if (!confirm("删除该规则？")) return;
    await api(`/admin/model_rules/${b.dataset.id}`, { method: "DELETE" });
    renderRules(body);
  });
}

function rowRule(r) {
  return `<tr>
    <td>${esc(r.family)}</td><td><code>${esc(r.pattern)}</code></td>
    <td>${r.priority}</td>
    <td>${r.enabled ? '<span class="pill">启用</span>' : '<span class="pill disabled">禁用</span>'}</td>
    <td class="row-actions">
      <button class="btn small edit" data-id="${r.id}">编辑</button>
      <button class="btn small danger del" data-id="${r.id}">删除</button>
    </td></tr>`;
}

function openRuleDrawer(id) {
  const rules = [];
  // 重新从 DOM 拿当前行数据
  $$("#r-tbody tr").forEach(tr => {
    const tds = tr.querySelectorAll("td");
    if (tds.length) rules.push({ id: Number(tr.querySelector(".edit").dataset.id), family: tds[0].textContent, pattern: tds[1].textContent, priority: tds[2].textContent, enabled: tds[3].textContent.includes("启用") ? 1 : 0 });
  });
  const r = id ? rules.find(x => x.id === id) : { family: "", pattern: "", priority: 100, enabled: 1 };
  openDrawer(id ? "编辑规则" : "新增规则", `
    <div class="form-grid">
      <label>family</label><input id="d-family" value="${esc(r.family)}" placeholder="如 claude">
      <label>pattern</label><input id="d-pattern" value="${esc(r.pattern)}" placeholder="claude-* 或 re:^claude-">
      <label>priority</label><input id="d-priority" type="number" value="${r.priority}">
      <label>enabled</label><select id="d-enabled"><option value="1" ${r.enabled?"selected":""}>启用</option><option value="0" ${!r.enabled?"selected":""}>禁用</option></select>
    </div>
    <p class="hint">pattern 支持 glob（claude-*）或正则（前缀 re:）。数字越小优先级越高，取第一条命中。</p>
  `, async () => {
    const payload = { family: $("#d-family").value.trim(), pattern: $("#d-pattern").value.trim(), priority: parseInt($("#d-priority").value)||100, enabled: $("#d-enabled").value === "1" };
    if (id) await api(`/admin/model_rules/${id}`, { method: "PUT", body: payload });
    else await api("/admin/model_rules", { method: "POST", body: payload });
    closeDrawer();
    const body = $("#view-body");
    await renderRules(body);
  });
}

/* ---------------- 关键词 ---------------- */
async function renderKeywords(body) {
  const kws = await api("/admin/keywords");
  body.innerHTML = `
    <div class="filter-bar">
      <input id="k-search" placeholder="搜索 keyword">
      <button class="btn small" id="k-add">新增关键词</button>
    </div>
    <table><thead><tr><th>category</th><th>keyword</th><th>priority</th><th>enabled</th><th>操作</th></tr></thead>
    <tbody id="k-tbody">${kws.map(k=>rowKeyword(k)).join("")}</tbody></table>
  `;
  $("#k-search").oninput = e => {
    const q = e.target.value.toLowerCase();
    $$("#k-tbody tr").forEach(tr => tr.style.display = tr.textContent.toLowerCase().includes(q) ? "" : "none");
  };
  $("#k-add").onclick = () => openKeywordDrawer(null);
  $$("#k-tbody .edit").forEach(b => b.onclick = () => openKeywordDrawer(Number(b.dataset.id)));
  $$("#k-tbody .del").forEach(b => b.onclick = async () => {
    if (!confirm("删除该关键词？")) return;
    await api(`/admin/keywords/${b.dataset.id}`, { method: "DELETE" });
    renderKeywords(body);
  });
}

function rowKeyword(k) {
  return `<tr>
    <td>${esc(k.category)}</td><td>${esc(k.keyword)}</td>
    <td>${k.priority}</td>
    <td>${k.enabled ? '<span class="pill">启用</span>' : '<span class="pill disabled">禁用</span>'}</td>
    <td class="row-actions">
      <button class="btn small edit" data-id="${k.id}">编辑</button>
      <button class="btn small danger del" data-id="${k.id}">删除</button>
    </td></tr>`;
}

function openKeywordDrawer(id) {
  const kws = [];
  $$("#k-tbody tr").forEach(tr => {
    const tds = tr.querySelectorAll("td");
    if (tds.length) kws.push({ id: Number(tr.querySelector(".edit").dataset.id), category: tds[0].textContent, keyword: tds[1].textContent, priority: tds[2].textContent, enabled: tds[3].textContent.includes("启用") ? 1 : 0 });
  });
  const k = id ? kws.find(x => x.id === id) : { category: "通用", keyword: "", priority: 100, enabled: 1 };
  openDrawer(id ? "编辑关键词" : "新增关键词", `
    <div class="form-grid">
      <label>category</label><input id="d-category" value="${esc(k.category)}" placeholder="通用/逆向/创作/渗透">
      <label>keyword</label><input id="d-keyword" value="${esc(k.keyword)}" placeholder="逆向 破解 脱壳（空格分隔，任一命中）">
      <label>priority</label><input id="d-priority" type="number" value="${k.priority}">
      <label>enabled</label><select id="d-enabled"><option value="1" ${k.enabled?"selected":""}>启用</option><option value="0" ${!k.enabled?"selected":""}>禁用</option></select>
    </div>
    <p class="hint">请求消息含任一 keyword 即归入该 category；多个命中取 priority 最小者。</p>
  `, async () => {
    const payload = { category: $("#d-category").value.trim(), keyword: $("#d-keyword").value.trim(), priority: parseInt($("#d-priority").value)||100, enabled: $("#d-enabled").value === "1" };
    if (id) await api(`/admin/keywords/${id}`, { method: "PUT", body: payload });
    else await api("/admin/keywords", { method: "POST", body: payload });
    closeDrawer();
    await renderKeywords($("#view-body"));
  });
}

/* ---------------- 破甲词包（包路由） ---------------- */
async function renderPacks(body) {
  const packs = await api("/admin/packs");
  body.innerHTML = `
    <div class="filter-bar">
      <button class="btn small" id="pk-add">新增破甲词包</button>
    </div>
    <table><thead><tr><th>名称</th><th>描述</th><th>字数</th><th>enabled</th><th>操作</th></tr></thead>
    <tbody>${packs.map(p=>rowPack(p)).join("") || `<tr><td colspan="5" class="empty">暂无破甲词包</td></tr>`}</tbody></table>
  `;
  $("#pk-add").onclick = () => openPackDrawer(null);
  $$("#view-body .edit").forEach(b => b.onclick = () => openPackDrawer(Number(b.dataset.id)));
  $$("#view-body .del").forEach(b => b.onclick = async () => {
    if (!confirm("删除该破甲词包？相关模型绑定也会删除。")) return;
    await api(`/admin/packs/${b.dataset.id}`, { method: "DELETE" });
    renderPacks(body);
  });
}

function rowPack(p) {
  return `<tr>
    <td><strong>${esc(p.name)}</strong></td>
    <td class="hint">${esc(p.description)}</td>
    <td>${p.content_len}</td>
    <td>${p.enabled ? '<span class="pill">启用</span>' : '<span class="pill disabled">禁用</span>'}</td>
    <td class="row-actions">
      <button class="btn small edit" data-id="${p.id}">编辑</button>
      <button class="btn small danger del" data-id="${p.id}">删除</button>
    </td></tr>`;
}

async function openPackDrawer(id) {
  const packs = await api("/admin/packs");
  const p = id ? await api(`/admin/packs/${id}`) : { name: "", description: "", content: "", enabled: 1 };
  openDrawer(id ? `编辑破甲词包 · ${p.name}` : "新增破甲词包", `
    <div class="form-grid">
      <label>名称</label><input id="d-name" value="${esc(p.name)}" placeholder="如 冷咖啡">
      <label>描述</label><input id="d-desc" value="${esc(p.description)}" placeholder="一句话说明">
      <label>启用</label><select id="d-enabled"><option value="1" ${p.enabled?"selected":""}>启用</option><option value="0" ${!p.enabled?"selected":""}>禁用</option></select>
    </div>
    <div style="margin-top:14px">
      <label class="hint">破甲词内容（${(p.content||"").length} 字）</label>
      <textarea id="d-content" style="min-height:55vh" spellcheck="false">${esc(p.content)}</textarea>
    </div>
  `, async () => {
    const payload = { name: $("#d-name").value.trim(), description: $("#d-desc").value.trim(), content: $("#d-content").value, enabled: $("#d-enabled").value === "1" };
    if (id) await api(`/admin/packs/${id}`, { method: "PUT", body: payload });
    else await api("/admin/packs", { method: "POST", body: payload });
    closeDrawer();
    renderPacks($("#view-body"));
  });
}

/* ---------------- 模型绑定（包路由） ---------------- */
async function renderBindings(body) {
  const [bindings, packs] = await Promise.all([api("/admin/bindings"), api("/admin/packs")]);
  const packOpts = packs.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  body.innerHTML = `
    <div class="filter-bar"><span class="hint">按模型 pattern 绑定破甲词包，命中可叠加多个，priority 小的先注入。</span></div>
    <table><thead><tr><th>模型 pattern</th><th>破甲词包</th><th>priority</th><th>enabled</th><th>操作</th></tr></thead>
    <tbody>${bindings.map(b=>rowBinding(b)).join("") || `<tr><td colspan="5" class="empty">暂无绑定</td></tr>`}</tbody></table>
    <div style="margin-top:14px" class="form-grid">
      <label>新增绑定</label>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <input id="b-pattern" placeholder="deepseek-* 或 re:^glm-" style="width:220px">
        <select id="b-pack" style="min-width:160px">${packOpts}</select>
        <input id="b-priority" type="number" value="100" style="width:90px">
        <button class="btn primary" id="b-add">绑定</button>
      </div>
    </div>
  `;
  $("#b-add").onclick = async () => {
    const pattern = $("#b-pattern").value.trim();
    const pack_id = Number($("#b-pack").value);
    if (!pattern || !pack_id) return;
    await api("/admin/bindings", { method: "POST", body: { pattern, pack_id, priority: parseInt($("#b-priority").value)||100, enabled: true } });
    renderBindings(body);
  };
  $$("#view-body .edit").forEach(x => x.onclick = () => openBindingDrawer(Number(x.dataset.id), packs));
  $$("#view-body .del").forEach(x => x.onclick = async () => {
    if (!confirm("删除该绑定？")) return;
    await api(`/admin/bindings/${x.dataset.id}`, { method: "DELETE" });
    renderBindings(body);
  });
}

function rowBinding(b) {
  return `<tr>
    <td><code>${esc(b.pattern)}</code></td>
    <td>${esc(b.pack_name || "(已删除包)")}</td>
    <td>${b.priority}</td>
    <td>${b.enabled ? '<span class="pill">启用</span>' : '<span class="pill disabled">禁用</span>'}</td>
    <td class="row-actions">
      <button class="btn small edit" data-id="${b.id}">编辑</button>
      <button class="btn small danger del" data-id="${b.id}">删除</button>
    </td></tr>`;
}

function openBindingDrawer(id, packs) {
  const bindings = [];
  $$("#view-body tr").forEach(tr => {
    const b = tr.querySelector(".edit");
    if (b && Number(b.dataset.id) === id) bindings.push({
      id, pattern: tr.querySelector("code").textContent,
      priority: tr.children[2].textContent,
      enabled: tr.children[3].textContent.includes("启用") ? 1 : 0,
      pack_name: tr.children[1].textContent.trim(),
    });
  });
  const b = bindings[0] || { pattern: "", priority: 100, enabled: 1 };
  const packOpts = packs.map(p => `<option value="${p.id}" ${p.name===b.pack_name?"selected":""}>${esc(p.name)}</option>`).join("");
  openDrawer("编辑绑定", `
    <div class="form-grid">
      <label>模型 pattern</label><input id="d-pattern" value="${esc(b.pattern)}">
      <label>破甲词包</label><select id="d-pack">${packOpts}</select>
      <label>priority</label><input id="d-priority" type="number" value="${b.priority}">
      <label>enabled</label><select id="d-enabled"><option value="1" ${b.enabled?"selected":""}>启用</option><option value="0" ${!b.enabled?"selected":""}>禁用</option></select>
    </div>
  `, async () => {
    await api(`/admin/bindings/${id}`, { method: "PUT", body: {
      pattern: $("#d-pattern").value.trim(), pack_id: Number($("#d-pack").value),
      priority: parseInt($("#d-priority").value)||100, enabled: $("#d-enabled").value === "1",
    }});
    closeDrawer();
    renderBindings($("#view-body"));
  });
}

/* ---------------- 破甲词矩阵（手风琴树 + 懒渲染） ---------------- */
async function renderPresets(body) {
  const data = await api("/admin/presets");
  state.presets = data.items;
  state.families = data.families;
  state.categories = data.categories;
  const gridBtn = `<button class="btn small" id="matrix-toggle">${state.matrixMode ? "手风琴视图" : "平铺矩阵"}</button>`;
  body.innerHTML = `
    <div class="filter-bar">
      <input id="p-search" placeholder="搜索破甲词">
      <button class="btn small" id="p-enable-all">全部启用</button>
      <button class="btn small" id="p-disable-all">全部禁用</button>
      ${gridBtn}
    </div>
    <div id="p-body"></div>
  `;
  $("#p-search").oninput = () => renderPresetsInner($("#p-body"));
  $("#matrix-toggle").onclick = () => { state.matrixMode = !state.matrixMode; renderPresets($("#view-body")); };
  $("#p-enable-all").onclick = async () => { for (const p of state.presets) await api(`/admin/presets/${p.id}`, { method: "PUT", body: { enabled: true } }); renderPresets($("#view-body")); };
  $("#p-disable-all").onclick = async () => { for (const p of state.presets) await api(`/admin/presets/${p.id}`, { method: "PUT", body: { enabled: false } }); renderPresets($("#view-body")); };
  renderPresetsInner($("#p-body"));
}

function renderPresetsInner(container) {
  const q = ($("#p-search")?.value || "").toLowerCase();
  const items = state.presets.filter(p => !q || `${p.family} ${p.category} ${p.preview}`.toLowerCase().includes(q));
  if (state.matrixMode) return renderMatrix(container, items);
  return renderAccordion(container, items);
}

function renderAccordion(container, items) {
  const byFamily = {};
  items.forEach(p => (byFamily[p.family] = byFamily[p.family] || []).push(p));
  container.innerHTML = "";
  for (const family of state.families) {
    const list = byFamily[family] || [];
    const open = state.expanded[family];
    const group = document.createElement("div");
    group.className = "family-group";
    const dots = state.categories.map(cat => {
      const p = list.find(x => x.category === cat);
      const cls = !p || !p.enabled ? (p ? "off" : "empty") : "on";
      const label = p ? (p.enabled ? "" : "禁") : "空";
      return `<span class="cat-dot"><span class="dot ${cls}"></span>${cat}${label}</span>`;
    }).join("");
    group.innerHTML = `
      <div class="family-head">
        <span class="family-name">${esc(family)}</span>
        <div class="family-dots">${dots}</div>
        <div class="family-actions">
          <button class="btn small copy-row" data-family="${esc(family)}">复制 other→本行</button>
        </div>
      </div>
      <div class="family-body ${open ? "" : "hidden"}">
        ${list.map(p => `<div class="cat-cell">
            <span class="cat-name">${esc(p.category)}</span>
            <span class="cat-preview" title="${esc(p.preview)}">${esc(p.preview)}</span>
            <span class="cat-meta">
              <span class="pill pos">${POSITION_LABEL[p.position] || "跟随全局"}</span>
              <span class="pill ${p.enabled ? "" : "disabled"}">${p.enabled ? "启用" : "禁用"}</span>
              <span class="hint">${p.content_len}字</span>
              <button class="btn small edit" data-id="${p.id}">编辑</button>
              <button class="btn small toggle" data-id="${p.id}" data-on="${p.enabled}">${p.enabled ? "禁用" : "启用"}</button>
            </span>
          </div>`).join("")}
      </div>`;
    group.querySelector(".family-head").onclick = e => {
      if (e.target.closest("button")) return;
      state.expanded[family] = !open;
      renderAccordion(container, items);
    };
    group.querySelector(".copy-row")?.addEventListener("click", async e => {
      e.stopPropagation();
      const src = items.find(x => x.family === "other" && x.category === "通用");
      if (!src) return;
      await api("/admin/presets/copy", { method: "POST", body: { src_id: src.id, scope: "row" } });
      renderPresets($("#view-body"));
    });
    group.querySelectorAll(".edit").forEach(b => b.onclick = () => openPresetDrawer(Number(b.dataset.id)));
    group.querySelectorAll(".toggle").forEach(b => b.onclick = async () => {
      await api(`/admin/presets/${b.dataset.id}`, { method: "PUT", body: { enabled: b.dataset.on !== "1" } });
      renderPresets($("#view-body"));
    });
    container.appendChild(group);
  }
}

function renderMatrix(container, items) {
  container.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "matrix";
  grid.style.gridTemplateColumns = `90px repeat(${state.categories.length}, 1fr)`;
  grid.innerHTML = `<div class="matrix-header"></div>` + state.categories.map(c => `<div class="matrix-header">${esc(c)}</div>`).join("");
  for (const family of state.families) {
    grid.innerHTML += `<div class="matrix-header">${esc(family)}</div>`;
    for (const cat of state.categories) {
      const p = items.find(x => x.family === family && x.category === cat);
      grid.innerHTML += `<div class="matrix-cell ${p ? "" : "empty-cell"}" data-id="${p ? p.id : ""}">
        <div class="mc-title">${p ? (p.enabled ? "启用" : "禁用") : "空"}</div>
        <div class="mc-preview">${esc(p ? p.preview : "")}</div>
      </div>`;
    }
  }
  grid.querySelectorAll(".matrix-cell[data-id]").forEach(cell => cell.onclick = () => openPresetDrawer(Number(cell.dataset.id)));
  container.appendChild(grid);
}

async function openPresetDrawer(id) {
  // 懒加载全文
  const detail = await api(`/admin/presets/${id}`);
  const summary = state.presets.find(p => p.id === id) || {};
  openDrawer(`编辑破甲词 · ${summary.family} × ${summary.category}`, `
    <div class="form-grid">
      <label>位置</label>
      <select id="d-position">
        <option value="" ${!detail.position ? "selected" : ""}>跟随全局</option>
        ${POSITIONS.map(p => `<option value="${p}" ${detail.position === p ? "selected" : ""}>${POSITION_LABEL[p]}</option>`).join("")}
      </select>
      <label>启用</label><select id="d-enabled"><option value="1" ${detail.enabled ? "selected" : ""}>启用</option><option value="0" ${!detail.enabled ? "selected" : ""}>禁用</option></select>
    </div>
    <div style="margin-top:14px">
      <label class="hint">破甲词正文（${detail.content.length} 字）</label>
      <textarea id="d-content" style="min-height:50vh" spellcheck="false">${esc(detail.content)}</textarea>
    </div>
  `, async () => {
    await api(`/admin/presets/${id}`, { method: "PUT", body: {
      content: $("#d-content").value,
      position: $("#d-position").value || null,
      enabled: $("#d-enabled").value === "1",
    }});
    closeDrawer();
    renderPresets($("#view-body"));
  });
}

/* ---------------- 日志 ---------------- */
async function renderLogs(body) {
  const page = state.page;
  const data = await api(`/admin/logs?page=${page}&page_size=20`);
  body.innerHTML = `
    <div class="filter-bar">
      <button class="btn small" id="log-refresh">刷新</button>
      <button class="btn small danger" id="log-clear">清空</button>
    </div>
    <table><thead><tr><th>时间</th><th>协议</th><th>model</th><th>路由/命中</th><th>注入</th><th>状态</th><th>耗时ms</th><th>首token</th></tr></thead>
    <tbody>${data.items.map(l => rowLog(l)).join("") || `<tr><td colspan="8" class="empty">暂无日志</td></tr>`}</tbody></table>
    <div class="pager">
      <button class="btn small" id="log-prev" ${page<=1?"disabled":""}>上一页</button>
      <span class="hint">第 ${page} 页 / 共 ${Math.max(1, Math.ceil(data.total/data.page_size))} 页（${data.total} 条）</span>
      <button class="btn small" id="log-next" ${page*data.page_size>=data.total?"disabled":""}>下一页</button>
    </div>
  `;
  $("#log-refresh").onclick = () => renderLogs(body);
  $("#log-clear").onclick = async () => { if (confirm("确认清空全部日志？")) { await api("/admin/logs", { method: "DELETE" }); renderLogs(body); } };
  $("#log-prev").onclick = () => { state.page--; renderLogs(body); };
  $("#log-next").onclick = () => { state.page++; renderLogs(body); };
  $$("tbody tr.log-row").forEach(tr => tr.onclick = () => showLogDetail(data.items[Number(tr.dataset.idx)]));
}

function rowLog(l, idx) {
  const inject = l.injected ? `<span class="pill">注入 ${l.inject_len}字</span>` : '<span class="pill disabled">未注入</span>';
  let hit;
  if (l.route_mode === "pack") hit = `<span class="pill pos">包</span> ${esc(l.packs || "-")}`;
  else hit = `${esc(l.family || "-")}/${esc(l.category || "-")}`;
  return `<tr class="log-row" data-idx="${idx}">
    <td>${new Date(l.ts*1000).toLocaleString()}</td>
    <td>${esc(l.protocol)}</td>
    <td>${esc(l.model || "-")}</td>
    <td>${hit}</td>
    <td>${inject}</td>
    <td>${l.status}</td>
    <td>${l.duration_ms}</td>
    <td>${l.first_token_ms ?? "-"}</td>
  </tr>`;
}

function showLogDetail(l) {
  if (!l) return;
  openDrawer("日志详情", `
    <div class="form-grid">
      <label>协议</label><div>${esc(l.protocol)}</div>
      <label>model</label><div>${esc(l.model)}</div>
      <label>family/category</label><div>${esc(l.family || "-")} / ${esc(l.category || "-")}</div>
      <label>路由模式</label><div>${l.route_mode === "pack" ? "破甲词包" : "精细路由"}</div>
      <label>命中包</label><div>${esc(l.packs || "-")}</div>
      <label>位置</label><div>${esc(l.position)}</div>
      <label>状态</label><div>${l.status}</div>
      <label>路径</label><div>${esc(l.request_path)}</div>
      <label>IP</label><div>${esc(l.client_ip)}</div>
    </div>
    ${l.usage ? `<div style="margin-top:14px"><label class="hint">usage</label><pre>${esc(JSON.stringify(l.usage, null, 2))}</pre></div>` : ""}
    ${l.error ? `<div style="margin-top:14px"><label class="hint">error</label><pre>${esc(l.error)}</pre></div>` : ""}
  `, null);
}

/* ---------------- 抽屉通用 ---------------- */
function openDrawer(title, bodyHTML, onSave) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = bodyHTML;
  $("#drawer-mask").classList.remove("hidden");
  $("#drawer").classList.remove("hidden");
  const saveBtn = $("#drawer-save");
  saveBtn.onclick = onSave ? onSave : closeDrawer;
  saveBtn.style.display = onSave ? "" : "none";
  $("#drawer-cancel").onclick = closeDrawer;
  $("#drawer-close").onclick = closeDrawer;
  $("#drawer-mask").onclick = closeDrawer;
}

function closeDrawer() {
  $("#drawer-mask").classList.add("hidden");
  $("#drawer").classList.add("hidden");
}

/* ---------------- 工具 ---------------- */
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
let flashTimer;
function flash(msg) {
  const btn = $("#s-save");
  if (btn) { const old = btn.textContent; btn.textContent = "已保存"; setTimeout(() => btn.textContent = old, 1200); }
  console.log("[flash]", msg);
}

/* ---------------- 初始化 ---------------- */
async function init() {
  $("#login-btn").onclick = doLogin;
  $("#login-password").onkeydown = e => { if (e.key === "Enter") doLogin(); };
  $("#logout-btn").onclick = async () => { await api("/admin/logout", { method: "POST" }); showLogin(); };
  $$(".nav-item").forEach(b => b.onclick = () => switchView(b.dataset.view));

  try {
    await api("/admin/me");
    await showMain();
  } catch {
    showLogin();
  }
}

async function doLogin() {
  try {
    await api("/admin/login", { method: "POST", body: { password: $("#login-password").value } });
    $("#login-msg").textContent = "";
    $("#login-password").value = "";
    await showMain();
  } catch (e) {
    $("#login-msg").textContent = e.message;
  }
}

init();
