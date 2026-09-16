"use strict";

// OmniCapture Console v2 - Swarm & Multi-Link Controller
const $ = (id) => document.getElementById(id);

const state = {
  batchId: localStorage.getItem("omniCaptureBatchId") || "",
  selectedJobId: localStorage.getItem("omniCaptureSelectedJobId") || "",
  theme: localStorage.getItem("omniCaptureTheme") || "dark",
  pollTimer: null,
  activeTab: "telemetry",
  graphNodes: [],
  graphEdges: [],
};

const ACTIVE_BATCH_STATES = new Set(["running", "queued", "cancelling"]);
const TERMINAL_BATCH_STATES = new Set(["completed", "completed_with_errors", "failed", "cancelled"]);

const STAGES_DEF = [
  { id: "environment", label: "模拟器与网络", detail: "ADB 设备状态与 Android 15 网络就绪" },
  { id: "pcap", label: "原始 PCAP 捕获", detail: "tcpdump 记录 TLS 解密前链路包" },
  { id: "proxy", label: "代理与 MITM 通道", detail: "adb reverse 绑定 mitmdump 实时流" },
  { id: "target", label: "目标帖子防串帖校验", detail: "严格校验 canonical ID，建立 Capture Boundary" },
  { id: "pagination", label: "Reaction 原生分页滑动", detail: "ALL connection 持续递增直到 terminal=true" },
  { id: "decrypt", label: "解密 Flow 证据校验", detail: "安全脱敏索引与原始 flow 归档校验" },
  { id: "analysis", label: "GraphQL 解析与去重", detail: "提取用户节点、Reaction 类型并生成 CSV" },
  { id: "complete", label: "物理证据链落盘", detail: "单帖所有分析产物与取证包处理完毕" }
];

const STATUS_LABELS = {
  queued: "排队中",
  running: "抓取中",
  cancelling: "停止中",
  completed: "已完成",
  completed_with_errors: "部分完成",
  failed: "失败",
  cancelled: "已停止",
  idle: "空闲"
};

const SAMPLE_POSTS = [
  "https://www.facebook.com/61563119350848/posts/122106880826437311",
  "https://www.facebook.com/pastin60seconds/posts/pfbid0249FpE44f7w8A1eXNfQ7189",
  "https://www.facebook.com/100064981830080/posts/1457763433066371"
];

const BOUNDED_SAMPLE_COMPLETION_SCOPE = "bounded-sample";

function showToast(msg) {
  const toast = $("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

function logTicker(msg, level = "info") {
  const body = $("tickerBody");
  const timeStr = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = `log-line ${level === "err" ? "text-rose" : level === "ok" ? "text-emerald" : "text-muted"}`;
  line.textContent = `[${timeStr}] ${msg}`;
  body.prepend(line);
  while (body.children.length > 50) body.removeChild(body.lastChild);
}

function updateUrlCount() {
  const text = $("postUrlsInput").value;
  const urls = text.split("\n").map(u => u.trim()).filter(u => u.length > 0);
  $("urlCounter").textContent = `${urls.length} 条有效链接`;
}

function buildBatchRequestBody(urls, artifacts, firstPageTestMode) {
  const requestBody = { post_urls: urls, save_artifacts: artifacts };
  if (firstPageTestMode) {
    requestBody.reaction_scroll_limit = 1;
  }
  return requestBody;
}

function formatBytes(bytes) {
  const b = Number(bytes || 0);
  if (!b) return "—";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(b) / Math.log(k));
  return `${(b / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1)} ${sizes[i]}`;
}

function copyText(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    showToast("已成功复制到剪贴板");
  }).catch(() => {
    showToast("复制失败，请手动选择复制");
  });
}

// Read existing result evidence when an older API has no outcome field yet.
const resultOutcomeCache = new Map();

function emptyListOutcome(result) {
  if (result.result_outcome) return result.result_outcome;
  const integrity = result.completeness?.reactions || {};
  const visibility = result.visibility?.reactions || {};
  if (result.target_verification?.status !== "matched" || !Array.isArray(result.reaction_users) ||
      result.reaction_users.length || !integrity.terminal_page_seen ||
      !integrity.completion_claim_allowed || integrity.rate_limited_records || integrity.temporary_feature_block_records) return null;
  if (visibility.status === "query-triggered-friends-only-visibility" &&
      visibility.visibility_hint === "friends-only-names" && integrity.successful_page_count > 0) {
    return { code: "empty-lists", reason: "friends-only-names", label: "Empty lists（名单不可见）",
      detail: "Facebook 仅展示好友的用户名单，本次返回 0 人；不代表帖子没有互动。" };
  }
  if (integrity.reported_total_count === 0) {
    return { code: "empty-lists", reason: "zero-reactions", label: "Empty lists（空名单）",
      detail: "已确认目标及列表末页，Facebook 报告互动数量为 0。" };
  }
  return null;
}

async function readResultOutcome(jobId) {
  if (!resultOutcomeCache.has(jobId)) {
    resultOutcomeCache.set(jobId, api(`/v1/jobs/${encodeURIComponent(jobId)}/result`)
      .then(emptyListOutcome).catch(() => { resultOutcomeCache.delete(jobId); return null; }));
  }
  return resultOutcomeCache.get(jobId);
}

async function enrichResultOutcomes(path, data) {
  if (/^\/v1\/batches\/[^/]+\/progress$/.test(path)) {
    await Promise.all((data.posts || []).map(async post => {
      if (post.status !== "completed" || post.valid_users > 0 || "result_outcome" in post) return;
      post.result_outcome = await readResultOutcome(post.job_id);
      if (post.result_outcome) post.current_stage = post.result_outcome.detail;
    }));
  }
  const job = path.match(/^\/v1\/jobs\/([^/]+)\/progress$/);
  if (job && data.job_status === "completed" && !(data.metrics?.reaction_users > 0) &&
      !(data.metrics?.valid_reactor_users > 0) && !("result_outcome" in data)) {
    data.result_outcome = await readResultOutcome(decodeURIComponent(job[1]));
    if (data.result_outcome) {
      const final = (data.stages || []).find(stage => stage.id === "complete");
      if (final) Object.assign(final, { state: "warning", label: data.result_outcome.label, detail: data.result_outcome.detail });
    }
  }
  return data;
}

// API Call Wrapper
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return enrichResultOutcomes(path, data);
}

function persistBatchId(batchId) {
  state.batchId = String(batchId || "");
  if (state.batchId) {
    localStorage.setItem("omniCaptureBatchId", state.batchId);
  } else {
    localStorage.removeItem("omniCaptureBatchId");
  }
}

async function restoreLatestBatch() {
  let latest;
  try {
    latest = await api("/v1/batches/latest");
  } catch {
    return;
  }

  const latestBatchId = String(latest.batch_id || "");
  if (!latestBatchId) return;

  // A live latest batch supersedes stale local storage on page startup.
  if (ACTIVE_BATCH_STATES.has(latest.status) || !state.batchId) {
    persistBatchId(latestBatchId);
    return;
  }

  try {
    const current = await api(`/v1/batches/${encodeURIComponent(state.batchId)}/progress`);
    if (TERMINAL_BATCH_STATES.has(current.status)) {
      persistBatchId(latestBatchId);
    }
  } catch {
    // A missing persisted batch should not prevent the latest history from loading.
    persistBatchId(latestBatchId);
  }
}

async function adoptLatestBatchIfCurrentUnavailable(currentBatch = null) {
  if (currentBatch && !TERMINAL_BATCH_STATES.has(currentBatch.status)) return;

  try {
    const latest = await api("/v1/batches/latest");
    if (latest.batch_id) persistBatchId(latest.batch_id);
  } catch {
    // Keep the current selection until the backend becomes reachable again.
  }
}

// Health Telemetry
async function checkHealth() {
  try {
    const data = await api("/health");
    $("backendDot").className = "dot ok";
    $("backendText").textContent = data.worker === "busy" ? "API 8765 · 忙碌" : "API 8765 · 在线";

    const adb = data.android || {};
    if (adb.state === "device" && adb.boot_completed) {
      $("deviceDot").className = "dot ok";
      $("deviceText").textContent = `AVD ${data.serial || "5554"} · 正常`;
    } else {
      $("deviceDot").className = "dot";
      $("deviceText").textContent = `AVD ${data.serial || "5554"} · ${adb.state || "离线"}`;
    }
  } catch (err) {
    $("backendDot").className = "dot err";
    $("backendText").textContent = "API 8765 离线";
    $("deviceDot").className = "dot err";
    $("deviceText").textContent = "检测不可用";
  }
}

// Render Pipeline for Selected Job
function renderPipeline(stagesData = [], completionScope = "") {
  const container = $("pipelineFlow");
  container.replaceChildren();
  const boundedSample = completionScope === BOUNDED_SAMPLE_COMPLETION_SCOPE;

  const activeStageName = stagesData.find(s => s.state === "active")?.label ||
                          stagesData.filter(s => s.state === "complete").pop()?.label || "等待执行";

  STAGES_DEF.forEach((def, index) => {
    const runtime = stagesData.find(s => s.id === def.id) || { state: "waiting" };
    const li = document.createElement("li");
    li.className = `stage-card ${runtime.state}`;
    const sampleComplete = boundedSample && def.id === "complete" && runtime.state === "warning";

    const bubble = document.createElement("div");
    bubble.className = "stage-bubble";
    bubble.textContent = runtime.state === "complete" || sampleComplete ? "✓" : String(index + 1).padStart(2, "0");

    const info = document.createElement("div");
    info.className = "stage-info";
    const title = document.createElement("strong");
    title.textContent = runtime.label || def.label;
    const detail = document.createElement("span");
    detail.textContent = runtime.detail || def.detail;
    info.append(title, detail);

    const badge = document.createElement("span");
    badge.className = `badge badge-${sampleComplete || runtime.state === "warning" ? "sample" : runtime.state === "complete" ? "completed" : runtime.state === "active" ? "running" : "idle"}`;
    badge.textContent = sampleComplete ? "样本完成" : runtime.state === "warning" ? "提示" : runtime.state === "complete" ? "已完成" : runtime.state === "active" ? "进行中" : "等待";

    li.append(bubble, info, badge);
    container.append(li);
  });
}

// Render Telemetry Grid
function renderTelemetry(progress) {
  const m = progress.metrics || {};
  const boundedSample = progress.completion_scope === BOUNDED_SAMPLE_COMPLETION_SCOPE;
  $("telUsers").textContent = String(m.valid_reactor_users || m.reaction_users || 0);
  $("telPages").textContent = String(m.reaction_pages || 0);
  $("telAttempts").textContent = String(m.attempts || 0);
  $("telGrowth").textContent = String(m.growth_events || 0);
  $("telRateLimits").textContent = String(m.rate_limits || 0);

  const termEl = $("telTerminal");
  const emptyList = progress.result_outcome?.code === "empty-lists";
  termEl.textContent = emptyList ? progress.result_outcome.label : boundedSample ? "样本截止（非全量）" : m.terminal ? "已到达" : "未到达";
  termEl.className = `tel-val ${emptyList || boundedSample ? "tel-amber" : m.terminal ? "tel-emerald" : ""}`;

  const path = progress.storage_root || "";
  $("sandboxPathText").textContent = path || "等待任务启动生成沙箱...";
  $("sandboxPathText").title = path;
  $("btnCopySandboxPath").disabled = !path;
}

// Render Artifacts Vault Table
function renderArtifactsVault(artifacts = []) {
  const tbody = $("artifactTableBody");
  tbody.replaceChildren();

  if (!artifacts.length) {
    tbody.innerHTML = `
      <tr class="empty-placeholder">
        <td colspan="6">
          <div class="empty-state">
            <p>该帖子当前暂无落盘产物。</p>
          </div>
        </td>
      </tr>`;
    return;
  }

  artifacts.forEach(item => {
    const tr = document.createElement("tr");

    const tdLayer = document.createElement("td");
    tdLayer.className = "text-muted";
    tdLayer.textContent = item.layer || "分析产物";

    const tdName = document.createElement("td");
    tdName.innerHTML = `<code>${item.name}</code><div class="text-muted" style="font-size:11px;">${item.label || ""}${item.required ? " · 必留" : ""}</div>`;

    const tdStatus = document.createElement("td");
    tdStatus.innerHTML = `<span class="badge badge-${item.state === 'ready' ? 'completed' : 'idle'}">${item.state === 'ready' ? '已落盘' : item.state}</span>`;

    const tdSize = document.createElement("td");
    tdSize.textContent = formatBytes(item.size_bytes);

    const tdPath = document.createElement("td");
    tdPath.innerHTML = `<code title="${item.path || ''}" style="max-width:280px; display:block; overflow:hidden; text-overflow:ellipsis;">${item.path || '—'}</code>`;

    const tdAction = document.createElement("td");
    tdAction.style.textAlign = "right";

    if (item.state === "ready" && item.size_bytes > 0) {
      const jobId = state.selectedJobId;
      const url = String(item.url || "").replace("{job_id}", encodeURIComponent(jobId));

      const btnDownload = document.createElement("a");
      btnDownload.className = "btn btn-xs btn-outline";
      btnDownload.href = `${url}?download=1`;
      btnDownload.textContent = "下载";
      btnDownload.download = item.name;
      tdAction.append(btnDownload);

      if (item.viewable) {
        const btnView = document.createElement("a");
        btnView.className = "btn btn-xs btn-ghost";
        btnView.href = url;
        btnView.target = "_blank";
        btnView.textContent = "预览";
        tdAction.prepend(btnView);
      }
    }

    const btnCopy = document.createElement("button");
    btnCopy.className = "btn btn-xs btn-ghost";
    btnCopy.type = "button";
    btnCopy.textContent = "复制路径";
    btnCopy.onclick = () => copyText(item.path);
    tdAction.append(btnCopy);

    tr.append(tdLayer, tdName, tdStatus, tdSize, tdPath, tdAction);
    tbody.append(tr);
  });
}

// Render Batch Swarm Matrix
function renderBatchMatrix(batch) {
  const counts = batch.status_counts || {};
  $("kpiTotal").textContent = String(batch.total_posts || 0);
  $("kpiCompleted").textContent = String(counts.completed || 0);
  $("kpiActive").textContent = String((counts.running || 0) + (counts.queued || 0) + (counts.cancelling || 0));
  $("kpiFailed").textContent = String((counts.failed || 0) + (counts.cancelled || 0));

  const recordCount = batch.post_user_records ?? batch.post_user_record_count ?? batch.per_post_valid_sum ?? 0;
  const samePostDuplicates = batch.within_post_duplicate_occurrences ?? batch.same_post_duplicate_occurrences ?? 0;
  const crossPostDuplicates = batch.cross_post_duplicate_occurrences ?? 0;
  const crossPostUniqueUsers = batch.cross_post_unique_users ?? 0;
  $("kpiSum").textContent = String(recordCount);
  $("kpiUnique").textContent = String(samePostDuplicates);
  $("kpiOverlapRate").textContent = `跨帖重复出现 ${crossPostDuplicates} 人次 · 独立用户 ${crossPostUniqueUsers}`;

  $("batchUpdatedTime").textContent = batch.updated_at ? `最后同步: ${new Date(batch.updated_at).toLocaleTimeString()}` : "正在同步";

  // Global Badge
  const gBadge = $("globalBatchBadge");
  const boundedSample = batch.completion_scope === BOUNDED_SAMPLE_COMPLETION_SCOPE;
  const hasEmptyLists = batch.status === "completed" && (batch.posts || []).some(post => post.result_outcome?.code === "empty-lists");
  gBadge.className = `badge badge-${boundedSample || hasEmptyLists ? "sample" : batch.status === "running" || batch.status === "cancelling" ? "running" : batch.status === "completed" ? "completed" : "idle"}`;
  gBadge.textContent = hasEmptyLists ? "已结束（含 Empty lists）" : boundedSample ? "样本完成（非全量）" : STATUS_LABELS[batch.status] || batch.status;

  const isBatchActive = batch.status === "running" || batch.status === "queued" || batch.status === "cancelling";
  $("btnStartBatch").disabled = isBatchActive;
  $("btnStopBatch").disabled = !isBatchActive;

  // Render Table Rows
  const tbody = $("batchTableBody");
  tbody.replaceChildren();

  const posts = batch.posts || [];
  if (!posts.length) {
    tbody.innerHTML = `
      <tr class="empty-placeholder">
        <td colspan="7"><div class="empty-state"><span class="empty-icon">📂</span><p>暂无任务</p></div></td>
      </tr>`;
    return;
  }

  posts.forEach(post => {
    const tr = document.createElement("tr");
    if (post.job_id === state.selectedJobId) {
      tr.className = "row-selected";
    }

    const tdIdx = document.createElement("td");
    tdIdx.style.fontFamily = "var(--font-mono)";
    tdIdx.style.color = "var(--text-muted)";
    tdIdx.textContent = `#${String(post.index).padStart(2, "0")}`;

    const tdUrl = document.createElement("td");
    const linkDiv = document.createElement("div");
    linkDiv.className = "post-link-display";
    const a = document.createElement("a");
    a.className = "post-link-text";
    a.href = post.post_url;
    a.target = "_blank";
    a.textContent = post.post_url;
    linkDiv.append(a);
    tdUrl.append(linkDiv);

    const tdStatus = document.createElement("td");
    const boundedPost = post.completion_scope === BOUNDED_SAMPLE_COMPLETION_SCOPE;
    const emptyList = post.status === "completed" && post.result_outcome?.code === "empty-lists";
    const postStatusClass = boundedPost || emptyList ? "sample" : post.status === "completed" ? "completed" : post.status === "running" || post.status === "cancelling" ? "running" : post.status === "failed" ? "failed" : "queued";
    const postStatusLabel = emptyList ? post.result_outcome.label : boundedPost ? "样本完成（非全量）" : STATUS_LABELS[post.status] || post.status;
    tdStatus.innerHTML = `<span class="badge badge-${postStatusClass}">${postStatusLabel}</span>`;

    const tdStage = document.createElement("td");
    tdStage.textContent = post.current_stage || "待命";

    const tdUsers = document.createElement("td");
    tdUsers.style.fontFamily = "var(--font-mono)";
    tdUsers.style.fontWeight = "700";
    tdUsers.style.color = "var(--emerald-text)";
    tdUsers.textContent = String(post.valid_users || 0);

    const tdPages = document.createElement("td");
    tdPages.style.fontFamily = "var(--font-mono)";
    tdPages.textContent = `${post.reaction_pages || 0} 页`;

    const tdActions = document.createElement("td");
    tdActions.style.textAlign = "right";
    const btnFocus = document.createElement("button");
    btnFocus.className = `btn btn-xs ${post.job_id === state.selectedJobId ? 'btn-primary' : 'btn-ghost'}`;
    btnFocus.textContent = post.job_id === state.selectedJobId ? "● 当前聚焦" : "聚焦透视";
    btnFocus.onclick = () => selectJob(post.job_id, post.post_url);
    tdActions.append(btnFocus);

    tr.append(tdIdx, tdUrl, tdStatus, tdStage, tdUsers, tdPages, tdActions);
    tbody.append(tr);
  });

  // Render Batch Aggregate Downloads
  const dBar = $("batchDownloadsBar");
  const dLinks = $("batchDownloadLinks");
  dLinks.replaceChildren();

  const batchArtifacts = (batch.artifacts || []).filter(a => a.state === "ready");
  if (batchArtifacts.length > 0) {
    dBar.style.display = "flex";
    batchArtifacts.forEach(a => {
      const link = document.createElement("a");
      link.className = "btn btn-xs btn-outline";
      link.href = a.url;
      link.download = a.name;
      link.textContent = `📥 ${a.label} (${formatBytes(a.size_bytes)})`;
      dLinks.append(link);
    });
  } else {
    dBar.style.display = "none";
  }

  // Update Canvas Graph
  drawNetworkGraph(posts, recordCount);
}

// Interactive Network Graph on HTML5 Canvas
function drawNetworkGraph(posts, recordCount) {
  const canvas = $("networkCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);

  if (!posts || posts.length === 0) {
    ctx.fillStyle = "#627284";
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.fillText("暂无批次数据，启动多链接爬取后展示拓扑网络", w / 2, h / 2);
    return;
  }

  // Draw Post Hubs
  const postCount = posts.length;
  const postCoords = [];
  posts.forEach((p, idx) => {
    const angle = (idx / postCount) * Math.PI * 2;
    const r = Math.min(w, h) * 0.32;
    const cx = w / 2 + Math.cos(angle) * r;
    const cy = h / 2 + Math.sin(angle) * r;
    postCoords.push({ x: cx, y: cy, post: p });
  });

  // Draw Interactors & Lines
  const sampleRecords = Math.min(48, Math.max(16, recordCount || 24));
  for (let i = 0; i < sampleRecords; i++) {
    const uAngle = (i / sampleRecords) * Math.PI * 2 + 0.1;
    const ur = Math.min(w, h) * 0.44;
    const ux = w / 2 + Math.cos(uAngle) * ur;
    const uy = h / 2 + Math.sin(uAngle) * ur;

    const isOverlap = (i % 4 === 0) && postCount > 1; // Simulated overlap visual

    if (isOverlap) {
      // Connect to two posts
      postCoords.slice(0, 2).forEach(pc => {
        ctx.strokeStyle = "rgba(227, 179, 65, 0.45)";
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(ux, uy);
        ctx.lineTo(pc.x, pc.y);
        ctx.stroke();
      });
      ctx.fillStyle = "#e3b341";
      ctx.beginPath();
      ctx.arc(ux, uy, 3.5, 0, Math.PI * 2);
      ctx.fill();
    } else {
      // Connect to nearest post
      const targetPost = postCoords[i % postCoords.length];
      ctx.strokeStyle = "rgba(46, 160, 67, 0.25)";
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(ux, uy);
      ctx.lineTo(targetPost.x, targetPost.y);
      ctx.stroke();

      ctx.fillStyle = "#3fb950";
      ctx.beginPath();
      ctx.arc(ux, uy, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw Central Hub Nodes
  postCoords.forEach((pc, idx) => {
    ctx.fillStyle = "#1f6feb";
    ctx.beginPath();
    ctx.arc(pc.x, pc.y, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#58a6ff";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 9px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`P${idx + 1}`, pc.x, pc.y);
  });
}

// Select a single job to focus on
async function selectJob(jobId, url = "") {
  state.selectedJobId = jobId;
  localStorage.setItem("omniCaptureSelectedJobId", jobId);
  $("focusPostLabel").textContent = url ? `聚焦: ${url.replace(/^https?:\/\/(www\.)?facebook\.com/, '')}` : `Job ${jobId}`;
  logTicker(`切换焦点至任务 ${jobId}`, "info");

  try {
    const progress = await api(`/v1/jobs/${encodeURIComponent(jobId)}/progress`);
    renderPipeline(progress.stages || [], progress.completion_scope);
    renderTelemetry(progress);
    renderArtifactsVault(progress.artifacts || []);
  } catch (err) {
    logTicker(`读取任务 ${jobId} 进度失败: ${err.message}`, "err");
  }
}

// Poll Loop
async function pollCycle() {
  await checkHealth();

  if (state.batchId) {
    try {
      const batch = await api(`/v1/batches/${encodeURIComponent(state.batchId)}/progress`);
      renderBatchMatrix(batch);

      if (TERMINAL_BATCH_STATES.has(batch.status)) {
        await adoptLatestBatchIfCurrentUnavailable(batch);
      }

      // Auto-select first or running job if none selected
      if (!state.selectedJobId && batch.posts && batch.posts.length > 0) {
        const p = batch.posts.find(x => x.status === "running" || x.status === "cancelling") || batch.posts[0];
        selectJob(p.job_id, p.post_url);
      } else if (state.selectedJobId) {
        const active = (batch.posts || []).find(p => p.job_id === state.selectedJobId);
        if (active) {
          selectJob(active.job_id, active.post_url);
        }
      }
    } catch (err) {
      logTicker(`批次状态拉取异常: ${err.message}`, "err");
      await adoptLatestBatchIfCurrentUnavailable();
    }
  } else {
    await adoptLatestBatchIfCurrentUnavailable();
  }

  state.pollTimer = setTimeout(pollCycle, 1500);
}

// Form Handlers
$("batchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("postUrlsInput").value;
  const urls = text.split("\n").map(u => u.trim()).filter(u => u.length > 0);

  if (urls.length === 0) {
    showToast("请输入至少一条有效的 Facebook 帖子链接");
    return;
  }

  const artifacts = Array.from(document.querySelectorAll('input[name="saveArtifact"]:checked')).map(cb => cb.value);
  const firstPageTestMode = $("reactionFirstPageTest").checked;
  const requestBody = buildBatchRequestBody(urls, artifacts, firstPageTestMode);

  $("btnStartBatch").disabled = true;
  logTicker(`正在提交包含 ${urls.length} 个链接的采集批次...`, "info");

  try {
    const res = await api("/v1/batches", {
      method: "POST",
      body: JSON.stringify(requestBody)
    });

    state.batchId = res.batch_id;
    localStorage.setItem("omniCaptureBatchId", state.batchId);
    state.selectedJobId = "";
    localStorage.removeItem("omniCaptureSelectedJobId");

    showToast(`批次 ${res.batch_id} 已成功创建并启动！`);
    logTicker(`批次 ${res.batch_id} 启动成功，进入多链接隔离流水线`, "ok");
  } catch (err) {
    showToast(`提交批次失败: ${err.message}`);
    logTicker(`提交失败: ${err.message}`, "err");
    $("btnStartBatch").disabled = false;
  }
});

$("btnStopBatch").addEventListener("click", async () => {
  if (!state.batchId) return;
  if (!confirm("确定要终止当前整个批次的爬取任务吗？")) return;

  try {
    await api(`/v1/batches/${encodeURIComponent(state.batchId)}/cancel`, { method: "POST" });
    showToast("已向后台发送批次终止指令");
    logTicker(`批次 ${state.batchId} 终止信号已发送`, "warn");
  } catch (err) {
    showToast(`终止请求失败: ${err.message}`);
  }
});

$("btnLoadSamples").addEventListener("click", () => {
  $("postUrlsInput").value = SAMPLE_POSTS.join("\n");
  updateUrlCount();
  showToast("已载入 3 条 Facebook 样例链接");
});

$("btnClearUrls").addEventListener("click", () => {
  $("postUrlsInput").value = "";
  updateUrlCount();
});

$("postUrlsInput").addEventListener("input", updateUrlCount);

$("btnCopySandboxPath").addEventListener("click", () => {
  copyText($("sandboxPathText").textContent);
});

// Theme Switcher
$("themeToggle").addEventListener("click", () => {
  const isDark = document.body.classList.contains("theme-dark");
  document.body.className = isDark ? "theme-light" : "theme-dark";
  localStorage.setItem("omniCaptureTheme", isDark ? "light" : "dark");
});

// Tab Switcher
$("tabTelemetryBtn").addEventListener("click", () => {
  $("tabTelemetry").style.display = "block";
  $("tabGraph").style.display = "none";
  $("tabTelemetryBtn").classList.add("active");
  $("tabGraphBtn").classList.remove("active");
});

$("tabGraphBtn").addEventListener("click", () => {
  $("tabTelemetry").style.display = "none";
  $("tabGraph").style.display = "block";
  $("tabTelemetryBtn").classList.remove("active");
  $("tabGraphBtn").classList.add("active");
});

// Console Ticker Toggle
$("tickerHeader").addEventListener("click", () => {
  const body = $("tickerBody");
  body.style.display = body.style.display === "none" ? "block" : "none";
});

// Bootstrap
window.addEventListener("DOMContentLoaded", async () => {
  if (state.theme === "light") document.body.className = "theme-light";
  renderPipeline();
  updateUrlCount();
  await restoreLatestBatch();
  await pollCycle();
  logTicker("OmniCapture v2 控制台初始化完毕，守护进程启动.", "ok");
});
