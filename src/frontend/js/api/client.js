const connectionCopy = {
  checking: "正在检查本地账本",
  connected: "本地账本已连接",
  disconnected: "本地账本连接中断",
};

function setConnectionState(state) {
  const indicator = document.querySelector("[data-connection-status]");
  if (!indicator) return;
  indicator.dataset.state = state;
  indicator.title = state === "disconnected"
    ? "无法连接本地服务；点击打开健康检查"
    : "点击打开本地服务健康检查";
  const label = indicator.querySelector("[data-live-label]");
  if (label) label.textContent = connectionCopy[state];
}

export async function request(url, options = {}, includeEnvelope = false) {
  const response = await fetch(url, options).catch((error) => {
    if (error.name === "AbortError") throw error;
    setConnectionState("disconnected");
    throw new Error("无法连接本地服务");
  });
  setConnectionState("connected");
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const validationDetails = payload?.body?.code === "VALIDATION_ERROR"
      && Array.isArray(payload?.body?.details)
      ? payload.body.details.map((item) => item?.msg).filter(Boolean)
      : [];
    const detail = validationDetails.length
      ? validationDetails
      : payload?.message || payload?.detail;
    const text = Array.isArray(detail)
      ? detail.map((item) => typeof item === "string" ? item : item?.msg).filter(Boolean).join("；")
      : detail || `请求失败（${response.status}）`;
    const error = new Error(text);
    error.code = payload?.body?.code;
    error.status = response.status;
    throw error;
  }
  const isEnvelope = payload && Object.hasOwn(payload, "body")
    && (payload.status === response.status || payload.status === "success");
  return isEnvelope && !includeEnvelope
    ? payload.body
    : payload;
}

export async function checkConnection() {
  setConnectionState("checking");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5_000);
  try {
    await request("/api/health", { cache: "no-store", signal: controller.signal });
    return true;
  } catch (_error) {
    setConnectionState("disconnected");
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
}

export const jsonRequest = (url, method, body, includeEnvelope = false) => request(url, {
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}, includeEnvelope);
