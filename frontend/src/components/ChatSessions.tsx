import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import {
  ChevronRight,
  Ellipsis,
  ListFilter,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'
import { errorMessage, type AuthClient } from '../lib/api'
import type { ChatSessionSummary } from '../lib/types'
import { Notice } from './Notice'

const minuteMs = 60_000
const sidebarSessionLimit = 6
const sessionDateFormat = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' })
const sessionTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium',
  timeStyle: 'short',
})
const relativeTimeFormat = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'always' })

function sessionTime(value: string, now: number) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null

  const minutesAgo = Math.floor((now - date.getTime()) / minuteMs)
  let label = sessionDateFormat.format(date)
  if (minutesAgo === 0) label = '刚刚'
  else if (minutesAgo > 0 && minutesAgo < 60)
    label = relativeTimeFormat.format(-minutesAgo, 'minute')
  else if (minutesAgo >= 60 && minutesAgo < 24 * 60)
    label = relativeTimeFormat.format(-Math.floor(minutesAgo / 60), 'hour')
  else if (date.getFullYear() !== new Date(now).getFullYear())
    label = `${date.getFullYear()}年${label}`

  return { label, full: sessionTimeFormat.format(date) }
}

function useChatSessions(
  client: AuthClient,
  revision: number,
  {
    archived = false,
    query = '',
    pinned,
    limit,
  }: {
    archived?: boolean
    query?: string
    pinned?: boolean
    limit?: number
  } = {},
) {
  const [items, setItems] = useState<ChatSessionSummary[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const active = useRef<AbortController | null>(null)

  useEffect(() => {
    setItems([])
  }, [client, archived, query, pinned, limit])

  useEffect(() => {
    const controller = new AbortController()
    active.current?.abort()
    active.current = controller
    setLoading(true)
    setError(null)
    setCursor(null)
    void client
      .listChatSessions({ archived, query, pinned, limit, signal: controller.signal })
      .then(
        (result) => {
          if (!controller.signal.aborted) {
            setItems(result.items)
            setCursor(result.next_cursor)
            setLoading(false)
          }
        },
        (failure) => {
          if (!controller.signal.aborted) {
            setError(failure)
            setLoading(false)
          }
        },
      )
    return () => active.current?.abort()
  }, [client, archived, query, pinned, limit, revision])

  async function loadMore() {
    if (cursor === null || loading) return
    const controller = new AbortController()
    active.current = controller
    setLoading(true)
    setError(null)
    try {
      const result = await client.listChatSessions({
        archived,
        query,
        pinned,
        limit,
        cursor,
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      setItems((current) => {
        const known = new Set(current.map((item) => item.id))
        return [...current, ...result.items.filter((item) => !known.has(item.id))]
      })
      setCursor(result.next_cursor)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure)
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  return { items, cursor, loading, error, loadMore }
}

type SessionList = ReturnType<typeof useChatSessions>

export type ChatSessionAction = 'pin' | 'rename' | 'delete'

type SessionSelection = {
  selectedId: string | null
  disabled: boolean
  onSelect: (session: ChatSessionSummary) => void
  onAction: (action: ChatSessionAction, session: ChatSessionSummary) => void
}

function SessionRow({
  item,
  selectedId,
  disabled,
  onSelect,
  onAction,
  now,
}: SessionSelection & {
  item: ChatSessionSummary
  now?: number
}) {
  const menuId = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const selected = selectedId === item.id
  const PinAction = item.pinned ? PinOff : Pin
  const time = now === undefined ? null : sessionTime(item.updated_at, now)

  return (
    <li className="chat-session-row" data-selected={selected || undefined}>
      <button
        type="button"
        className="chat-session-name"
        aria-label={item.title}
        aria-description={item.pinned ? '已置顶' : undefined}
        aria-current={selected ? 'true' : undefined}
        title={item.title}
        disabled={disabled}
        onClick={() => onSelect(item)}
      >
        {item.pinned && <Pin size={13} aria-hidden="true" />}
        <span>{item.title}</span>
      </button>
      {time && (
        <time className="chat-directory-time" dateTime={item.updated_at} title={time.full}>
          {time.label}
        </time>
      )}
      <button
        ref={trigger}
        type="button"
        className="icon-button chat-session-menu-trigger"
        aria-label={`会话操作 ${item.title}`}
        title="会话操作"
        popoverTarget={menuId}
        disabled={disabled}
      >
        <Ellipsis size={17} aria-hidden="true" />
      </button>
      <div
        className="chat-session-menu"
        id={menuId}
        popover="auto"
        role="group"
        aria-label="会话操作"
        onBeforeToggle={(event) => {
          if (
            event.newState !== 'open' ||
            CSS.supports('position-area: block-end span-inline-start')
          )
            return
          const bounds = trigger.current?.getBoundingClientRect()
          if (!bounds) return
          const above = innerHeight - bounds.bottom < 132 && bounds.top >= 132
          event.currentTarget.style.inset = above
            ? `auto ${innerWidth - bounds.right}px ${innerHeight - bounds.top}px auto`
            : `${bounds.bottom}px ${innerWidth - bounds.right}px auto auto`
        }}
        onToggle={(event) => {
          if (event.newState === 'open') event.currentTarget.querySelector('button')?.focus()
        }}
      >
        {(
          [
            ['pin', PinAction, item.pinned ? '取消置顶' : '置顶'],
            ['rename', Pencil, '重命名'],
            ['delete', Trash2, '删除'],
          ] as const
        ).map(([action, Icon, label]) => (
          <button
            key={action}
            type="button"
            className={action === 'delete' ? 'chat-session-delete' : undefined}
            disabled={disabled}
            onClick={(event) => {
              event.currentTarget.closest<HTMLElement>('[popover]')?.hidePopover()
              onAction(action, item)
            }}
          >
            <Icon size={15} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
    </li>
  )
}

function SidebarSessionGroup({
  label,
  emptyMessage,
  moreLabel,
  list: { items, error, loading, cursor, loadMore },
  ...selection
}: SessionSelection & {
  label: string
  emptyMessage?: string
  moreLabel?: string
  list: SessionList
}) {
  const headingId = useId()

  return (
    <section aria-labelledby={headingId}>
      <details className="sidebar-session-group" open>
        <summary id={headingId} className="nav-heading sidebar-session-heading">
          {label}
          <ChevronRight size={14} aria-hidden="true" />
        </summary>
        {items.length > 0 && (
          <ul className="sidebar-sessions-list">
            {items.map((item) => (
              <SessionRow key={item.id} item={item} {...selection} />
            ))}
          </ul>
        )}
        {Boolean(error) ? (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        ) : loading && items.length === 0 ? (
          <p className="sidebar-sessions-empty" role="status">
            正在读取会话…
          </p>
        ) : items.length === 0 && emptyMessage ? (
          <p className="sidebar-sessions-empty">{emptyMessage}</p>
        ) : null}
        {moreLabel && cursor !== null && (
          <button
            type="button"
            className="btn btn-ghost btn-sm sidebar-sessions-more"
            disabled={loading}
            onClick={() => void loadMore()}
          >
            {loading ? '正在读取…' : moreLabel}
          </button>
        )}
      </details>
    </section>
  )
}

export function SidebarChatSessions({
  client,
  revision,
  ...selection
}: SessionSelection & { client: AuthClient; revision: number }) {
  const pinned = useChatSessions(client, revision, { pinned: true, limit: sidebarSessionLimit })
  const recent = useChatSessions(client, revision, { pinned: false, limit: sidebarSessionLimit })

  return (
    <>
      {pinned.items.length > 0 ? (
        <SidebarSessionGroup label="已置顶" moreLabel="更多置顶会话" list={pinned} {...selection} />
      ) : Boolean(pinned.error) ? (
        <Notice kind="error" error={pinned.error}>
          {errorMessage(pinned.error)}
        </Notice>
      ) : null}
      <SidebarSessionGroup
        label="最近会话"
        emptyMessage="暂无最近会话。"
        list={recent}
        {...selection}
      />
    </>
  )
}

export function ChatSessions({
  client,
  selectedId,
  revision,
  disabled,
  onSelect,
  onAction,
  onNewSession,
}: SessionSelection & {
  client: AuthClient
  revision: number
  onNewSession: () => void
}) {
  const [archived, setArchived] = useState(false)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [openPanel, setOpenPanel] = useState<'search' | 'filter' | null>(null)
  const [now, setNow] = useState(Date.now)
  const searchButton = useRef<HTMLButtonElement>(null)
  const searchInputId = useId()
  const panelId = useId()
  const searchOpen = openPanel === 'search'
  const searchLabel = searchOpen ? '搜索' : '查找会话'
  const hasFilters = archived || query !== ''
  const { items, cursor, loading, error, loadMore } = useChatSessions(client, revision, {
    archived,
    query,
  })

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), minuteMs)
    return () => window.clearInterval(timer)
  }, [])

  function search(event: FormEvent) {
    event.preventDefault()
    if (!searchOpen) {
      setOpenPanel('search')
      return
    }
    const value = draft.trim()
    setQuery(value)
    if (!value) setOpenPanel(null)
  }

  return (
    <section className="workspace-body" aria-labelledby="chat-directory-title">
      <div className="workspace-thread chat-directory">
        <header className="chat-directory-header" data-search-open={searchOpen || undefined}>
          <div>
            <span className="page-kicker">Session History</span>
            <h1 id="chat-directory-title">全部会话</h1>
          </div>
          <div className="chat-directory-tools" role="group" aria-label="会话目录操作">
            <form
              onSubmit={search}
              className="chat-directory-search"
              role="search"
              aria-label="查找会话"
              onKeyDown={(event) => {
                if (!searchOpen || event.key !== 'Escape' || event.nativeEvent.isComposing) return
                event.preventDefault()
                setOpenPanel(null)
                searchButton.current?.focus()
              }}
            >
              {searchOpen && (
                <div className="input-wrap">
                  <input
                    id={searchInputId}
                    type="search"
                    aria-label="搜索会话"
                    value={draft}
                    onChange={(event) => {
                      const value = event.target.value
                      setDraft(value)
                      if (!value.trim()) setQuery('')
                    }}
                    maxLength={100}
                    placeholder="制度、文号或问题"
                    autoFocus
                  />
                </div>
              )}
              <button
                ref={searchButton}
                type="submit"
                className="icon-button"
                aria-label={searchLabel}
                title={searchLabel}
                aria-expanded={searchOpen}
                aria-controls={searchOpen ? searchInputId : undefined}
                aria-description={query ? `当前搜索词为 ${query}` : undefined}
                data-active={query !== '' || undefined}
              >
                <Search size={17} aria-hidden="true" />
              </button>
            </form>
            <button
              type="button"
              className="icon-button"
              aria-label="筛选会话"
              title="筛选会话"
              aria-expanded={openPanel === 'filter'}
              aria-controls={openPanel === 'filter' ? panelId : undefined}
              aria-description={archived ? '正在查看已归档会话' : undefined}
              data-active={archived || undefined}
              onClick={() => setOpenPanel(openPanel === 'filter' ? null : 'filter')}
            >
              <ListFilter size={17} aria-hidden="true" />
            </button>
            <button
              className="btn btn-primary"
              type="button"
              disabled={disabled}
              onClick={onNewSession}
            >
              <Plus size={15} aria-hidden="true" />
              新会话
            </button>
          </div>
        </header>

        {openPanel === 'filter' && (
          <div id={panelId} className="chat-directory-panel">
            <label className="chat-directory-archived">
              <input
                type="checkbox"
                checked={archived}
                onChange={(event) => setArchived(event.target.checked)}
                autoFocus
              />
              已归档
            </label>
          </div>
        )}

        {items.length > 0 && (
          <ul className="chat-directory-list">
            {items.map((item) => (
              <SessionRow
                key={item.id}
                item={item}
                selectedId={selectedId}
                disabled={disabled}
                onSelect={onSelect}
                onAction={onAction}
                now={now}
              />
            ))}
          </ul>
        )}

        {Boolean(error) && (
          <Notice kind="error" error={error}>
            {errorMessage(error)}
          </Notice>
        )}
        {loading && (
          <p className="chat-directory-status" role="status">
            正在读取会话…
          </p>
        )}
        {!loading && !error && items.length === 0 && (
          <p className="chat-directory-status">{hasFilters ? '没有匹配的会话。' : '暂无会话。'}</p>
        )}
        {cursor !== null && (
          <button
            type="button"
            className="btn btn-sm"
            disabled={loading}
            onClick={() => void loadMore()}
          >
            更多会话
          </button>
        )}
      </div>
    </section>
  )
}
