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
  category?: string | null
  tags?: string[]
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
  category?: string
  tag?: string
}

export interface FacetCount {
  name: string
  count: number
}

export interface LibraryFacetsResponse {
  total_documents: number
  total_chunks: number
  categories: FacetCount[]
  tags: FacetCount[]
  validity_counts: Record<string, number>
}

export interface LibraryPublishedVersion {
  id: string
  document_number: string | null
  version_note: string | null
  published_date: string | null
  effective_date: string | null
  validity_status: ValidityStatus
  validity_checked_at: string | null
  source_url: string | null
  page_count: number | null
  chunk_count: number
  published_at: string | null
}

export interface LibraryDocumentSummary {
  id: string
  title: string
  issuer: string | null
  category: string | null
  tags: string[]
  version: LibraryPublishedVersion
  updated_at: string
}

export interface LibraryListQuery {
  page?: number
  page_size?: number
  q?: string
  category?: string
  tag?: string
  validity_status?: ValidityStatus
  include_repealed?: boolean
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
  include_repealed?: boolean
  validity_status?: ValidityStatus
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
  has_image: boolean
}

export interface SearchOutcome {
  query: string
  pipeline: string
  results: SearchHit[]
}

type ChatSource = Pick<
  SearchHit,
  | 'chunk_id'
  | 'document'
  | 'version'
  | 'chapter'
  | 'section'
  | 'article'
  | 'heading'
  | 'page_start'
  | 'page_end'
>

interface ChatChunkCitation extends ChatSource {
  kind?: 'chunk'
  number: number
}

export interface ChatReference extends ChatSource {
  content: string
}

export interface ChatVersionReference {
  kind: 'version'
  document: SearchHit['document']
  version: SearchHit['version'] & { validity_checked_at: string | null }
}

interface ChatVersionCitation extends ChatVersionReference {
  number: number
}

export type ChatCitation = ChatChunkCitation | ChatVersionCitation

export type ChatConfidenceLevel = 'high' | 'medium' | 'low'

export interface ChatConfidence {
  score: number
  level: ChatConfidenceLevel
  reasons: string[]
}

export interface ChatAnswerContent {
  answer: string
  citations: ChatCitation[]
  confidence?: ChatConfidence | null
}

export interface ChatOutcome extends ChatAnswerContent {
  status: 'answered' | 'partial' | 'insufficient_evidence'
  turn_id?: string
}

export type ChatStepState = 'running' | 'completed' | 'failed' | 'interrupted'

interface ChatModelStep {
  attempt: number
  state: ChatStepState
}

export interface ChatToolStep {
  id: string
  tool: 'search_knowledge' | 'get_article' | 'get_document_status' | 'reuse_answer_evidence'
  state: ChatStepState
  target?: string | null
  summary?: string | null
}

export type ChatExecutionStep = ChatModelStep | ChatToolStep

export interface ChatTurn {
  id: string
  question: string
  state: 'running' | 'completed' | 'interrupted' | 'failed'
  result: ChatOutcome | null
  execution?: ChatExecutionStep[]
  tool_steps?: ChatToolStep[]
}

export interface ChatSessionCreated {
  id: string
}

export interface ChatSessionSummary extends ChatSessionCreated {
  title: string
  archived: boolean
  pinned: boolean
  updated_at: string
}

export type ChatSessionUpdate = Partial<Pick<ChatSessionSummary, 'title' | 'archived' | 'pinned'>>

export interface ChatSessionList {
  items: ChatSessionSummary[]
  next_cursor: string | null
}

export interface ChatSessionHistory extends ChatSessionSummary {
  turns: ChatTurn[]
  through_seq: number
  next_before_seq: number | null
}

export type ChatStreamUpdate =
  | { type: 'turn'; turn_id: string }
  | { type: 'progress'; stage: 'retrieving' | 'generating' }
  | { type: 'model'; step: ChatModelStep }
  | { type: 'tool'; step: ChatToolStep }
  | { type: 'answer_delta'; text: string }
  | { type: 'paragraph'; text: string; citations: ChatCitation[] }

export type FavoriteKind = 'qa' | 'chunk' | 'link'

export interface FavoriteFolderSummary {
  id: string
  parent_id: string | null
  name: string
  position: number
  item_count: number
  subfolder_count: number
  created_at: string
  updated_at: string
}

export interface FavoriteTreeResponse {
  folders: FavoriteFolderSummary[]
  root_item_count: number
  total_item_count: number
}

export interface FavoriteChunkPayload {
  chunk_id: number
  document_id: string
  document_title: string
  issuer: string | null
  version_id: string
  document_number: string | null
  version_note: string | null
  validity_status: ValidityStatus
  effective_date: string | null
  source_url: string | null
  chapter: string
  section: string
  article: string
  heading: string
  page_start: number | null
  page_end: number | null
  content: string
}

export interface FavoriteQaPayload {
  session_id: string
  turn_id: string
  question: string
  answer: string
  status: ChatOutcome['status']
  citations: ChatCitation[]
}

export interface FavoriteLinkPayload {
  url: string
}

export interface FavoriteItem {
  id: string
  folder_id: string | null
  kind: FavoriteKind
  title: string
  subtitle: string
  note: string | null
  source_key: string | null
  payload: FavoriteChunkPayload | FavoriteQaPayload | FavoriteLinkPayload | Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface FavoriteListResponse {
  folders: FavoriteFolderSummary[]
  items: FavoriteItem[]
}

export interface FavoriteSourceMatch {
  id: string
  folder_id: string | null
}

export interface FavoriteLookupResponse {
  items: Record<string, FavoriteSourceMatch>
}

export interface FavoriteFolderCreateInput {
  name: string
  parent_id?: string | null
}

export interface FavoriteFolderUpdateInput {
  name?: string
  parent_id?: string | null
}

export interface FavoriteCreateInput {
  kind: FavoriteKind
  folder_id?: string | null
  title?: string
  note?: string | null
  chunk_id?: number
  session_id?: string
  turn_id?: string
  url?: string
}

export interface FavoriteUpdateInput {
  title?: string
  note?: string | null
  folder_id?: string | null
  url?: string
}
