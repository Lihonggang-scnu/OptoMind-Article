"use strict";

const state = {
  catalog: null,
  run: null,
  selectedRunId: null,
  selectedRouteId: null,
  selectedStage: 0,
  hiddenRoutes: new Set(),
  simulation: {
    index: -1,
    running: false,
    speed: 10,
    timer: null,
  },
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

function setLoading(loading) {
  $("#loading-state").hidden = !loading;
  if (loading) $("#replay-content").hidden = true;
  if (loading) $("#error-state").hidden = true;
}

function showError(error) {
  $("#loading-state").hidden = true;
  $("#replay-content").hidden = true;
  $("#error-state").hidden = false;
  $("#error-message").textContent = error instanceof Error ? error.message : String(error);
}

async function initialize() {
  setLoading(true);
  try {
    const catalog = await getJson("/api/catalog");
    state.catalog = catalog;
    renderArchiveSummary();
    renderRunList();
    const requested = decodeURIComponent(location.hash.replace(/^#run=/, ""));
    const initial = state.catalog.runs.some((run) => run.run_id === requested)
      ? requested
      : state.catalog.runs[0]?.run_id;
    if (initial) {
      await loadRun(initial);
    } else {
      $("#loading-state").hidden = true;
    }
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
    list.replaceChildren(node("p", "sidebar-empty", "当前没有可回放的正式运行记录。"));
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
    clearSimulationTimer();
    state.run = await getJson(`/api/runs/${encodeURIComponent(runId)}`);
    state.selectedRunId = runId;
    state.selectedRouteId = state.run.routes.find((route) => route.winner)?.route_id
      || state.run.routes[0]?.route_id
      || null;
    state.selectedStage = 0;
    state.hiddenRoutes.clear();
    state.simulation.index = -1;
    state.simulation.running = false;
    location.hash = `run=${encodeURIComponent(runId)}`;
    renderRunList();
    renderRun();
    document.body.classList.remove("sidebar-open");
    $("#loading-state").hidden = true;
    $("#replay-content").hidden = false;
    window.scrollTo({ top: 0, behavior: "instant" });
  } catch (error) {
    showError(error);
  }
}

function renderRun() {
  renderHero();
  renderKpis();
  renderSimulation();
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

function eventDescription(event) {
  const type = event.event_type;
  if (type === "request_received") return "研究问题已写入本次运行证据链。";
  if (type === "problem_analyzed") return `问题理解完成，状态：${event.status || "已分析"}。`;
  if (type === "scoring_standard_fixed") return `已锁定 ${event.metric_count || "多"} 项指标及本次评分公式。`;
  if (type === "routes_planned_from_literature") return `文献检索形成 ${event.route_count || 0} 条候选路线，关联 ${event.papers || 0} 篇文献记录。`;
  if (type === "control_route_planned") return "独立记忆对照路线已经生成，未接收文献内容。";
  if (type === "method_research_completed") return `方法研究完成，整理 ${event.evidence_count || 0} 项证据记录。`;
  if (type === "strategy_planned") return `实验策略已形成：${event.normal_route_count || 0} 条文献路线，${event.control_route_count || 0} 条对照路线。`;
  if (type === "route_round_quota_allocated") return `已为 ${event.routes || 0} 条路线分配每条 ${event.rounds_per_route || 0} 轮的探索额度。`;
  if (type === "wave_started") return `第 ${event.wave || "—"} 个执行波次开始，${event.racing?.length || 0} 条路线进入排程。`;
  if (type === "route_started") return `${event.route_id || "路线"} 开始第 ${event.iteration_id || "新"} 轮任务。`;
  if (type === "tmm_batch_executed") return `VeriTMM 已执行本波次任务批次，共 ${event.tasks || 0} 个任务。`;
  if (type === "route_completed") return `${event.route_id || "路线"} 本轮状态为 ${statusLabel(event.run_status)}，产生 ${event.valid_candidates || 0} 个物理有效候选。`;
  if (type === "track_status") return `${event.route_id || "路线"} 当前状态：${statusLabel(event.status)}。`;
  if (type === "feedback_decided") return `${event.route_id || "路线"} 的观测已转化为下一步反馈决定。`;
  if (type === "reflection_completed") return `${event.route_id || "路线"} 已完成本轮路线反思。`;
  if (type === "route_revised") return `${event.route_id || "路线"} 已根据真实结果更新后续实验计划。`;
  if (type === "scoring_standard_ranking_written") return `冻结标准排名已经写入，共 ${event.ranked || event.routes || 0} 条路线进入汇总。`;
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

const simulationStages = [
  { label: "问题与目标", events: ["request_received"] },
  { label: "理解与定标", events: ["problem_analyzed", "scoring_standard_fixed"] },
  { label: "文献与路线", events: ["routes_planned_from_literature", "control_route_planned", "method_research_completed"] },
  { label: "实验计划", events: ["strategy_planned", "route_round_quota_allocated"] },
  { label: "仿真与观测", events: ["wave_started", "route_started", "tmm_batch_executed", "route_completed", "track_status"] },
  { label: "反馈再规划", events: ["feedback_decided", "reflection_completed", "route_revised"] },
  { label: "比较与交付", events: ["scoring_standard_ranking_written", "tournament_summarized", "research_finished"] },
];

function formatEventElapsed(seconds) {
  const value = Math.max(0, Math.floor(finiteNumber(seconds) ?? 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function simulationEvents() {
  return state.run?.event_timeline || [];
}

function simulationStageLabel(eventType) {
  return simulationStages.find((stage) => stage.events.includes(eventType))?.label || "研究状态";
}

function clearSimulationTimer() {
  if (state.simulation.timer !== null) {
    window.clearTimeout(state.simulation.timer);
    state.simulation.timer = null;
  }
}

function scheduleSimulationStep() {
  clearSimulationTimer();
  if (!state.simulation.running) return;
  const events = simulationEvents();
  if (state.simulation.index >= events.length - 1) {
    state.simulation.running = false;
    renderSimulation();
    return;
  }
  const speed = Math.min(10, Math.max(1, finiteNumber(state.simulation.speed) || 10));
  const delay = Math.max(45, Math.round(560 / speed));
  state.simulation.timer = window.setTimeout(() => {
    state.simulation.timer = null;
    state.simulation.index += 1;
    renderSimulation();
    scheduleSimulationStep();
  }, delay);
}

function renderSimulation() {
  const events = simulationEvents();
  const current = state.simulation.index;
  const hasEvents = events.length > 0;
  const toggle = $("#simulation-toggle");
  const reset = $("#simulation-reset");
  const speed = $("#simulation-speed");
  const fill = $("#simulation-progress-fill");
  const readout = $("#simulation-readout");
  const clock = $("#simulation-clock");
  const timeline = $("#simulation-timeline");
  if (!hasEvents) {
    toggle.disabled = true;
    reset.disabled = true;
    fill.style.width = "0%";
    clock.textContent = "没有事件流";
    readout.replaceChildren(node("span", "simulation-empty", "本组没有可播放的事件流记录。"));
    timeline.replaceChildren(node("div", "empty-state", "未找到 RESEARCH_EVENTS.jsonl。"));
    return;
  }
  toggle.disabled = false;
  reset.disabled = false;
  speed.value = String(state.simulation.speed);
  toggle.textContent = state.simulation.running
    ? "暂停模拟"
    : current >= events.length - 1
      ? "重新播放"
      : current >= 0
        ? "继续模拟"
        : "开始模拟";
  const completed = Math.max(0, Math.min(events.length, current + 1));
  fill.style.width = `${(completed / events.length) * 100}%`;
  const activeEvent = events[current] || null;
  if (!activeEvent) {
    clock.textContent = `准备播放 · ${events.length} 条事件`;
    readout.replaceChildren(
      node("strong", "", "准备开始"),
      node("span", "", `将按原始顺序播放 ${events.length} 条阶段事件；当前速度 ${state.simulation.speed}×。`),
    );
  } else {
    clock.textContent = `${state.simulation.running ? "播放中" : current >= events.length - 1 ? "已完成" : "已暂停"} · ${formatEventElapsed(activeEvent.elapsed_seconds)}`;
    const copy = node("div", "simulation-readout-copy");
    copy.append(
      node("strong", "", `${eventLabel(activeEvent.event_type)} · ${simulationStageLabel(activeEvent.event_type)}`),
      node("span", "", eventDescription(activeEvent)),
    );
    readout.replaceChildren(
      node("span", "simulation-sequence", `${completed} / ${events.length}`),
      copy,
    );
  }
  const items = events.map((event, index) => {
    const stateClass = index < current ? " completed" : index === current ? " current" : " pending";
    const item = node("article", `simulation-event${stateClass}`);
    item.dataset.sequence = String(event.sequence || index + 1);
    const marker = node("span", "simulation-event-marker", String(event.sequence || index + 1).padStart(3, "0"));
    const copy = node("div", "simulation-event-copy");
    const head = node("div", "simulation-event-head");
    head.append(
      node("strong", "", eventLabel(event.event_type)),
      node("span", "simulation-stage", simulationStageLabel(event.event_type)),
    );
    copy.append(
      head,
      node("p", "", eventDescription(event)),
      node("time", "", `${formatEventElapsed(event.elapsed_seconds)} · ${event.event_type}`),
    );
    item.append(marker, copy);
    return item;
  });
  timeline.replaceChildren(...items);
  if (current >= 0) {
    const active = timeline.querySelector(".simulation-event.current");
    if (active) active.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function toggleSimulation() {
  const events = simulationEvents();
  if (!events.length) return;
  if (state.simulation.running) {
    state.simulation.running = false;
    clearSimulationTimer();
    renderSimulation();
    return;
  }
  if (state.simulation.index >= events.length - 1) state.simulation.index = -1;
  state.simulation.running = true;
  if (state.simulation.index < 0) state.simulation.index = 0;
  renderSimulation();
  scheduleSimulationStep();
}

function resetSimulation() {
  clearSimulationTimer();
  state.simulation.index = -1;
  state.simulation.running = false;
  renderSimulation();
}

function changeSimulationSpeed(event) {
  state.simulation.speed = Math.min(10, Math.max(1, Number(event.target.value) || 10));
  if (state.simulation.running) scheduleSimulationStep();
  else renderSimulation();
}

$("#dialog-close").addEventListener("click", () => $("#iteration-dialog").close());
$("#iteration-dialog").addEventListener("click", (event) => {
  if (event.target === $("#iteration-dialog")) $("#iteration-dialog").close();
});
$("#retry-button").addEventListener("click", initialize);
$("#simulation-toggle").addEventListener("click", toggleSimulation);
$("#simulation-reset").addEventListener("click", resetSimulation);
$("#simulation-speed").addEventListener("change", changeSimulationSpeed);
$("#mobile-menu").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.body.classList.remove("sidebar-open");
});

initialize();
