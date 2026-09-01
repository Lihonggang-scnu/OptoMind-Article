"use strict";

const state = {
  mode: "replay",
  catalog: null,
  run: null,
  selectedRunId: null,
  selectedRouteId: null,
  selectedStage: 0,
  hiddenRoutes: new Set(),
  readiness: null,
  liveRuns: [],
  activeLiveRunId: null,
  livePoller: null,
  accessToken: "",
};

const researchExamples = {
  swir: "我需要一个双功能多层膜：在近红外 800–1500 nm 波段透射率尽可能高，同时在紫外 200–400 nm 波段反射率尽可能高。衬底是熔融石英，总层数控制在 30 层以内。",
  gas: "我正在为工业烟气在线监测系统设计共享前端红外滤光膜。探测器需要同时透过一氧化碳 4.55–4.75 μm 和二氧化碳 4.15–4.35 μm 两个窄波段，并在 3.60–4.00 μm 和 4.85–5.20 μm 抑制背景热辐射。请在空气正入射、CaF2 基底条件下，使用常规可沉积红外无机介质材料设计不超过 24 层的平面多层膜。",
};

const $ = (selector) => document.querySelector(selector);
const svgNS = "http://www.w3.org/2000/svg";
const literaturePalette = ["#6d64d8", "#008b87", "#3478c8", "#9b62b5", "#2f9f7c"];

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function svgNode(tag, attributes = {}) {
  const element = document.createElementNS(svgNS, tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, maximumFractionDigits = 0) {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(number);
}

function formatScore(value, digits = 6) {
  const number = finiteNumber(value);
  return number === null ? "未产生" : number.toFixed(digits);
}

function formatDelta(value) {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return `${number >= 0 ? "+" : ""}${number.toFixed(6)}`;
}

function formatPercent(value, digits = 2) {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function formatDuration(seconds) {
  const value = finiteNumber(seconds);
  if (value === null) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.round((value % 3600) / 60);
  return hours > 0 ? `${hours}小时${minutes}分` : `${minutes}分钟`;
}

function artifactUrl(runId, path) {
  const encodedPath = String(path)
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `/artifacts/${encodeURIComponent(runId)}/${encodedPath}`;
}

function artifactLink(runId, file, label = null) {
  const link = node("a", "artifact-link", label || file.label || file.path);
  link.href = artifactUrl(runId, file.path || file);
  link.target = "_blank";
  link.rel = "noreferrer";
  return link;
}

function routeColor(route, index = 0) {
  if (route.source === "llm_memory_control") return "#e78934";
  return literaturePalette[index % literaturePalette.length];
}

function routeById(routeId) {
  return state.run?.routes?.find((route) => route.route_id === routeId) || null;
}

function publicRouteTitle(route) {
  const labels = {
    periodic_stack: "周期性高低折射率膜系",
    chirped_stack: "渐变厚度宽带膜系",
    custom_layered_stack: "定制多层干涉结构",
    defect_cavity: "缺陷腔多通带滤光结构",
    optimize_existing_stack: "非周期厚度优化膜系",
  };
  const structure = labels[route.route_kind] || "多层薄膜候选路线";
  const materials = [...new Set(route.materials || [])]
    .slice(0, 3)
    .map(displayMaterial)
    .join(" / ");
  return `${materials ? `${materials} · ` : ""}${structure}`;
}

function publicHypothesis(route) {
  const labels = {
    periodic_stack: "检验周期性高、低折射率膜层能否形成目标反射带，同时维持指定波段的透射能力。",
    chirped_stack: "检验沿膜厚方向逐步改变光学厚度，能否扩大目标反射带并兼顾所需透射窗口。",
    custom_layered_stack: "检验按目标波段定制的多层干涉结构，能否同时协调多个透射、反射或吸收要求。",
    defect_cavity: "检验在反射膜系中引入缺陷腔，能否在抑制背景中形成所需窄通带并保持带外性能。",
    optimize_existing_stack: "检验解除严格周期约束、分别优化各层厚度后，能否在同一冻结标准下提高综合得分。",
  };
  const base = labels[route.route_kind] || "检验该材料与层结构组合能否在统一评价标准下改善目标波段的综合光学性能。";
  if (route.source === "llm_memory_control") {
    return `该路线不接收检索文献，仅依据模型已有知识独立提出。${base}`;
  }
  return base;
}

function displayMaterial(material) {
  const normalized = String(material || "");
  const labels = {
    al2o3: "Al2O3",
    hfo2: "HfO2",
    mgf2: "MgF2",
    ge: "Ge",
    si: "Si",
    sio2: "SiO2",
    zns: "ZnS",
  };
  return labels[normalized.toLowerCase()] || normalized;
}

function metricDisplayName(metric) {
  const labels = {
    mean_transmittance: "波段平均透射率",
    mean_reflectance: "波段平均反射率",
    reflectance_stopband: "阻带平均反射率",
  };
  return labels[metric?.metric] || metric?.metric || "光学观测量";
}

function metricByVariable(variable) {
  return state.run?.scoring?.metrics?.find((metric) => metric.variable === variable) || null;
}

function statusLabel(value) {
  const labels = {
    completed: "执行完成",
    compiled: "任务已生成",
    invalid: "任务未通过核验",
    failed: "执行未完成",
    not_run: "未进入仿真",
    stopped_round_limit: "达到轮次上限",
    stopped_llm_advice: "充分探索后停止",
    active: "继续研究",
    finished: "研究完成",
  };
  return labels[value] || value || "状态已记录";
}

function actionLabel(action) {
  const labels = {
    refine_route: "继续并调整",
    stop_completed: "结束路线",
    retry_compilation: "修正任务后重试",
    continue_route: "继续实验",
  };
  return labels[action] || action || "结果已记录";
}

function readableFeedback(iteration) {
  const action = iteration.feedback?.action;
  if (iteration.compilation_status === "invalid") {
    return "本轮任务未通过可执行性核验，因此不进入评分；核验状态与错误类别被保留，供后续任务修正使用。";
  }
  if (iteration.run_status === "failed" || iteration.run_status === "not_run") {
    return "本轮没有形成可评分的仿真观测；系统保留未执行或失败状态，不用缺失值替代物理结果。";
  }
  if (action === "refine_route" || action === "continue_route") {
    return "本轮结果仍有可利用信息：保留已验证候选，并根据观测偏差调整下一轮任务。";
  }
  if (action === "retry_compilation") {
    return "本轮任务未能进入有效仿真，系统保留错误类别并修正下一轮任务表达。";
  }
  if (action === "stop_completed") {
    return "该路线已达到明确停止条件，系统结束继续探索并保留历史最优结果。";
  }
  return "本轮观测、候选和状态已写入证据链。";
}

function readableTermination(route) {
  const reason = String(route.termination_reason || "");
  if (reason.includes("per-route cap") || route.status === "stopped_round_limit") {
    return "达到每条路线的实验轮次上限，结束探索并保留历史最优结果。";
  }
  if (reason.includes("explicitly assessed no further benefit")) {
    const match = reason.match(/after\s+(\d+)\s+executed rounds/i);
    return `完成${match ? match[1] : "多"}轮有效实验后，判断继续探索的预期收益有限。`;
  }
  if (route.status) return statusLabel(route.status);
  return "路线停止依据已记录在原始产物中。";
}

function regionLabel(metric) {
  const range = metric?.region?.wavelength_nm;
  if (Array.isArray(range) && range.length >= 2) {
    return `${formatNumber(range[0], 2)}–${formatNumber(range[1], 2)} nm`;
  }
  return "动态波段";
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload.error) message = payload.error;
    } catch (_) {
      // Keep the HTTP status if the body is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

async function postJson(url, payload, token = "") {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.error) message = body.error;
    } catch (_) {
      // Keep the HTTP status when no JSON error body is available.
    }
    throw new Error(message);
  }
  return response.json();
}

function liveStatusLabel(status) {
  const labels = {
    starting: "正在启动",
    running: "研究进行中",
    stopping: "正在停止",
    stopped: "已停止",
    completed: "研究完成",
    failed: "运行未完成",
    interrupted: "服务重启前中断",
  };
  return labels[status] || status || "状态待确认";
}

function formatElapsed(seconds) {
  const value = Math.max(0, Math.floor(finiteNumber(seconds) ?? 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function setMode(mode) {
  state.mode = mode === "live" ? "live" : "replay";
  const live = state.mode === "live";
  $("#live-content").hidden = !live;
  $("#replay-content").hidden = live || !state.run;
  $("#mode-live").classList.toggle("active", live);
  $("#mode-replay").classList.toggle("active", !live);
  $("#mode-live").setAttribute("aria-selected", live ? "true" : "false");
  $("#mode-replay").setAttribute("aria-selected", live ? "false" : "true");
  if (live) {
    location.hash = state.activeLiveRunId ? `live=${encodeURIComponent(state.activeLiveRunId)}` : "live";
    renderLiveWorkspace();
  } else if (state.selectedRunId) {
    location.hash = `run=${encodeURIComponent(state.selectedRunId)}`;
  }
  window.scrollTo({ top: 0, behavior: "instant" });
}

function setLoading(loading) {
  $("#loading-state").hidden = !loading;
  if (loading) {
    $("#replay-content").hidden = true;
    $("#live-content").hidden = true;
  }
  if (loading) $("#error-state").hidden = true;
}

function showError(error) {
  $("#loading-state").hidden = true;
  $("#replay-content").hidden = true;
  $("#live-content").hidden = true;
  $("#error-state").hidden = false;
  $("#error-message").textContent = error instanceof Error ? error.message : String(error);
}

async function initialize() {
  setLoading(true);
  try {
    const [catalog, readiness, livePayload] = await Promise.all([
      getJson("/api/catalog"),
      getJson("/api/live/readiness"),
      getJson("/api/live/runs"),
    ]);
    state.catalog = catalog;
    state.readiness = readiness;
    state.liveRuns = livePayload.runs || [];
    const liveHash = location.hash.match(/^#live(?:=(.+))?$/);
    if (liveHash?.[1]) {
      const requestedLiveId = decodeURIComponent(liveHash[1]);
      if (state.liveRuns.some((run) => run.run_id === requestedLiveId)) {
        state.activeLiveRunId = requestedLiveId;
      }
    }
    renderArchiveSummary();
    renderRunList();
    $("#mode-replay").disabled = !state.catalog.runs.length;
    renderReadiness();
    renderLiveHistory();
    const requested = decodeURIComponent(location.hash.replace(/^#run=/, ""));
    const initial = state.catalog.runs.some((run) => run.run_id === requested)
      ? requested
      : state.catalog.runs[0]?.run_id;
    if (initial) {
      await loadRun(initial);
    } else {
      $("#loading-state").hidden = true;
    }
    if (liveHash || !initial) setMode("live");
  } catch (error) {
    showError(error);
  }
}

function renderArchiveSummary() {
  const totals = state.catalog.totals || {};
  const container = $("#archive-stats");
  const rows = [
    [totals.runs, "组运行"],
    [totals.routes, "条路线"],
    [totals.iterations, "轮记录"],
  ];
  container.replaceChildren(
    ...rows.map(([value, label]) => {
      const item = node("div", "archive-stat");
      item.append(node("strong", "", formatNumber(value)), node("span", "", label));
      return item;
    }),
  );
}

function renderRunList() {
  const list = $("#run-list");
  if (!state.catalog.runs.length) {
    list.replaceChildren(node("p", "sidebar-empty", "完成一次真实研究后，运行会出现在这里。"));
    return;
  }
  const buttons = state.catalog.runs.map((run, index) => {
    const button = node("button", "run-nav-button");
    button.type = "button";
    button.dataset.runId = run.run_id;
    button.setAttribute("aria-current", run.run_id === state.selectedRunId ? "page" : "false");
    const number = node("span", "run-number", String(run.group || index + 1).padStart(2, "0"));
    const copy = node("span", "run-nav-copy");
    copy.append(
      node("strong", "", run.title),
      node(
        "span",
        "",
        `${run.route_count}条路线 · 冠军 ${formatScore(run.winner?.score, 3)}`,
      ),
    );
    button.append(number, copy);
    button.addEventListener("click", () => loadRun(run.run_id));
    return button;
  });
  list.replaceChildren(...buttons);
}

async function loadRun(runId) {
  if (!runId) return;
  setLoading(true);
  try {
    state.run = await getJson(`/api/runs/${encodeURIComponent(runId)}`);
    state.selectedRunId = runId;
    state.selectedRouteId = state.run.routes.find((route) => route.winner)?.route_id
      || state.run.routes[0]?.route_id
      || null;
    state.selectedStage = 0;
    state.hiddenRoutes.clear();
    location.hash = `run=${encodeURIComponent(runId)}`;
    renderRunList();
    renderRun();
    document.body.classList.remove("sidebar-open");
    $("#loading-state").hidden = true;
    setMode("replay");
    window.scrollTo({ top: 0, behavior: "instant" });
  } catch (error) {
    showError(error);
  }
}

function renderRun() {
  renderHero();
  renderKpis();
  renderWorkflow();
  renderScoring();
  renderComparison();
  renderChartLegend();
  renderChart();
  renderRouteTabs();
  renderSelectedRoute();
  renderLeaderboard();
  renderChampion();
  renderEvidence();
}

function renderHero() {
  const summary = state.run.summary;
  $("#group-label").textContent = `第 ${summary.group || "—"} 组正式运行`;
  $("#run-status").textContent = statusLabel(summary.status);
  $("#run-title").textContent = summary.title;
  $("#run-question").textContent = summary.question;
  $("#run-id").textContent = summary.run_id;
  const tags = (summary.tags || []).map((tag) => node("span", "tag", tag));
  $("#run-tags").replaceChildren(...tags);
  const finalLink = $("#open-final-answer");
  finalLink.href = artifactUrl(summary.run_id, "FINAL_ANSWER.md");
}

function renderKpis() {
  const summary = state.run.summary;
  const rows = [
    [summary.route_count, "并行研究路线"],
    [summary.iteration_count, "逐轮实验记录"],
    [summary.completed_execution_count, "完成仿真执行"],
    [summary.physically_valid_candidate_count, "物理有效候选"],
    [summary.forward_evaluations, "前向计算次数"],
    [formatDuration(summary.wall_seconds), "完整运行墙钟时间", true],
  ];
  const cards = rows.map(([value, label, preformatted]) => {
    const card = node("div", "kpi-card");
    card.append(
      node("strong", "", preformatted ? value : formatNumber(value)),
      node("span", "", label),
    );
    return card;
  });
  $("#kpi-grid").replaceChildren(...cards);
}

function renderWorkflow() {
  const workflow = $("#workflow");
  const buttons = state.run.evidence.map((stage, index) => {
    const button = node("button", `workflow-step${state.selectedStage === index ? " active" : ""}`);
    button.type = "button";
    button.setAttribute("aria-pressed", state.selectedStage === index ? "true" : "false");
    button.append(
      node("span", "workflow-node", String(index + 1).padStart(2, "0")),
      node("strong", "", stage.label),
    );
    button.addEventListener("click", () => {
      state.selectedStage = index;
      renderWorkflow();
    });
    return button;
  });
  workflow.replaceChildren(...buttons);
  renderStageDetail();
}

function renderStageDetail() {
  const stage = state.run.evidence[state.selectedStage];
  const detail = $("#stage-detail");
  if (!stage) {
    detail.replaceChildren(node("div", "empty-state", "该阶段没有可展示的产物"));
    return;
  }
  const copy = node("div");
  copy.append(node("h3", "", stage.label), node("p", "", stage.description));
  const files = node("div", "stage-files");
  files.append(...stage.files.map((file) => artifactLink(state.selectedRunId, file)));
  detail.replaceChildren(copy, files);
}

function renderScoring() {
  $("#formula").textContent = state.run.scoring.formula || "未记录评分公式";
  const rows = (state.run.scoring.metrics || []).map((metric, index) => {
    const row = node("div", "metric-row");
    const copy = node("div", "metric-copy");
    copy.append(
      node("strong", "", metricDisplayName(metric)),
      node("code", "metric-code", metric.variable || metric.metric),
    );
    row.append(
      node("span", "metric-index", String(index + 1).padStart(2, "0")),
      copy,
      node("span", "", regionLabel(metric)),
    );
    return row;
  });
  $("#metric-list").replaceChildren(...rows);
}

function renderComparison() {
  const comparison = state.run.source_comparison || {};
  const valid = $("#comparison-valid");
  valid.textContent = comparison.valid ? "可直接比较" : "比较条件不足";
  valid.className = `comparison-valid${comparison.valid ? " valid" : ""}`;
  const literature = finiteNumber(comparison.literature_best?.score);
  const control = finiteNumber(comparison.control_best?.score);
  const maximum = Math.max(literature ?? 0, control ?? 0, 0.0001);
  const rows = [
    { label: "文献启发路线", value: literature, color: "#6d64d8" },
    { label: "记忆对照路线", value: control, color: "#e78934" },
  ].map((item) => {
    const row = node("div", "comparison-row");
    const label = node("div", "comparison-label");
    const dot = node("span", "legend-dot");
    dot.style.setProperty("--route-color", item.color);
    label.append(dot, node("span", "", item.label));
    const track = node("div", "bar-track");
    const fill = node("div", "bar-fill");
    fill.style.setProperty("--route-color", item.color);
    fill.style.width = `${item.value !== null ? Math.max(2, (item.value / maximum) * 100) : 0}%`;
    track.append(fill);
    row.append(label, track, node("span", "comparison-score", formatScore(item.value, 4)));
    return row;
  });
  $("#comparison-bars").replaceChildren(...rows);

  const conclusion = $("#comparison-conclusion");
  const delta = finiteNumber(comparison.delta_control_minus_literature);
  if (!comparison.valid || delta === null) {
    conclusion.replaceChildren(
      node("strong", "", "本组未形成完整跨来源比较。"),
      document.createTextNode(" 原始状态仍保留在排名和路线汇总中。"),
    );
    return;
  }
  const winnerLabel = delta >= 0 ? "独立记忆对照路线" : "文献启发路线";
  conclusion.replaceChildren(
    node("strong", "", `${winnerLabel}领先。`),
    document.createTextNode(` 两类路线在相同冻结标准下的最佳得分差为 ${Math.abs(delta).toFixed(6)}。`),
  );
}

function renderChartLegend() {
  const legend = $("#chart-legend");
  const buttons = state.run.routes.map((route, index) => {
    const button = node("button", `legend-button${state.hiddenRoutes.has(route.route_id) ? " muted" : ""}`);
    button.type = "button";
    const color = routeColor(route, index);
    const dot = node("span", "legend-dot");
    dot.style.setProperty("--route-color", color);
    button.append(dot, node("strong", "", route.route_id), node("span", "", route.source_label));
    button.addEventListener("click", () => {
      if (state.hiddenRoutes.has(route.route_id)) state.hiddenRoutes.delete(route.route_id);
      else state.hiddenRoutes.add(route.route_id);
      renderChartLegend();
      renderChart();
    });
    return button;
  });
  legend.replaceChildren(...buttons);
}

function renderChart() {
  const container = $("#score-chart");
  const visibleRoutes = state.run.routes.filter(
    (route) => route.trajectory?.length && !state.hiddenRoutes.has(route.route_id),
  );
  if (!visibleRoutes.length) {
    container.replaceChildren(node("div", "empty-state", "请选择至少一条有得分记录的路线"));
    return;
  }
  const width = 960;
  const height = 340;
  const margin = { top: 20, right: 26, bottom: 42, left: 58 };
  const scores = visibleRoutes.flatMap((route) => route.trajectory.map((point) => Number(point.score)));
  let minimum = Math.min(...scores);
  let maximum = Math.max(...scores);
  const rawRange = maximum - minimum;
  const padding = rawRange > 0 ? rawRange * 0.14 : Math.max(maximum * 0.08, 0.1);
  minimum = Math.max(0, minimum - padding);
  maximum += padding;
  const roundCount = Math.max(...visibleRoutes.map((route) => route.trajectory.length), 2);
  const x = (round) => margin.left + ((round - 1) / Math.max(1, roundCount - 1)) * (width - margin.left - margin.right);
  const y = (score) => margin.top + ((maximum - score) / (maximum - minimum)) * (height - margin.top - margin.bottom);

  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" });
  const yTicks = 5;
  for (let index = 0; index < yTicks; index += 1) {
    const ratio = index / (yTicks - 1);
    const score = maximum - ratio * (maximum - minimum);
    const position = y(score);
    svg.append(
      svgNode("line", {
        x1: margin.left,
        y1: position,
        x2: width - margin.right,
        y2: position,
        class: "chart-grid-line",
      }),
    );
    const label = svgNode("text", {
      x: margin.left - 10,
      y: position + 3,
      "text-anchor": "end",
      class: "chart-axis-label",
    });
    label.textContent = score.toFixed(2);
    svg.append(label);
  }
  for (let round = 1; round <= roundCount; round += 1) {
    const label = svgNode("text", {
      x: x(round),
      y: height - 14,
      "text-anchor": "middle",
      class: "chart-axis-label",
    });
    label.textContent = `第${round}轮`;
    svg.append(label);
  }

  visibleRoutes.forEach((route) => {
    const originalIndex = state.run.routes.findIndex((item) => item.route_id === route.route_id);
    const color = routeColor(route, originalIndex);
    const points = route.trajectory.map((point, index) => [x(index + 1), y(Number(point.score)), point]);
    const pathData = points.map(([px, py], index) => `${index === 0 ? "M" : "L"}${px.toFixed(2)},${py.toFixed(2)}`).join(" ");
    const path = svgNode("path", { d: pathData, class: "chart-line" });
    path.style.setProperty("--route-color", color);
    svg.append(path);
    points.forEach(([px, py, point], pointIndex) => {
      const circle = svgNode("circle", {
        cx: px,
        cy: py,
        r: route.winner && pointIndex === points.length - 1 ? 5 : 4,
        class: `chart-point${route.winner ? " winner-point" : ""}`,
      });
      circle.style.setProperty("--route-color", color);
      const title = svgNode("title");
      title.textContent = `${route.route_id} · 第${pointIndex + 1}轮 · ${formatScore(point.score)}`;
      circle.append(title);
      svg.append(circle);
    });
  });
  container.replaceChildren(svg);
}

function renderRouteTabs() {
  const tabs = $("#route-tabs");
  const buttons = state.run.routes.map((route, index) => {
    const active = route.route_id === state.selectedRouteId;
    const button = node("button", `route-tab${active ? " active" : ""}`);
    button.type = "button";
    button.role = "tab";
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.style.setProperty("--route-color", routeColor(route, index));
    const dot = node("span", "legend-dot");
    dot.style.setProperty("--route-color", routeColor(route, index));
    button.append(dot, node("span", "", route.route_id), route.winner ? node("strong", "", "冠军") : document.createTextNode(""));
    button.addEventListener("click", () => {
      state.selectedRouteId = route.route_id;
      renderRouteTabs();
      renderSelectedRoute();
    });
    return button;
  });
  tabs.replaceChildren(...buttons);
}

function renderSelectedRoute() {
  const route = routeById(state.selectedRouteId);
  if (!route) return;
  const overview = $("#route-overview");
  const copy = node("div");
  const heading = node("div", "route-title-row");
  heading.append(node("h3", "", publicRouteTitle(route)));
  const source = node("span", `source-badge${route.source === "llm_memory_control" ? " control" : ""}`, route.source_label);
  heading.append(source);
  if (route.winner) heading.append(node("span", "winner-badge", "本组冠军"));
  copy.append(heading, node("p", "hypothesis-label", "本路线待验证的科学判断"));
  copy.append(node("p", "hypothesis", publicHypothesis(route)));
  const meta = node("div", "route-meta-list");
  (route.materials || []).forEach((material) => meta.append(node("span", "", displayMaterial(material))));
  if (route.evidence_ids?.length) meta.append(node("span", "", `${route.evidence_ids.length}项文献证据`));
  meta.append(node("span", "", `${route.rounds.length}轮记录`));
  copy.append(meta);

  const progress = node("div", "route-progress");
  const rows = [
    [formatScore(route.progress.initial_score, 4), "首次有效得分"],
    [formatScore(route.progress.peak_score, 4), "路线历史最高"],
    [formatPercent(route.progress.net_change_percent), "首次至末次有效轮净变化"],
    [route.progress.strictly_increasing ? "是" : "否", "有效得分是否逐轮上升"],
  ];
  rows.forEach(([value, label]) => {
    const item = node("div", "progress-stat");
    item.append(node("strong", "", value), node("span", "", label));
    progress.append(item);
  });
  progress.append(node("p", "termination-note", readableTermination(route)));
  overview.replaceChildren(copy, progress);

  const grid = $("#iteration-grid");
  const cards = route.rounds.map((iteration) => {
    const card = node("button", "iteration-card");
    card.type = "button";
    const routeIndex = state.run.routes.findIndex((item) => item.route_id === route.route_id);
    card.style.setProperty("--route-color", routeColor(route, routeIndex));
    const head = node("div", "iteration-head");
    head.append(node("strong", "", `路线第 ${iteration.route_round} 轮`));
    const completed = iteration.run_status === "completed";
    head.append(node("span", `iteration-status${completed ? "" : " failed"}`, statusLabel(iteration.run_status || iteration.compilation_status)));
    const score = iteration.best_candidate?.frozen_score;
    const delta = finiteNumber(iteration.delta_from_previous);
    card.append(
      head,
      node("div", "iteration-score", formatScore(score, 4)),
      node("span", "iteration-score-label", "冻结标准得分"),
    );
    if (delta !== null) {
      card.append(node("span", `iteration-delta${delta < 0 ? " negative" : ""}`, `${formatDelta(delta)} 较上一有效轮`));
    }
    card.append(
      node("p", "iteration-feedback", readableFeedback(iteration)),
      node("span", "iteration-open", "打开本轮证据 →"),
    );
    card.addEventListener("click", () => openIterationDialog(route, iteration));
    return card;
  });
  grid.replaceChildren(...cards);
}

function renderLeaderboard() {
  $("#leaderboard-count").textContent = `${state.run.leaderboard.length}条可比较路线`;
  const rows = state.run.leaderboard.map((item) => {
    const row = node("div", "rank-row");
    const copy = node("div", "rank-copy");
    copy.append(
      node("strong", "", item.route_id),
      node("span", "rank-source", item.source_label),
    );
    row.append(
      node("span", "rank-number", String(item.rank).padStart(2, "0")),
      copy,
      node("span", "rank-score", formatScore(item.score, 4)),
    );
    return row;
  });
  $("#leaderboard").replaceChildren(...rows);
}

function renderChampion() {
  const panel = $("#champion-panel");
  const champion = state.run.champion;
  if (!champion) {
    panel.replaceChildren(node("div", "empty-state", "本组没有产生可评分冠军"));
    return;
  }
  const content = node("div", "champion-content");
  content.append(
    node("span", "champion-kicker", "VERIFIED CHAMPION"),
    node("h3", "", champion.route_id),
    node("span", "source-badge", champion.source_label),
    node("div", "champion-score", formatScore(champion.score, 6)),
    node("span", "champion-score-label", "冻结标准冠军得分"),
  );
  const metrics = node("div", "champion-metrics");
  Object.entries(champion.metric_values || {}).forEach(([variable, value]) => {
    const metric = node("div", "champion-metric");
    const definition = metricByVariable(variable);
    const label = definition
      ? `${metricDisplayName(definition)} · ${regionLabel(definition)}`
      : variable;
    metric.append(node("strong", "", formatScore(value, 6)), node("span", "", label));
    metrics.append(metric);
  });
  content.append(metrics);
  const candidate = champion.candidate || {};
  const materialNames = [...new Set(candidate.layer_materials || [])].map(displayMaterial);
  content.append(
    node(
      "p",
      "champion-meta",
      `候选：${champion.candidate_id || "—"}\n` +
        `产生于：${champion.iteration_id || "—"} · ${candidate.layer_count || "—"}层` +
        `${materialNames.length ? ` · 材料 ${materialNames.join(" / ")}` : ""}`,
    ),
  );
  panel.replaceChildren(content);
}

function renderEvidence() {
  const grid = $("#evidence-grid");
  const cards = state.run.evidence.map((stage, index) => {
    const card = node("article", "evidence-card");
    const head = node("div", "evidence-card-head");
    head.append(
      node("span", "evidence-number", String(index + 1).padStart(2, "0")),
      node("h3", "", stage.label),
    );
    const links = node("div", "evidence-links");
    links.append(...stage.files.map((file) => artifactLink(state.selectedRunId, file)));
    card.append(head, node("p", "", stage.description), links);
    return card;
  });
  grid.replaceChildren(...cards);
}

function openIterationDialog(route, iteration) {
  const dialog = $("#iteration-dialog");
  $("#dialog-kicker").textContent = `${route.route_id} · ${route.source_label}`;
  $("#dialog-title").textContent = `路线第 ${iteration.route_round} 轮（${iteration.iteration_id}）`;
  const content = $("#dialog-content");
  const candidate = iteration.best_candidate || {};
  const summary = node("div", "dialog-summary-grid");
  const stats = [
    [formatScore(candidate.frozen_score, 6), "冻结标准得分"],
    [formatDelta(iteration.delta_from_previous), "较上一有效轮"],
    [formatNumber(iteration.physically_valid_candidate_count), "物理有效候选"],
    [formatScore(candidate.robustness_score, 4), "扰动鲁棒性评分"],
  ];
  stats.forEach(([value, label]) => {
    const item = node("div", "dialog-stat");
    item.append(node("strong", "", value), node("span", "", label));
    summary.append(item);
  });
  content.replaceChildren(summary);

  if (candidate.candidate_id) {
    const candidateSection = node("section", "dialog-section");
    candidateSection.append(node("h3", "", "本轮代表候选"));
    const block = node("div", "feedback-block");
    const uniqueMaterials = [...new Set(candidate.layer_materials || [])].map(displayMaterial);
    block.append(
      node("strong", "", candidate.candidate_id),
      node(
        "p",
        "",
        `${candidate.layer_count || "—"}层 · ${uniqueMaterials.join(" / ") || "材料记录见原始任务"}`,
      ),
    );
    candidateSection.append(block);
    content.append(candidateSection);
  }

  const metricEntries = Object.entries(candidate.metric_values || {});
  if (metricEntries.length) {
    const metricSection = node("section", "dialog-section");
    metricSection.append(node("h3", "", "冻结指标观测值"));
    const grid = node("div", "metric-value-grid");
    metricEntries.forEach(([variable, value]) => {
      const row = node("div", "metric-value-row");
      const definition = metricByVariable(variable);
      const label = definition
        ? `${metricDisplayName(definition)} · ${regionLabel(definition)}`
        : variable;
      row.append(node("span", "", label), node("strong", "", formatScore(value, 6)));
      grid.append(row);
    });
    metricSection.append(grid);
    content.append(metricSection);
  }

  const feedbackSection = node("section", "dialog-section");
  feedbackSection.append(node("h3", "", "本轮结果如何影响下一步"));
  const feedback = node("div", "feedback-block");
  feedback.append(
    node("strong", "", actionLabel(iteration.feedback?.action)),
    node("p", "", readableFeedback(iteration)),
  );
  if (iteration.feedback?.preserve_candidate_ids?.length) {
    feedback.append(
      node(
        "p",
        "",
        `保留 ${iteration.feedback.preserve_candidate_ids.length} 个已验证候选作为后续比较基准。`,
      ),
    );
  }
  feedbackSection.append(feedback);
  content.append(feedbackSection);

  if (iteration.failure_categories?.length || iteration.compilation_errors?.length) {
    const failureSection = node("section", "dialog-section");
    failureSection.append(node("h3", "", "异常与未执行状态"));
    const block = node("div", "feedback-block");
    const rows = [...iteration.failure_categories, ...iteration.compilation_errors];
    block.append(node("p", "", rows.join("；")));
    failureSection.append(block);
    content.append(failureSection);
  }

  const rawSection = node("section", "dialog-section");
  rawSection.append(node("h3", "", "本轮原始只读证据"));
  const links = node("div", "raw-file-list");
  links.append(...(iteration.raw_files || []).map((file) => artifactLink(state.selectedRunId, file)));
  rawSection.append(links.childElementCount ? links : node("div", "empty-state", "本轮没有可公开的原始文件链接"));
  content.append(rawSection);

  dialog.showModal();
}

const liveStages = [
  { key: "request", label: "接收问题", events: ["request_received"] },
  { key: "standard", label: "理解与定标", events: ["problem_analyzed", "scoring_standard_fixed"] },
  { key: "research", label: "文献与路线", events: ["routes_planned_from_literature", "control_route_planned", "method_research_completed"] },
  { key: "strategy", label: "实验计划", events: ["strategy_planned", "route_round_quota_allocated"] },
  { key: "execution", label: "仿真执行", events: ["wave_started", "route_started", "tmm_batch_executed", "route_completed"] },
  { key: "feedback", label: "反馈再规划", events: ["feedback_decided", "reflection_completed", "route_revised"] },
  { key: "result", label: "排名与交付", events: ["scoring_standard_ranking_written", "tournament_summarized", "research_finished"] },
];

function renderReadiness() {
  const readiness = state.readiness || {};
  const rows = [
    ["Qwen 规划与编译", readiness.qwen_configured, false],
    ["Semantic Scholar 文献密钥", readiness.semantic_scholar_configured, true],
    ["VeriTMM 物理执行器", readiness.veritmm_available, false],
    ["运行目录可写", readiness.output_root_writable, false],
  ];
  const container = $("#readiness-panel");
  const title = node("div", "readiness-title", "服务端运行条件");
  const items = rows.map(([label, ready, optional]) => {
    const row = node("div", "readiness-row");
    const value = node("strong", ready ? "" : optional ? "optional" : "missing");
    value.textContent = ready ? "已就绪" : optional ? "未配置，可降级" : "待配置";
    row.append(node("span", "", label), value);
    return row;
  });
  const access = node("div", "readiness-row");
  access.append(
    node("span", "", "真实运行访问控制"),
    node("strong", "", readiness.access_token_required ? "需要口令" : "当前设备可直接运行"),
  );
  container.replaceChildren(title, ...items, access);
  $("#access-token-field").hidden = !readiness.access_token_required;
  const start = $("#start-research");
  start.disabled = !readiness.ready_for_real_run;
  start.textContent = readiness.ready_for_real_run
    ? "启动真实端到端研究"
    : "请先在服务端配置 Qwen 密钥";
}

function renderLiveWorkspace() {
  renderReadiness();
  renderLiveHistory();
  const run = state.liveRuns.find((item) => item.run_id === state.activeLiveRunId) || null;
  renderActiveLiveRun(run);
}

function renderLiveHistory() {
  const container = $("#live-history");
  if (!state.liveRuns.length) {
    container.replaceChildren(node("div", "empty-state", "尚未从网页发起真实研究。"));
    return;
  }
  const cards = state.liveRuns.map((run) => {
    const card = node(
      "button",
      `live-history-card${run.run_id === state.activeLiveRunId ? " active" : ""}`,
    );
    card.type = "button";
    const question = String(run.question || "未记录问题");
    const created = run.created_at_utc
      ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(run.created_at_utc))
      : "时间未记录";
    card.append(
      node("code", "", run.run_id),
      node("strong", "", question.length > 72 ? `${question.slice(0, 72)}…` : question),
      node("span", "", `${liveStatusLabel(run.status)} · ${created}`),
    );
    card.addEventListener("click", () => selectLiveRun(run.run_id));
    return card;
  });
  container.replaceChildren(...cards);
}

function eventDescription(event) {
  const type = event.event_type;
  if (type === "request_received") return "研究问题已写入本次运行证据链。";
  if (type === "problem_analyzed") return `问题理解完成，状态：${event.status || "已分析"}。`;
  if (type === "scoring_standard_fixed") return `已锁定 ${event.metric_count || "多"} 项指标及本次评分公式。`;
  if (type === "routes_planned_from_literature") return `文献检索形成 ${event.route_count || 0} 条候选路线，关联 ${event.papers || 0} 篇文献记录。`;
  if (type === "control_route_planned") return "独立记忆对照路线已经生成，未接收文献内容。";
  if (type === "strategy_planned") return `实验策略已形成：${event.normal_route_count || 0} 条文献路线，${event.control_route_count || 0} 条对照路线。`;
  if (type === "route_started") return `${event.route_id || "路线"} 开始第 ${event.iteration_id || "新"} 轮任务。`;
  if (type === "tmm_batch_executed") return `VeriTMM 已执行本波次任务批次，共 ${event.tasks || 0} 个任务。`;
  if (type === "route_completed") return `${event.route_id || "路线"} 本轮状态为 ${statusLabel(event.run_status)}，产生 ${event.valid_candidates || 0} 个物理有效候选。`;
  if (type === "feedback_decided") return `${event.route_id || "路线"} 的观测已转化为下一步反馈决定。`;
  if (type === "route_revised") return `${event.route_id || "路线"} 已根据真实结果更新后续实验计划。`;
  if (type === "scoring_standard_ranking_written") return "冻结标准排名已经写入。";
  if (type === "tournament_summarized") return "跨路线比较、冠军与鲁棒性汇总已经完成。";
  if (type === "research_finished") return `本次研究结束，最终状态：${event.status || "已记录"}。`;
  return "该阶段状态已经写入运行事件流。";
}

function eventLabel(type) {
  const labels = {
    request_received: "接收问题",
    problem_analyzed: "问题理解",
    scoring_standard_fixed: "冻结标准",
    routes_planned_from_literature: "文献路线",
    control_route_planned: "对照路线",
    method_research_completed: "方法研究",
    strategy_planned: "实验计划",
    route_round_quota_allocated: "轮次分配",
    wave_started: "开始波次",
    route_started: "开始路线",
    tmm_batch_executed: "执行仿真",
    route_completed: "完成路线轮次",
    feedback_decided: "形成反馈",
    reflection_completed: "路线反思",
    route_revised: "调整路线",
    scoring_standard_ranking_written: "冻结排名",
    tournament_summarized: "结果汇总",
    research_finished: "研究完成",
  };
  return labels[type] || type || "状态记录";
}

function renderActiveLiveRun(run) {
  const section = $("#active-run-section");
  if (!run) {
    section.hidden = true;
    stopLivePolling();
    return;
  }
  section.hidden = false;
  $("#live-run-id").textContent = run.run_id;
  $("#live-question").textContent = run.question;
  $("#live-elapsed").textContent = formatElapsed(run.elapsed_seconds);
  const status = $("#live-status");
  status.textContent = liveStatusLabel(run.status);
  status.className = `live-status ${run.status || ""}`;

  const kpis = [
    [run.event_count, "阶段事件"],
    [run.iteration_count, "迭代目录"],
    [run.completed_execution_count, "完成仿真轮次"],
    [run.physically_valid_candidate_count, "物理有效候选"],
  ].map(([value, label]) => {
    const item = node("div", "live-kpi");
    item.append(node("strong", "", formatNumber(value)), node("span", "", label));
    return item;
  });
  $("#live-kpis").replaceChildren(...kpis);

  const eventTypes = new Set(run.event_types || (run.recent_events || []).map((event) => event.event_type));
  let currentStage = 0;
  liveStages.forEach((stage, index) => {
    if (stage.events.some((event) => eventTypes.has(event))) currentStage = index;
  });
  if (run.status === "completed") currentStage = liveStages.length - 1;
  const stages = liveStages.map((stage, index) => {
    const item = node("div", `live-stage${index < currentStage ? " done" : ""}${index === currentStage ? " current" : ""}`);
    item.append(node("strong", "", String(index + 1).padStart(2, "0")), document.createTextNode(stage.label));
    return item;
  });
  $("#live-stage-track").replaceChildren(...stages);

  const eventRows = (run.recent_events || []).slice(-18).map((event) => {
    const row = node("div", "live-event-row");
    row.append(
      node("time", "", formatElapsed(event.elapsed_seconds)),
      node("strong", "", eventLabel(event.event_type)),
      node("span", "", eventDescription(event)),
    );
    return row;
  });
  if (run.launch_error) {
    const row = node("div", "live-event-row");
    row.append(node("time", "", "—"), node("strong", "", "启动失败"), node("span", "", run.launch_error));
    eventRows.push(row);
  }
  $("#live-event-stream").replaceChildren(
    ...(eventRows.length ? eventRows : [node("div", "empty-state", "进程已经启动，正在等待第一条阶段事件。")]),
  );

  const active = ["starting", "running", "stopping"].includes(run.status);
  $("#stop-live-run").hidden = !active;
  $("#stop-live-run").disabled = run.status === "stopping";
  $("#open-live-replay").hidden = !run.replay_available;
  if (active) startLivePolling(run.run_id);
  else stopLivePolling();
}

async function selectLiveRun(runId) {
  stopLivePolling();
  state.activeLiveRunId = runId;
  setMode("live");
  try {
    const run = await getJson(`/api/live/runs/${encodeURIComponent(runId)}`);
    const index = state.liveRuns.findIndex((item) => item.run_id === runId);
    if (index >= 0) state.liveRuns[index] = run;
    else state.liveRuns.unshift(run);
    renderLiveHistory();
    renderActiveLiveRun(run);
  } catch (error) {
    showLiveFormError(error);
  }
}

function stopLivePolling() {
  if (state.livePoller !== null) {
    window.clearInterval(state.livePoller);
    state.livePoller = null;
  }
}

function startLivePolling(runId) {
  if (state.livePoller !== null) return;
  state.livePoller = window.setInterval(() => refreshLiveRun(runId), 2500);
}

async function refreshLiveRun(runId) {
  try {
    const run = await getJson(`/api/live/runs/${encodeURIComponent(runId)}`);
    const index = state.liveRuns.findIndex((item) => item.run_id === runId);
    if (index >= 0) state.liveRuns[index] = run;
    else state.liveRuns.unshift(run);
    if (state.activeLiveRunId === runId) renderActiveLiveRun(run);
    renderLiveHistory();
    if (!["starting", "running", "stopping"].includes(run.status)) {
      stopLivePolling();
      state.catalog = await getJson("/api/catalog");
      renderArchiveSummary();
      renderRunList();
      $("#mode-replay").disabled = !state.catalog.runs.length;
    }
  } catch (_) {
    // A transient polling failure must not erase the last visible run state.
  }
}

function showLiveFormError(error) {
  const box = $("#live-form-error");
  box.hidden = false;
  box.textContent = error instanceof Error ? error.message : String(error);
}

async function startResearch(event) {
  event.preventDefault();
  const errorBox = $("#live-form-error");
  errorBox.hidden = true;
  const button = $("#start-research");
  const question = $("#research-question").value.trim();
  const hours = finiteNumber($("#wall-time-hours").value) ?? 3;
  const payload = {
    question,
    maximum_iterations: 6,
    maximum_initial_routes: Number($("#initial-routes").value),
    route_planning_maximum_routes: Number($("#literature-routes").value),
    max_rounds_per_route: Number($("#max-rounds").value),
    minimum_rounds_before_llm_stop: Number($("#minimum-rounds").value),
    maximum_refinement_rounds: 1,
    maximum_method_research_rounds: 2,
    method_research_wall_time_seconds: 360,
    s2_request_budget_seconds: 75,
    wall_time_seconds: Math.round(hours * 3600),
    task_compiler_tier: $("#compiler-tier").value,
    online_method_research: $("#online-research").checked,
    qwen_method_synthesis: $("#method-synthesis").checked,
    control_route: $("#control-route").checked,
  };
  state.accessToken = $("#access-token").value.trim();
  button.disabled = true;
  button.textContent = "正在启动研究进程…";
  try {
    const run = await postJson("/api/live/runs", payload, state.accessToken);
    stopLivePolling();
    state.liveRuns.unshift(run);
    state.activeLiveRunId = run.run_id;
    renderLiveWorkspace();
    $("#active-run-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showLiveFormError(error);
  } finally {
    button.disabled = !state.readiness?.ready_for_real_run;
    button.textContent = state.readiness?.ready_for_real_run
      ? "启动真实端到端研究"
      : "请先在服务端配置 Qwen 密钥";
  }
}

$("#dialog-close").addEventListener("click", () => $("#iteration-dialog").close());
$("#iteration-dialog").addEventListener("click", (event) => {
  if (event.target === $("#iteration-dialog")) $("#iteration-dialog").close();
});
$("#retry-button").addEventListener("click", initialize);
$("#mode-live").addEventListener("click", () => setMode("live"));
$("#mode-replay").addEventListener("click", () => {
  if (state.run) setMode("replay");
});
$("#research-form").addEventListener("submit", startResearch);
$("#research-question").addEventListener("input", () => {
  $("#question-count").textContent = `${$("#research-question").value.length} / 6000`;
});
document.querySelectorAll(".example-button").forEach((button) => {
  button.addEventListener("click", () => {
    const value = researchExamples[button.dataset.example] || "";
    $("#research-question").value = value;
    $("#question-count").textContent = `${value.length} / 6000`;
    $("#research-question").focus();
  });
});
$("#stop-live-run").addEventListener("click", async () => {
  if (!state.activeLiveRunId) return;
  try {
    const run = await postJson(
      `/api/live/runs/${encodeURIComponent(state.activeLiveRunId)}/stop`,
      {},
      state.accessToken,
    );
    const index = state.liveRuns.findIndex((item) => item.run_id === run.run_id);
    if (index >= 0) state.liveRuns[index] = run;
    renderLiveWorkspace();
  } catch (error) {
    showLiveFormError(error);
  }
});
$("#open-live-replay").addEventListener("click", async () => {
  if (!state.activeLiveRunId) return;
  state.catalog = await getJson("/api/catalog");
  renderArchiveSummary();
  renderRunList();
  $("#mode-replay").disabled = !state.catalog.runs.length;
  if (state.catalog.runs.some((run) => run.run_id === state.activeLiveRunId)) {
    await loadRun(state.activeLiveRunId);
  }
});
$("#mobile-menu").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.body.classList.remove("sidebar-open");
});

initialize();
