import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Folder, FolderPlus, Star } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { errorMessage } from '../lib/api'
import type {
  FavoriteCreateInput,
  FavoriteFolderSummary,
  FavoriteItem,
  FavoriteKind,
} from '../lib/types'
import { Dialog } from './Dialog'
import { Notice } from './Notice'
import { Select, type SelectOption } from './Select'

export function buildFolderSelectOptions(
  folders: FavoriteFolderSummary[],
  excludeIds?: ReadonlySet<string>,
): SelectOption<string>[] {
  const byParent = new Map<string | null, FavoriteFolderSummary[]>()
  for (const folder of folders) {
    if (excludeIds?.has(folder.id)) continue
    const list = byParent.get(folder.parent_id) ?? []
    list.push(folder)
    byParent.set(folder.parent_id, list)
  }
  const options: SelectOption<string>[] = [
    {
      value: '',
      label: '收藏夹栏（根目录）',
      icon: <Star size={14} />,
      depth: 0,
    },
  ]
  const walk = (parentId: string | null, depth: number) => {
    for (const folder of byParent.get(parentId) ?? []) {
      options.push({
        value: folder.id,
        label: folder.name,
        depth,
        icon: <Folder size={14} />,
        meta: folder.item_count > 0 ? String(folder.item_count) : undefined,
      })
      walk(folder.id, depth + 1)
    }
  }
  walk(null, 1)
  return options
}

export type SaveFavoriteTarget =
  | {
      kind: 'chunk'
      chunkId: number
      defaultTitle: string
      defaultFolderId?: string | null
    }
  | {
      kind: 'qa'
      sessionId: string
      turnId: string
      defaultTitle: string
      defaultFolderId?: string | null
    }
  | {
      kind: 'link'
      defaultTitle?: string
      defaultUrl?: string
      defaultFolderId?: string | null
    }

const kindTitles: Record<FavoriteKind, string> = {
  chunk: '收藏条文',
  qa: '收藏问答',
  link: '添加外部链接',
}

export function SaveFavoriteDialog({
  target,
  onClose,
  onSaved,
}: {
  target: SaveFavoriteTarget
  onClose: () => void
  onSaved: (item: FavoriteItem) => void
}) {
  const { client } = useAuth()
  const [folders, setFolders] = useState<FavoriteFolderSummary[]>([])
  const [folderId, setFolderId] = useState<string>(target.defaultFolderId ?? '')
  const [title, setTitle] = useState(
    target.kind === 'link' ? (target.defaultTitle ?? '') : target.defaultTitle.slice(0, 255),
  )
  const [url, setUrl] = useState(target.kind === 'link' ? (target.defaultUrl ?? '') : '')
  const [note, setNote] = useState('')
  const [creatingFolder, setCreatingFolder] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let active = true
    client
      .getFavoriteTree()
      .then((tree) => {
        if (active) setFolders(tree.folders)
      })
      .catch(() => {
        // Users can still save to root if the tree failed to load.
      })
    return () => {
      active = false
    }
  }, [client])

  const folderOptions = useMemo(() => buildFolderSelectOptions(folders), [folders])
  const selectedFolderName = useMemo(
    () => folders.find((folder) => folder.id === folderId)?.name ?? '收藏夹栏',
    [folders, folderId],
  )

  async function handleQuickCreateFolder() {
    const trimmed = newFolderName.trim()
    if (!trimmed || busy) return
    setBusy(true)
    setError(null)
    try {
      const created = await client.createFavoriteFolder({
        name: trimmed,
        parent_id: folderId || null,
      })
      setFolders((previous) => [...previous, created])
      setFolderId(created.id)
      setNewFolderName('')
      setCreatingFolder(false)
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const trimmedTitle = title.trim()
    if (!trimmedTitle) return
    if (target.kind === 'link' && !url.trim()) return

    setBusy(true)
    setError(null)
    try {
      const payload: FavoriteCreateInput =
        target.kind === 'chunk'
          ? {
              kind: 'chunk',
              chunk_id: target.chunkId,
              folder_id: folderId || null,
              title: trimmedTitle,
              note: note.trim() || null,
            }
          : target.kind === 'qa'
            ? {
                kind: 'qa',
                session_id: target.sessionId,
                turn_id: target.turnId,
                folder_id: folderId || null,
                title: trimmedTitle,
                note: note.trim() || null,
              }
            : {
                kind: 'link',
                folder_id: folderId || null,
                title: trimmedTitle,
                url: url.trim(),
                note: note.trim() || null,
              }
      const saved = await client.createFavorite(payload)
      onSaved(saved)
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog title={kindTitles[target.kind]} kicker="Favorites" busy={busy} onClose={onClose}>
      <form className="fav-dialog-form" onSubmit={submit}>
        <div className="form-field">
          <label htmlFor="fav-title">标题</label>
          <div className="input-wrap">
            <input
              id="fav-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={255}
              placeholder="输入便于查找的标题"
              disabled={busy}
              required
              autoFocus
            />
          </div>
        </div>

        {target.kind === 'link' && (
          <div className="form-field">
            <label htmlFor="fav-url">链接地址</label>
            <div className="input-wrap">
              <input
                id="fav-url"
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                maxLength={2048}
                placeholder="https://www.pbc.gov.cn/..."
                disabled={busy}
                required
              />
            </div>
          </div>
        )}

        <div className="form-field">
          <div className="fav-field-head">
            <label htmlFor="fav-folder">保存到文件夹</label>
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => setCreatingFolder((value) => !value)}
            >
              <FolderPlus size={13} aria-hidden="true" />
              {creatingFolder ? '取消新建' : '新建文件夹'}
            </button>
          </div>
          <Select
            id="fav-folder"
            value={folderId}
            options={folderOptions}
            onChange={setFolderId}
            disabled={busy}
          />
          {creatingFolder && (
            <div className="fav-inline-create">
              <input
                value={newFolderName}
                onChange={(event) => setNewFolderName(event.target.value)}
                maxLength={80}
                placeholder={`在「${selectedFolderName}」下新建文件夹…`}
                disabled={busy}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    void handleQuickCreateFolder()
                  }
                }}
              />
              <button
                type="button"
                className="btn"
                disabled={busy || !newFolderName.trim()}
                onClick={() => void handleQuickCreateFolder()}
              >
                创建
              </button>
            </div>
          )}
        </div>

        <div className="form-field">
          <label htmlFor="fav-note">备注（可选）</label>
          <textarea
            id="fav-note"
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={500}
            placeholder="补充适用场景或记忆要点…"
            disabled={busy}
          />
        </div>

        {Boolean(error) && (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        )}

        <div className="fav-dialog-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !title.trim() || (target.kind === 'link' && !url.trim())}
          >
            {busy ? '保存中…' : '保存收藏'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
