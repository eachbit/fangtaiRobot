const state = {
  users: [],
  cases: [],
  sessionId: null,
  auditJobId: null,
  auditPollTimer: null,
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
  await loadAuditJobs();
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
    state.sessionId = null;
    renderProfile();
  });
  $("caseSelect").addEventListener("change", () => {
    state.sessionId = null;
    const id = Number($("caseSelect").value);
    const item = state.cases.find((caseItem) => caseItem.id === id);
    if (item) {
      $("messages").value = item.user_messages.join("\n");
    }
  });
  $("recommendBtn").addEventListener("click", recommend);
  $("auditStartBtn").addEventListener("click", startAuditJob);
  $("auditCancelBtn").addEventListener("click", cancelAuditJob);
  $("auditRefreshBtn").addEventListener("click", loadAuditJobs);
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
    const result = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        user_id: $("userSelect").value ? Number($("userSelect").value) : null,
        session_id: state.sessionId,
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
  state.sessionId = result.session_id || state.sessionId;
  $("answer").textContent = result.answer || "";
  $("constraints").textContent = JSON.stringify(result.constraints, null, 2);
  $("menu").innerHTML = result.menu.map(renderRecipe).join("");
  $("scoreCard").innerHTML = Object.entries(result.score_card)
    .map(([key, value]) => `<div class="score-item"><strong>${labelOf(key)}</strong><br>${value}</div>`)
    .join("");
  renderNutrition(result.nutrition);
  $("warnings").innerHTML = result.warnings.length
    ? result.warnings.map((warning) => `<li>${warning}</li>`).join("")
    : "<li>无硬性风险提示</li>";
}

function renderNutrition(nutrition) {
  if (!nutrition) {
    $("nutritionSummary").innerHTML = "<span class=\"muted\">暂无营养估算</span>";
    return;
  }
  $("nutritionSummary").innerHTML = `
    <div><strong>${nutrition.table.dish_count}</strong><span>道菜</span></div>
    <div><strong>${nutrition.table.people_count}</strong><span>人用餐</span></div>
    <div><strong>${nutrition.per_person.kcal}</strong><span>kcal/人</span></div>
    <div><strong>${nutrition.confidence.level}</strong><span>可信度</span></div>
  `;
}

async function startAuditJob() {
  setAuditButtons(true);
  try {
    const job = await api("/api/audit/jobs", {
      method: "POST",
      body: JSON.stringify(buildAuditRequest()),
    });
    state.auditJobId = job.job_id;
    renderAuditJob(job);
    pollAuditJob();
  } catch (error) {
    alert(`启动评测失败：${error.message}`);
    setAuditButtons(false);
  }
}

function buildAuditRequest() {
  const source = $("auditSource") ? $("auditSource").value : "fixed";
  if (source !== "agent_generated") {
    return {};
  }
  const count = clampNumber(Number($("auditCount").value || 10), 1, 200);
  const seed = Number($("auditSeed").value || 20260725);
  $("auditCount").value = String(count);
  $("auditSeed").value = String(Number.isFinite(seed) ? seed : 20260725);
  return {
    source: "agent_generated",
    count,
    seed: Number($("auditSeed").value),
  };
}

function clampNumber(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

async function cancelAuditJob() {
  if (!state.auditJobId) return;
  try {
    const job = await api(`/api/audit/jobs/${state.auditJobId}/cancel`, { method: "POST", body: "{}" });
    renderAuditJob(job);
  } catch (error) {
    alert(`取消失败：${error.message}`);
  }
}

async function loadAuditJobs() {
  try {
    const payload = await api("/api/audit/jobs");
    renderAuditJobs(payload.jobs || []);
    if (!state.auditJobId && payload.jobs && payload.jobs.length) {
      state.auditJobId = payload.jobs[0].job_id;
      const job = await api(`/api/audit/jobs/${state.auditJobId}`);
      renderAuditJob(job);
    }
  } catch (error) {
    $("auditOverview").innerHTML = `<div class="audit-empty">评测控制台暂不可用：${escapeHtml(error.message)}</div>`;
  }
}

async function pollAuditJob() {
  clearAuditPoll();
  if (!state.auditJobId) return;
  state.auditPollTimer = window.setInterval(async () => {
    try {
      const job = await api(`/api/audit/jobs/${state.auditJobId}`);
      renderAuditJob(job);
      if (!["queued", "running", "canceling"].includes(job.status)) {
        clearAuditPoll();
        setAuditButtons(false);
        loadAuditJobs();
      }
    } catch (error) {
      clearAuditPoll();
      setAuditButtons(false);
      $("auditOverview").innerHTML = `<div class="audit-empty">轮询失败：${escapeHtml(error.message)}</div>`;
    }
  }, 500);
}

function renderAuditJob(job) {
  const total = job.progress.total || 0;
  const completed = job.progress.completed || 0;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  $("auditProgressBar").style.width = `${percent}%`;
  $("auditOverview").innerHTML = `
    <div class="audit-stat"><strong>${statusText(job.status)}</strong><span>任务状态</span></div>
    <div class="audit-stat"><strong>${completed}/${total}</strong><span>进度</span></div>
    <div class="audit-stat pass"><strong>${job.summary.passed}</strong><span>通过</span></div>
    <div class="audit-stat fail"><strong>${job.summary.failed}</strong><span>失败</span></div>
    <div class="audit-stat"><strong>${job.summary.duration_ms}ms</strong><span>耗时</span></div>
  `;
  $("auditRecords").innerHTML = job.records && job.records.length
    ? job.records.map(renderAuditRecord).join("")
    : "<div class=\"audit-empty\">任务运行后会在这里显示每个场景的询问、回答和查错结果。</div>";
  setAuditButtons(["queued", "running", "canceling"].includes(job.status));
}

function renderAuditJobs(jobs) {
  $("auditJobs").innerHTML = jobs.length
    ? jobs.map((job) => `
      <button class="job-item ${job.job_id === state.auditJobId ? "active" : ""}" onclick="selectAuditJob('${job.job_id}')">
        <span>${statusText(job.status)}</span>
        <strong>${job.summary.passed}/${job.summary.total} 通过</strong>
        <small>${new Date(job.created_at * 1000).toLocaleTimeString()}</small>
      </button>
    `).join("")
    : "<div class=\"audit-empty\">暂无后台评测任务</div>";
}

async function selectAuditJob(jobId) {
  state.auditJobId = jobId;
  const job = await api(`/api/audit/jobs/${jobId}`);
  renderAuditJob(job);
  loadAuditJobs();
}

function renderAuditRecord(record) {
  const issues = record.issues.length
    ? record.issues.map((issue) => `<li>${escapeHtml(issue)}</li>`).join("")
    : "<li>未发现硬性问题</li>";
  return `
    <article class="audit-record ${record.status}">
      <header>
        <strong>${escapeHtml(record.name)}</strong>
        <span>${record.status === "passed" ? "通过" : "失败"} · ${record.elapsed_ms}ms</span>
      </header>
      ${renderAuditMeta(record)}
      <div class="audit-dialog">${record.messages.map((message) => `<p>${escapeHtml(message)}</p>`).join("")}</div>
      <p class="audit-answer">${escapeHtml(record.answer || "")}</p>
      <div class="tags">${record.menu.map((item) => `<span class="tag">${escapeHtml(item.name)}</span>`).join("")}</div>
      ${renderAuditAdvisories(record)}
      <ul>${issues}</ul>
      <details>
        <summary>调试 JSON</summary>
        <pre>${escapeHtml(JSON.stringify(record.debug, null, 2))}</pre>
      </details>
    </article>
  `;
}

function renderAuditMeta(record) {
  const debug = record.debug || {};
  const review = debug.agent_review || {};
  const items = [];
  if (debug.source) {
    items.push(`source: ${debug.source}`);
  }
  if (review.review_agent) {
    items.push(`agent_review: ${review.review_agent}`);
  }
  if (review.naturalness) {
    items.push(`naturalness ${review.naturalness}/5`);
  }
  if (review.clarity) {
    items.push(`clarity ${review.clarity}/5`);
  }
  return items.length
    ? `<div class="audit-meta">${items.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`
    : "";
}

function renderAuditAdvisories(record) {
  const advisories = (record.debug || {}).nutrition_advisories || [];
  if (!advisories.length) {
    return "";
  }
  return `
    <div class="audit-advisory">
      <strong>nutrition advisory</strong>
      <ul>${advisories.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function setAuditButtons(isRunning) {
  $("auditStartBtn").disabled = isRunning;
  $("auditCancelBtn").disabled = !isRunning || !state.auditJobId;
}

function clearAuditPoll() {
  if (state.auditPollTimer) {
    window.clearInterval(state.auditPollTimer);
    state.auditPollTimer = null;
  }
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
    nutrition_balance: "营养均衡",
    menu_count: "推荐数量",
  };
  return labels[key] || key;
}

function statusText(status) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    canceling: "取消中",
    canceled: "已取消",
    completed: "已完成",
    failed: "异常失败",
  };
  return labels[status] || status;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

init().catch((error) => {
  $("status").textContent = "启动失败";
  console.error(error);
});
