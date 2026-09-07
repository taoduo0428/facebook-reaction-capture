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

// API Call Wrapper
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
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
function renderPipeline(stagesData = []) {
  const container = $("pipelineFlow");
  container.replaceChildren();

  const activeStageName = stagesData.find(s => s.state === "active")?.label ||
                          stagesData.filter(s => s.state === "complete").pop()?.label || "等待执行";

  STAGES_DEF.forEach((def, index) => {
    const runtime = stagesData.find(s => s.id === def.id) || { state: "waiting" };
    const li = document.createElement("li");
    li.className = `stage-card ${runtime.state}`;

    const bubble = document.createElement("div");
    bubble.className = "stage-bubble";
    bubble.textContent = runtime.state === "complete" ? "✓" : String(index + 1).padStart(2, "0");

    const info = document.createElement("div");
    info.className = "stage-info";
    const title = document.createElement("strong");
    title.textContent = def.label;
    const detail = document.createElement("span");
    detail.textContent = runtime.detail || def.detail;
    info.append(title, detail);

    const badge = document.createElement("span");
    badge.className = `badge badge-${runtime.state === "complete" ? "completed" : runtime.state === "active" ? "running" : "idle"}`;
    badge.textContent = runtime.state === "complete" ? "已完成" : runtime.state === "active" ? "进行中" : "等待";

    li.append(bubble, info, badge);
    container.append(li);
  });
}

// Render Telemetry Grid
function renderTelemetry(progress) {
  const m = progress.metrics || {};
  $("telUsers").textContent = String(m.valid_reactor_users || m.reaction_users || 0);
  $("telPages").textContent = String(m.reaction_pages || 0);
  $("telAttempts").textContent = String(m.attempts || 0);
  $("telGrowth").textContent = String(m.growth_events || 0);
  $("telRateLimits").textContent = String(m.rate_limits || 0);

  const termEl = $("telTerminal");
  termEl.textContent = m.terminal ? "已到达 (Full)" : "未到达";
  termEl.className = `tel-val ${m.terminal ? "tel-emerald" : ""}`;

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
  $("kpiActive").textContent = String((counts.running || 0) + (counts.queued || 0));
  $("kpiFailed").textContent = String((counts.failed || 0) + (counts.cancelled || 0));

  const sum = batch.per_post_valid_sum || 0;
  const uniq = batch.cross_post_unique_users || 0;
  $("kpiSum").textContent = String(sum);
  $("kpiUnique").textContent = String(uniq);

  // Compute Overlap Rate
  const overlap = sum > 0 ? (((sum - uniq) / sum) * 100).toFixed(1) : "0.0";
  $("kpiOverlapRate").textContent = `去重率: ${overlap}% (${sum - uniq} 人多重重叠)`;

  $("batchUpdatedTime").textContent = batch.updated_at ? `最后同步: ${new Date(batch.updated_at).toLocaleTimeString()}` : "正在同步";

  // Global Badge
  const gBadge = $("globalBatchBadge");
  gBadge.className = `badge badge-${batch.status === "running" ? "running" : batch.status === "completed" ? "completed" : "idle"}`;
  gBadge.textContent = STATUS_LABELS[batch.status] || batch.status;

  const isBatchActive = batch.status === "running" || batch.status === "queued";
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
    tdStatus.innerHTML = `<span class="badge badge-${post.status === 'completed' ? 'completed' : post.status === 'running' ? 'running' : post.status === 'failed' ? 'failed' : 'queued'}">${STATUS_LABELS[post.status] || post.status}</span>`;

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
  drawNetworkGraph(posts, sum, uniq);
}

// Interactive Network Graph on HTML5 Canvas
function drawNetworkGraph(posts, totalInteractions, uniqueUsers) {
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
  const sampleUsers = Math.min(48, Math.max(16, uniqueUsers || 24));
  for (let i = 0; i < sampleUsers; i++) {
    const uAngle = (i / sampleUsers) * Math.PI * 2 + 0.1;
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
    renderPipeline(progress.stages || []);
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

      // Auto-select first or running job if none selected
      if (!state.selectedJobId && batch.posts && batch.posts.length > 0) {
        const p = batch.posts.find(x => x.status === "running") || batch.posts[0];
        selectJob(p.job_id, p.post_url);
      } else if (state.selectedJobId) {
        const active = (batch.posts || []).find(p => p.job_id === state.selectedJobId);
        if (active) {
          selectJob(active.job_id, active.post_url);
        }
      }
    } catch (err) {
      logTicker(`批次状态拉取异常: ${err.message}`, "err");
    }
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

  $("btnStartBatch").disabled = true;
  logTicker(`正在提交包含 ${urls.length} 个链接的采集批次...`, "info");

  try {
    const res = await api("/v1/batches", {
      method: "POST",
      body: JSON.stringify({ post_urls: urls, save_artifacts: artifacts })
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
window.addEventListener("DOMContentLoaded", () => {
  if (state.theme === "light") document.body.className = "theme-light";
  renderPipeline();
  updateUrlCount();
  pollCycle();
  logTicker("OmniCapture v2 控制台初始化完毕，守护进程启动.", "ok");
});
