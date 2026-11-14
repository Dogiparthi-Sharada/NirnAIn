/**
 * Authenticated fetch helpers for the NirnAIn API and the typed RFC 7807 error they throw.
 *
 * Purpose: Every backend call needs the caller's Supabase token, one silent token refresh on 401
 * and problem+json errors turned into readable messages and per-field errors, so screens never
 * parse responses themselves.
 * How it fits: Used by every query hook in `lib/` (`apiFetch`, `apiFetchOrNull`), by plan exports
 * (`apiDownload`) and by screens for messages (`errorMessage`); calls `supabase.auth` and `fetch`;
 * layer: hook/lib.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { getEnv } from './env'
import { supabase } from './supabase'

/** Typed view of an RFC 7807 `application/problem+json` error (or a transport failure). */
export class ApiError extends Error {
  readonly status: number
  readonly title: string
  readonly detail: string
  /** Field-level validation messages keyed by camelCase field name, when the server provides them. */
  readonly fieldErrors: Record<string, string>
  /** Problem-detail extension members (anything beyond type/title/status/detail/instance/errors). */
  readonly extensions: Record<string, unknown>

  /** Builds the error from the problem's status, title, detail, field errors and extension members. */
  constructor(
    status: number,
    title: string,
    detail: string,
    fieldErrors: Record<string, string> = {},
    extensions: Record<string, unknown> = {},
  ) {
    super(detail || title)
    this.name = 'ApiError'
    this.status = status
    this.title = title
    this.detail = detail
    this.fieldErrors = fieldErrors
    this.extensions = extensions
  }
}

/** True when `error` is an `ApiError` (narrows the type). */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export interface ApiRequestInit extends Omit<RequestInit, 'body'> {
  /** Serialised as JSON with the matching Content-Type. */
  json?: unknown
  body?: BodyInit | null
}

const DEFAULT_TITLES: Record<number, string> = {
  400: 'Bad request',
  401: 'Not signed in',
  403: 'Access denied',
  404: 'Not found',
  409: 'Conflict',
  422: 'Validation failed',
  429: 'Too many requests',
  500: 'Server error',
  502: 'Service unavailable',
  503: 'Service unavailable',
}

const PROBLEM_KEYS = new Set(['type', 'title', 'status', 'detail', 'instance', 'errors'])

/** True for a plain JSON object (not null, not an array). */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Collects field errors from either an RFC 7807 `errors` extension
 * (`[{ field | loc, message | msg }]`) or FastAPI's default `detail: [{ loc, msg }]`.
 */
function extractFieldErrors(list: unknown): Record<string, string> {
  const fieldErrors: Record<string, string> = {}
  if (!Array.isArray(list)) return fieldErrors
  for (const entry of list) {
    if (!isRecord(entry)) continue
    const message = typeof entry.message === 'string' ? entry.message : typeof entry.msg === 'string' ? entry.msg : null
    let field: string | null = typeof entry.field === 'string' ? entry.field : null
    if (!field && Array.isArray(entry.loc)) {
      const last = entry.loc[entry.loc.length - 1]
      if (typeof last === 'string' && last !== 'body') field = last
    }
    if (field && message && !fieldErrors[field]) fieldErrors[field] = message
  }
  return fieldErrors
}

/** Reads a failed response's problem+json body into an `ApiError`, falling back to a title per status. */
async function toApiError(response: Response): Promise<ApiError> {
  const fallbackTitle = DEFAULT_TITLES[response.status] ?? `Request failed (${response.status})`
  let payload: unknown = null
  try {
    const text = await response.text()
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = null
  }
  if (!isRecord(payload)) return new ApiError(response.status, fallbackTitle, '')

  const title = typeof payload.title === 'string' && payload.title ? payload.title : fallbackTitle
  let detail = ''
  let fieldErrors = extractFieldErrors(payload.errors)
  if (typeof payload.detail === 'string') {
    detail = payload.detail
  } else if (Array.isArray(payload.detail)) {
    fieldErrors = { ...extractFieldErrors(payload.detail), ...fieldErrors }
    detail = 'Some fields are invalid.'
  }
  const extensions = Object.fromEntries(Object.entries(payload).filter(([key]) => !PROBLEM_KEYS.has(key)))
  return new ApiError(response.status, title, detail, fieldErrors, extensions)
}

/** The current Supabase access token, or null when signed out. */
async function accessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

/** Sends one request with the auth and JSON headers; a transport failure becomes a status-0 `ApiError`. */
async function send(path: string, init: ApiRequestInit, token: string | null): Promise<Response> {
  const { json, headers: initHeaders, body, ...rest } = init
  const headers = new Headers(initHeaders)
  headers.set('Accept', 'application/json, application/problem+json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let requestBody: BodyInit | null | undefined = body
  if (json !== undefined) {
    headers.set('Content-Type', 'application/json')
    requestBody = JSON.stringify(json)
  }
  const url = `${getEnv().apiUrl}${path.startsWith('/') ? path : `/${path}`}`
  try {
    return await fetch(url, { ...rest, headers, body: requestBody })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Network error', "We couldn't reach the server. Check your connection and try again.")
  }
}

/**
 * Authenticated JSON request against `VITE_API_URL`.
 * On 401 it refreshes the Supabase session once and retries; if that fails the
 * user is signed out locally (the route guard then sends them to /login).
 */
export async function apiFetch<TResponse>(path: string, init: ApiRequestInit = {}): Promise<TResponse> {
  let response = await send(path, init, await accessToken())

  if (response.status === 401) {
    const { data, error } = await supabase.auth.refreshSession()
    if (!error && data.session) {
      response = await send(path, init, data.session.access_token)
    }
    if (response.status === 401) {
      await supabase.auth.signOut({ scope: 'local' })
    }
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as TResponse
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as TResponse
}

/** `apiFetch` for a resource that may not exist yet (e.g. a project's latest run): 404 resolves to `null`. */
export async function apiFetchOrNull<TResponse>(path: string, init: ApiRequestInit = {}): Promise<TResponse | null> {
  try {
    return await apiFetch<TResponse>(path, init)
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null
    throw error
  }
}

/** Authenticated file download: saves the response body under the server's filename (or `fallbackName`). */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  let response = await send(path, {}, await accessToken())
  if (response.status === 401) {
    const { data, error } = await supabase.auth.refreshSession()
    if (!error && data.session) response = await send(path, {}, data.session.access_token)
  }
  if (!response.ok) throw await toApiError(response)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="?([^";]+)"?/i.exec(disposition)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = match?.[1] ?? fallbackName
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Human-readable message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.detail || error.title
  if (error instanceof Error && error.message) return error.message
  return 'Something went wrong. Please try again.'
}
