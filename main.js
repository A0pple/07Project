const API_URL =
  "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/" +
  "{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}";
const EDITS_URL =
  "https://wikimedia.org/api/rest_v1/metrics/edits/per-page/" +
  "{project}/{article}/{editor_type}/{granularity}/{start}/{end}";
const TOP_ARTICLES_URL =
  "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/" +
  "{project}/all-access/{year}/{month}/{day}";
const SUMMARY_URL = "https://{project}/api/rest_v1/page/summary/{title}?redirect=true";
const USER_AGENT = "WikipediaViewsWeb/1.0 (https://github.com/)";

const LANGUAGE_LABELS = new Map([
  ["en.wikipedia.org", "en-wiki"],
  ["he.wikipedia.org", "he-wiki"],
]);

const state = {
  currentData: [],
  sort: {
    date: false,
    views: true,
    edits: true,
  },
  pointLookup: new Map(),
  chartPoints: [],
  chartMeta: {},
  articleSummaries: new Map(),
  summaryRequests: new Set(),
  tooltipVisibleFor: null,
  trendingCache: new Map(),
  trendingAbort: null,
};

const selectors = {
  articleInput: document.getElementById("article-input"),
  fetchButton: document.getElementById("fetch-button"),
  rangeInput: document.getElementById("range-input"),
  rangeOutput: document.getElementById("range-output"),
  languageSelect: document.getElementById("language-select"),
  status: document.getElementById("status"),
  totals: document.getElementById("totals"),
  subtitle: document.getElementById("subtitle"),
  tableBody: document.querySelector("#results-table tbody"),
  tableHeaders: Array.from(document.querySelectorAll("#results-table th")),
  chart: document.getElementById("chart"),
  suggestionsHeading: document.getElementById("suggestions-heading"),
  suggestionsTrack: document.getElementById("suggestions-track"),
  tooltip: document.getElementById("tooltip"),
};

const dpr = window.devicePixelRatio || 1;

init();

function init() {
  selectors.articleInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      triggerFetch();
    }
  });

  selectors.fetchButton.addEventListener("click", triggerFetch);
  selectors.rangeInput.addEventListener("input", handleRangeChange);
  selectors.languageSelect.addEventListener("change", handleFilterChange);
  selectors.tableHeaders.forEach((header) =>
    header.addEventListener("click", () => handleSort(header.dataset.column))
  );
  selectors.tableBody.addEventListener("dblclick", handleRowDoubleClick);

  selectors.chart.addEventListener("mousemove", handleChartHover);
  selectors.chart.addEventListener("mouseleave", clearChartHover);
  window.addEventListener("resize", () => drawChart());

  selectors.suggestionsTrack.addEventListener("pointerleave", hideTooltip);

  updateRangeOutput(getSelectedDays());
  updateSubtitle();
  requestTrendingArticles();
}

function handleRangeChange() {
  const days = getSelectedDays();
  updateRangeOutput(days);
  updateSubtitle();
  maybeNotifyFilters();
}

function handleFilterChange() {
  updateSubtitle();
  requestTrendingArticles(true);
  maybeNotifyFilters();
}

function maybeNotifyFilters() {
  if (state.currentData.length === 0) {
    setStatus("Ready.");
    return;
  }

  const language = LANGUAGE_LABELS.get(selectors.languageSelect.value) ?? "en-wiki";
  const days = getSelectedDays();
  setStatus(`Filters updated — press Fetch to refresh (${language}, ${days} days).`);
}

function getSelectedDays() {
  return Math.min(365, Math.max(7, Number.parseInt(selectors.rangeInput.value, 10) || 60));
}

function updateRangeOutput(days) {
  selectors.rangeOutput.textContent = `${days} day${days === 1 ? "" : "s"}`;
}

function updateSubtitle() {
  const days = getSelectedDays();
  const language = LANGUAGE_LABELS.get(selectors.languageSelect.value) ?? "en-wiki";
  selectors.subtitle.textContent = `Explore how often an article was viewed in the last ${days} days on ${language}.`;
}

function decodeUnicode(input) {
  if (typeof input !== "string") return input;
  let value = input;
  let previous;
  while (previous !== value) {
    previous = value;
    value = value.replace(/\\\\u/gi, "\\u").replace(/\\\\U/g, "\\U");
  }
  try {
    return value.replace(/\\u[0-9a-fA-F]{4}/g, (m) => String.fromCharCode(parseInt(m.slice(2), 16)));
  } catch (error) {
    return input;
  }
}

async function triggerFetch() {
  const raw = selectors.articleInput.value.trim();
  const article = decodeUnicode(raw);
  if (!article) {
    alert("Please enter a Wikipedia article title.");
    return;
  }

  selectors.articleInput.value = article;
  const project = selectors.languageSelect.value;
  const days = getSelectedDays();
  const language = LANGUAGE_LABELS.get(project) ?? project;
  setStatus(`Fetching data… (${language}, last ${days} days)`);
  setTotals("Totals — views: –, edits: –");
  clearTable();
  clearChart();
  disableFetch(true);

  try {
    const { data, warning } = await fetchPageMetrics(article, project, days);
    if (!data || data.length === 0) {
      setStatus("No data returned for that article.");
      state.currentData = [];
      drawChart();
      return;
    }

    state.currentData = data;
    state.sort = { date: false, views: true, edits: true };
    const sorted = [...data].sort((a, b) => b.views - a.views);
    populateTable(sorted);
    drawChart();
    let status = `Metrics for '${article}' - ${language}, last ${days} days`;
    if (warning) {
      status += ` — ${warning}`;
    }
    setStatus(status);
  } catch (error) {
    console.error(error);
    const message = error?.message ?? "Unknown error";
    setStatus("Could not retrieve data.");
    alert(message);
    state.currentData = [];
    drawChart();
  } finally {
    disableFetch(false);
  }
}

function disableFetch(disabled) {
  selectors.fetchButton.disabled = disabled;
}

function setStatus(message) {
  selectors.status.textContent = message;
}

function setTotals(message) {
  selectors.totals.textContent = message;
}

function clearTable() {
  selectors.tableBody.innerHTML = "";
  state.pointLookup.clear();
}

function populateTable(data) {
  clearTable();
  let totalViews = 0;
  let totalEdits = 0;
  const fragment = document.createDocumentFragment();

  for (const entry of data) {
    const row = document.createElement("tr");
    row.dataset.date = entry.date.toISOString();

    const dateCell = document.createElement("td");
    dateCell.textContent = formatDate(entry.date);
    const viewsCell = document.createElement("td");
    viewsCell.textContent = formatNumber(entry.views);
    const editsCell = document.createElement("td");
    editsCell.textContent = formatNumber(entry.edits);

    row.append(dateCell, viewsCell, editsCell);
    fragment.append(row);

    totalViews += entry.views;
    totalEdits += entry.edits;
    state.pointLookup.set(entry.date.toISOString(), row);
  }

  selectors.tableBody.append(fragment);
  setTotals(`Totals — views: ${formatNumber(totalViews)}, edits: ${formatNumber(totalEdits)}`);
}

function handleSort(column) {
  if (!column || state.currentData.length === 0) return;

  const descending = !state.sort[column];
  state.sort[column] = descending;

  let sorted;
  if (column === "date") {
    sorted = [...state.currentData].sort((a, b) =>
      descending ? b.date - a.date : a.date - b.date
    );
  } else if (column === "views") {
    sorted = [...state.currentData].sort((a, b) =>
      descending ? b.views - a.views : a.views - b.views
    );
  } else {
    sorted = [...state.currentData].sort((a, b) =>
      descending ? b.edits - a.edits : a.edits - b.edits
    );
  }

  populateTable(sorted);
  drawChart(sorted);
}

function handleRowDoubleClick(event) {
  const row = event.target.closest("tr");
  if (!row) return;
  const iso = row.dataset.date;
  const date = iso ? new Date(iso) : null;
  const article = selectors.articleInput.value.trim();
  if (!date || !article) return;

  const formatted = `${date.getMonth() + 1}/${date.getDate()}/${date.getFullYear()}`;
  const params = new URLSearchParams({
    q: `"${article}"`,
    tbs: `cdr:1,cd_min:${formatted},cd_max:${formatted}`,
  });
  const url = `https://www.google.com/search?${params.toString()}`;
  window.open(url, "_blank", "noopener");
}

function clearChart() {
  const canvas = selectors.chart;
  const ctx = canvas.getContext("2d");
  resizeCanvas(canvas, ctx);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  state.chartPoints = [];
  state.chartMeta = {};
}

function drawChart(data = state.currentData) {
  const canvas = selectors.chart;
  const ctx = canvas.getContext("2d");
  resizeCanvas(canvas, ctx);
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  state.chartPoints = [];
  state.chartMeta = {};

  if (!data || data.length === 0) {
    ctx.fillStyle = "#8892b0";
    ctx.font = `${14 * dpr}px sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("No data yet. Fetch to see the trend.", canvas.width / 2, canvas.height / 2);
    return;
  }

  const sorted = [...data].sort((a, b) => a.date - b.date);
  const padding = 30 * dpr;
  const width = canvas.width;
  const height = canvas.height;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  const maxViews = Math.max(...sorted.map((d) => d.views));
  const minViews = Math.min(...sorted.map((d) => d.views));
  const range = Math.max(1, maxViews - minViews);
  const count = sorted.length;

  const points = [];
  if (count === 1) {
    points.push({
      x: padding + plotWidth / 2,
      y: height - padding - plotHeight / 2,
    });
  } else {
    const step = plotWidth / (count - 1);
    sorted.forEach((entry, index) => {
      const normalized = range === 0 ? 0 : (entry.views - minViews) / range;
      const x = padding + index * step;
      const y = height - padding - normalized * plotHeight;
      points.push({ x, y });
    });
  }

  ctx.save();
  ctx.strokeStyle = "#cdd5eb";
  ctx.lineWidth = 1 * dpr;
  ctx.beginPath();
  ctx.moveTo(padding, height - padding);
  ctx.lineTo(width - padding, height - padding);
  ctx.moveTo(padding, padding);
  ctx.lineTo(padding, height - padding);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "rgba(79, 70, 229, 0.15)";
  ctx.beginPath();
  ctx.moveTo(points[0].x, height - padding);
  points.forEach((point, index) => {
    if (index === 0) {
      ctx.lineTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  ctx.lineTo(points[points.length - 1].x, height - padding);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = "#4f46e5";
  ctx.lineWidth = 2 * dpr;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i += 1) {
    ctx.lineTo(points[i].x, points[i].y);
  }
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#4f46e5";
  points.forEach((point) => {
    ctx.beginPath();
    ctx.arc(point.x, point.y, 3 * dpr, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#4f46e5";
  ctx.font = `${12 * dpr}px sans-serif`;
  ctx.textAlign = "left";
  ctx.fillText(`Peak: ${formatNumber(maxViews)}`, padding + 8 * dpr, padding + 12 * dpr);
  ctx.textAlign = "right";
  ctx.fillStyle = "#6b7280";
  ctx.fillText(`Min: ${formatNumber(minViews)}`, width - padding, padding + 12 * dpr);
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#6b7280";
  ctx.font = `${11 * dpr}px sans-serif`;
  ctx.textAlign = "center";
  const firstLabel = formatShortDate(sorted[0].date);
  const lastLabel = formatShortDate(sorted[sorted.length - 1].date);
  ctx.fillText(firstLabel, points[0].x, height - padding + 16 * dpr);
  ctx.fillText(lastLabel, points[points.length - 1].x, height - padding + 16 * dpr);
  ctx.restore();

  state.chartPoints = points.map((point, index) => ({
    x: point.x,
    y: point.y,
    date: sorted[index].date,
    views: sorted[index].views,
    edits: sorted[index].edits,
  }));
  state.chartMeta = { padding, width, height };
}

function resizeCanvas(canvas, ctx) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function handleChartHover(event) {
  if (!state.chartPoints.length) return;

  const rect = selectors.chart.getBoundingClientRect();
  const x = (event.clientX - rect.left) * dpr;
  const padding = state.chartMeta.padding ?? 0;
  const width = state.chartMeta.width ?? selectors.chart.width;
  if (x < padding || x > width - padding) {
    clearChartHover();
    return;
  }

  const nearest = [...state.chartPoints].reduce((best, point) => {
    if (!best) return point;
    const current = Math.abs(point.x - x);
    const bestDistance = Math.abs(best.x - x);
    return current < bestDistance ? point : best;
  }, null);

  if (!nearest) return;
  drawChart();
  highlightPoint(nearest);
  selectRowForDate(nearest.date);
}

function highlightPoint(point) {
  const canvas = selectors.chart;
  const ctx = canvas.getContext("2d");
  const padding = state.chartMeta.padding ?? 0;

  ctx.save();
  ctx.strokeStyle = "#4f46e5";
  ctx.lineWidth = 1 * dpr;
  ctx.setLineDash([4 * dpr, 2 * dpr]);
  ctx.beginPath();
  ctx.moveTo(point.x, padding);
  ctx.lineTo(point.x, (state.chartMeta.height ?? canvas.height) - padding);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#4f46e5";
  ctx.lineWidth = 2 * dpr;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 6 * dpr, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#4f46e5";
  ctx.font = `${11 * dpr}px sans-serif`;
  ctx.textAlign = "center";
  const text = `${formatDate(point.date)} — ${formatNumber(point.views)} views, ${formatNumber(point.edits)} edits`;
  const textX = Math.min(
    Math.max(point.x, padding + 50 * dpr),
    (state.chartMeta.width ?? canvas.width) - padding - 50 * dpr
  );
  const textY = point.y - 12 * dpr < padding ? point.y + 20 * dpr : point.y - 12 * dpr;
  ctx.fillText(text, textX, textY);
  ctx.restore();
}

function clearChartHover() {
  drawChart();
  if (selectors.tableBody) {
    const selected = selectors.tableBody.querySelector("tr.selected");
    if (selected) selected.classList.remove("selected");
  }
}

function selectRowForDate(date) {
  const iso = date.toISOString();
  const row = state.pointLookup.get(iso);
  if (!row) return;

  selectors.tableBody.querySelectorAll("tr").forEach((tr) => tr.classList.remove("selected"));
  row.classList.add("selected");
  row.scrollIntoView({ block: "nearest" });
}

function formatNumber(value) {
  return Number(value).toLocaleString();
}

function formatDate(date) {
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function formatShortDate(date) {
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

async function fetchPageMetrics(article, project, days) {
  const [startDate, endDate] = computeDateRange(days);
  const articleSlug = encodeURIComponent(article.replace(/ /g, "_"));
  const url = API_URL.replace("{project}", project)
    .replace("{access}", "all-access")
    .replace("{agent}", "user")
    .replace("{article}", articleSlug)
    .replace("{granularity}", "daily")
    .replace("{start}", startDate)
    .replace("{end}", endDate);

  const viewsResponse = await safeFetch(url);
  if (!viewsResponse.ok) {
    if (viewsResponse.status === 404) {
      throw new Error("Article not found or no pageviews available. Check the title and try again.");
    }
    throw new Error(`HTTP error ${viewsResponse.status}: ${viewsResponse.statusText}`);
  }
  const viewsData = await viewsResponse.json();
  const items = viewsData.items ?? [];
  if (!items.length) {
    return { data: [], warning: null };
  }

  const views = items
    .map((entry) => {
      const timestamp = entry?.timestamp;
      const count = entry?.views;
      if (!timestamp || typeof count !== "number") return null;
      const date = parseTimestamp(timestamp);
      return date ? { date, views: count } : null;
    })
    .filter(Boolean);

  if (!views.length) {
    return { data: [], warning: null };
  }

  const { map: edits, warning } = await fetchPageEdits(articleSlug, project, startDate, endDate);
  const data = views.map((entry) => ({
    date: entry.date,
    views: entry.views,
    edits: edits.get(entry.date.toISOString()) ?? 0,
  }));
  return { data, warning };
}

async function fetchPageEdits(articleSlug, project, startDate, endDate) {
  const url = EDITS_URL.replace("{project}", project)
    .replace("{article}", articleSlug)
    .replace("{editor_type}", "all-editor-types")
    .replace("{granularity}", "daily")
    .replace("{start}", startDate)
    .replace("{end}", endDate);

  try {
    const response = await safeFetch(url);
    if (!response.ok) {
      if (response.status === 404) {
        return { map: new Map(), warning: null };
      }
      throw new Error(`HTTP error while fetching edits: ${response.status}`);
    }

    const data = await response.json();
    const items = data.items ?? [];
    const map = new Map();
    for (const item of items) {
      const results = item?.results ?? [];
      for (const entry of results) {
        const timestamp = entry?.timestamp;
        const count = entry?.edits;
        if (!timestamp || typeof count !== "number") continue;
        const date = parseIsoDate(timestamp);
        if (!date) continue;
        map.set(date.toISOString(), Math.trunc(count));
      }
    }
    return { map, warning: null };
  } catch (error) {
    console.warn(error);
    return {
      map: new Map(),
      warning: error instanceof Error ? error.message : "Network error while fetching edits",
    };
  }
}

function parseTimestamp(timestamp) {
  try {
    const dt = new Date(
      Date.UTC(
        Number(timestamp.slice(0, 4)),
        Number(timestamp.slice(4, 6)) - 1,
        Number(timestamp.slice(6, 8))
      )
    );
    return dt;
  } catch (error) {
    return null;
  }
}

function parseIsoDate(value) {
  try {
    return new Date(value);
  } catch (error) {
    return null;
  }
}

function computeDateRange(days) {
  const now = new Date();
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1));
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  const startStr = formatDateStamp(start);
  const endStr = formatDateStamp(end);
  return [startStr, endStr];
}

function formatDateStamp(date) {
  const year = date.getUTCFullYear();
  const month = `${date.getUTCMonth() + 1}`.padStart(2, "0");
  const day = `${date.getUTCDate()}`.padStart(2, "0");
  return `${year}${month}${day}`;
}

async function requestTrendingArticles(force = false) {
  const project = selectors.languageSelect.value;
  const cached = state.trendingCache.get(project);
  if (!force && cached) {
    applyTrendingArticles(project, cached);
    return;
  }

  if (state.trendingAbort) {
    state.trendingAbort.abort();
  }

  const controller = new AbortController();
  state.trendingAbort = controller;
  selectors.suggestionsHeading.textContent = "Trending this week (loading…)";
  selectors.suggestionsTrack.innerHTML = "";

  try {
    const articles = await fetchTopArticles(project, { signal: controller.signal });
    if (controller.signal.aborted) return;
    if (!articles.length) {
      selectors.suggestionsHeading.textContent = "Trending this week (unavailable)";
      return;
    }
    state.trendingCache.set(project, articles);
    applyTrendingArticles(project, articles);
  } catch (error) {
    if (controller.signal.aborted) return;
    console.warn(error);
    selectors.suggestionsHeading.textContent = "Trending this week (unavailable)";
  }
}

function applyTrendingArticles(project, articles) {
  if (selectors.languageSelect.value !== project) return;
  selectors.suggestionsHeading.textContent = "Trending this week";
  selectors.suggestionsTrack.innerHTML = "";
  for (const title of articles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-button";
    button.textContent = formatSuggestionTitle(title);
    button.addEventListener("click", () => {
      selectors.articleInput.value = formatSuggestionTitle(title);
      triggerFetch();
    });
    button.addEventListener("pointerenter", (event) => showTooltip(event, project, title));
    button.addEventListener("pointermove", positionTooltip);
    button.addEventListener("pointerleave", hideTooltip);
    selectors.suggestionsTrack.append(button);
  }
}

function formatSuggestionTitle(title) {
  try {
    return decodeURIComponent(title).replace(/_/g, " ");
  } catch (error) {
    return title.replace(/_/g, " ");
  }
}

async function showTooltip(event, project, article) {
  const tooltip = selectors.tooltip;
  const key = `${project}|${article}`;
  tooltip.hidden = false;
  tooltip.dataset.visible = "true";

  const cached = state.articleSummaries.get(key);
  if (cached) {
    tooltip.textContent = cached;
  } else if (!state.summaryRequests.has(key)) {
    tooltip.textContent = "Loading summary…";
    fetchArticleSummary(project, article)
      .then((summary) => {
        if (summary) {
          state.articleSummaries.set(key, summary);
          if (state.tooltipVisibleFor === key) {
            tooltip.textContent = summary;
          }
        }
      })
      .catch(() => {
        const fallback = "Summary unavailable.";
        state.articleSummaries.set(key, fallback);
        if (state.tooltipVisibleFor === key) {
          tooltip.textContent = fallback;
        }
      });
  } else {
    tooltip.textContent = "Loading summary…";
  }

  state.tooltipVisibleFor = key;
  positionTooltip(event);
}

function positionTooltip(event) {
  const tooltip = selectors.tooltip;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.bottom + 12;
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function hideTooltip() {
  state.tooltipVisibleFor = null;
  const tooltip = selectors.tooltip;
  tooltip.hidden = true;
  tooltip.dataset.visible = "false";
}

async function fetchArticleSummary(project, article) {
  const key = `${project}|${article}`;
  if (state.articleSummaries.has(key)) {
    return state.articleSummaries.get(key);
  }
  if (state.summaryRequests.has(key)) {
    return null;
  }

  state.summaryRequests.add(key);
  const slug = encodeURIComponent(article.replace(/ /g, "_"));
  const url = SUMMARY_URL.replace("{project}", project).replace("{title}", slug);

  try {
    const response = await safeFetch(url);
    if (!response.ok) {
      return "Summary unavailable.";
    }
    const data = await response.json();
    const extract = data.extract || data.title;
    if (!extract) return "Summary unavailable.";
    const sentence = extract.replace(/\s+/g, " ").trim();
    const firstSentence = sentence.match(/[^.!?]+[.!?]/);
    return firstSentence ? firstSentence[0] : `${sentence}.`;
  } catch (error) {
    console.warn(error);
    return "Summary unavailable.";
  } finally {
    state.summaryRequests.delete(key);
  }
}

async function fetchTopArticles(project, { days = 7, limit = 20, signal } = {}) {
  const aggregated = new Map();
  const today = new Date();
  const tasks = [];

  for (let offset = 1; offset <= days; offset += 1) {
    const day = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - offset));
    const year = day.getUTCFullYear();
    const month = `${day.getUTCMonth() + 1}`.padStart(2, "0");
    const dayStr = `${day.getUTCDate()}`.padStart(2, "0");
    const projectSlug = project.replace(/\.org$/, "");
    const url = TOP_ARTICLES_URL.replace("{project}", projectSlug)
      .replace("{year}", String(year))
      .replace("{month}", month)
      .replace("{day}", dayStr);

    tasks.push(
      safeFetch(url, { signal })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          const items = data?.items ?? [];
          for (const item of items) {
            for (const entry of item?.articles ?? []) {
              const title = entry?.article;
              const views = entry?.views;
              if (!title || typeof views !== "number") continue;
              if (title === "Main_Page" || title.startsWith("Special:")) continue;
              const key = title;
              aggregated.set(key, (aggregated.get(key) ?? 0) + views);
            }
          }
        })
        .catch(() => null)
    );
  }

  await Promise.all(tasks);
  const sorted = [...aggregated.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([title]) => title);
  return sorted;
}

async function safeFetch(url, options = {}) {
  const merged = {
    ...options,
    headers: {
      "Api-User-Agent": USER_AGENT,
      ...(options.headers || {}),
    },
  };
  return fetch(url, merged);
}
