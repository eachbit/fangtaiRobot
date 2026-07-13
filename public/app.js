const state = {
  users: [],
  cases: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
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
  select.innerHTML = state.users
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
  $("userSelect").addEventListener("change", renderProfile);
  $("caseSelect").addEventListener("change", () => {
    const id = Number($("caseSelect").value);
    const item = state.cases.find((caseItem) => caseItem.id === id);
    if (item) {
      $("messages").value = item.user_messages.join("\n");
    }
  });
  $("recommendBtn").addEventListener("click", recommend);
}

function renderProfile() {
  const user = currentUser();
  if (!user) return;
  $("profile").innerHTML = `
    <strong>特殊人群：</strong>${arrayText(user.特殊人群)}<br>
    <strong>过敏食材：</strong>${arrayText(user.过敏食材)}<br>
    <strong>健康需求：</strong>${arrayText(user.健康需求)}
  `;
}

function currentUser() {
  const id = Number($("userSelect").value);
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
    const result = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        user_id: Number($("userSelect").value),
        messages,
      }),
    });
    renderResult(result);
  } catch (error) {
    alert(`推荐失败：${error.message}`);
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
    ? result.warnings.map((warning) => `<li>${warning}</li>`).join("")
    : "<li>无硬性风险提示</li>";
}

function renderRecipe(item) {
  return `
    <article class="recipe">
      <h3>${item.name}</h3>
      <p><strong>推荐理由：</strong>${item.reason}</p>
      <p><strong>食材：</strong>${item.ingredients}</p>
      <div class="tags">${item.labels.slice(0, 10).map((label) => `<span class="tag">${label}</span>`).join("")}</div>
    </article>
  `;
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
