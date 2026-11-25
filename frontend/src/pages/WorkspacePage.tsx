import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ArrowUpRight,
  FolderOpen,
  Info,
  Pencil,
  Square,
} from 'lucide-react'
import { Link, Outlet, useLocation, useMatch, useNavigate, type Path } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { AppLayout } from '../components/AppLayout'
import { AuthDialog } from '../components/AuthDialog'
import { ChatAnswer } from '../components/ChatAnswer'
import {
  ChatSessions,
  SidebarChatSessions,
  type ChatSessionAction,
} from '../components/ChatSessions'
import { Dialog } from '../components/Dialog'
import { Notice } from '../components/Notice'
import { SaveFavoriteDialog, type SaveFavoriteTarget } from '../components/SaveFavoriteDialog'
import { ApiError, errorMessage } from '../lib/api'
import type {
  ChatExecutionStep,
  ChatSessionSummary,
  ChatSessionUpdate,
  ChatToolStep,
  ChatTurn,
  FavoriteSourceMatch,
} from '../lib/types'

const pendingTurnId = 'pending'
const toolLabels: Record<ChatToolStep['tool'], string> = {
  search_knowledge: '检索相关制度',
  get_article: '读取制度条文',
  get_document_status: '核对制度版本与时效',
  reuse_answer_evidence: '复核上一轮引用',
}
const toolStates: Record<ChatToolStep['state'], string> = {
  running: '进行中',
  completed: '已完成',
  failed: '未取得结果',
  interrupted: '已中断',
}

type UnconfirmedTurn = {
  sessionId: string
  turnId?: string
  previousTurnId?: string
  question: string
  draft: string
}

type WorkspaceTurn = ChatTurn & {
  preview?: string
}

function stepKey(step: ChatExecutionStep) {
  return 'attempt' in step ? `model:${step.attempt}` : `tool:${step.id}`
}

function recordStep(steps: ChatExecutionStep[], update: ChatExecutionStep) {
  const key = stepKey(update)
  return steps.some((step) => stepKey(step) === key)
    ? steps.map((step) => (stepKey(step) === key ? update : step))
    : [...steps, update]
}

function ExecutionTimeline({ turn }: { turn: WorkspaceTurn }) {
  const steps = turn.execution ?? turn.tool_steps
  if (!steps?.length) return null
  return (
    <ol
      className="chat-tool-steps"
      aria-label="执行过程"
      aria-live={turn.state === 'running' ? 'polite' : undefined}
    >
      {steps.map((step) => (
        <li key={stepKey(step)}>
          <span className="chat-step-main">
            <span className={`step-dot step-dot-${step.state}`} aria-hidden="true" />
            <span>
              {'attempt' in step ? `第 ${step.attempt} 次模型调用` : toolLabels[step.tool]}
            </span>
          </span>
          <span className="chat-step-state" data-state={step.state}>
            {'attempt' in step && step.state === 'failed' ? '调用失败' : toolStates[step.state]}
          </span>
          {'tool' in step && (step.target || step.summary) && (
            <div className="chat-execution-details">
              {step.target && <span>{step.target}</span>}
              {step.summary && <span>{step.summary}</span>}
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}

function turnStatus(turn: WorkspaceTurn, stage: 'preparing' | 'generating' | null) {
  if (stage === null) return '上一轮正在收尾…'
  const step = turn.execution?.at(-1)
  if (step?.state === 'running' && 'tool' in step) return '正在查询制度…'
  if (stage === 'generating') return '正在生成回答…'
  if (step?.state === 'running') return '正在请求模型…'
  return '正在准备回答…'
}

function readTabValue(key: string | null): string | null {
  if (!key) return null
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function rememberTabValue(key: string | null, value: string | null) {
  if (!key) return
  try {
    if (value !== null) sessionStorage.setItem(key, value)
    else sessionStorage.removeItem(key)
  } catch {
    // The current page can continue using its in-memory state.
  }
}

type Drafts = Record<string, string>
const newDraftSlot = 'new'
const guestDraftSlot = 'guest'
const guestDraftKey = 'finoracle.chat.guest.v1'

function readDrafts(key: string | null): Drafts {
  try {
    const saved: unknown = JSON.parse(readTabValue(key) ?? 'null')
    if (saved && typeof saved === 'object' && !Array.isArray(saved))
      return Object.fromEntries(
        Object.entries(saved)
          .filter((entry): entry is [string, string] => typeof entry[1] === 'string')
          .map(([slot, text]) => [slot, text.slice(0, 2000)]),
      )
  } catch {
    // A malformed stored draft should leave the composer usable.
  }
  return {}
}

function replaceDraft(drafts: Drafts, slot: string, text: string): Drafts {
  if ((drafts[slot] ?? '') === text) return drafts
  const updated = { ...drafts }
  if (text) updated[slot] = text
  else delete updated[slot]
  return updated
}

export function WorkspacePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const prompt = location.state as {
    authMode?: 'login' | 'register'
    from?: Path
    sessionId?: string
  } | null
  const [guestQuestion, setGuestQuestion] = useState(
    () => readTabValue(guestDraftKey)?.slice(0, 2000) ?? '',
  )

  useLayoutEffect(() => {
    rememberTabValue(guestDraftKey, guestQuestion || null)
  }, [guestQuestion])

  return (
    <>
      <Workspace
        key={user?.id ?? 'guest'}
        guestQuestion={guestQuestion}
        setGuestQuestion={setGuestQuestion}
        requestedSessionId={prompt?.sessionId}
      />
      {!user && prompt?.authMode && (
        <AuthDialog
          initialMode={prompt.authMode}
          onClose={() => navigate('/chat', { replace: true })}
          onAuthenticated={() => navigate(prompt.from ?? '/chat', { replace: true })}
        />
      )}
    </>
  )
}

function Workspace({
  guestQuestion,
  setGuestQuestion,
  requestedSessionId,
}: {
  guestQuestion: string
  setGuestQuestion: (text: string) => void
  requestedSessionId?: string
}) {
  const { client, user } = useAuth()
  const navigate = useNavigate()
  const showChat = Boolean(useMatch('/chat'))
  const showHistory = Boolean(useMatch('/history') && user)
  const inWorkspace = showChat || showHistory
  const draftsKey = user ? `finoracle.chat.drafts.v1:${user.id}` : null
  const sessionKey = user ? `finoracle.chat.session.v1:${user.id}` : null
  const [sessionId, setSessionId] = useState(() =>
    guestQuestion.trim() ? null : readTabValue(sessionKey),
  )
  const [restoreId, setRestoreId] = useState(sessionId)
  const [restoreError, setRestoreError] = useState<unknown>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [restoring, setRestoring] = useState(Boolean(restoreId))
  const [turns, setTurns] = useState<WorkspaceTurn[]>([])
  const [sessionSummary, setSessionSummary] = useState<ChatSessionSummary | null>(null)
  const [sessionListRevision, setSessionListRevision] = useState(0)
  const [sessionDialog, setSessionDialog] = useState<{
    action: 'rename' | 'delete'
    item: ChatSessionSummary
  } | null>(null)
  const [sessionTitle, setSessionTitle] = useState('')
  const [sessionActionError, setSessionActionError] = useState<unknown>(null)
  const [managing, setManaging] = useState(false)
  const [historyPage, setHistoryPage] = useState<{ before: number | null; through: number } | null>(
    null,
  )
  const [olderTurns, setOlderTurns] = useState<ChatTurn[]>([])
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [historyError, setHistoryError] = useState<unknown>(null)
  const selectedSession = useRef(sessionId)
  const sessionSummaryRevision = useRef(0)
  const historyHead = useRef<string | null>(null)
  const [drafts, setDrafts] = useState(() => {
    const saved = readDrafts(draftsKey)
    return user && guestQuestion.trim() ? replaceDraft(saved, guestDraftSlot, guestQuestion) : saved
  })
  const [activeNewDraft, setActiveNewDraft] = useState(() =>
    drafts[guestDraftSlot] ? guestDraftSlot : newDraftSlot,
  )
  const draftSlot = sessionId ?? activeNewDraft
  const question = user ? (drafts[draftSlot] ?? '') : guestQuestion
  const [stage, setStage] = useState<'preparing' | 'generating' | null>(null)
  const [stopped, setStopped] = useState(false)
  const [askError, setAskError] = useState<unknown>(null)
  const [savedTurns, setSavedTurns] = useState<Record<string, FavoriteSourceMatch>>({})
  const [favSaveTarget, setFavSaveTarget] = useState<SaveFavoriteTarget | null>(null)
  const [favBusyKey, setFavBusyKey] = useState<string | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const unconfirmedTurn = useRef<UnconfirmedTurn | null>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const messageScroll = useRef<HTMLElement>(null)
  const followingReply = useRef(false)
  const wasAsking = useRef(false)
  const asking = stage !== null
  const sessionControlsDisabled = asking || managing
  const latestTurn = turns.at(-1)
  const waiting = restoring || latestTurn?.state === 'running'
  const archived = sessionSummary?.archived === true
  const currentTurnIds = new Set(turns.map((turn) => turn.id))
  const displayedTurns: WorkspaceTurn[] = [
    ...olderTurns.filter((turn) => !currentTurnIds.has(turn.id)),
    ...turns,
  ]

  useEffect(() => {
    if (!inWorkspace) activeRequest.current?.abort()
  }, [inWorkspace])

  useEffect(() => {
    if (!user || !showChat || !requestedSessionId) return
    if (requestedSessionId !== selectedSession.current) {
      resetSession({
        id: requestedSessionId,
        title: '',
        archived: false,
        pinned: false,
        updated_at: new Date().toISOString(),
      })
    }
    navigate('/chat', { replace: true, state: null })
  }, [navigate, requestedSessionId, showChat, user])

  const completedTurnIdsKey = displayedTurns
    .filter((turn) => turn.state !== 'running' && turn.result && turn.id !== pendingTurnId)
    .map((turn) => turn.id)
    .join(',')

  useEffect(() => {
    if (!user || !showChat || !completedTurnIdsKey) {
      setSavedTurns({})
      return
    }
    const controller = new AbortController()
    const keys = completedTurnIdsKey.split(',').map((id) => `qa:${id}`)
    client
      .lookupFavorites(keys, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) setSavedTurns(res.items)
      })
      .catch(() => {
        // Non-blocking lookup
      })
    return () => controller.abort()
  }, [client, completedTurnIdsKey, showChat, user])

  async function toggleTurnFavorite(turn: WorkspaceTurn) {
    if (!sessionId || turn.id === pendingTurnId || favBusyKey) return
    const key = `qa:${turn.id}`
    const existing = savedTurns[key]
    if (existing) {
      setFavBusyKey(key)
      try {
        await client.deleteFavorite(existing.id)
        setSavedTurns((previous) => {
          const next = { ...previous }
          delete next[key]
          return next
        })
      } catch (error) {
        setSessionActionError(error)
      } finally {
        setFavBusyKey(null)
      }
      return
    }
    setFavSaveTarget({
      kind: 'qa',
      sessionId,
      turnId: turn.id,
      defaultTitle: turn.question.slice(0, 255),
    })
  }

  useLayoutEffect(() => {
    rememberTabValue(draftsKey, Object.keys(drafts).length ? JSON.stringify(drafts) : null)
  }, [draftsKey, drafts])

  useLayoutEffect(() => {
    if (!sessionKey || !guestQuestion) return
    if (guestQuestion.trim()) rememberTabValue(sessionKey, null)
    setGuestQuestion('')
  }, [guestQuestion, sessionKey, setGuestQuestion])

  useLayoutEffect(() => {
    if (!showChat) return
    const active = asking || wasAsking.current
    wasAsking.current = asking
    const scroll = messageScroll.current
    if (active && followingReply.current && scroll) scroll.scrollTop = scroll.scrollHeight
  }, [asking, latestTurn, stage, showChat])

  useEffect(() => {
    if (!inWorkspace || !restoreId) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      const summaryRevision = sessionSummaryRevision.current
      try {
        const history = await client.getChatSession(restoreId)
        if (!active || selectedSession.current !== restoreId) return
        if (sessionSummaryRevision.current === summaryRevision) setSessionSummary(history)
        const head = history.turns[0]?.id ?? null
        if (head !== historyHead.current) {
          historyHead.current = head
          setOlderTurns([])
          setHistoryPage({ before: history.next_before_seq, through: history.through_seq })
        }
        setTurns((previous) => {
          const localTurns = new Map(previous.map((turn) => [turn.id, turn]))
          return history.turns.map((turn) => {
            const local = localTurns.get(turn.id)
            const savedSteps = turn.execution ?? turn.tool_steps ?? []
            return {
              ...turn,
              result: turn.result ?? (turn.state !== 'completed' ? (local?.result ?? null) : null),
              execution: savedSteps.reduce(recordStep, local?.execution ?? []),
            }
          })
        })
        setRestoreError(null)
        const latest = history.turns.at(-1)
        const expected = unconfirmedTurn.current
        if (expected?.sessionId === restoreId) {
          if (latest?.state !== 'running') setSessionListRevision((value) => value + 1)
          const recovered = expected.turnId
            ? history.turns.find((turn) => turn.id === expected.turnId)
            : latest
          if (
            recovered?.state === 'completed' &&
            (expected.turnId ||
              (recovered.id !== expected.previousTurnId &&
                recovered.question === expected.question))
          ) {
            unconfirmedTurn.current = null
            setAskError(null)
            setStopped(false)
            setDrafts((current) =>
              current[restoreId] === expected.draft
                ? replaceDraft(current, restoreId, '')
                : current,
            )
          } else if (latest?.state !== 'running') {
            unconfirmedTurn.current = null
          }
        }
        if (latest?.state === 'running') timer = setTimeout(load, 1000)
      } catch (error) {
        if (!active || selectedSession.current !== restoreId) return
        if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
          rememberTabValue(sessionKey, null)
          setDrafts((current) => replaceDraft(current, restoreId, ''))
          unconfirmedTurn.current = null
          setSessionId(null)
          selectedSession.current = null
          historyHead.current = null
          setRestoreId(null)
          setRestoreError(null)
          setTurns([])
          setSessionSummary(null)
          setOlderTurns([])
          setHistoryPage(null)
        } else {
          setRestoreError(error)
        }
      } finally {
        if (active && (selectedSession.current === restoreId || selectedSession.current === null))
          setRestoring(false)
      }
    }
    void load()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [client, sessionKey, restoreId, restoreAttempt, inWorkspace])

  useEffect(
    () => () => {
      activeRequest.current?.abort()
      activeRequest.current = null
    },
    [],
  )

  const starters = [
    {
      title: '先厘清制度范围',
      description: '业务背景 / 适用对象 / 待确认事项',
      question: '我想了解【业务场景】下适用的制度要求，重点关注【待确认事项】。',
    },
    {
      title: '从具体条款出发',
      description: '制度名称 / 条文编号 / 理解难点',
      question: '关于【制度名称】中的【条文编号】，我希望进一步理解【具体问题】。',
    },
  ]

  function updateQuestion(text: string) {
    if (user) setDrafts((current) => replaceDraft(current, draftSlot, text))
    else setGuestQuestion(text)
    setAskError(null)
    setStopped(false)
  }

  function chooseStarter(text: string) {
    updateQuestion(text)
    textarea.current?.focus()
  }

  function requestLogin() {
    navigate('/chat', { replace: true, state: { authMode: 'login' } })
  }

  function selectSession(summary: ChatSessionSummary | null) {
    if (!user) {
      requestLogin()
      return
    }
    if (!showChat) navigate('/chat')
    else if (summary === null) textarea.current?.focus({ preventScroll: true })
    if (summary === null && !drafts[guestDraftSlot]) setActiveNewDraft(newDraftSlot)
    resetSession(summary)
  }

  function resetSession(summary: ChatSessionSummary | null) {
    const id = summary?.id ?? null
    if (id === sessionId) return
    if (messageScroll.current) messageScroll.current.scrollTop = 0
    selectedSession.current = id
    historyHead.current = null
    rememberTabValue(sessionKey, id)
    unconfirmedTurn.current = null
    setSessionId(id)
    setRestoreId(id)
    setRestoreError(null)
    setRestoring(Boolean(id))
    setTurns([])
    setSessionSummary(summary)
    setHistoryPage(null)
    setOlderTurns([])
    setLoadingOlder(false)
    setHistoryError(null)
    setSessionActionError(null)
    setAskError(null)
    setStopped(false)
  }

  async function manageSession(id: string, change: ChatSessionUpdate | 'delete') {
    if (managing || asking) return
    setManaging(true)
    setSessionActionError(null)
    try {
      if (change === 'delete') {
        await client.deleteChatSession(id)
        setDrafts((current) => replaceDraft(current, id, ''))
        if (selectedSession.current === id) resetSession(null)
      } else {
        const updated = await client.updateChatSession(id, change)
        if (selectedSession.current === id) {
          sessionSummaryRevision.current += 1
          setSessionSummary(updated)
        }
      }
      setSessionDialog(null)
      setSessionListRevision((value) => value + 1)
    } catch (error) {
      setSessionActionError(error)
    } finally {
      setManaging(false)
    }
  }

  function handleSessionAction(action: ChatSessionAction, item: ChatSessionSummary) {
    if (action === 'pin') {
      void manageSession(item.id, { pinned: !item.pinned })
      return
    }
    setSessionActionError(null)
    setSessionTitle(item.title)
    setSessionDialog({ action, item })
  }

  function closeSessionDialog() {
    setSessionDialog(null)
    setSessionActionError(null)
  }

  async function loadOlderTurns() {
    if (!sessionId || historyPage?.before == null || loadingOlder) return
    const head = historyHead.current
    setLoadingOlder(true)
    setHistoryError(null)
    try {
      const history = await client.getChatSession(sessionId, {
        before: historyPage.before,
        through: historyPage.through,
      })
      if (selectedSession.current !== sessionId || historyHead.current !== head) return
      setOlderTurns((current) => [...history.turns, ...current])
      setHistoryPage({ before: history.next_before_seq, through: history.through_seq })
    } catch (error) {
      if (selectedSession.current === sessionId) setHistoryError(error)
    } finally {
      if (selectedSession.current === sessionId) setLoadingOlder(false)
    }
  }

  function updateTurn(id: string, update: (turn: WorkspaceTurn) => WorkspaceTurn) {
    setTurns((previous) => previous.map((turn) => (turn.id === id ? update(turn) : turn)))
  }

  async function submitQuestion() {
    const text = question.trim()
    if (!text || activeRequest.current || waiting || restoreError || archived || managing) return
    if (!user) {
      requestLogin()
      return
    }
    const controller = new AbortController()
    activeRequest.current = controller
    followingReply.current = true
    updateQuestion('')
    setStage('preparing')
    setTurns((previous) => [
      ...previous,
      {
        id: pendingTurnId,
        question: text,
        state: 'running',
        result: null,
      },
    ])
    let id = sessionId
    let turnId: string | undefined
    try {
      if (id === null) {
        const created = await client.createChatSession(controller.signal)
        controller.signal.throwIfAborted()
        if (activeRequest.current !== controller) return
        id = created.id
        selectedSession.current = id
        setSessionId(id)
        setActiveNewDraft(newDraftSlot)
        rememberTabValue(sessionKey, id)
      }
      const result = await client.askStream(
        text,
        (update) => {
          if (activeRequest.current !== controller) return
          if (update.type === 'turn') {
            turnId = update.turn_id
            updateTurn(pendingTurnId, (turn) => ({ ...turn, id: update.turn_id }))
            return
          }
          if (update.type === 'progress') {
            setStage(update.stage === 'generating' ? 'generating' : 'preparing')
            return
          }
          if (update.type === 'tool' || update.type === 'model') {
            updateTurn(turnId ?? pendingTurnId, (turn) => ({
              ...turn,
              preview:
                update.step.state === 'running' ||
                (update.type === 'model' && update.step.state !== 'completed')
                  ? undefined
                  : turn.preview,
              execution: recordStep(turn.execution ?? [], update.step),
            }))
            return
          }
          if (update.type === 'answer_delta') {
            updateTurn(turnId ?? pendingTurnId, (turn) => ({
              ...turn,
              preview: (turn.preview ?? '') + update.text,
            }))
            return
          }
          updateTurn(turnId ?? pendingTurnId, (turn) => {
            return {
              ...turn,
              result: {
                status: 'partial',
                answer: turn.result ? `${turn.result.answer}\n\n${update.text}` : update.text,
                citations: [...(turn.result?.citations ?? []), ...update.citations],
              },
            }
          })
        },
        controller.signal,
        id,
        globalThis.crypto?.randomUUID?.(),
      )
      if (activeRequest.current === controller) {
        const completedId = result.turn_id ?? turnId
        if (!completedId)
          throw new ApiError('会话未能正确保存，请重新加载。', 502, 'INVALID_RESPONSE')
        updateTurn(turnId ?? pendingTurnId, (turn) => ({
          ...turn,
          id: completedId,
          state: 'completed',
          preview: undefined,
          result,
        }))
        setSessionListRevision((value) => value + 1)
        if (!sessionSummary?.title) {
          setSessionSummary({
            id,
            title: text.slice(0, 80),
            archived: false,
            pinned: false,
            updated_at: new Date().toISOString(),
          })
        }
      }
    } catch (error) {
      if (activeRequest.current === controller) {
        const interrupted = controller.signal.aborted
        const retrySlot = id ?? draftSlot
        setDrafts((current) => replaceDraft(current, retrySlot, question))
        if (turnId) {
          updateTurn(turnId, (turn) => ({
            ...turn,
            state: interrupted ? 'interrupted' : 'failed',
            preview: undefined,
            execution: turn.execution?.map((step) =>
              step.state === 'running' ? { ...step, state: 'interrupted' } : step,
            ),
          }))
        } else {
          setTurns((previous) => previous.filter((turn) => turn.id !== pendingTurnId))
        }
        if (interrupted) setStopped(true)
        else setAskError(error)
        if (id) {
          unconfirmedTurn.current = {
            sessionId: id,
            turnId,
            previousTurnId: turns.at(-1)?.id,
            question: text,
            draft: question,
          }
          setRestoring(true)
          setRestoreId(id)
          setRestoreAttempt((value) => value + 1)
        }
      }
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = null
        setStage(null)
      }
    }
  }

  function ask(event: FormEvent) {
    event.preventDefault()
    void submitQuestion()
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submitQuestion()
    }
  }

  function stop() {
    activeRequest.current?.abort()
  }

  const hasConversation =
    displayedTurns.length > 0 ||
    Boolean(sessionSummary?.title) ||
    restoring ||
    Boolean(restoreError)

  const composerSection = (
    <div className="workspace-composer-dock">
      <form className="composer" onSubmit={ask}>
        <label className="sr-only" htmlFor="question">
          你想了解什么？
        </label>
        <textarea
          ref={textarea}
          id="question"
          rows={2}
          enterKeyHint="send"
          placeholder="输入制度问题，或描述你的业务场景…"
          value={question}
          onChange={(event) => updateQuestion(event.target.value)}
          onKeyDown={onComposerKeyDown}
          maxLength={2000}
          readOnly={asking}
          aria-describedby="question-help"
        />
        <div className="composer-bottom">
          <span className="composer-scope-pill">
            <FolderOpen size={13} aria-hidden="true" />
            全部已发布制度
          </span>
          {asking ? (
            <button key="stop" className="btn btn-danger" type="button" onClick={stop}>
              <Square size={12} aria-hidden="true" />
              停止生成
            </button>
          ) : (
            <button
              key="send"
              className="btn btn-primary"
              type="submit"
              disabled={
                !question.trim() || waiting || Boolean(restoreError) || archived || managing
              }
            >
              发送问题
              <ArrowUpRight size={15} aria-hidden="true" />
            </button>
          )}
        </div>
      </form>
      <div className="composer-caption">
        <span id="question-help">
          <Info size={12} aria-hidden="true" />
          回答附带制度出处，可打开引用核对原文。
        </span>
        <span className="composer-count">{question.length} / 2000</span>
      </div>
      {Boolean(askError) && (
        <div style={{ marginTop: 10 }}>
          <Notice kind="error" error={askError}>
            {errorMessage(askError)} 可用同一问题重试。
          </Notice>
        </div>
      )}
      {stopped && (
        <p className="chat-turn-state-note" role="status" style={{ marginTop: 10 }}>
          已停止生成。
        </p>
      )}
    </div>
  )

  const deletingSession = sessionDialog?.action === 'delete'
  const toolbarTitle = showHistory ? (
    <Link className="workspace-back-link" to="/chat">
      <ArrowLeft size={14} aria-hidden="true" />
      返回问答
    </Link>
  ) : (
    <div className="workspace-session-meta">
      <strong className="workspace-session-title" title={sessionSummary?.title}>
        {sessionSummary?.title || '智能问答'}
      </strong>
      {archived && <span className="tag tag-neutral">已归档</span>}
    </div>
  )
  const toolbarActions = showChat && sessionSummary?.title && (
    <div className="workspace-header-actions">
      <button
        type="button"
        className="icon-button"
        disabled={managing || asking}
        aria-label="重命名"
        title="重命名"
        onClick={() => handleSessionAction('rename', sessionSummary)}
      >
        <Pencil size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="icon-button"
        disabled={managing || asking || waiting}
        aria-label={archived ? '恢复会话' : '归档会话'}
        title={archived ? '恢复会话' : '归档会话'}
        onClick={() => void manageSession(sessionSummary.id, { archived: !archived })}
      >
        {archived ? (
          <ArchiveRestore size={16} aria-hidden="true" />
        ) : (
          <Archive size={16} aria-hidden="true" />
        )}
      </button>
    </div>
  )

  return (
    <AppLayout
      workspace={{
        onNewSession: () => selectSession(null),
        sessionControlsDisabled,
        toolbar: inWorkspace ? { title: toolbarTitle, actions: toolbarActions } : undefined,
        sessions: (closeMenu) => (
          <SidebarChatSessions
            client={client}
            revision={sessionListRevision}
            selectedId={showChat ? sessionId : null}
            disabled={sessionControlsDisabled}
            onAction={handleSessionAction}
            onSelect={(summary) => {
              selectSession(summary)
              closeMenu()
            }}
          />
        ),
      }}
    >
      {!sessionDialog && Boolean(sessionActionError) && (
        <Notice kind="error" error={sessionActionError}>
          {errorMessage(sessionActionError)}
        </Notice>
      )}
      {showHistory ? (
        <ChatSessions
          client={client}
          selectedId={sessionId}
          revision={sessionListRevision}
          disabled={sessionControlsDisabled}
          onAction={handleSessionAction}
          onSelect={selectSession}
          onNewSession={() => selectSession(null)}
        />
      ) : showChat ? (
        <div className="workspace-shell">
          <section
            ref={messageScroll}
            className="workspace-body"
            aria-label="问答内容"
            tabIndex={0}
            onScroll={(event) => {
              if (!asking) return
              const { scrollHeight, scrollTop, clientHeight } = event.currentTarget
              followingReply.current = scrollHeight - scrollTop - clientHeight <= 64
            }}
          >
            {!hasConversation ? (
              <div className="workspace-thread workspace-welcome">
                <div className="workspace-welcome-artwork" aria-hidden="true">
                  <div className="workspace-welcome-grid" />
                  <span className="artwork-cross artwork-cross-tl">+</span>
                  <span className="artwork-cross artwork-cross-br">+</span>
                  <div className="artwork-frame" />
                  <div className="artwork-signal" />
                  <span className="artwork-ruler">01 — POLICY WORKSPACE</span>
                </div>
                <header className="workspace-welcome-head">
                  <div className="workspace-welcome-kicker-row">
                    <span className="page-kicker">Policy Workspace</span>
                    <span className="workspace-welcome-tag" aria-hidden="true">
                      SYS // VERIFIED CITATIONS
                    </span>
                  </div>
                  <h1>
                    查制度，<span className="highlight-word">核出处</span>
                  </h1>
                  <p>从业务场景或条款出发，定位适用制度并核对原文。</p>
                </header>
                <div className="starter-list">
                  {starters.map((starter, index) => (
                    <button
                      className="starter-card"
                      key={starter.title}
                      disabled={asking}
                      onClick={() => chooseStarter(starter.question)}
                    >
                      <span className="starter-index" aria-hidden="true">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="starter-copy">
                        <strong>{starter.title}</strong>
                        <small>{starter.description}</small>
                      </span>
                      <ArrowUpRight size={16} className="starter-arrow" aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="workspace-thread workspace-conversation">
                {Boolean(historyError) && (
                  <Notice kind="error" error={historyError}>
                    {errorMessage(historyError)}
                  </Notice>
                )}

                {historyPage?.before != null && (
                  <button
                    type="button"
                    className="btn btn-sm chat-history-more"
                    disabled={loadingOlder}
                    onClick={() => void loadOlderTurns()}
                  >
                    {loadingOlder ? '正在读取…' : '查看更早的问答'}
                  </button>
                )}

                {restoring && (
                  <p className="chat-request-status" role="status">
                    正在恢复会话…
                  </p>
                )}

                {Boolean(restoreError) && (
                  <Notice kind="error" error={restoreError}>
                    {errorMessage(restoreError)}{' '}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setRestoreAttempt((value) => value + 1)}
                    >
                      重新加载
                    </button>
                  </Notice>
                )}

                {displayedTurns.length > 0 && (
                  <div className="chat-transcript" aria-label="会话记录">
                    {displayedTurns.map((turn) => {
                      const outcome = turn.preview
                        ? { answer: turn.preview, citations: [] }
                        : turn.result
                      return (
                        <article className="chat-turn" key={turn.id}>
                          <p className="chat-turn-question">{turn.question}</p>
                          <div className="chat-turn-assistant">
                            {turn.state === 'running' && (
                              <p
                                className="chat-request-status"
                                role="status"
                                data-live={(turn === latestTurn && asking) || undefined}
                              >
                                {turnStatus(turn, turn === latestTurn ? stage : null)}
                              </p>
                            )}
                            <ExecutionTimeline turn={turn} />
                            {turn.state === 'interrupted' && (
                              <p className="chat-turn-state-note">本轮回答未完成。</p>
                            )}
                            {turn.state === 'failed' && (
                              <p className="chat-turn-state-note">本轮生成失败。</p>
                            )}
                            {outcome && (
                              <ChatAnswer
                                outcome={outcome}
                                live={turn === latestTurn && asking && turn.state === 'running'}
                                favorite={
                                  sessionId &&
                                  turn.state !== 'running' &&
                                  turn.result &&
                                  turn.id !== pendingTurnId
                                    ? {
                                        saved: Boolean(savedTurns[`qa:${turn.id}`]),
                                        busy: favBusyKey === `qa:${turn.id}`,
                                        onToggle: () => void toggleTurnFavorite(turn),
                                      }
                                    : undefined
                                }
                              />
                            )}
                          </div>
                        </article>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </section>
          {composerSection}
        </div>
      ) : (
        <Outlet />
      )}
      {sessionDialog && (
        <Dialog
          title={deletingSession ? '删除会话' : '重命名会话'}
          busy={managing}
          onClose={closeSessionDialog}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (deletingSession) void manageSession(sessionDialog.item.id, 'delete')
              else if (sessionTitle.trim())
                void manageSession(sessionDialog.item.id, { title: sessionTitle.trim() })
            }}
          >
            {deletingSession ? (
              <p>删除「{sessionDialog.item.title}」及其问答记录？此操作无法撤销。</p>
            ) : (
              <div className="form-field">
                <label htmlFor="chat-session-title">会话标题</label>
                <div className="input-wrap">
                  <input
                    id="chat-session-title"
                    value={sessionTitle}
                    onChange={(event) => setSessionTitle(event.target.value)}
                    maxLength={80}
                    disabled={managing}
                    autoFocus
                  />
                </div>
              </div>
            )}
            {Boolean(sessionActionError) && (
              <Notice kind="error" error={sessionActionError}>
                {errorMessage(sessionActionError)}
              </Notice>
            )}
            <div className="profile-actions">
              <button
                type="button"
                className="btn"
                disabled={managing}
                onClick={closeSessionDialog}
              >
                取消
              </button>
              <button
                type="submit"
                className={`btn ${deletingSession ? 'btn-danger' : 'btn-primary'}`}
                disabled={managing || (!deletingSession && !sessionTitle.trim())}
              >
                {deletingSession ? '确认删除' : '保存标题'}
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {favSaveTarget && (
        <SaveFavoriteDialog
          target={favSaveTarget}
          onClose={() => setFavSaveTarget(null)}
          onSaved={(saved) => {
            if (saved.source_key) {
              setSavedTurns((previous) => ({
                ...previous,
                [saved.source_key!]: { id: saved.id, folder_id: saved.folder_id },
              }))
            }
            setFavSaveTarget(null)
          }}
        />
      )}
    </AppLayout>
  )
}
