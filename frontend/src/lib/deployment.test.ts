// @vitest-environment node
import { expect, it } from 'vitest'
import { createNginxConfig, normalizeApiPrefix } from '../../scripts/nginx-config'

it('builds the same API route for default and custom prefixes', () => {
  const template = 'location ^~ __API_PROXY_PREFIX__ { proxy_pass http://api:8000; }'
  expect(createNginxConfig('/api/v1', template)).toBe(
    'location ^~ /api/v1/ { proxy_pass http://api:8000; }',
  )
  expect(createNginxConfig('/v2/', template)).toBe(
    'location ^~ /v2/ { proxy_pass http://api:8000; }',
  )
  expect(normalizeApiPrefix('/financial/v1')).toBe('/financial/v1')
})

it('applies the prefix to the nested administrator upload route', () => {
  const template = 'location ^~ __API_PROXY_PREFIX__ { location ^~ __API_PROXY_PREFIX__admin/ { } }'
  expect(createNginxConfig('/api/v1', template)).toBe(
    'location ^~ /api/v1/ { location ^~ /api/v1/admin/ { } }',
  )
})

it('rejects root, remote URLs and configuration injection as API prefixes', () => {
  for (const prefix of [
    '/',
    '',
    'https://example.com',
    '/api; return 200;',
    '/api\nmalicious',
    '//api',
  ]) {
    expect(() => normalizeApiPrefix(prefix)).toThrow()
  }
})
