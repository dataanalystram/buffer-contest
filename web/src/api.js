// Thin client for the HuhQueue REST API. The UI uses exactly the same API as agents/CLI (headless).
function cookie(name) {
  return document.cookie.split('; ').find((c) => c.startsWith(name + '='))?.split('=')[1] || ''
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : detail?.message || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = cookie('huhq_csrf')
  let payload
  if (form) payload = form
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const r = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' })
  const isJson = (r.headers.get('content-type') || '').includes('json')
  const data = isJson ? await r.json() : await r.text()
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith('/api/auth')) window.dispatchEvent(new Event('huhq:logout'))
    throw new ApiError(r.status, data?.detail ?? data)
  }
  return data
}

export const get = (p) => api(p)
export const post = (p, body = {}) => api(p, { method: 'POST', body })
export const patch = (p, body = {}) => api(p, { method: 'PATCH', body })
export const put = (p, body = {}) => api(p, { method: 'PUT', body })
export const del = (p) => api(p, { method: 'DELETE' })

export async function upload(files, fields = {}) {
  const form = new FormData()
  for (const f of files) form.append('files', f, f.webkitRelativePath || f.name)
  for (const [k, v] of Object.entries(fields)) form.append(k, v)
  return api('/api/media', { method: 'POST', form })
}
