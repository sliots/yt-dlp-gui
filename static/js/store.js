export const store = {
  channels: [],
  channelSummary: {},
  status: {},
  config: null,
  selectedIds: new Set(),
  filters: {
    query: "",
    enabled: "all",
    vidType: "all",
    dlType: "all",
    runtime: "all",
    sort: "manual",
  },
};

const listeners = new Set();

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emit() {
  listeners.forEach((listener) => listener(store));
}

export function setChannels(payload) {
  store.channels = payload.items || [];
  store.channelSummary = payload.summary || {};
  const ids = new Set(store.channels.map((channel) => channel.channel_id));
  store.selectedIds = new Set([...store.selectedIds].filter((id) => ids.has(id)));
  emit();
}

export function setStatus(status) {
  store.status = status || {};
  emit();
}

export function updateRuntime(runtime) {
  const channel = store.channels.find((item) => item.channel_id === runtime.channel_id);
  if (!channel) return;
  channel.runtime = { ...(channel.runtime || {}), ...runtime };
  emit();
}
