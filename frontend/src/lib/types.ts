export interface User {
  id: string
  username: string
  email: string
  role: 'user' | 'admin'
  status: 'active' | 'disabled'
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export type ProfileInput = Pick<User, 'username' | 'email'>

export interface UserListQuery {
  page?: number
  page_size?: number
  q?: string
  role?: User['role']
  status?: User['status']
}

export interface UserPage {
  items: User[]
  total: number
  page: number
  page_size: number
}

export interface RegisterInput {
  username: string
  email: string
  password: string
  confirm_password: string
}

export interface SessionSnapshot {
  user: User | null
  expiresAt: number | null
  hasSession: boolean
  notice: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type VersionStatus =
  'uploaded' | 'parsing' | 'parsed' | 'indexing' | 'ready' | 'published' | 'archived' | 'failed'
export type ValidityStatus = 'unknown' | 'effective' | 'not_yet_effective' | 'amended' | 'repealed'
export type ParseStatus = 'completed' | 'needs_ocr' | 'empty'
export type JobKind = 'parse' | 'index' | 'cleanup' | 'pipeline'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface VersionBrief {
  id: string
  status: VersionStatus
  version_note: string | null
  validity_status: ValidityStatus
  published_at: string | null
  created_at: string
}

export interface DocumentSummary {
  id: string
  external_id: string | null
  title: string
  issuer: string | null
  version_count: number
  published_version: VersionBrief | null
  latest_version: VersionBrief | null
  created_at: string
  updated_at: string
}

export interface DocumentListQuery {
  page?: number
  page_size?: number
  q?: string
  published?: boolean
}

export interface ImportJob {
  id: string
  version_id: string
  document_id: string
  document_title: string
  file_name: string
  kind: JobKind
  // 附加方案任务的入库方案名
  ingest: string | null
  status: JobStatus
  stage: string | null
  progress_current: number
  progress_total: number | null
  attempts: number
  max_attempts: number
  run_after: string
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export interface JobListQuery {
  page?: number
  page_size?: number
  status?: JobStatus
  kind?: JobKind
  document_id?: string
}

export interface ParseOptions {
  ocr: string
  chunker: string
  max_chunk_chars: number
  chunk_overlap: number
}

export interface VersionMetadata {
  document_number: string | null
  version_note: string | null
  published_date: string | null
  effective_date: string | null
  validity_status: ValidityStatus
  validity_checked_at: string | null
  source_url: string | null
  download_url: string | null
  expected_articles: number | null
}

export interface DocumentVersion extends VersionMetadata {
  id: string
  document_id: string
  status: VersionStatus
  file_name: string
  file_type: string
  size_bytes: number
  sha256: string
  has_pdf: boolean
  parse_options: Partial<ParseOptions>
  parser_version: string | null
  parse_status: ParseStatus | null
  page_count: number | null
  chunk_count: number
  parse_stats: Record<string, unknown> | null
  parse_warnings: string[] | null
  index_model: string | null
  indexed_at: string | null
  latest_job: ImportJob | null
  published_at: string | null
  created_at: string
  updated_at: string
}

export interface DocumentChunk {
  id: number
  ordinal: number
  external_id: string | null
  chapter: string
  section: string
  article: string
  heading: string
  part: number
  part_count: number
  content: string
  page_start: number | null
  page_end: number | null
  sources: string[]
}

export interface PipelineInfo {
  name: string
  label: string
  description: string
  ingest: string
  channels: string[]
  default: boolean
  available: boolean
  unavailable_reason: string | null
}

export type PipelineIndexStatus = 'not_built' | 'building' | 'ready' | 'failed'

export interface VersionPipeline {
  pipeline: PipelineInfo
  status: PipelineIndexStatus
  supported: boolean
  unit_count: number
  page_count: number | null
  index_model: string | null
  indexed_at: string | null
}

export interface SearchInput {
  query: string
  top_k?: number
  pipeline?: string
  document_ids?: string[]
}

export interface SearchHit {
  chunk_id: number
  score: number
  dense_rank: number | null
  dense_score: number | null
  keyword_rank: number | null
  keyword_score: number | null
  visual_rank: number | null
  visual_score: number | null
  document: { id: string; title: string; issuer: string | null }
  version: {
    id: string
    document_number: string | null
    version_note: string | null
    published_date: string | null
    effective_date: string | null
    validity_status: ValidityStatus
    source_url: string | null
  }
  ordinal: number
  chapter: string
  section: string
  article: string
  heading: string
  content: string
  page_start: number | null
  page_end: number | null
  bbox: number[] | null
  modality: string | null
}

export interface SearchOutcome {
  query: string
  pipeline: string
  results: SearchHit[]
}
