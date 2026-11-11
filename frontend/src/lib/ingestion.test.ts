// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { AuthClient } from './api'
import {
  DEFAULT_PARSE_OPTIONS,
  EMPTY_METADATA,
  VALIDITY_LABELS,
  articlePath,
  buildImportForm,
  metadataForm,
  metadataPatch,
  pageLabel,
  pipelineSteps,
  previewKind,
  validateMetadata,
  validateParseOptions,
  validateUpload,
  versionPermissions,
} from './ingestion'
import type { DocumentVersion, ImportJob } from './types'

const job = (overrides: Partial<ImportJob> = {}) =>
  ({ id: 'job', kind: 'parse', status: 'succeeded', ...overrides }) as ImportJob
const version = (overrides: Partial<DocumentVersion> = {}) =>
  ({
    id: 'version',
    status: 'parsed',
    has_pdf: false,
    file_type: 'txt',
    latest_job: job(),
    document_number: null,
    version_note: null,
    published_date: null,
    effective_date: null,
    validity_status: 'unknown',
    validity_checked_at: null,
    source_url: null,
    download_url: null,
    expected_articles: null,
    ...overrides,
  }) as DocumentVersion

describe('version state rules', () => {
  it('never presents an unverified version as currently effective', () => {
    expect(VALIDITY_LABELS.unknown).toBe('时效待核验')
  })

  it('offers each action only in the states the backend accepts', () => {
    const ready = versionPermissions(version({ status: 'ready' }))
    expect(ready).toMatchObject({ publish: true, approve: false, reparse: true, remove: true })
    const published = versionPermissions(version({ status: 'published' }))
    expect(published).toMatchObject({
      withdraw: true,
      publish: false,
      reparse: false,
      remove: false,
    })
    expect(versionPermissions(version({ status: 'archived' }))).toMatchObject({
      approve: true,
      remove: false,
    })
    expect(
      versionPermissions(version({ status: 'failed', latest_job: job({ status: 'failed' }) })),
    ).toMatchObject({ retry: true, reparse: true, remove: true })
  })

  it('blocks mutations while a job is still active', () => {
    const can = versionPermissions(
      version({ status: 'ready', latest_job: job({ status: 'queued' }) }),
    )
    expect(can).toMatchObject({ publish: false, reparse: false, remove: false })
  })

  it('places the failure at the stage that failed', () => {
    const failedIndex = pipelineSteps(
      version({ status: 'failed', latest_job: job({ kind: 'index', status: 'failed' }) }),
    )
    expect(failedIndex[2]).toEqual({ label: '向量化', state: 'failed' })
    expect(failedIndex[1].state).toBe('done')
    const failedParse = pipelineSteps(version({ status: 'failed' }))
    expect(failedParse[1].state).toBe('failed')
  })

  it('marks a published version complete and an archived one as archived', () => {
    expect(
      pipelineSteps(version({ status: 'published' })).every((step) => step.state === 'done'),
    ).toBe(true)
    expect(pipelineSteps(version({ status: 'archived' }))[4]).toEqual({
      label: '已归档',
      state: 'done',
    })
  })

  it('previews PDFs and browser-readable images only', () => {
    expect(previewKind(version({ has_pdf: true, file_type: 'doc' }))).toBe('pdf')
    expect(previewKind(version({ file_type: 'png' }))).toBe('image')
    expect(previewKind(version({ file_type: 'tiff' }))).toBeNull()
    expect(previewKind(version({ file_type: 'docx' }))).toBeNull()
  })
})

describe('import form', () => {
  const file = new File(['第一条'], '办法.txt', { type: 'text/plain' })

  it('validates type, emptiness and size before uploading', () => {
    expect(validateUpload({ name: '办法.PDF', size: 10 })).toBeNull()
    expect(validateUpload({ name: '表格.xlsx', size: 10 })).toContain('不支持')
    expect(validateUpload({ name: '空.txt', size: 0 })).toContain('空')
    expect(validateUpload({ name: '大.pdf', size: 50 * 1024 * 1024 + 1 })).toContain('50 MB')
  })

  it('sends a title for new documents and omits blank metadata', () => {
    const form = buildImportForm({
      file,
      title: ' 新制度 ',
      issuer: '  ',
      metadata: { ...EMPTY_METADATA, document_number: '银发〔2026〕1号' },
      options: DEFAULT_PARSE_OPTIONS,
    })
    expect(form.get('title')).toBe('新制度')
    expect(form.has('issuer')).toBe(false)
    expect(form.get('document_number')).toBe('银发〔2026〕1号')
    expect(form.has('effective_date')).toBe(false)
    expect(form.get('validity_status')).toBe('unknown')
    expect(form.get('max_chunk_chars')).toBe('800')
  })

  it('does not resend document details when adding a version', () => {
    const form = buildImportForm({
      file,
      documentId: 'doc-1',
      title: '不应发送',
      issuer: '不应发送',
      metadata: EMPTY_METADATA,
      options: DEFAULT_PARSE_OPTIONS,
    })
    expect(form.get('document_id')).toBe('doc-1')
    expect(form.has('title')).toBe(false)
    expect(form.has('issuer')).toBe(false)
  })

  it('applies the backend parse option limits', () => {
    expect(validateParseOptions(DEFAULT_PARSE_OPTIONS)).toBeNull()
    expect(validateParseOptions({ ...DEFAULT_PARSE_OPTIONS, max_chunk_chars: 50 })).toContain('100')
    expect(
      validateParseOptions({ ...DEFAULT_PARSE_OPTIONS, max_chunk_chars: 200, chunk_overlap: 200 }),
    ).toContain('小于')
  })

  it('rejects malformed metadata', () => {
    expect(validateMetadata({ ...EMPTY_METADATA, source_url: 'ftp://x' })).toContain('http')
    expect(validateMetadata({ ...EMPTY_METADATA, expected_articles: '0' })).toContain('正整数')
    expect(
      validateMetadata({
        ...EMPTY_METADATA,
        source_url: 'https://example.com/a',
        expected_articles: '12',
      }),
    ).toBeNull()
  })

  it('patches only changed metadata and clears emptied fields', () => {
    const original = version({ document_number: '旧文号', expected_articles: 8 })
    const form = {
      ...metadataForm(original),
      document_number: '',
      version_note: '第二次修订',
      expected_articles: '9',
    }
    expect(metadataPatch(original, form)).toEqual({
      document_number: null,
      version_note: '第二次修订',
      expected_articles: 9,
    })
    expect(metadataPatch(original, metadataForm(original))).toEqual({})
  })
})

describe('citation labels', () => {
  it('formats page ranges and missing pages', () => {
    expect(pageLabel(3, 3)).toBe('第 3 页')
    expect(pageLabel(3, 5)).toBe('第 3–5 页')
    expect(pageLabel(null, null)).toBe('无页码')
  })

  it('prefers the legal path and falls back to the heading', () => {
    const base = { chapter: '', section: '', article: '', heading: '' }
    expect(articlePath({ ...base, chapter: '第一章', article: '第二条' })).toBe('第一章 · 第二条')
    expect(articlePath({ ...base, heading: '报送流程' })).toBe('报送流程')
    expect(articlePath(base)).toBe('无条号内容')
  })
})

describe('ingestion client', () => {
  const user = {
    id: 'admin-id',
    username: 'admin',
    email: 'admin@example.com',
    role: 'admin',
    status: 'active',
    last_login_at: null,
    created_at: '2026-11-10T00:00:00Z',
    updated_at: '2026-11-10T00:00:00Z',
  }
  const pair = {
    access_token: 'access',
    refresh_token: 'refresh',
    token_type: 'bearer',
    expires_in: 1800,
    user,
  }
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  type Handler = (url: URL, init: RequestInit) => Response

  async function signedIn(handler: Handler) {
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), 'http://local')
      if (url.pathname.endsWith('/auth/login')) return json(pair)
      return handler(url, init ?? {})
    })
    const client = new AuthClient({ fetcher: fetcher as typeof fetch })
    await client.login('admin', 'secret123')
    return client
  }
  const header = (init: RequestInit, name: string) => (init.headers as Record<string, string>)[name]

  it('keeps the question out of the URL when searching', async () => {
    const client = await signedIn((url, init) => {
      expect(url.pathname).toBe('/api/v1/search')
      expect(url.search).toBe('')
      expect(init.method).toBe('POST')
      expect(JSON.parse(String(init.body))).toEqual({ query: '理财', top_k: 5 })
      return json({ query: '理财', results: [] })
    })
    await expect(client.search({ query: '理财', top_k: 5 })).resolves.toEqual({
      query: '理财',
      results: [],
    })
  })

  it('lets the browser set the multipart boundary on uploads', async () => {
    const form = new FormData()
    const client = await signedIn((url, init) => {
      expect(url.pathname).toBe('/api/v1/admin/imports')
      expect(init.body).toBe(form)
      expect(header(init, 'Content-Type')).toBeUndefined()
      expect(header(init, 'Authorization')).toBe('Bearer access')
      return json({ id: 'job-1' }, 202)
    })
    await expect(client.createImport(form)).resolves.toMatchObject({ id: 'job-1' })
  })

  it('still sends JSON bodies as JSON', async () => {
    const client = await signedIn((_url, init) => {
      expect(header(init, 'Content-Type')).toBe('application/json')
      return json({ id: 'version' })
    })
    await client.updateVersion('version', { version_note: '修订' })
  })

  it('encodes document and job filters', async () => {
    const seen: string[] = []
    const client = await signedIn((url) => {
      seen.push(url.pathname + url.search)
      return json({ items: [], total: 0, page: 1, page_size: 20 })
    })
    await client.listDocuments({ q: ' 办法 ', published: false })
    await client.listJobs({ status: 'failed', kind: 'index', page: 2 })
    expect(seen[0]).toBe(
      `/api/v1/admin/documents?page=1&page_size=20&q=${encodeURIComponent('办法')}&published=false`,
    )
    expect(seen[1]).toBe('/api/v1/admin/imports?page=2&page_size=20&status=failed&kind=index')
  })

  it('rejects malformed list responses', async () => {
    const client = await signedIn(() => json({ items: 'nope' }))
    await expect(client.listDocuments()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' })
  })

  it('sends the bearer token when reading files and surfaces API errors', async () => {
    let mode: 'ok' | 'missing' = 'ok'
    const client = await signedIn((url, init) => {
      expect(url.search).toBe('?variant=pdf')
      expect(header(init, 'Authorization')).toBe('Bearer access')
      return mode === 'ok'
        ? new Response('%PDF', { headers: { 'Content-Type': 'application/pdf' } })
        : json({ code: 'FILE_NOT_AVAILABLE', message: '没有 PDF' }, 404)
    })
    const blob = await client.fetchVersionFile('version', 'pdf')
    expect(blob.type).toBe('application/pdf')
    mode = 'missing'
    await expect(client.fetchVersionFile('version', 'pdf')).rejects.toMatchObject({
      code: 'FILE_NOT_AVAILABLE',
      status: 404,
    })
  })
})
