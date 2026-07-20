const state = {
  users: [],
  cases: [],
  sessionId: null,
  menuVersion: null,
  submittedMessages: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: response.statusText }));
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return response.json();
}

async function init() {
  const health = await api("/api/health");
  $("status").textContent = `${health.recipe_count} 菜谱 / ${health.user_count} 档案`;

  state.users = (await api("/api/users")).users;
  state.cases = (await api("/api/cases")).cases;
  renderUsers();
  renderCases();
  bindEvents();
}

function renderUsers() {
  const select = $("userSelect");
  select.innerHTML =
    `<option value="">从对话自动识别用户特征</option>` +
    state.users
    .map((user) => `<option value="${user.id}">用户 ${user.id}：${user.性别} / ${user.年龄}岁 / ${user.口味偏好}</option>`)
    .join("");
  renderProfile();
}

function renderCases() {
  const select = $("caseSelect");
  select.innerHTML =
    `<option value="">自定义输入</option>` +
    state.cases.map((item) => `<option value="${item.id}">用例 ${item.id}（${item.turn_count}轮）</option>`).join("");
}

function bindEvents() {
  $("userSelect").addEventListener("change", () => {
    renderProfile();
    resetSession(false);
  });
  $("caseSelect").addEventListener("change", () => {
    const id = Number($("caseSelect").value);
    const item = state.cases.find((caseItem) => caseItem.id === id);
    if (item) {
      $("messages").value = item.user_messages.join("\n");
      resetSession(false);
    }
  });
  $("recommendBtn").addEventListener("click", recommend);
  $("newSessionBtn").addEventListener("click", () => resetSession(true));
}

function renderProfile() {
  const user = currentUser();
  if (!user) {
    $("profile").innerHTML = `
      不使用固定健康档案。系统会从对话中识别年龄、性别、过敏、疾病/特殊人群、口味偏好和健康目标。
    `;
    return;
  }
  $("profile").innerHTML = `
    <strong>特殊人群：</strong>${arrayText(user.特殊人群)}<br>
    <strong>过敏食材：</strong>${arrayText(user.过敏食材)}<br>
    <strong>健康需求：</strong>${arrayText(user.健康需求)}
  `;
}

function currentUser() {
  const value = $("userSelect").value;
  if (!value) return null;
  const id = Number(value);
  return state.users.find((user) => user.id === id);
}

async function recommend() {
  const messages = $("messages")
    .value.split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  if (!messages.length) {
    alert("请输入至少一轮对话。");
    return;
  }
  $("recommendBtn").disabled = true;
  $("recommendBtn").textContent = "生成中...";
  try {
    const delta = sessionDelta(messages);
    const body = {
      user_id: $("userSelect").value ? Number($("userSelect").value) : null,
    };
    if (delta !== null) {
      body.message = delta;
      body.session_id = state.sessionId;
      body.menu_version = state.menuVersion;
    } else {
      body.messages = messages;
    }
    const result = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.sessionId = result.session_id || null;
    state.menuVersion = result.menu_version || null;
    state.submittedMessages = messages;
    renderSessionState();
    renderResult(result);
  } catch (error) {
    if (error.status === 409) {
      resetSession(false);
      alert("会话版本已变化，请重新提交当前完整对话。");
    } else {
      alert(`推荐失败：${error.message}`);
    }
  } finally {
    $("recommendBtn").disabled = false;
    $("recommendBtn").textContent = "生成推荐";
  }
}

function renderResult(result) {
  $("answer").textContent = result.answer || "";
  $("constraints").textContent = JSON.stringify(result.constraints, null, 2);
  $("menu").innerHTML = result.menu.map(renderRecipe).join("");
  $("scoreCard").innerHTML = Object.entries(result.score_card)
    .map(([key, value]) => `<div class="score-item"><strong>${labelOf(key)}</strong><br>${value}</div>`)
    .join("");
  $("warnings").innerHTML = result.warnings.length
    ? result.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")
    : "<li>无硬性风险提示</li>";
  renderNutrition(result);
  renderChanges(result.changes);
}

function renderRecipe(item) {
  return `
    <article class="recipe">
      <h3>${escapeHtml(item.name)}</h3>
      <p><strong>推荐理由：</strong>${escapeHtml(item.reason)}</p>
      <p><strong>食材：</strong>${escapeHtml(item.ingredients)}</p>
      ${renderRecipeNutrition(item.nutrition)}
      <div class="tags">${item.labels.slice(0, 10).map((label) => `<span class="tag">${escapeHtml(label)}</span>`).join("")}</div>
    </article>
  `;
}

function sessionDelta(messages) {
  if (!state.sessionId || messages.length !== state.submittedMessages.length + 1) return null;
  const prefixMatches = state.submittedMessages.every((message, index) => message === messages[index]);
  return prefixMatches ? messages[messages.length - 1] : null;
}

function resetSession(clearMessages) {
  state.sessionId = null;
  state.menuVersion = null;
  state.submittedMessages = [];
  if (clearMessages) {
    $("messages").value = "";
    $("caseSelect").value = "";
  }
  renderSessionState();
}

function renderSessionState() {
  $("sessionState").textContent = state.sessionId
    ? `会话进行中 · 菜单版本 ${state.menuVersion}`
    : "尚未创建会话";
}

function renderRecipeNutrition(nutrition) {
  if (!nutrition) return "";
  const n = nutrition.nutrients;
  return `<div class="recipe-nutrition">
    <span>${formatNumber(n.kcal)} kcal</span>
    <span>蛋白质 ${formatNumber(n.protein_g)}g</span>
    <span>脂肪 ${formatNumber(n.fat_g)}g</span>
    <span>钠 ${formatNumber(n.sodium_mg)}mg</span>
  </div>`;
}

function renderNutrition(result) {
  const nutrition = result.nutrition;
  if (!nutrition) return;
  const confidence = result.confidence || nutrition.confidence;
  const badge = $("confidenceBadge");
  badge.textContent = `${confidenceLabel(confidence.level)} · ${Math.round((confidence.score || 0) * 100)}%`;
  badge.dataset.level = confidence.level;
  $("nutritionSummary").innerHTML = `
    <div><strong>${formatNumber(nutrition.table_total.kcal)}</strong><span>整桌 kcal</span></div>
    <div><strong>${formatNumber(nutrition.per_person.kcal)}</strong><span>人均 kcal</span></div>
    <div><strong>${formatNumber(nutrition.per_person.protein_g)}g</strong><span>人均蛋白质</span></div>
    <div><strong>${formatNumber(nutrition.per_person.sodium_mg)}mg</strong><span>人均钠</span></div>`;
  $("nutritionComponents").innerHTML = Object.entries(result.nutrition_score.components)
    .map(([key, item]) => `<div class="nutrient-item" data-status="${item.status}">
      <span>${nutrientLabel(key)}</span><strong>${formatNumber(item.value)} ${nutrientUnit(key)}</strong>
      <small>${statusLabel(item.status)}</small></div>`).join("");
}

function renderChanges(changes) {
  if (!changes) return;
  const replacements = changes.replaced_dishes || [];
  const details = replacements.length
    ? `<ul>${replacements.map((item) => `<li>#${item.old_id} → #${item.new_id}：${escapeHtml(item.reason)}</li>`).join("")}</ul>`
    : "<p>本轮没有替换已确认菜品。</p>";
  $("changes").innerHTML = `<div class="change-count"><strong>${changes.change_count}</strong><span>处菜单变化</span></div>
    <p>模式：${changeModeLabel(changes.mode)} · 保留 ${changes.kept_dishes.length} 道</p>${details}`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}

function confidenceLabel(level) { return ({ high: "高可信", medium: "中可信", low: "低可信" })[level] || "未知"; }
function statusLabel(status) { return ({ within_range: "目标内", high: "偏高", low: "偏低" })[status] || status; }
function changeModeLabel(mode) { return ({ initial: "首次生成", minimal_revision: "最小修改", full_regeneration: "全部重做" })[mode] || mode; }
function nutrientUnit(key) { return key === "sodium_mg" ? "mg" : key === "kcal" ? "kcal" : "g"; }
function nutrientLabel(key) { return ({ kcal: "能量", protein_g: "蛋白质", fat_g: "脂肪", carbohydrate_g: "碳水", fiber_g: "膳食纤维", sugar_g: "糖", sodium_mg: "钠" })[key] || key; }

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function arrayText(value) {
  return value && value.length ? value.join("、") : "无";
}

function labelOf(key) {
  const labels = {
    official_recipe: "官方菜谱",
    allergy_passed: "过敏校验",
    health_match: "健康匹配",
    taste_match: "口味匹配",
    scenario_match: "场景匹配",
    minimal_change: "最小修改",
    menu_count: "推荐数量",
  };
  return labels[key] || key;
}

init().catch((error) => {
  $("status").textContent = "启动失败";
  console.error(error);
});
