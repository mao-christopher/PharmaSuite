export async function errorMessage(res) {
  try {
    const body = await res.json();
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => String(d.msg).replace(/^Value error, /, '')).join('; ');
    }
    return body.detail || res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function request(url, { method = 'GET', body } = {}) {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}
