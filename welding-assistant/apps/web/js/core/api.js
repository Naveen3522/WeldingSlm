// One small place for JSON calls to the server: never throws, always {ok, data}.

export async function call(method, url, data) {
  try {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: data ? JSON.stringify(data) : undefined,
    });
    return { ok: response.ok, data: await response.json().catch(() => ({})) };
  } catch (error) {
    return { ok: false, data: { error: "Server not reachable." } };
  }
}
