export async function request(url, options = {}) {
  const response = await fetch(url, options).catch((error) => {
    if (error.name === "AbortError") throw error;
    throw new Error("无法连接本地服务");
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.message || payload?.detail;
    const text = Array.isArray(detail)
      ? detail.map((item) => item.msg).join("；")
      : detail || `请求失败（${response.status}）`;
    throw new Error(text);
  }
  const isEnvelope = payload && Object.hasOwn(payload, "body")
    && (payload.status === response.status || payload.status === "success");
  return isEnvelope
    ? payload.body
    : payload;
}

export const jsonRequest = (url, method, body) => request(url, {
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
