import { api } from "./api.js";
import { emit, setChannels, store, subscribe } from "./store.js";

const FILTER_KEY = "ytdlp.channelFilters";
const ASMR_FILTER = "(?i)(ASMR|安眠|KU100|asmr|Asmr)";

const iconPaths = {
  plus: ["M12 5v14", "M5 12h14"],
  grip: ["M9 5h.01", "M9 12h.01", "M9 19h.01", "M15 5h.01", "M15 12h.01", "M15 19h.01"],
  edit: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6", "M10 11v6", "M14 11v6"],
  play: ["m7 4 13 8-13 8Z"],
  check: ["m5 12 4 4L19 6"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  radio: ["M12 12h.01", "M8.5 8.5a5 5 0 0 0 0 7", "M15.5 8.5a5 5 0 0 1 0 7"],
  search: ["m21 21-4.3-4.3", "M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z"],
  copy: ["M8 8h11v11H8z", "M5 16H3V3h13v2"],
  up: ["m18 15-6-6-6 6"],
  down: ["m6 9 6 6 6-6"],
  link: ["M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1L10.8 5.2"],
  save: ["M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z", "M17 21v-8H7v8", "M7 3v5h8"],
};

function makeIcon(name, size = 17) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const value of iconPaths[name] || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", value);
    svg.appendChild(path);
  }
  return svg;
}

function button(label, iconName, className = "icon-button") {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.title = label;
  element.setAttribute("aria-label", label);
  if (iconName) element.appendChild(makeIcon(iconName));
  return element;
}

function textButton(label, iconName, className = "") {
  const element = button(label, iconName, `button ${className}`.trim());
  element.appendChild(document.createTextNode(label));
  return element;
}

function badge(text, tone = "") {
  const element = document.createElement("span");
  element.className = `badge ${tone}`.trim();
  element.textContent = text;
  return element;
}

function runtimeLabel(runtime = {}) {
  const labels = {
    idle: "未运行",
    queued: "排队中",
    running: "下载中",
    success: "成功",
    warning: "有警告",
    error: "失败",
    stopped: "已停止",
    disabled: "已禁用",
  };
  return labels[runtime.state || "idle"] || runtime.state || "未运行";
}

function runtimeTone(runtime = {}) {
  if (runtime.state === "success") return "success";
  if (runtime.state === "warning") return "warning";
  if (runtime.state === "error") return "error";
  if (runtime.state === "running" || runtime.state === "queued") return "active";
  return "";
}

function formatDate(value) {
  if (!value) return "从未";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function loadFilters() {
  try {
    Object.assign(store.filters, JSON.parse(localStorage.getItem(FILTER_KEY) || "{}"));
  } catch {
    localStorage.removeItem(FILTER_KEY);
  }
}

function saveFilters() {
  localStorage.setItem(FILTER_KEY, JSON.stringify(store.filters));
}

export function initChannels({ toast, confirmAction }) {
  loadFilters();

  const refs = {
    tableBody: document.querySelector("#channels-table tbody"),
    mobileList: document.querySelector("#channels-mobile"),
    count: document.querySelector("#channel-count"),
    search: document.querySelector("#channel-search"),
    enabled: document.querySelector("#channel-filter-enabled"),
    vidType: document.querySelector("#channel-filter-vid"),
    dlType: document.querySelector("#channel-filter-dl"),
    runtime: document.querySelector("#channel-filter-runtime"),
    sort: document.querySelector("#channel-sort"),
    dragHint: document.querySelector("#channel-drag-hint"),
    batchBar: document.querySelector("#batch-bar"),
    batchCount: document.querySelector("#batch-count"),
    batchAction: document.querySelector("#batch-action"),
    batchValue: document.querySelector("#batch-value"),
    batchFilterText: document.querySelector("#batch-filter-text"),
    batchApply: document.querySelector("#batch-apply"),
    drawer: document.querySelector("#channel-drawer"),
    drawerTitle: document.querySelector("#channel-drawer-title"),
    form: document.querySelector("#channel-form"),
    formError: document.querySelector("#channel-form-error"),
    deleteButton: document.querySelector("#channel-delete"),
    copyButton: document.querySelector("#channel-copy"),
    testButton: document.querySelector("#channel-test"),
    resolveButton: document.querySelector("#channel-resolve"),
    resolveStatus: document.querySelector("#channel-resolve-status"),
    outputPreview: document.querySelector("#channel-output-preview"),
    urlPreview: document.querySelector("#channel-url-preview"),
    folderWarning: document.querySelector("#channel-folder-warning"),
    filterPreset: document.querySelector("#channel-filter-preset"),
    saveButton: document.querySelector("#channel-save"),
  };

  refs.search.value = store.filters.query;
  refs.enabled.value = store.filters.enabled;
  refs.vidType.value = store.filters.vidType;
  refs.dlType.value = store.filters.dlType;
  refs.runtime.value = store.filters.runtime;
  refs.sort.value = store.filters.sort;

  let drawerMode = "add";
  let drawerChannelId = null;
  let drawerMetadata = {};
  let dirty = false;
  let draggedId = null;

  function filteredChannels() {
    const query = store.filters.query.trim().toLocaleLowerCase();
    let items = store.channels.filter((channel) => {
      const haystack = [channel.folder_name, channel.youtube_id, channel.youtube_channel_id, channel.channel_title]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase();
      if (query && !haystack.includes(query)) return false;
      if (store.filters.enabled !== "all" && String(channel.enabled) !== store.filters.enabled) {
        return false;
      }
      if (store.filters.vidType !== "all" && channel.vid_type !== store.filters.vidType) return false;
      if (store.filters.dlType !== "all" && channel.dl_type !== store.filters.dlType) return false;
      if (
        store.filters.runtime !== "all" &&
        (channel.runtime?.state || "idle") !== store.filters.runtime
      ) {
        return false;
      }
      return true;
    });

    const sorters = {
      name: (a, b) => a.folder_name.localeCompare(b.folder_name, "zh-CN"),
      status: (a, b) => runtimeLabel(a.runtime).localeCompare(runtimeLabel(b.runtime), "zh-CN"),
      recent: (a, b) =>
        new Date(b.runtime?.last_finished_at || 0) - new Date(a.runtime?.last_finished_at || 0),
      type: (a, b) => `${a.vid_type}-${a.dl_type}`.localeCompare(`${b.vid_type}-${b.dl_type}`),
    };
    if (sorters[store.filters.sort]) items.sort(sorters[store.filters.sort]);
    return items;
  }

  function dragAllowed() {
    return (
      store.filters.sort === "manual" &&
      !store.filters.query &&
      store.filters.enabled === "all" &&
      store.filters.vidType === "all" &&
      store.filters.dlType === "all" &&
      store.filters.runtime === "all"
    );
  }

  async function refresh() {
    const payload = await api("/channels");
    setChannels(payload);
  }

  function render() {
    const items = filteredChannels();
    const canDrag = dragAllowed();
    refs.tableBody.replaceChildren();
    refs.mobileList.replaceChildren();
    refs.dragHint.textContent = canDrag ? "拖动排序，顺序即执行顺序" : "筛选或视图排序时已暂停拖拽";
    refs.count.textContent = `${items.length} / ${store.channels.length} 个频道`;
    refs.batchBar.hidden = store.selectedIds.size === 0;
    refs.batchCount.textContent = `已选择 ${store.selectedIds.size} 个`;

    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 8;
      cell.className = "empty-state";
      cell.textContent = store.channels.length ? "没有符合当前筛选条件的频道" : "还没有频道，先添加一个";
      row.appendChild(cell);
      refs.tableBody.appendChild(row);

      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = cell.textContent;
      refs.mobileList.appendChild(empty);
      return;
    }

    for (const channel of items) {
      refs.tableBody.appendChild(buildRow(channel, canDrag));
      refs.mobileList.appendChild(buildCard(channel, canDrag));
    }
  }

  function buildRow(channel, canDrag) {
    const row = document.createElement("tr");
    row.dataset.channelId = channel.channel_id;
    if (store.selectedIds.has(channel.channel_id)) row.classList.add("selected");
    if (canDrag) {
      row.draggable = true;
      row.addEventListener("dragstart", () => {
        draggedId = channel.channel_id;
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => {
        draggedId = null;
        row.classList.remove("dragging");
      });
      row.addEventListener("dragover", (event) => event.preventDefault());
      row.addEventListener("drop", (event) => {
        event.preventDefault();
        if (draggedId && draggedId !== channel.channel_id) {
          moveRelative(draggedId, channel.channel_id);
        }
      });
    }

    const dragCell = document.createElement("td");
    dragCell.className = "drag-cell";
    if (canDrag) dragCell.appendChild(makeIcon("grip"));
    row.appendChild(dragCell);

    const selectCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = store.selectedIds.has(channel.channel_id);
    checkbox.setAttribute("aria-label", `选择 ${channel.folder_name}`);
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => toggleSelected(channel.channel_id, checkbox.checked));
    selectCell.appendChild(checkbox);
    row.appendChild(selectCell);

    const name = document.createElement("td");
    const strong = document.createElement("strong");
    strong.textContent = channel.channel_title || channel.folder_name;
    const subtitle = document.createElement("span");
    subtitle.className = "subtle";
    subtitle.textContent = channel.folder_name;
    name.append(strong, subtitle);
    row.appendChild(name);

    const source = document.createElement("td");
    source.textContent = channel.youtube_channel_id || `@${channel.youtube_id}`;
    row.appendChild(source);

    const types = document.createElement("td");
    const typeWrap = document.createElement("div");
    typeWrap.className = "badge-row";
    typeWrap.append(
      badge(channel.vid_type, "neutral"),
      badge(channel.dl_type === "audio" ? "音频" : "视频", "accent"),
      channel.is_first ? badge("首次", "warning") : document.createTextNode(""),
      channel.enabled ? document.createTextNode("") : badge("停用", "neutral"),
    );
    types.appendChild(typeWrap);
    row.appendChild(types);

    const runtime = document.createElement("td");
    const runtimeWrap = document.createElement("div");
    runtimeWrap.className = "runtime-cell";
    runtimeWrap.append(
      badge(runtimeLabel(channel.runtime), runtimeTone(channel.runtime)),
      channel.runtime?.state === "running"
        ? document.createTextNode(`${Math.round(channel.runtime.percent || 0)}%`)
        : document.createTextNode(channel.runtime?.last_error || ""),
    );
    runtime.appendChild(runtimeWrap);
    row.appendChild(runtime);

    const recent = document.createElement("td");
    recent.textContent = formatDate(channel.runtime?.last_finished_at || channel.resolved_at);
    row.appendChild(recent);

    const actions = document.createElement("td");
    actions.className = "row-actions";
    const edit = button("编辑", "edit");
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      openDrawer("edit", channel);
    });
    const test = button("检测", "radio");
    test.addEventListener("click", async (event) => {
      event.stopPropagation();
      await testChannel(channel);
    });
    const remove = button("删除", "trash", "icon-button danger");
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteChannel(channel);
    });
    actions.append(edit, test, remove);
    if (canDrag) {
      const up = button("上移", "up");
      const down = button("下移", "down");
      up.addEventListener("click", (event) => {
        event.stopPropagation();
        move(channel.channel_id, -1);
      });
      down.addEventListener("click", (event) => {
        event.stopPropagation();
        move(channel.channel_id, 1);
      });
      actions.prepend(up, down);
    }
    row.appendChild(actions);

    row.addEventListener("click", (event) => {
      if (event.target.closest("button, input")) return;
      openDrawer("edit", channel);
    });
    return row;
  }

  function buildCard(channel, canDrag) {
    const card = document.createElement("article");
    card.className = "channel-card";
    if (store.selectedIds.has(channel.channel_id)) card.classList.add("selected");

    const header = document.createElement("div");
    header.className = "channel-card-header";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = store.selectedIds.has(channel.channel_id);
    checkbox.setAttribute("aria-label", `选择 ${channel.folder_name}`);
    checkbox.addEventListener("change", () => toggleSelected(channel.channel_id, checkbox.checked));
    const title = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = channel.channel_title || channel.folder_name;
    const subtitle = document.createElement("span");
    subtitle.className = "subtle";
    subtitle.textContent = channel.youtube_channel_id || `@${channel.youtube_id}`;
    title.append(strong, subtitle);
    header.append(checkbox, title, badge(runtimeLabel(channel.runtime), runtimeTone(channel.runtime)));
    card.appendChild(header);

    const meta = document.createElement("div");
    meta.className = "badge-row";
    meta.append(
      badge(channel.vid_type, "neutral"),
      badge(channel.dl_type === "audio" ? "音频" : "视频", "accent"),
      channel.is_first ? badge("首次", "warning") : document.createTextNode(""),
      channel.enabled ? document.createTextNode("") : badge("停用", "neutral"),
    );
    card.appendChild(meta);

    const footer = document.createElement("div");
    footer.className = "channel-card-footer";
    const recent = document.createElement("span");
    recent.textContent = formatDate(channel.runtime?.last_finished_at);
    const edit = textButton("编辑", "edit", "secondary");
    edit.addEventListener("click", () => openDrawer("edit", channel));
    footer.append(recent, edit);
    if (canDrag) {
      const up = button("上移", "up");
      const down = button("下移", "down");
      up.addEventListener("click", () => move(channel.channel_id, -1));
      down.addEventListener("click", () => move(channel.channel_id, 1));
      footer.append(up, down);
    }
    card.appendChild(footer);
    return card;
  }

  function toggleSelected(channelId, selected) {
    if (selected) store.selectedIds.add(channelId);
    else store.selectedIds.delete(channelId);
    emit();
  }

  async function move(channelId, delta) {
    const ids = store.channels.map((channel) => channel.channel_id);
    const index = ids.indexOf(channelId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    await persistOrder(ids);
  }

  async function moveRelative(sourceId, targetId) {
    const ids = store.channels.map((channel) => channel.channel_id);
    const source = ids.indexOf(sourceId);
    const target = ids.indexOf(targetId);
    if (source < 0 || target < 0 || source === target) return;
    ids.splice(source, 1);
    ids.splice(target, 0, sourceId);
    await persistOrder(ids);
  }

  async function persistOrder(ids) {
    try {
      const payload = await api("/channels/order", {
        method: "PUT",
        body: JSON.stringify({ channel_ids: ids }),
      });
      setChannels(payload);
    } catch (error) {
      toast(error.message, false);
      await refresh();
    }
  }

  function openDrawer(mode, channel = null) {
    drawerMode = mode;
    drawerChannelId = channel?.channel_id || null;
    drawerMetadata = channel
      ? {
          youtube_channel_id: channel.youtube_channel_id,
          channel_title: channel.channel_title,
          resolved_at: channel.resolved_at,
        }
      : {};
    dirty = false;
    refs.form.reset();
    refs.formError.hidden = true;
    refs.resolveStatus.textContent = "";
    refs.deleteButton.hidden = mode !== "edit";
    refs.copyButton.hidden = mode !== "edit";
    refs.testButton.hidden = mode !== "edit";
    refs.drawerTitle.textContent = mode === "add" ? "添加频道" : "频道详情";

    if (channel) {
      setFormValue("folder_name", channel.folder_name);
      setFormValue("youtube_id", channel.source_url || channel.youtube_channel_id || `@${channel.youtube_id}`);
      setFormValue("vid_type", channel.vid_type);
      setFormValue("dl_type", channel.dl_type);
      setFormChecked("enabled", channel.enabled);
      setFormChecked("is_first", channel.is_first);
      setFormChecked("filter_enabled", channel.filter_enabled);
      setFormValue("title_filter", channel.title_filter);
      refs.resolveStatus.textContent = channel.channel_title
        ? `${channel.channel_title}${channel.resolved_at ? ` · 验证于 ${formatDate(channel.resolved_at)}` : ""}`
        : "尚未联网验证频道";
    } else {
      setFormValue("vid_type", "videos");
      setFormValue("dl_type", "audio");
      setFormChecked("enabled", true);
      setFormChecked("is_first", true);
      setFormChecked("filter_enabled", false);
      setFormValue("title_filter", "");
    }
    updatePreviews();
    refs.drawer.showModal();
    document.querySelector("#channel-folder").focus();
  }

  function formValue(name) {
    return refs.form.elements.namedItem(name).value.trim();
  }

  function formChecked(name) {
    return refs.form.elements.namedItem(name).checked;
  }

  function setFormValue(name, value) {
    refs.form.elements.namedItem(name).value = value ?? "";
  }

  function setFormChecked(name, value) {
    refs.form.elements.namedItem(name).checked = Boolean(value);
  }

  function bodyFromForm() {
    return {
      folder_name: formValue("folder_name"),
      youtube_id: formValue("youtube_id"),
      vid_type: formValue("vid_type"),
      dl_type: formValue("dl_type"),
      enabled: formChecked("enabled"),
      is_first: formChecked("is_first"),
      filter_enabled: formChecked("filter_enabled"),
      title_filter: formValue("title_filter"),
      ...Object.fromEntries(
        Object.entries(drawerMetadata).filter(([, value]) => value),
      ),
    };
  }

  function updatePreviews() {
    const folder = formValue("folder_name") || "频道目录";
    const source = formValue("youtube_id") || "channel";
    const vidType = formValue("vid_type") || "videos";
    refs.outputPreview.textContent = `/downloads/${folder}/...`;
    refs.urlPreview.textContent = source.startsWith("http")
      ? `${source.replace(/\/$/, "")}/${vidType}`
      : `https://www.youtube.com/${source.startsWith("UC") ? "channel" : "@"}${source.replace(/^@/, "")}/${vidType}`;
    const conflictCount = store.channelSummary.folder_conflicts?.[folder] || 0;
    refs.folderWarning.hidden = conflictCount < 2;
    refs.folderWarning.textContent =
      conflictCount > 1 ? `有 ${conflictCount} 个频道共享此目录，文件可能混放。` : "";
  }

  async function saveChannel() {
    refs.formError.hidden = true;
    const body = bodyFromForm();
    if (!body.folder_name || !body.youtube_id) {
      refs.formError.textContent = "文件夹名称和 YouTube 频道不能为空";
      refs.formError.hidden = false;
      return;
    }
    refs.saveButton.disabled = true;
    try {
      const payload =
        drawerMode === "add"
          ? await api("/channels", { method: "POST", body: JSON.stringify(body) })
          : await api(`/channels/${drawerChannelId}`, { method: "PATCH", body: JSON.stringify(body) });
      setChannels(payload);
      dirty = false;
      refs.drawer.close();
      toast(drawerMode === "add" ? "频道已添加" : "频道已保存", true);
    } catch (error) {
      refs.formError.textContent = error.message;
      refs.formError.hidden = false;
    } finally {
      refs.saveButton.disabled = false;
    }
  }

  async function resolveChannel() {
    refs.resolveButton.disabled = true;
    refs.resolveStatus.textContent = "正在联网验证...";
    try {
      const result =
        drawerMode === "add"
          ? await api("/channels/resolve", {
              method: "POST",
              body: JSON.stringify(bodyFromForm()),
            })
          : await api(`/channels/${drawerChannelId}/resolve`, { method: "POST" });
      if (!result.ok) throw new Error(result.message || "验证失败");
      refs.resolveStatus.textContent = `${
        result.channel?.channel_title || result.channel_title || "已解析"
      } · ${result.youtube_channel_id}`;
      if (drawerMode === "add") {
        drawerMetadata = {
          youtube_channel_id: result.youtube_channel_id,
          channel_title: result.channel_title,
          resolved_at: new Date().toISOString(),
        };
        setFormValue("youtube_id", result.youtube_channel_id);
        dirty = true;
        updatePreviews();
      } else {
        await refresh();
      }
      toast("频道已验证并更新", true);
    } catch (error) {
      refs.resolveStatus.textContent = error.message;
    } finally {
      refs.resolveButton.disabled = false;
    }
  }

  async function testChannel(channel = null) {
    const id = channel?.channel_id || drawerChannelId;
    if (!id) return;
    refs.testButton.disabled = true;
    refs.resolveStatus.textContent = "正在执行 Dry-run 检测...";
    try {
      const result = await api(`/channels/${id}/test`, { method: "POST" });
      refs.resolveStatus.textContent = result.message || (result.ok ? "检测成功" : "检测失败");
      toast(result.ok ? "频道检测成功" : "频道检测失败", Boolean(result.ok));
      await refresh();
    } catch (error) {
      refs.resolveStatus.textContent = error.message;
      toast(error.message, false);
    } finally {
      refs.testButton.disabled = false;
    }
  }

  async function deleteChannel(channel = null) {
    const target =
      channel || store.channels.find((item) => item.channel_id === drawerChannelId);
    if (!target) return;
    const ok = await confirmAction(
      "删除频道",
      `确认删除“${target.channel_title || target.folder_name}”？下载文件会保留，仅删除配置和运行状态。`,
    );
    if (!ok) return;
    try {
      const payload = await api(`/channels/${target.channel_id}`, { method: "DELETE" });
      setChannels(payload);
      dirty = false;
      if (refs.drawer.open) refs.drawer.close();
      toast("频道已删除，下载文件未改动", true);
    } catch (error) {
      toast(error.message, false);
    }
  }

  async function copyChannel() {
    const source = store.channels.find((item) => item.channel_id === drawerChannelId);
    if (!source) return;
    const body = {
      folder_name: `${source.folder_name}-copy`,
      youtube_id: source.source_url || source.youtube_channel_id || `@${source.youtube_id}`,
      vid_type: source.vid_type,
      dl_type: source.dl_type,
      enabled: false,
      is_first: source.is_first,
      filter_enabled: source.filter_enabled,
      title_filter: source.title_filter,
    };
    try {
      const payload = await api("/channels", { method: "POST", body: JSON.stringify(body) });
      setChannels(payload);
      dirty = false;
      refs.drawer.close();
      toast("副本已创建并默认停用", true);
    } catch (error) {
      toast(error.message, false);
    }
  }

  async function applyBulk() {
    const action = refs.batchAction.value;
    const body = { action, channel_ids: [...store.selectedIds] };
    if (action === "set_vid_type") body.vid_type = refs.batchValue.value;
    if (action === "set_dl_type") body.dl_type = refs.batchValue.value;
    if (action === "set_is_first") body.is_first = refs.batchValue.value === "true";
    if (action === "set_filter") {
      body.filter_enabled = refs.batchValue.value === "enable";
      body.title_filter =
        refs.batchFilterText.value.trim() || ASMR_FILTER;
    }
    if (action === "delete") {
      const ok = await confirmAction(
        "批量删除频道",
        `确认删除选中的 ${store.selectedIds.size} 个频道？下载文件会保留。`,
      );
      if (!ok) return;
    }
    try {
      const result = await api("/channels/bulk-actions", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (result.items) {
        setChannels(result);
        store.selectedIds.clear();
        emit();
      } else if (result.ok) {
        store.selectedIds.clear();
        emit();
      }
      toast(action === "run" ? "已开始运行选中频道" : "批量操作完成", true);
    } catch (error) {
      toast(error.message, false);
    }
  }

  function updateBulkValue() {
    const action = refs.batchAction.value;
    const values = {
      set_vid_type: [["videos", "视频"], ["streams", "直播"], ["shorts", "Shorts"]],
      set_dl_type: [["audio", "音频"], ["video", "视频"]],
      set_is_first: [["true", "启用首次"], ["false", "取消首次"]],
      set_filter: [["enable", "启用 ASMR 过滤"], ["disable", "关闭过滤"]],
    };
    refs.batchValue.replaceChildren();
    for (const [value, label] of values[action] || []) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      refs.batchValue.appendChild(option);
    }
    refs.batchValue.hidden = !values[action];
    refs.batchFilterText.hidden = action !== "set_filter";
    if (action === "set_filter" && !refs.batchFilterText.value) {
      refs.batchFilterText.value = ASMR_FILTER;
    }
  }

  refs.search.addEventListener("input", () => {
    store.filters.query = refs.search.value;
    saveFilters();
    render();
  });
  for (const [element, field] of [
    [refs.enabled, "enabled"],
    [refs.vidType, "vidType"],
    [refs.dlType, "dlType"],
    [refs.runtime, "runtime"],
    [refs.sort, "sort"],
  ]) {
    element.addEventListener("change", () => {
      store.filters[field] = element.value;
      saveFilters();
      render();
    });
  }
  document.querySelector("#channel-add").addEventListener("click", () => openDrawer("add"));
  document.querySelector("#channel-refresh").addEventListener("click", refresh);
  document.querySelector("#select-all-channels").addEventListener("change", (event) => {
    const visible = filteredChannels();
    if (event.target.checked) visible.forEach((channel) => store.selectedIds.add(channel.channel_id));
    else visible.forEach((channel) => store.selectedIds.delete(channel.channel_id));
    emit();
  });
  document.querySelector("#clear-channel-selection").addEventListener("click", () => {
    store.selectedIds.clear();
    emit();
  });
  refs.batchAction.addEventListener("change", updateBulkValue);
  refs.batchApply.addEventListener("click", applyBulk);
  refs.form.addEventListener("input", () => {
    dirty = true;
    refs.formError.hidden = true;
    updatePreviews();
  });
  refs.form.addEventListener("change", () => {
    dirty = true;
    updatePreviews();
  });
  refs.form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveChannel();
  });
  refs.resolveButton.addEventListener("click", resolveChannel);
  refs.testButton.addEventListener("click", () => testChannel());
  refs.deleteButton.addEventListener("click", () => deleteChannel());
  refs.copyButton.addEventListener("click", copyChannel);
  refs.filterPreset.addEventListener("click", () => {
    setFormChecked("filter_enabled", true);
    setFormValue("title_filter", ASMR_FILTER);
    dirty = true;
  });
  document.querySelector("#channel-drawer-close").addEventListener("click", async () => {
    if (dirty && !(await confirmAction("关闭频道详情", "存在未保存修改，确认关闭？"))) return;
    refs.drawer.close();
  });
  refs.drawer.addEventListener("cancel", (event) => {
    if (dirty) {
      event.preventDefault();
      confirmAction("关闭频道详情", "存在未保存修改，确认关闭？").then((ok) => {
        if (!ok) return;
        dirty = false;
        refs.drawer.close();
      });
    }
  });

  updateBulkValue();
  subscribe(() => render());
  return { refresh, render, updateRuntime: () => render() };
}
