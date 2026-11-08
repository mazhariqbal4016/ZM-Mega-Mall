export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'https://zm-mega-mall-production.up.railway.app'

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiFetch(path, { token, ...options } = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('Accept', 'application/json')
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload?.detail ? payload.detail : 'The backend request failed.'
    throw new ApiError(response.status, detail)
  }
  return payload
}

export function jsonBody(value) {
  return JSON.stringify(value)
}
