import type {
  DocumentVersion,
  ImportJob,
  JobKind,
  JobStatus,
  ParseOptions,
  ParseStatus,
  ValidityStatus,
  VersionMetadata,
  VersionStatus,
} from './types'

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024
export const ACCEPTED_TYPES = [
  'pdf',
  'docx',
  'doc',
  'rtf',
  'odt',
  'txt',
  'md',
  'markdown',
  'png',
  'jpg',
  'jpeg',
  'tif',
  'tiff',
  'bmp',
  'webp',
] as const
const BROWSER_IMAGE_TYPES = new Set(['png', 'jpg', 'jpeg', 'bmp', 'webp'])

export const VERSION_STATUS_LABELS: Record<VersionStatus, string> = {
  uploaded: '排队解析',
  parsing: '解析中',
  parsed: '待批准',
  indexing: '向量化中',
  ready: '待发布',
  published: '已发布',
  archived: '已归档',
  failed: '处理失败',
}
export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}
export const JOB_KIND_LABELS: Record<JobKind, string> = {
  parse: '解析',
  index: '向量化',
  cleanup: '清理',
}
export const STAGE_LABELS: Record<string, string> = {
  downloading: '读取原件',
  converting: '格式转换',
  parsing: '解析文本',
  ocr: 'OCR 识别',
  chunking: '切分条文',
  saving: '保存结果',
  embedding: '生成向量',
  indexing: '写入索引',
}
// “未知”不能被读作现行有效，因此单独写明待核验。
export const VALIDITY_LABELS: Record<ValidityStatus, string> = {
  unknown: '时效待核验',
  effective: '现行有效',
  not_yet_effective: '尚未生效',
  amended: '已修订',
  repealed: '已废止',
}
export const PARSE_STATUS_LABELS: Record<ParseStatus, string> = {
  completed: '完整解析',
  needs_ocr: '含待 OCR 内容',
  empty: '没有文本',
}
export const OCR_OPTIONS = [
  { value: 'auto', label: '自动选择' },
  { value: 'rapidocr', label: 'RapidOCR' },
  { value: 'tesseract', label: 'Tesseract' },
  { value: 'none', label: '不识别图片' },
]
export const CHUNKER_OPTIONS = [
  { value: 'auto', label: '自动检测' },
  { value: 'article', label: '按“第X条”' },
  { value: 'item', label: '按“一、二、”' },
  { value: 'length', label: '按长度' },
]
export const DEFAULT_PARSE_OPTIONS: ParseOptions = {
  ocr: 'auto',
  chunker: 'auto',
  max_chunk_chars: 800,
  chunk_overlap: 100,
}

export type Tone = 'neutral' | 'active' | 'good' | 'warn' | 'bad'

export function versionTone(status: VersionStatus): Tone {
  if (status === 'failed') return 'bad'
  if (status === 'published') return 'good'
  if (status === 'parsed' || status === 'ready') return 'warn'
  if (status === 'archived') return 'neutral'
  return 'active'
}

export function jobTone(status: JobStatus): Tone {
  if (status === 'failed') return 'bad'
  if (status === 'succeeded') return 'good'
  if (status === 'cancelled') return 'neutral'
  return 'active'
}

export function validityTone(status: ValidityStatus): Tone {
  if (status === 'effective') return 'good'
  if (status === 'unknown') return 'warn'
  if (status === 'repealed') return 'bad'
  return 'neutral'
}

export function isVersionBusy(status: VersionStatus): boolean {
  return status === 'uploaded' || status === 'parsing' || status === 'indexing'
}

export function isJobActive(status: JobStatus): boolean {
  return status === 'queued' || status === 'running'
}

export interface VersionPermissions {
  approve: boolean
  publish: boolean
  withdraw: boolean
  reparse: boolean
  remove: boolean
  retry: boolean
}

/** 与后端状态规则一致；任务进行中时变更操作会被后端以 409 拒绝，这里提前隐藏。 */
export function versionPermissions(version: DocumentVersion): VersionPermissions {
  const { status, latest_job: job } = version
  const idle = !job || !isJobActive(job.status)
  return {
    approve: idle && (status === 'parsed' || status === 'archived'),
    publish: idle && status === 'ready',
    withdraw: status === 'published',
    reparse: idle && (status === 'parsed' || status === 'failed' || status === 'ready'),
    remove: idle && ['uploaded', 'parsed', 'failed', 'ready'].includes(status),
    retry: status === 'failed' && job?.status === 'failed',
  }
}

export function versionName(version: { version_note: string | null; created_at: string }): string {
  return version.version_note || `${formatDate(version.created_at)} 上传的版本`
}

export type StepState = 'done' | 'current' | 'failed' | 'pending'
export interface PipelineStep {
  label: string
  state: StepState
}

/** 上传、解析、向量化、发布是不同状态，这里把版本状态展开成可读的流程。 */
export function pipelineSteps(version: DocumentVersion): PipelineStep[] {
  const labels = ['上传原件', '解析切片', '向量化', '待发布', '已发布']
  const positions: Record<VersionStatus, number> = {
    uploaded: 1,
    parsing: 1,
    parsed: 2,
    indexing: 2,
    ready: 3,
    published: 4,
    archived: 4,
    failed: version.latest_job?.kind === 'index' ? 2 : 1,
  }
  const position = positions[version.status]
  return labels.map((label, index) => {
    if (index < position) return { label, state: 'done' }
    if (index > position) return { label, state: 'pending' }
    if (version.status === 'archived') return { label: '已归档', state: 'done' }
    if (version.status === 'published') return { label, state: 'done' }
    return { label, state: version.status === 'failed' ? 'failed' : 'current' }
  })
}

export function jobPercent(job: Pick<ImportJob, 'progress_current' | 'progress_total'>) {
  if (!job.progress_total) return null
  return Math.min(100, Math.round((job.progress_current / job.progress_total) * 100))
}

export function jobProgressText(job: ImportJob): string {
  const stage = job.stage ? (STAGE_LABELS[job.stage] ?? job.stage) : JOB_STATUS_LABELS[job.status]
  return job.progress_total ? `${stage} ${job.progress_current} / ${job.progress_total}` : stage
}

export function pageLabel(start: number | null, end: number | null): string {
  if (start === null) return '无页码'
  return end !== null && end !== start ? `第 ${start}–${end} 页` : `第 ${start} 页`
}

export function articlePath(chunk: {
  chapter: string
  section: string
  article: string
  heading: string
}): string {
  const path = [chunk.chapter, chunk.section, chunk.article].filter(Boolean).join(' · ')
  return path || chunk.heading || '无条号内容'
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function formatDate(value: string | null): string {
  if (!value) return '未填写'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未填写'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

export function formatDateTime(value: string | null): string {
  if (!value) return '尚无记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '尚无记录'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function fileExtension(name: string): string {
  const index = name.lastIndexOf('.')
  return index < 0 ? '' : name.slice(index + 1).toLowerCase()
}

export function validateUpload(file: Pick<File, 'name' | 'size'>): string | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(fileExtension(file.name)))
    return '不支持这种文件格式。'
  if (file.size === 0) return '文件是空的。'
  if (file.size > MAX_UPLOAD_BYTES) return '文件不能超过 50 MB。'
  return null
}

export function validateParseOptions(options: ParseOptions): string | null {
  const { max_chunk_chars: size, chunk_overlap: overlap } = options
  if (!Number.isInteger(size) || size < 100 || size > 4000)
    return '单个切片的最大字数应在 100–4000 之间。'
  if (!Number.isInteger(overlap) || overlap < 0 || overlap >= size)
    return '切片重叠字数不能为负数，且要小于最大字数。'
  return null
}

export type MetadataForm = Record<keyof VersionMetadata, string>

export const EMPTY_METADATA: MetadataForm = {
  document_number: '',
  version_note: '',
  published_date: '',
  effective_date: '',
  validity_status: 'unknown',
  validity_checked_at: '',
  source_url: '',
  download_url: '',
  expected_articles: '',
}

export function metadataForm(version: VersionMetadata): MetadataForm {
  const form = { ...EMPTY_METADATA }
  for (const key of Object.keys(form) as (keyof VersionMetadata)[])
    form[key] = String(version[key] ?? '')
  return form
}

export function validateMetadata(form: MetadataForm): string | null {
  const count = form.expected_articles.trim()
  if (count && !/^[1-9]\d{0,4}$/.test(count)) return '应有条数应为正整数。'
  for (const url of [form.source_url, form.download_url])
    if (url.trim() && !/^https?:\/\/\S+$/i.test(url.trim()))
      return '网址需要以 http:// 或 https:// 开头。'
  return null
}

/** 只提交改动过的字段；清空的字段传 null，后端据此清除。 */
export function metadataPatch(
  version: VersionMetadata,
  form: MetadataForm,
): Partial<VersionMetadata> {
  const current = metadataForm(version)
  const patch: Record<string, string | number | null> = {}
  for (const key of Object.keys(form) as (keyof VersionMetadata)[]) {
    const value = form[key].trim()
    if (value === current[key]) continue
    if (key === 'expected_articles') patch[key] = value ? Number(value) : null
    else patch[key] = value || null
  }
  return patch as Partial<VersionMetadata>
}

export function buildImportForm(input: {
  file: File
  documentId?: string
  title?: string
  issuer?: string
  metadata: MetadataForm
  options: ParseOptions
}): FormData {
  const form = new FormData()
  form.set('file', input.file)
  if (input.documentId) form.set('document_id', input.documentId)
  else {
    form.set('title', input.title?.trim() ?? '')
    if (input.issuer?.trim()) form.set('issuer', input.issuer.trim())
  }
  for (const [key, value] of Object.entries(input.metadata))
    if (value.trim()) form.set(key, value.trim())
  for (const [key, value] of Object.entries(input.options)) form.set(key, String(value))
  return form
}

export function previewKind(version: DocumentVersion): 'pdf' | 'image' | null {
  if (version.has_pdf) return 'pdf'
  return BROWSER_IMAGE_TYPES.has(version.file_type) ? 'image' : null
}
