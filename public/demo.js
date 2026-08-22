const demoState = {
  step: 1,
  sessionId: null,
  revisionSessionId: null,
  initialResult: null,
  revisionResult: null,
  currentResult: null,
  health: null,
  running: false,
};

const stepData = {
  1: {
    title: "连接服务",
    command: "GET /api/health",
    narration: "先展示服务在线，并确认官方菜谱和健康档案已经加载。",
  },
  2: {
    title: "首轮推荐",
    command: "4个人吃午餐，先推荐4道菜。",
    narration: "系统识别人数、餐次和菜品数量，并从官方菜谱库生成首轮菜单。",
  },
  3: {
    title: "最小修改",
    command: "我不吃鸡蛋，其他菜尽量别动。",
    narration: "系统把不吃鸡蛋识别为忌口，只替换必要菜品，其他菜尽量保留。",
  },
  4: {
    title: "营养评审",
    command: "我有高血压，也想减脂，推荐4道菜，尽量清淡一点。",
    narration: "系统同时处理健康目标和口味要求，给出本地营养结果与风险提示。",
  },
  5: {
    title: "版本回滚",
    command: "恢复第一版菜单",
    narration: "系统保留菜单历史，回滚会创建新的当前版本，并继续执行硬约束校验。",
  },
};

const $ = (id) => document.getElementById(id);

function setStep(step) {
  demoState.step = step;
  const data = stepData[step];
  $("stageTitle").textContent = data.title;
  $("commandText").textContent = data.command;
  $("narrationText").textContent = data.narration;
  document.querySelectorAll(".step-item").forEach((item) => {
    const active = Number(item.dataset.step) === step;
    item.classList.toggle("active", active);
  });
}

function setStepState(step, state) {
  const item = document.querySelector(`.step-item[data-step="${step}"]`);
  if (!item) return;
  const label = item.querySelector(".step-state");
  item.classList.toggle("done", state === "done");
  item.classList.toggle("running", state === "running");
  label.textContent = state === "done" ? "已完成" : state === "running" ? "运行中" : "待运行";
}

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 2600);
}

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

async function timed(label, callback) {
  const started = performance.now();
  const result = await callback();
  const elapsed = Math.round(performance.now() - started);
  $("latencyChip").textContent = `${elapsed} ms`;
  $("footerStatus").textContent = `${label} · ${elapsed} ms`;
  return result;
}

async function runHealth() {
  const health = await timed("服务检查完成", () => api("/api/health"));
  demoState.health = health;
  $("metricStatus").textContent = health.status === "ok" ? "在线" : "异常";
  $("metricRecipes").textContent = health.recipe_count;
  $("metricDishes").textContent = "--";
  $("metricVersion").textContent = "--";
  $("connectionPill").classList.add("connected");
  $("connectionText").textContent = "API 在线";
  $("answerCopy").textContent = `服务已连接，当前加载 ${health.recipe_count} 道官方菜谱和 ${health.user_count} 份健康档案。`;
  $("changeBadge").textContent = "连接正常";
  return health;
}

async function runRecommendation(messages, type) {
  const payload = { messages };
  if (type === "revision" && demoState.revisionSessionId) {
    payload.session_id = demoState.revisionSessionId;
  }
  const result = await timed("推荐完成", () => api("/api/recommend", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  demoState.currentResult = result;
  if (type === "initial") {
    demoState.initialResult = result;
    demoState.revisionSessionId = result.session_id;
  }
  if (type === "revision") {
    demoState.revisionResult = result;
  }
  renderResult(result);
  return result;
}

async function runNutrition() {
  const result = await timed("营养评审完成", () => api("/api/recommend", {
    method: "POST",
    body: JSON.stringify({
      messages: ["我有高血压，也想减脂，推荐4道菜，尽量清淡一点。"],
    }),
  }));
  demoState.currentResult = result;
  renderResult(result);
  return result;
}

async function runRollback() {
  if (!demoState.revisionSessionId) {
    await runRecommendation(["4个人吃午餐，先推荐4道菜。"], "initial");
    await runRecommendation(["我不吃鸡蛋，其他菜尽量别动。"], "revision");
  }
  await timed("历史读取完成", () => api(`/api/sessions/${demoState.revisionSessionId}/history`));
  const result = await timed("回滚完成", () => api("/api/recommend", {
    method: "POST",
    body: JSON.stringify({
      session_id: demoState.revisionSessionId,
      messages: [],
      rollback_to: 1,
    }),
  }));
  demoState.currentResult = result;
  renderResult(result);
  return result;
}

async function runStep(step = demoState.step) {
  if (demoState.running) return;
  demoState.running = true;
  setStep(step);
  setStepState(step, "running");
  $("runStepButton").disabled = true;
  $("runAllButton").disabled = true;
  try {
    if (step === 1) await runHealth();
    if (step === 2) await runRecommendation(["4个人吃午餐，先推荐4道菜。"], "initial");
    if (step === 3) {
      if (!demoState.revisionSessionId) {
        await runRecommendation(["4个人吃午餐，先推荐4道菜。"], "initial");
      }
      await runRecommendation(["我不吃鸡蛋，其他菜尽量别动。"], "revision");
    }
    if (step === 4) await runNutrition();
    if (step === 5) await runRollback();
    setStepState(step, "done");
    showToast(`${stepData[step].title}已完成`);
    if (step < 5) setStep(step + 1);
  } catch (error) {
    setStepState(step, "idle");
    showToast(`运行失败：${error.message}`, true);
  } finally {
    demoState.running = false;
    $("runStepButton").disabled = false;
    $("runAllButton").disabled = false;
  }
}

async function runAll() {
  if (demoState.running) return;
  for (let step = 1; step <= 5; step += 1) {
    setStep(step);
    await runStep(step);
  }
  setStep(5);
}

function renderResult(result) {
  const menu = result.menu || [];
  $("metricDishes").textContent = menu.length || "--";
  $("metricVersion").textContent = result.menu_version ? `v${result.menu_version}` : "--";
  $("menuList").innerHTML = menu.length
    ? menu.map((item, index) => `
      <article class="dish-card">
        <div class="dish-number">${String(index + 1).padStart(2, "0")}</div>
        <div class="dish-content">
          <h4>${escapeHtml(item.name)}</h4>
          <p>${escapeHtml(item.reason || "来自官方菜谱库的约束匹配结果")}</p>
          <div class="dish-tags">${(item.labels || []).slice(0, 4).map((label) => `<span>${escapeHtml(label)}</span>`).join("")}</div>
        </div>
      </article>
    `).join("")
    : '<div class="empty-state">暂无菜单结果。</div>';

  const constraints = result.constraints || {};
  const items = [
    ["用餐人数", constraints.people_count ? `${constraints.people_count} 人` : "未指定"],
    ["菜品数量", constraints.requested_dish_count ? `${constraints.requested_dish_count} 道` : `${menu.length || "--"} 道`],
    ["餐次", constraints.meal || "未指定"],
    ["忌口", (constraints.avoid_ingredients || []).join("、") || "无"],
    ["过敏", (constraints.allergens || []).join("、") || "无"],
    ["健康目标", (constraints.health_goals || []).join("、") || "无"],
  ];
  $("constraintList").innerHTML = items.map(([label, value]) => `
    <div class="constraint-row"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");

  const nutrition = result.nutrition;
  if (nutrition && nutrition.table && nutrition.per_person) {
    $("nutritionGrid").innerHTML = [
      ["热量", nutrition.per_person.kcal, "kcal/人"],
      ["蛋白质", nutrition.per_person.protein_g, "g/人"],
      ["脂肪", nutrition.per_person.fat_g, "g/人"],
      ["钠", nutrition.per_person.sodium_mg, "mg/人"],
    ].map(([label, value, unit]) => `
      <div class="nutrition-item"><span>${label}</span><strong>${escapeHtml(value)}</strong><small>${unit}</small></div>
    `).join("");
  }
  const review = result.nutrition_review || {};
  $("reviewSummary").textContent = review.summary || "营养评审将在结果返回后显示。";
  $("answerCopy").textContent = result.answer || "暂无中文说明。";
  $("changeBadge").textContent = result.changes?.mode === "minimal_revision"
    ? `最小修改 · 替换 ${result.changes.change_count || 0} 道`
    : result.changes?.mode === "rollback"
      ? `已回滚 · 当前 v${result.menu_version}`
      : "新菜单";
}

function resetDemo() {
  window.location.reload();
}

function copyCommand() {
  navigator.clipboard.writeText(stepData[demoState.step].command)
    .then(() => showToast("本步输入已复制"))
    .catch(() => showToast("复制失败，请手动选择文本", true));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

document.querySelectorAll(".step-item").forEach((item) => {
  item.addEventListener("click", () => setStep(Number(item.dataset.step)));
});
document.getElementById("runStepButton").addEventListener("click", () => runStep());
document.getElementById("runAllButton").addEventListener("click", runAll);
document.getElementById("resetButton").addEventListener("click", resetDemo);
document.getElementById("copyCommandButton").addEventListener("click", copyCommand);

setStep(1);
