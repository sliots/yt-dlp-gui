import { api, clearToken, getToken, setToken, verifyToken, websocketUrl } from "./api.js";
import { initChannels } from "./channels.js";
import { setStatus, store, updateRuntime } from "./store.js";

const THEME_KEY = "ytdlp.theme";
const LOG_MAX = 600;
const ISSUE_MAX = 300;

const elements = {
  tokenDialog: document.querySelector("#token-dialog"),
  tokenForm: document.querySelector("#token-form"),
  tokenInput: document.querySelector("#token-input"),
  tokenError: document.querySelector("#token-error"),
  tokenChange: document.querySelector("#token-change"),
  toast: document.querySelector("#toast-container"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmTitle: document.querySelector("#confirm-title"),
  confirmMessage: document.querySelector("#confirm-message"),
  confirmAccept: document.querySelector("#confirm-accept"),
  confirmCancel: document.querySelector("#confirm-cancel"),
  connection: document.querySelector("#connection-state"),
  logArea: document.querySelector("#log-area"),
  issues: document.querySelector("#log-issues"),
  issuesHeader: document.querySelector("#issues-header"),
  autoScroll: document.querySelector("#auto-scroll"),
};

let tokenResolver = null;
let pollTimer = null;
let websocket = null;
let reconnectTimer = null;
let reconnectDelay = 1000;
let issues = { warning: 0, error: 0 };
let confirmResolver = null;

function toast(message, ok = true) {
  const item = document.createElement("div");
  item.className = `toast ${ok ? "ok" : "error"}`;
  item.textContent = message;
  elements.toast.appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

function confirmAction(title, message) {
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = message;
  elements.confirmDialog.showModal();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function settleConfirm(value) {
  elements.confirmDialog.close();
  if (confirmResolver) confirmResolver(value);
  confirmResolver = null;
}

function applyTheme(preference) {
  const systemLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  const resolved = preference === "system" ? (systemLight ? "light" : "dark") : preference;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = preference;
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    const active = button.dataset.themeChoice === preference;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function initTheme() {
  const preference = localStorage.getItem(THEME_KEY) || "system";
  applyTheme(preference);
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.themeChoice;
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
    });
  });
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if ((localStorage.getItem(THEME_KEY) || "system") === "system") applyTheme("system");
  });
}

function requestToken(message = "输入 APP_TOKEN 以访问控制台") {
  elements.tokenError.textContent = message;
  elements.tokenInput.value = "";
  if (!elements.tokenDialog.open) elements.tokenDialog.showModal();
  setTimeout(() => elements.tokenInput.focus(), 0);
}

function hideTokenDialog() {
  if (elements.tokenDialog.open) elements.tokenDialog.close();
}

async function ensureAuthenticated() {
  if (!getToken()) {
    requestToken();
    return false;
  }
  try {
    await verifyToken();
    hideTokenDialog();
    return true;
  } catch {
    requestToken("Token 无效，请重新输入");
    return false;
  }
}

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function activate(selected) {
    tabs.forEach((tab) => {
      const active = tab === selected;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      document.querySelector(`#${tab.dataset.panel}`).hidden = !active;
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(index + delta + tabs.length) % tabs.length];
      next.focus();
      activate(next);
    });
  });
}

function setConnection(state, text) {
  elements.connection.className = `connection ${state}`;
  elements.connection.lastElementChild.textContent = text;
}

function appendLog(level, message, timestamp) {
  const row = document.createElement("div");
  row.className = `log-line ${level}`;
  const time = timestamp ? new Date(timestamp) : new Date();
  const timeText = Number.isNaN(time.getTime()) ? "" : time.toLocaleTimeString("zh-CN", { hour12: false });
  row.textContent = `${timeText} ${message}`.trim();
  elements.logArea.appendChild(row);
  while (elements.logArea.children.length > LOG_MAX) elements.logArea.firstElementChild.remove();
  if (elements.autoScroll.checked) elements.logArea.scrollTop = elements.logArea.scrollHeight;

  if (level === "WARNING" || level === "ERROR") {
    const issue = row.cloneNode(true);
    elements.issues.appendChild(issue);
    while (elements.issues.children.length > ISSUE_MAX) elements.issues.firstElementChild.remove();
    if (level === "WARNING") issues.warning += 1;
    else issues.error += 1;
    elements.issuesHeader.textContent = `${issues.warning} 个警告 · ${issues.error} 个错误`;
    if (elements.autoScroll.checked) elements.issues.scrollTop = elements.issues.scrollHeight;
  }
}

function clearLogs() {
  elements.logArea.replaceChildren();
  elements.issues.replaceChildren();
  issues = { warning: 0, error: 0 };
  elements.issuesHeader.textContent = "";
  document.querySelector("#log-stats").hidden = true;
  document.querySelector("#run-summary-label").hidden = true;
}

function connectWebSocket() {
  if (!getToken()) return;
  if (websocket) websocket.close();
  setConnection("connecting", "连接日志服务");
  websocket = new WebSocket(websocketUrl());
  websocket.addEventListener("open", () => {
    reconnectDelay = 1000;
    setConnection("online", "日志已连接");
  });
  websocket.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload.level === "PING" || payload.level === "SYNC") return;
    if (payload.level === "PROGRESS") {
      try {
        const progress = JSON.parse(payload.msg);
        if (progress.channel_id) {
          updateRuntime({
            channel_id: progress.channel_id,
            state: "running",
            percent: progress.percent,
          });
        }
      } catch {
        return;
      }
      return;
    }
    appendLog(payload.level, payload.msg, payload.ts);
  });
  websocket.addEventListener("close", () => {
    websocket = null;
    if (!getToken()) return;
    setConnection("offline", "日志断开，正在重连");
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectWebSocket, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  });
}

function renderStatus(status) {
  setStatus(status);
  const state = status.state || "idle";
  const labels = {
    idle: "已停止",
    running: "运行中",
    waiting: "等待下一轮",
    stopping: "正在停止",
  };
  document.querySelector("#status-mode").textContent = labels[state] || state;
  document.querySelector("#status-card").dataset.state = state;
  document.querySelector("#progress-fill").style.width = `${status.progress_percent || 0}%`;
  document.querySelector("#progress-text").textContent = `${Math.round(status.progress_percent || 0)}%`;
  document.querySelector("#channel-progress").textContent =
    status.total_channels > 0
      ? `${(status.current_channel_index || 0) + 1} / ${status.total_channels}`
      : "--";
  document.querySelector("#channel-label").textContent = status.current_channel_label || "";
  document.querySelector("#countdown-display").textContent =
    status.next_round_seconds > 0
      ? `下一轮 ${Math.floor(status.next_round_seconds / 60)}m ${status.next_round_seconds % 60}s`
      : "";

  const active = Boolean(status.active);
  document.querySelectorAll("[data-control-start]").forEach((button) => {
    button.disabled = active;
  });
  document.querySelector("#control-stop").disabled = !active || state === "stopping";
  document.querySelector("#control-update").disabled = active;

  const summary = status.current_run || status.last_run;
  const stats = document.querySelector("#log-stats");
  const label = document.querySelector("#run-summary-label");
  if (summary) {
    stats.hidden = false;
    label.hidden = false;
    label.textContent = status.current_run ? "当前轮次" : "上一轮摘要";
    const fields = {
      "stat-total": summary.total_channels,
      "stat-complete": summary.completed_channels,
      "stat-warning": summary.warning_channels,
      "stat-duration": formatDuration(summary.duration_seconds),
      "stat-downloaded": summary.downloaded_files,
      "stat-archive": summary.archive_skipped,
      "stat-filtered": summary.filtered_skipped,
      "stat-member": summary.member_skipped,
    };
    Object.entries(fields).forEach(([id, value]) => {
      document.querySelector(`#${id}`).textContent = value ?? 0;
    });
  }
}

function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

async function pollStatus() {
  clearTimeout(pollTimer);
  if (document.visibilityState === "visible" && getToken()) {
    try {
      renderStatus(await api("/status"));
    } catch (error) {
      if (error.status !== 401) setConnection("offline", error.message);
    }
  }
  pollTimer = setTimeout(pollStatus, 1000);
}

async function runControl(path, label) {
  try {
    const result = await api(path, { method: "POST" });
    toast(result.reason || label, Boolean(result.ok));
    await pollStatus();
  } catch (error) {
    toast(error.message, false);
  }
}

async function loadConfig() {
  const config = await api("/config");
  store.config = config;
  const general = config.general || {};
  const limits = config.download_limits || {};
  const values = {
    "cfg-output": general.output_base_path,
    "cfg-proxy": general.proxy_url,
    "cfg-sleep-requests": general.sleep_requests,
    "cfg-sleep-time": general.sleep_time,
    "cfg-wait": general.wait_time_minutes,
    "cfg-dateafter": general.dateafter,
    "cfg-filename": general.filename_format,
    "cfg-normal-timeout": general.normal_timeout,
    "cfg-first-timeout": general.first_run_timeout,
    "cfg-cookies-source": general.cookies_source,
    "cfg-cookies-browser": general.cookies_browser,
    "cfg-cookies-profile": general.cookies_browser_profile,
    "cfg-cookies-container": general.cookies_browser_container,
    "cfg-po-url": general.po_token_base_url,
    "cfg-log-history": general.log_max_history,
    "cfg-normal-limit": limits.normal_limit,
    "cfg-first-limit": limits.first_run_limit,
  };
  Object.entries(values).forEach(([id, value]) => {
    const element = document.querySelector(`#${id}`);
    if (element && value !== undefined && value !== null) element.value = value;
  });
  document.querySelector("#cfg-quiet").checked = Boolean(general.quiet_mode);
  document.querySelector("#cfg-po-enabled").checked = general.po_token_enabled !== false;
  toggleCookieSections();
  await checkCookieStatus();
}

async function saveConfig() {
  const body = {
    proxy_url: document.querySelector("#cfg-proxy").value.trim(),
    sleep_requests: Number(document.querySelector("#cfg-sleep-requests").value),
    sleep_time: document.querySelector("#cfg-sleep-time").value.trim(),
    wait_time_minutes: Number(document.querySelector("#cfg-wait").value),
    quiet_mode: document.querySelector("#cfg-quiet").checked,
    dateafter: document.querySelector("#cfg-dateafter").value.trim(),
    filename_format: document.querySelector("#cfg-filename").value.trim(),
    normal_timeout: Number(document.querySelector("#cfg-normal-timeout").value),
    first_run_timeout: Number(document.querySelector("#cfg-first-timeout").value),
    cookies_source: document.querySelector("#cfg-cookies-source").value,
    cookies_browser: document.querySelector("#cfg-cookies-browser").value,
    cookies_browser_profile: document.querySelector("#cfg-cookies-profile").value.trim(),
    cookies_browser_container: document.querySelector("#cfg-cookies-container").value.trim(),
    po_token_enabled: document.querySelector("#cfg-po-enabled").checked,
    po_token_base_url: document.querySelector("#cfg-po-url").value.trim(),
    log_max_history: Number(document.querySelector("#cfg-log-history").value),
    normal_limit: Number(document.querySelector("#cfg-normal-limit").value),
    first_run_limit: Number(document.querySelector("#cfg-first-limit").value),
  };
  try {
    await api("/config", { method: "PATCH", body: JSON.stringify(body) });
    toast("设置已保存", true);
    await loadConfig();
  } catch (error) {
    toast(error.message, false);
  }
}

function toggleCookieSections() {
  const source = document.querySelector("#cfg-cookies-source").value;
  document.querySelector("#cookie-file-section").hidden = source !== "file";
  document.querySelector("#cookie-browser-section").hidden = source !== "browser";
}

async function checkCookieStatus() {
  const source = document.querySelector("#cfg-cookies-source").value;
  const warning = document.querySelector("#cookie-warning");
  if (source !== "file") {
    warning.hidden = true;
    return;
  }
  try {
    const result = await api("/cookies/status");
    warning.hidden = result.exists;
  } catch {
    warning.hidden = true;
  }
}

async function uploadCookies() {
  const input = document.querySelector("#cookie-file");
  if (!input.files[0]) {
    toast("请选择 cookies.txt", false);
    return;
  }
  const data = new FormData();
  data.append("file", input.files[0]);
  try {
    await api("/cookies", { method: "POST", body: data });
    input.value = "";
    toast("Cookies 已上传", true);
    await checkCookieStatus();
  } catch (error) {
    toast(error.message, false);
  }
}

async function saveCookiesText() {
  const content = document.querySelector("#cookies-text").value.trim();
  try {
    await api("/cookies/text", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    document.querySelector("#cookies-text").value = "";
    toast("Cookies 已保存", true);
    await checkCookieStatus();
  } catch (error) {
    toast(error.message, false);
  }
}

async function restartService() {
  const ok = await confirmAction("重启服务", "正在下载的任务会被中断。确认重启？");
  if (!ok) return;
  await runControl("/control/restart", "服务正在重启");
  setTimeout(() => location.reload(), 3000);
}

function bindEvents() {
  document.querySelectorAll("[data-control-start]").forEach((button) => {
    button.addEventListener("click", () =>
      runControl(`/control/${button.dataset.controlStart}`, "任务已启动"),
    );
  });
  document.querySelector("#control-stop").addEventListener("click", () =>
    runControl("/control/stop", "正在停止"),
  );
  document.querySelector("#control-update").addEventListener("click", () =>
    runControl("/control/update-ytdlp", "yt-dlp 更新完成"),
  );
  document.querySelector("#clear-logs").addEventListener("click", clearLogs);
  document.querySelector("#settings-save").addEventListener("click", saveConfig);
  document.querySelector("#restart-service").addEventListener("click", restartService);
  document.querySelector("#cfg-cookies-source").addEventListener("change", toggleCookieSections);
  document.querySelector("#cookies-upload").addEventListener("click", uploadCookies);
  document.querySelector("#cookies-save-text").addEventListener("click", saveCookiesText);
  elements.tokenChange.addEventListener("click", () => requestToken("更换 APP_TOKEN"));
  elements.tokenForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setToken(elements.tokenInput.value.trim());
    if (!(await ensureAuthenticated())) return;
    await afterAuthentication();
  });
  elements.confirmAccept.addEventListener("click", () => settleConfirm(true));
  elements.confirmCancel.addEventListener("click", () => settleConfirm(false));
  elements.confirmDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    settleConfirm(false);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") pollStatus();
  });
}

async function afterAuthentication() {
  await Promise.all([initChannelsController.refresh(), loadConfig(), pollStatus()]);
  connectWebSocket();
}

const initChannelsController = initChannels({ toast, confirmAction });

async function main() {
  initTheme();
  initTabs();
  bindEvents();
  window.addEventListener("auth-required", () => {
    requestToken("Token 已失效，请重新输入");
    if (websocket) websocket.close();
  });
  if (await ensureAuthenticated()) await afterAuthentication();
  else pollStatus();
}

main();
