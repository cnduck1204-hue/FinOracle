import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export function normalizeApiPrefix(prefix: string): string {
  const normalized = prefix.replace(/\/$/, '')
  if (!/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(normalized)) {
    throw new Error('API prefix must be a non-root same-origin path, for example /api/v1 or /v2.')
  }
  return normalized
}

export function createNginxConfig(prefix: string, template: string): string {
  return template.replace('__API_PROXY_PREFIX__', `${normalizeApiPrefix(prefix)}/`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const template = readFileSync(new URL('../nginx.conf.template', import.meta.url), 'utf8')
  writeFileSync(
    'nginx.generated.conf',
    createNginxConfig(process.env.VITE_API_BASE_URL || '/api/v1', template),
  )
}
