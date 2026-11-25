import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  ArrowUpRight,
  Bookmark,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Folder,
  FolderInput,
  FolderPlus,
  Layers,
  Link2,
  LoaderCircle,
  MessageSquareText,
  Pencil,
  Plus,
  Search,
  Star,
  Trash2,
  X,
} from 'lucide-react'
import Markdown from 'react-markdown'
import { useNavigate } from 'react-router-dom'
import remarkGfm from 'remark-gfm'
import { useAuth } from '../auth/AuthProvider'
import { Dialog } from '../components/Dialog'
import { ValidityTag } from '../components/IngestionUi'
import { Notice } from '../components/Notice'
import {
  SaveFavoriteDialog,
  buildFolderSelectOptions,
  type SaveFavoriteTarget,
} from '../components/SaveFavoriteDialog'
import { Select } from '../components/Select'
import { errorMessage } from '../lib/api'
import { formatDate, formatDateTime } from '../lib/dates'
import { articlePath, pageLabel } from '../lib/ingestion'
import type {
  FavoriteChunkPayload,
  FavoriteFolderSummary,
  FavoriteItem,
  FavoriteKind,
  FavoriteLinkPayload,
  FavoriteListResponse,
  FavoriteQaPayload,
  FavoriteTreeResponse,
} from '../lib/types'

type ViewMode =
  { type: 'root' } | { type: 'folder'; folderId: string } | { type: 'all'; kind?: FavoriteKind }

type SelectionKey = `folder:${string}` | `item:${string}`

type ActiveDialog =
  | { type: 'create-folder'; parentId: string | null }
  | { type: 'rename-folder'; folder: FavoriteFolderSummary }
  | { type: 'edit-item'; item: FavoriteItem }
  | { type: 'move'; folderIds: string[]; itemIds: string[]; label: string }
  | { type: 'delete-item'; item: FavoriteItem }
  | { type: 'delete-folder'; folder: FavoriteFolderSummary }
  | { type: 'batch-delete'; folderIds: string[]; itemIds: string[] }
  | { type: 'preview'; item: FavoriteItem }

const kindLabels: Record<FavoriteKind, string> = {
  qa: '问答记录',
  chunk: '制度条文',
  link: '外部链接',
}

function collectSubtreeIds(folders: FavoriteFolderSummary[], rootIds: string[]): Set<string> {
  const byParent = new Map<string | null, string[]>()
  for (const folder of folders) {
    const list = byParent.get(folder.parent_id) ?? []
    list.push(folder.id)
    byParent.set(folder.parent_id, list)
  }
  const result = new Set<string>()
  const stack = [...rootIds]
  while (stack.length) {
    const current = stack.pop()!
    if (result.has(current)) continue
    result.add(current)
    for (const child of byParent.get(current) ?? []) stack.push(child)
  }
  return result
}

function ItemKindIcon({ kind, size = 16 }: { kind: FavoriteKind; size?: number }) {
  if (kind === 'qa') return <MessageSquareText size={size} aria-hidden="true" />
  if (kind === 'chunk') return <FileText size={size} aria-hidden="true" />
  return <Link2 size={size} aria-hidden="true" />
}

function PreviewDialog({
  item,
  folderName,
  onClose,
  onEdit,
  onDelete,
  onOpenSession,
}: {
  item: FavoriteItem
  folderName: string | null
  onClose: () => void
  onEdit: (item: FavoriteItem) => void
  onDelete: (item: FavoriteItem) => void
  onOpenSession: (sessionId: string) => void
}) {
  const chunk = item.kind === 'chunk' ? (item.payload as FavoriteChunkPayload) : null
  const qa = item.kind === 'qa' ? (item.payload as FavoriteQaPayload) : null
  const link = item.kind === 'link' ? (item.payload as FavoriteLinkPayload) : null

  return (
    <Dialog title={item.title} kicker={kindLabels[item.kind]} busy={false} onClose={onClose} wide>
      <div className="fav-preview">
        <div className="fav-preview-subbar">
          <span className="fav-folder-chip">
            {folderName ? (
              <>
                <Folder size={12} aria-hidden="true" />
                {folderName}
              </>
            ) : (
              <>
                <Star size={12} aria-hidden="true" />
                收藏夹栏
              </>
            )}
          </span>
          <span className="fav-preview-time">收藏于 {formatDateTime(item.created_at)}</span>
        </div>

        {chunk && (
          <>
            <div className="fav-preview-meta">
              <span>{chunk.document_title}</span>
              <span>{articlePath(chunk)}</span>
              <span>{pageLabel(chunk.page_start, chunk.page_end)}</span>
              <ValidityTag status={chunk.validity_status} />
            </div>
            {(chunk.version_note || chunk.document_number || chunk.effective_date) && (
              <dl className="search-citation">
                {chunk.version_note && (
                  <div>
                    <dt>版本</dt>
                    <dd>{chunk.version_note}</dd>
                  </div>
                )}
                {chunk.document_number && (
                  <div>
                    <dt>文号</dt>
                    <dd>{chunk.document_number}</dd>
                  </div>
                )}
                {chunk.effective_date && (
                  <div>
                    <dt>生效日期</dt>
                    <dd>{formatDate(chunk.effective_date)}</dd>
                  </div>
                )}
              </dl>
            )}
            <blockquote className="fav-preview-quote">{chunk.content}</blockquote>
            {chunk.source_url && (
              <a
                className="chat-source-link"
                href={chunk.source_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                查看发布依据 <ExternalLink size={12} aria-hidden="true" />
              </a>
            )}
          </>
        )}

        {qa && (
          <>
            <div className="fav-preview-question">
              <span className="fav-preview-label">提问</span>
              <p>{qa.question}</p>
            </div>
            <div className="fav-preview-answer">
              <span className="fav-preview-label">回答内容</span>
              <div className="chat-answer-body">
                <Markdown
                  skipHtml
                  disallowedElements={['img']}
                  remarkPlugins={[remarkGfm]}
                  components={{ a: ({ children }) => children }}
                >
                  {qa.answer}
                </Markdown>
              </div>
            </div>
            {qa.citations?.length > 0 && (
              <div className="fav-preview-citations">
                <span className="fav-preview-label">引用依据（{qa.citations.length}）</span>
                <ul>
                  {qa.citations.map((citation) => (
                    <li key={`${citation.number}-${citation.document.id}`}>
                      <span>[{citation.number}]</span>
                      <strong>{citation.document.title}</strong>
                      {'article' in citation && citation.article && (
                        <small>{citation.article}</small>
                      )}
                      <ValidityTag status={citation.version.validity_status} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {link && (
          <div className="fav-preview-link-box">
            <span className="fav-preview-label">外部链接</span>
            <a
              className="chat-source-link fav-preview-url"
              href={link.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {link.url} <ExternalLink size={12} aria-hidden="true" />
            </a>
          </div>
        )}

        {item.note && (
          <div className="fav-preview-note">
            <span className="fav-preview-label">我的备注</span>
            <p>{item.note}</p>
          </div>
        )}

        <div className="fav-dialog-actions fav-dialog-actions-split">
          <button type="button" className="btn btn-danger" onClick={() => onDelete(item)}>
            <Trash2 size={14} aria-hidden="true" />
            删除
          </button>
          <div className="fav-dialog-actions-group">
            <button type="button" className="btn" onClick={() => onEdit(item)}>
              <Pencil size={14} aria-hidden="true" />
              编辑
            </button>
            {qa && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => onOpenSession(qa.session_id)}
              >
                前往原会话
                <ArrowUpRight size={14} aria-hidden="true" />
              </button>
            )}
            {link && (
              <a
                className="btn btn-primary"
                href={link.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                打开链接
                <ExternalLink size={14} aria-hidden="true" />
              </a>
            )}
            <button type="button" className="btn" onClick={onClose}>
              关闭
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}

export function FavoritesPage() {
  const { client } = useAuth()
  const navigate = useNavigate()

  const [tree, setTree] = useState<FavoriteTreeResponse | null>(null)
  const [list, setList] = useState<FavoriteListResponse | null>(null)
  const [view, setView] = useState<ViewMode>({ type: 'root' })
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [rootExpanded, setRootExpanded] = useState(true)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [selected, setSelected] = useState<Set<SelectionKey>>(() => new Set())
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [dialog, setDialog] = useState<ActiveDialog | null>(null)
  const [addTarget, setAddTarget] = useState<SaveFavoriteTarget | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<unknown>(null)

  // Dialog form state
  const [folderNameInput, setFolderNameInput] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [editNote, setEditNote] = useState('')
  const [editUrl, setEditUrl] = useState('')
  const [targetFolderId, setTargetFolderId] = useState<string>('')

  const selectAllRef = useRef<HTMLInputElement>(null)
  const batchSelectAllRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput.trim()), 200)
    return () => clearTimeout(timer)
  }, [searchInput])

  const loadAll = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true)
      setError(null)
      try {
        const [treeData, listData] = await Promise.all([
          client.getFavoriteTree(signal),
          searchQuery
            ? client.listFavorites({ q: searchQuery, signal })
            : view.type === 'folder'
              ? client.listFavorites({ folder_id: view.folderId, signal })
              : view.type === 'all'
                ? client.listFavorites({ scope: 'all', kind: view.kind ?? null, signal })
                : client.listFavorites({ scope: 'root', signal }),
        ])
        if (signal.aborted) return
        setTree(treeData)
        setList(listData)
      } catch (caught) {
        if (!signal.aborted) setError(caught)
      } finally {
        if (!signal.aborted) setLoading(false)
      }
    },
    [client, searchQuery, view],
  )

  useEffect(() => {
    const controller = new AbortController()
    void loadAll(controller.signal)
    return () => controller.abort()
  }, [loadAll, revision])

  useEffect(() => {
    setSelected(new Set())
  }, [view, searchQuery, revision])

  const folders = tree?.folders ?? []
  const foldersById = useMemo(
    () => new Map(folders.map((folder) => [folder.id, folder])),
    [folders],
  )
  const childrenByParent = useMemo(() => {
    const map = new Map<string | null, FavoriteFolderSummary[]>()
    for (const folder of folders) {
      const group = map.get(folder.parent_id) ?? []
      group.push(folder)
      map.set(folder.parent_id, group)
    }
    return map
  }, [folders])

  const breadcrumbs = useMemo(() => {
    if (view.type !== 'folder') return []
    const path: FavoriteFolderSummary[] = []
    let current = foldersById.get(view.folderId)
    const seen = new Set<string>()
    while (current && !seen.has(current.id)) {
      seen.add(current.id)
      path.unshift(current)
      current = current.parent_id ? foldersById.get(current.parent_id) : undefined
    }
    return path
  }, [foldersById, view])

  const activeFolderId = view.type === 'folder' ? view.folderId : null
  const displayedFolders = list?.folders ?? []
  const displayedItems = list?.items ?? []
  const totalRows = displayedFolders.length + displayedItems.length
  const allSelected = totalRows > 0 && selected.size === totalRows
  const partiallySelected = selected.size > 0 && selected.size < totalRows
  const showLocationBadge = Boolean(searchQuery) || view.type === 'all'

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = partiallySelected
    if (batchSelectAllRef.current) batchSelectAllRef.current.indeterminate = partiallySelected
  }, [partiallySelected])

  const selectedFolderIds = useMemo(
    () =>
      [...selected]
        .filter((key) => key.startsWith('folder:'))
        .map((key) => key.slice('folder:'.length)),
    [selected],
  )
  const selectedItemIds = useMemo(
    () =>
      [...selected]
        .filter((key) => key.startsWith('item:'))
        .map((key) => key.slice('item:'.length)),
    [selected],
  )

  const allFolderSelectOptions = useMemo(() => buildFolderSelectOptions(folders), [folders])
  const moveSelectOptions = useMemo(() => {
    if (dialog?.type !== 'move') return allFolderSelectOptions
    const excluded = collectSubtreeIds(folders, dialog.folderIds)
    return buildFolderSelectOptions(folders, excluded)
  }, [allFolderSelectOptions, dialog, folders])

  const refresh = () => setRevision((value) => value + 1)

  function toggleSelection(key: SelectionKey) {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function toggleSelectAll() {
    if (allSelected) {
      setSelected(new Set())
      return
    }
    const next = new Set<SelectionKey>()
    for (const folder of displayedFolders) next.add(`folder:${folder.id}`)
    for (const item of displayedItems) next.add(`item:${item.id}`)
    setSelected(next)
  }

  function switchView(nextView: ViewMode) {
    setSearchInput('')
    setSearchQuery('')
    setView(nextView)
    setMobileNavOpen(false)
  }

  function openFolder(folderId: string) {
    setSearchInput('')
    setSearchQuery('')
    setView({ type: 'folder', folderId })
    setMobileNavOpen(false)
    setRootExpanded(true)
    setExpandedIds((previous) => {
      const next = new Set(previous)
      let current = foldersById.get(folderId)
      while (current?.parent_id) {
        next.add(current.parent_id)
        current = foldersById.get(current.parent_id)
      }
      return next
    })
  }

  function toggleExpand(folderId: string) {
    setExpandedIds((previous) => {
      const next = new Set(previous)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return next
    })
  }

  function openCreateFolderDialog(parentId: string | null = activeFolderId) {
    setActionError(null)
    setFolderNameInput('')
    setTargetFolderId(parentId ?? '')
    setDialog({ type: 'create-folder', parentId })
  }

  function openRenameFolderDialog(folder: FavoriteFolderSummary) {
    setActionError(null)
    setFolderNameInput(folder.name)
    setDialog({ type: 'rename-folder', folder })
  }

  function openEditItemDialog(item: FavoriteItem) {
    setActionError(null)
    setEditTitle(item.title)
    setEditNote(item.note ?? '')
    setEditUrl(item.kind === 'link' ? ((item.payload as FavoriteLinkPayload).url ?? '') : '')
    setTargetFolderId(item.folder_id ?? '')
    setDialog({ type: 'edit-item', item })
  }

  function openMoveDialog(folderIds: string[], itemIds: string[], label: string) {
    setActionError(null)
    setTargetFolderId(activeFolderId ?? '')
    setDialog({ type: 'move', folderIds, itemIds, label })
  }

  function openDeleteItemDialog(item: FavoriteItem) {
    setActionError(null)
    setDialog({ type: 'delete-item', item })
  }

  async function submitDialog(event: FormEvent) {
    event.preventDefault()
    if (!dialog || busy) return
    setBusy(true)
    setActionError(null)
    try {
      if (dialog.type === 'create-folder') {
        const name = folderNameInput.trim()
        if (!name) return
        const parentId = targetFolderId || null
        const created = await client.createFavoriteFolder({ name, parent_id: parentId })
        if (parentId) {
          setExpandedIds((previous) => new Set(previous).add(parentId))
        }
        setRootExpanded(true)
        if (view.type === 'all') setView({ type: 'folder', folderId: created.id })
        setDialog(null)
        refresh()
      } else if (dialog.type === 'rename-folder') {
        const name = folderNameInput.trim()
        if (!name) return
        await client.updateFavoriteFolder(dialog.folder.id, { name })
        setDialog(null)
        refresh()
      } else if (dialog.type === 'edit-item') {
        const title = editTitle.trim()
        if (!title) return
        if (dialog.item.kind === 'link' && !editUrl.trim()) return
        await client.updateFavorite(dialog.item.id, {
          title,
          note: editNote.trim() || null,
          folder_id: targetFolderId || null,
          ...(dialog.item.kind === 'link' ? { url: editUrl.trim() } : {}),
        })
        setDialog(null)
        refresh()
      } else if (dialog.type === 'move') {
        await client.batchMoveFavorites({
          folder_ids: dialog.folderIds,
          item_ids: dialog.itemIds,
          target_folder_id: targetFolderId || null,
        })
        setDialog(null)
        refresh()
      } else if (dialog.type === 'delete-item') {
        await client.deleteFavorite(dialog.item.id)
        setDialog(null)
        refresh()
      } else if (dialog.type === 'delete-folder') {
        await client.deleteFavoriteFolder(dialog.folder.id)
        if (view.type === 'folder' && view.folderId === dialog.folder.id) {
          setView(
            dialog.folder.parent_id
              ? { type: 'folder', folderId: dialog.folder.parent_id }
              : { type: 'root' },
          )
        }
        setDialog(null)
        refresh()
      } else if (dialog.type === 'batch-delete') {
        await client.batchDeleteFavorites({
          folder_ids: dialog.folderIds,
          item_ids: dialog.itemIds,
        })
        setDialog(null)
        refresh()
      }
    } catch (caught) {
      setActionError(caught)
    } finally {
      setBusy(false)
    }
  }

  const rootHasChildren = (childrenByParent.get(null)?.length ?? 0) > 0

  function renderTreeBranch(parentId: string | null, depth: number) {
    const branch = childrenByParent.get(parentId) ?? []
    if (!branch.length) return null
    return (
      <ul className="fav-tree-branch" role="group">
        {branch.map((folder) => {
          const hasChildren = (childrenByParent.get(folder.id)?.length ?? 0) > 0
          const expanded = expandedIds.has(folder.id)
          const active = !searchQuery && view.type === 'folder' && view.folderId === folder.id
          return (
            <li key={folder.id}>
              <div
                className={`fav-tree-node${active ? ' active' : ''}`}
                style={{ paddingLeft: `${8 + Math.min(depth, 5) * 16}px` }}
              >
                <button
                  type="button"
                  className="fav-tree-toggle"
                  aria-label={expanded ? `折叠 ${folder.name}` : `展开 ${folder.name}`}
                  aria-expanded={hasChildren ? expanded : undefined}
                  disabled={!hasChildren}
                  onClick={() => toggleExpand(folder.id)}
                >
                  {hasChildren ? (
                    expanded ? (
                      <ChevronDown size={13} aria-hidden="true" />
                    ) : (
                      <ChevronRight size={13} aria-hidden="true" />
                    )
                  ) : (
                    <span className="fav-tree-spacer" aria-hidden="true" />
                  )}
                </button>
                <button
                  type="button"
                  className="fav-tree-select"
                  onClick={() => openFolder(folder.id)}
                >
                  <Folder size={14} aria-hidden="true" />
                  <span className="fav-tree-name" title={folder.name}>
                    {folder.name}
                  </span>
                  {folder.item_count > 0 && (
                    <span className="fav-tree-count">{folder.item_count}</span>
                  )}
                </button>
              </div>
              {hasChildren && expanded && renderTreeBranch(folder.id, depth + 1)}
            </li>
          )
        })}
      </ul>
    )
  }

  const headingTitle = searchQuery
    ? `搜索“${searchQuery}”`
    : view.type === 'folder'
      ? (breadcrumbs.at(-1)?.name ?? '文件夹')
      : view.type === 'all'
        ? view.kind
          ? kindLabels[view.kind]
          : '全部收藏'
        : '收藏夹栏'

  const summaryParts = [
    displayedFolders.length > 0 && `${displayedFolders.length} 个文件夹`,
    displayedItems.length > 0 && `${displayedItems.length} 项收藏`,
  ].filter(Boolean)

  const summaryText = summaryParts.length ? summaryParts.join(' · ') : '0 项'

  return (
    <>
      <header className="page-heading">
        <div className="page-heading-text">
          <span className="page-kicker">Favorites</span>
          <h1>我的收藏</h1>
          <p>整理收藏的问答记录、制度条文与外部参考链接，支持多级目录归类与全文检索。</p>
        </div>
        <div className="heading-actions">
          <button
            type="button"
            className="btn"
            onClick={() => setAddTarget({ kind: 'link', defaultFolderId: activeFolderId })}
          >
            <Link2 size={14} aria-hidden="true" />
            添加链接
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => openCreateFolderDialog(activeFolderId)}
          >
            <FolderPlus size={14} aria-hidden="true" />
            新建文件夹
          </button>
        </div>
      </header>

      <section className="fav-shell" aria-label="收藏夹管理">
        <aside className="fav-sidebar" aria-label="收藏夹目录">
          <div className="fav-sidebar-top">
            <div className="fav-search-box">
              <Search size={14} className="fav-search-icon" aria-hidden="true" />
              <input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="搜索标题、备注或链接…"
                aria-label="搜索收藏夹"
              />
              {searchInput && (
                <button
                  type="button"
                  className="fav-search-clear"
                  aria-label="清空搜索"
                  onClick={() => {
                    setSearchInput('')
                    setSearchQuery('')
                  }}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              )}
            </div>

            <button
              type="button"
              className="fav-sidebar-mobile-toggle"
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((value) => !value)}
            >
              <span>目录：{headingTitle}</span>
              <ChevronDown
                size={15}
                className={mobileNavOpen ? 'rotated' : undefined}
                aria-hidden="true"
              />
            </button>
          </div>

          <nav
            className={`fav-tree${mobileNavOpen ? ' is-open' : ''}`}
            aria-label="收藏视图与文件夹树"
          >
            <div className="fav-tree-section-head">
              <span>文件夹目录</span>
              <button
                type="button"
                className="icon-button fav-tree-add-btn"
                title="在目录中新建文件夹"
                aria-label="在目录中新建文件夹"
                onClick={() => openCreateFolderDialog(activeFolderId)}
              >
                <Plus size={14} aria-hidden="true" />
              </button>
            </div>

            <div
              className={`fav-tree-node${!searchQuery && view.type === 'root' ? ' active' : ''}`}
              style={{ paddingLeft: '8px' }}
            >
              <button
                type="button"
                className="fav-tree-toggle"
                aria-label={rootExpanded ? '折叠收藏夹栏' : '展开收藏夹栏'}
                aria-expanded={rootHasChildren ? rootExpanded : undefined}
                disabled={!rootHasChildren}
                onClick={() => setRootExpanded((value) => !value)}
              >
                {rootHasChildren ? (
                  rootExpanded ? (
                    <ChevronDown size={13} aria-hidden="true" />
                  ) : (
                    <ChevronRight size={13} aria-hidden="true" />
                  )
                ) : (
                  <span className="fav-tree-spacer" aria-hidden="true" />
                )}
              </button>
              <button
                type="button"
                className="fav-tree-select"
                onClick={() => switchView({ type: 'root' })}
              >
                <Star size={14} aria-hidden="true" />
                <span className="fav-tree-name">收藏夹栏</span>
                {(tree?.root_item_count ?? 0) > 0 && (
                  <span className="fav-tree-count">{tree?.root_item_count}</span>
                )}
              </button>
            </div>
            {rootExpanded && renderTreeBranch(null, 1)}

            <div className="fav-tree-divider" />

            <div className="fav-tree-section-head">
              <span>按范围与类型</span>
            </div>

            <button
              type="button"
              className={`fav-tree-shortcut${!searchQuery && view.type === 'all' && !view.kind ? ' active' : ''}`}
              onClick={() => switchView({ type: 'all' })}
            >
              <Layers size={14} aria-hidden="true" />
              <span className="fav-tree-name">全部收藏</span>
              {tree && <span className="fav-tree-count">{tree.total_item_count}</span>}
            </button>

            {(['qa', 'chunk', 'link'] as FavoriteKind[]).map((kind) => (
              <button
                key={kind}
                type="button"
                className={`fav-tree-shortcut${!searchQuery && view.type === 'all' && view.kind === kind ? ' active' : ''}`}
                onClick={() => switchView({ type: 'all', kind })}
              >
                <ItemKindIcon kind={kind} size={14} />
                <span className="fav-tree-name">{kindLabels[kind]}</span>
              </button>
            ))}
          </nav>
        </aside>

        <div className="fav-main" aria-labelledby="favorites-heading">
          <header className="fav-toolbar">
            <div className="fav-toolbar-title">
              {breadcrumbs.length > 0 && !searchQuery && (
                <div className="fav-breadcrumbs" aria-label="当前路径">
                  <button
                    type="button"
                    className="fav-crumb-link"
                    onClick={() => switchView({ type: 'root' })}
                  >
                    收藏夹栏
                  </button>
                  {breadcrumbs.map((crumb, index) => (
                    <span key={crumb.id} className="fav-crumb-segment">
                      <ChevronRight size={12} aria-hidden="true" />
                      {index === breadcrumbs.length - 1 ? (
                        <span className="fav-crumb-current">{crumb.name}</span>
                      ) : (
                        <button
                          type="button"
                          className="fav-crumb-link"
                          onClick={() => openFolder(crumb.id)}
                        >
                          {crumb.name}
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              )}
              <div className="fav-toolbar-heading-row">
                <h2 id="favorites-heading">{headingTitle}</h2>
                {list && <span className="fav-summary-count">{summaryText}</span>}
                {loading && (
                  <LoaderCircle
                    size={14}
                    className="spinner fav-inline-spinner"
                    aria-label="正在加载"
                  />
                )}
              </div>
            </div>

            <div className="fav-toolbar-controls">
              {searchQuery && (
                <button
                  type="button"
                  className="text-button text-button-muted"
                  onClick={() => {
                    setSearchInput('')
                    setSearchQuery('')
                  }}
                >
                  清空搜索
                </button>
              )}
              {totalRows > 0 && (
                <label className="fav-select-all">
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    className="ui-checkbox"
                    checked={allSelected}
                    onChange={toggleSelectAll}
                  />
                  <span>全选</span>
                </label>
              )}
            </div>
          </header>

          {selected.size > 0 && (
            <div className="fav-batch-bar" role="region" aria-label="批量操作">
              <label className="fav-batch-check">
                <input
                  ref={batchSelectAllRef}
                  type="checkbox"
                  className="ui-checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                />
                <span>已选 {selected.size} 项</span>
              </label>
              <div className="fav-batch-actions">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() =>
                    openMoveDialog(selectedFolderIds, selectedItemIds, `选中的 ${selected.size} 项`)
                  }
                >
                  <FolderInput size={14} aria-hidden="true" />
                  移动到…
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => {
                    setActionError(null)
                    setDialog({
                      type: 'batch-delete',
                      folderIds: selectedFolderIds,
                      itemIds: selectedItemIds,
                    })
                  }}
                >
                  <Trash2 size={14} aria-hidden="true" />
                  删除选中
                </button>
                <button
                  type="button"
                  className="text-button text-button-muted"
                  onClick={() => setSelected(new Set())}
                >
                  取消选择
                </button>
              </div>
            </div>
          )}

          {Boolean(error) && (
            <Notice kind="error" error={error}>
              {errorMessage(error)}
            </Notice>
          )}

          {loading && !list ? (
            <div className="fav-empty" role="status">
              <LoaderCircle size={20} className="spinner" aria-hidden="true" />
              <p>正在加载收藏夹…</p>
            </div>
          ) : totalRows === 0 ? (
            <div className="fav-empty">
              <div className="empty-symbol">
                <Bookmark size={22} strokeWidth={1.6} aria-hidden="true" />
              </div>
              <h3>{searchQuery ? '没有匹配的收藏内容' : '当前视图还没有收藏'}</h3>
              <p>
                {searchQuery
                  ? '可以尝试更换关键词，或在左侧切换目录浏览。'
                  : '你可以在智能问答或全文检索中点击「收藏」，也可以点击右上角添加外部链接或新建文件夹。'}
              </p>
              {!searchQuery && (
                <div className="fav-empty-actions">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setAddTarget({ kind: 'link', defaultFolderId: activeFolderId })}
                  >
                    <Link2 size={14} aria-hidden="true" />
                    添加链接
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => openCreateFolderDialog(activeFolderId)}
                  >
                    <FolderPlus size={14} aria-hidden="true" />
                    新建文件夹
                  </button>
                </div>
              )}
            </div>
          ) : (
            <ol className={`fav-rows${loading ? ' is-loading' : ''}`} aria-label="收藏内容列表">
              {displayedFolders.map((folder, index) => {
                const key: SelectionKey = `folder:${folder.id}`
                const checked = selected.has(key)
                const metaParts = [
                  folder.subfolder_count > 0 && `${folder.subfolder_count} 个子文件夹`,
                  folder.item_count > 0 && `${folder.item_count} 项收藏`,
                ].filter(Boolean)
                return (
                  <li key={folder.id} className={`fav-row${checked ? ' selected' : ''}`}>
                    <input
                      type="checkbox"
                      className="ui-checkbox fav-row-check"
                      aria-label={`选择文件夹 ${folder.name}`}
                      checked={checked}
                      onChange={() => toggleSelection(key)}
                    />
                    <span className="fav-row-index" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <button
                      type="button"
                      className="fav-row-main"
                      onClick={() => openFolder(folder.id)}
                    >
                      <span className="fav-row-icon fav-row-icon-folder">
                        <Folder size={16} aria-hidden="true" />
                      </span>
                      <span className="fav-row-content">
                        <span className="fav-row-headline">
                          <span className="fav-row-title" title={folder.name}>
                            {folder.name}
                          </span>
                          <span className="fav-kind-badge">文件夹</span>
                        </span>
                        <span className="fav-row-subline">
                          <span className="fav-row-subtitle">
                            {metaParts.length ? metaParts.join(' · ') : '空文件夹'}
                          </span>
                          <span className="fav-row-date">{formatDate(folder.updated_at)}</span>
                        </span>
                      </span>
                    </button>
                    <div className="fav-row-actions">
                      <button
                        type="button"
                        className="icon-button"
                        title="在此新建子文件夹"
                        aria-label={`在 ${folder.name} 下新建子文件夹`}
                        onClick={() => openCreateFolderDialog(folder.id)}
                      >
                        <FolderPlus size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        title="重命名文件夹"
                        aria-label={`重命名 ${folder.name}`}
                        onClick={() => openRenameFolderDialog(folder)}
                      >
                        <Pencil size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        title="移动文件夹"
                        aria-label={`移动 ${folder.name}`}
                        onClick={() => openMoveDialog([folder.id], [], `文件夹「${folder.name}」`)}
                      >
                        <FolderInput size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button fav-row-delete"
                        title="删除文件夹"
                        aria-label={`删除 ${folder.name}`}
                        onClick={() => {
                          setActionError(null)
                          setDialog({ type: 'delete-folder', folder })
                        }}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </li>
                )
              })}

              {displayedItems.map((item, index) => {
                const key: SelectionKey = `item:${item.id}`
                const checked = selected.has(key)
                const rowNumber = displayedFolders.length + index + 1
                const chunkValidity =
                  item.kind === 'chunk'
                    ? (item.payload as FavoriteChunkPayload).validity_status
                    : null
                const linkUrl =
                  item.kind === 'link' ? (item.payload as FavoriteLinkPayload).url : null
                const qaSessionId =
                  item.kind === 'qa' ? (item.payload as FavoriteQaPayload).session_id : null
                const folderName = item.folder_id
                  ? (foldersById.get(item.folder_id)?.name ?? null)
                  : null

                return (
                  <li key={item.id} className={`fav-row${checked ? ' selected' : ''}`}>
                    <input
                      type="checkbox"
                      className="ui-checkbox fav-row-check"
                      aria-label={`选择收藏 ${item.title}`}
                      checked={checked}
                      onChange={() => toggleSelection(key)}
                    />
                    <span className="fav-row-index" aria-hidden="true">
                      {String(rowNumber).padStart(2, '0')}
                    </span>
                    <button
                      type="button"
                      className="fav-row-main"
                      onClick={() => setDialog({ type: 'preview', item })}
                    >
                      <span className={`fav-row-icon fav-row-icon-${item.kind}`}>
                        <ItemKindIcon kind={item.kind} />
                      </span>
                      <span className="fav-row-content">
                        <span className="fav-row-headline">
                          <span className="fav-row-title" title={item.title}>
                            {item.title}
                          </span>
                          {chunkValidity && <ValidityTag status={chunkValidity} />}
                          {showLocationBadge && (
                            <span className="fav-kind-badge">{kindLabels[item.kind]}</span>
                          )}
                          {showLocationBadge && folderName && (
                            <span className="fav-folder-chip">
                              <Folder size={11} aria-hidden="true" />
                              {folderName}
                            </span>
                          )}
                        </span>
                        <span className="fav-row-subline">
                          <span className="fav-row-subtitle" title={item.subtitle}>
                            {item.subtitle || kindLabels[item.kind]}
                          </span>
                          {item.note && (
                            <span className="fav-row-note" title={item.note}>
                              备注：{item.note}
                            </span>
                          )}
                          <span className="fav-row-date">{formatDate(item.created_at)}</span>
                        </span>
                      </span>
                    </button>
                    <div className="fav-row-actions">
                      {linkUrl && (
                        <a
                          className="icon-button"
                          href={linkUrl}
                          target="_blank"
                          rel="noreferrer noopener"
                          title="在新标签页打开链接"
                          aria-label={`打开链接 ${item.title}`}
                        >
                          <ExternalLink size={14} aria-hidden="true" />
                        </a>
                      )}
                      {qaSessionId && (
                        <button
                          type="button"
                          className="icon-button"
                          title="前往原会话"
                          aria-label={`前往原会话 ${item.title}`}
                          onClick={() => navigate('/chat', { state: { sessionId: qaSessionId } })}
                        >
                          <ArrowUpRight size={14} aria-hidden="true" />
                        </button>
                      )}
                      <button
                        type="button"
                        className="icon-button"
                        title="编辑收藏"
                        aria-label={`编辑 ${item.title}`}
                        onClick={() => openEditItemDialog(item)}
                      >
                        <Pencil size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        title="移动到文件夹"
                        aria-label={`移动 ${item.title}`}
                        onClick={() => openMoveDialog([], [item.id], `「${item.title}」`)}
                      >
                        <FolderInput size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button fav-row-delete"
                        title="删除收藏"
                        aria-label={`删除 ${item.title}`}
                        disabled={busy}
                        onClick={() => openDeleteItemDialog(item)}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </section>

      {addTarget && (
        <SaveFavoriteDialog
          target={addTarget}
          onClose={() => setAddTarget(null)}
          onSaved={() => {
            setAddTarget(null)
            refresh()
          }}
        />
      )}

      {dialog?.type === 'preview' && (
        <PreviewDialog
          item={dialog.item}
          folderName={
            dialog.item.folder_id ? (foldersById.get(dialog.item.folder_id)?.name ?? null) : null
          }
          onClose={() => setDialog(null)}
          onEdit={(item) => openEditItemDialog(item)}
          onDelete={(item) => openDeleteItemDialog(item)}
          onOpenSession={(sessionId) => {
            setDialog(null)
            navigate('/chat', { state: { sessionId } })
          }}
        />
      )}

      {dialog && dialog.type !== 'preview' && (
        <Dialog
          title={
            dialog.type === 'create-folder'
              ? '新建文件夹'
              : dialog.type === 'rename-folder'
                ? '重命名文件夹'
                : dialog.type === 'edit-item'
                  ? '编辑收藏'
                  : dialog.type === 'move'
                    ? '移动到文件夹'
                    : dialog.type === 'delete-item'
                      ? '删除收藏'
                      : dialog.type === 'delete-folder'
                        ? '删除文件夹'
                        : '批量删除收藏'
          }
          kicker="Favorites"
          busy={busy}
          onClose={() => setDialog(null)}
        >
          <form className="fav-dialog-form" onSubmit={submitDialog}>
            {(dialog.type === 'create-folder' || dialog.type === 'rename-folder') && (
              <>
                <div className="form-field">
                  <label htmlFor="fav-folder-name">文件夹名称</label>
                  <div className="input-wrap">
                    <input
                      id="fav-folder-name"
                      value={folderNameInput}
                      onChange={(event) => setFolderNameInput(event.target.value)}
                      maxLength={80}
                      placeholder="例如：银行监管、证券合规、常用条文"
                      disabled={busy}
                      required
                      autoFocus
                    />
                  </div>
                </div>
                {dialog.type === 'create-folder' && (
                  <div className="form-field">
                    <label htmlFor="fav-create-parent">上级目录</label>
                    <Select
                      id="fav-create-parent"
                      value={targetFolderId}
                      options={allFolderSelectOptions}
                      onChange={setTargetFolderId}
                      disabled={busy}
                    />
                  </div>
                )}
              </>
            )}

            {dialog.type === 'edit-item' && (
              <>
                <div className="form-field">
                  <label htmlFor="fav-edit-title">标题</label>
                  <div className="input-wrap">
                    <input
                      id="fav-edit-title"
                      value={editTitle}
                      onChange={(event) => setEditTitle(event.target.value)}
                      maxLength={255}
                      disabled={busy}
                      required
                      autoFocus
                    />
                  </div>
                </div>
                {dialog.item.kind === 'link' && (
                  <div className="form-field">
                    <label htmlFor="fav-edit-url">链接地址</label>
                    <div className="input-wrap">
                      <input
                        id="fav-edit-url"
                        type="url"
                        value={editUrl}
                        onChange={(event) => setEditUrl(event.target.value)}
                        maxLength={2048}
                        disabled={busy}
                        required
                      />
                    </div>
                  </div>
                )}
                <div className="form-field">
                  <label htmlFor="fav-edit-folder">所属文件夹</label>
                  <Select
                    id="fav-edit-folder"
                    value={targetFolderId}
                    options={allFolderSelectOptions}
                    onChange={setTargetFolderId}
                    disabled={busy}
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="fav-edit-note">备注</label>
                  <textarea
                    id="fav-edit-note"
                    rows={2}
                    value={editNote}
                    onChange={(event) => setEditNote(event.target.value)}
                    maxLength={500}
                    placeholder="补充适用场景或记忆要点…"
                    disabled={busy}
                  />
                </div>
              </>
            )}

            {dialog.type === 'move' && (
              <div className="form-field">
                <label htmlFor="fav-move-target">将 {dialog.label} 移动到</label>
                <Select
                  id="fav-move-target"
                  value={targetFolderId}
                  options={moveSelectOptions}
                  onChange={setTargetFolderId}
                  disabled={busy}
                />
              </div>
            )}

            {dialog.type === 'delete-item' && (
              <>
                <p className="admin-confirm-name">{dialog.item.title}</p>
                <p>确定删除这条收藏记录吗？删除后将无法恢复。</p>
              </>
            )}

            {dialog.type === 'delete-folder' && (
              <>
                <p className="admin-confirm-name">{dialog.folder.name}</p>
                <p>确定删除此文件夹吗？其包含的子文件夹与收藏项也会一并移除，此操作无法撤销。</p>
              </>
            )}

            {dialog.type === 'batch-delete' && (
              <p>
                确定删除选中的 {dialog.folderIds.length + dialog.itemIds.length}{' '}
                项内容吗？若包含文件夹，其内部子目录与收藏也会一并删除，此操作无法撤销。
              </p>
            )}

            {Boolean(actionError) && (
              <Notice kind="error" error={actionError}>
                {errorMessage(actionError)}
              </Notice>
            )}

            <div className="fav-dialog-actions">
              <button type="button" className="btn" disabled={busy} onClick={() => setDialog(null)}>
                取消
              </button>
              <button
                type="submit"
                className={`btn ${
                  dialog.type === 'delete-item' ||
                  dialog.type === 'delete-folder' ||
                  dialog.type === 'batch-delete'
                    ? 'btn-danger'
                    : 'btn-primary'
                }`}
                disabled={
                  busy ||
                  ((dialog.type === 'create-folder' || dialog.type === 'rename-folder') &&
                    !folderNameInput.trim()) ||
                  (dialog.type === 'edit-item' &&
                    (!editTitle.trim() || (dialog.item.kind === 'link' && !editUrl.trim())))
                }
              >
                {dialog.type === 'delete-item' ||
                dialog.type === 'delete-folder' ||
                dialog.type === 'batch-delete'
                  ? '确认删除'
                  : '确定'}
              </button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  )
}
