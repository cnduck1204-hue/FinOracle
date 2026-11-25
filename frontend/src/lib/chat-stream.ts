import type {
  ChatCitation,
  ChatConfidence,
  ChatOutcome,
  ChatStepState,
  ChatStreamUpdate,
} from './types'

type ChatStreamMessage =
  | ChatStreamUpdate
  | { type: 'done'; result: ChatOutcome }
  | { type: 'error'; message: string; code: string; requestId?: string }

function hasCitationTargets(value: unknown): value is ChatCitation[] {
  return (
    Array.isArray(value) &&
    value.every((item: unknown) => {
      if (!item || typeof item !== 'object') return false
      const citation = item as Partial<ChatCitation>
      if (
        typeof citation.number !== 'number' ||
        !Number.isInteger(citation.number) ||
        citation.number < 1
      ) {
        return false
      }
      if (citation.kind === 'version') {
        return typeof citation.version?.id === 'string' && citation.version.id.length > 0
      }
      return (
        (citation.kind === 'chunk' || citation.kind === undefined) &&
        'chunk_id' in citation &&
        typeof citation.chunk_id === 'number' &&
        Number.isInteger(citation.chunk_id) &&
        citation.chunk_id > 0
      )
    })
  )
}

function isStepState(state: unknown): state is ChatStepState {
  return (
    state === 'running' || state === 'completed' || state === 'failed' || state === 'interrupted'
  )
}

function isChatConfidence(value: unknown): value is ChatConfidence {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<ChatConfidence>
  return (
    typeof candidate.score === 'number' &&
    Number.isInteger(candidate.score) &&
    candidate.score >= 0 &&
    candidate.score <= 100 &&
    (candidate.level === 'high' || candidate.level === 'medium' || candidate.level === 'low') &&
    Array.isArray(candidate.reasons) &&
    candidate.reasons.every((item) => typeof item === 'string')
  )
}

function parseMessage(event: string, data: string): ChatStreamMessage | undefined {
  if (
    !['turn', 'progress', 'model', 'tool', 'answer_delta', 'paragraph', 'done', 'error'].includes(
      event,
    )
  )
    return
  const payload = JSON.parse(data) as Record<string, unknown> | null
  if (!payload || typeof payload !== 'object') throw new TypeError('Invalid SSE payload')

  switch (event) {
    case 'turn':
      if (typeof payload.turn_id === 'string' && payload.turn_id) {
        return { type: 'turn', turn_id: payload.turn_id }
      }
      break
    case 'progress':
      if (payload.stage === 'retrieving' || payload.stage === 'generating') {
        return { type: 'progress', stage: payload.stage }
      }
      break
    case 'model': {
      const { attempt, state } = payload
      if (
        typeof attempt === 'number' &&
        Number.isSafeInteger(attempt) &&
        attempt > 0 &&
        isStepState(state)
      ) {
        return { type: 'model', step: { attempt, state } }
      }
      break
    }
    case 'tool': {
      const { id, tool, state, target, summary } = payload
      if (
        typeof id === 'string' &&
        id &&
        (tool === 'search_knowledge' ||
          tool === 'get_article' ||
          tool === 'get_document_status' ||
          tool === 'reuse_answer_evidence') &&
        isStepState(state) &&
        (target == null || typeof target === 'string') &&
        (summary == null || typeof summary === 'string')
      ) {
        return { type: 'tool', step: { id, tool, state, target, summary } }
      }
      break
    }
    case 'answer_delta':
      if (typeof payload.text === 'string' && payload.text) {
        return { type: 'answer_delta', text: payload.text }
      }
      break
    case 'paragraph':
      if (typeof payload.text === 'string' && hasCitationTargets(payload.citations)) {
        return {
          type: 'paragraph',
          text: payload.text,
          citations: payload.citations,
        }
      }
      break
    case 'done': {
      const { status, answer, citations, turn_id, confidence } = payload
      if (
        (status === 'answered' || status === 'partial' || status === 'insufficient_evidence') &&
        typeof answer === 'string' &&
        hasCitationTargets(citations) &&
        (turn_id === undefined || (typeof turn_id === 'string' && turn_id)) &&
        (confidence == null || isChatConfidence(confidence))
      ) {
        return {
          type: 'done',
          result: {
            status,
            answer,
            citations,
            ...(turn_id && { turn_id }),
            ...(confidence != null && { confidence }),
          },
        }
      }
      break
    }
    case 'error':
      if (typeof payload.message === 'string' && typeof payload.code === 'string') {
        return {
          type: 'error',
          message: payload.message,
          code: payload.code,
          requestId: typeof payload.request_id === 'string' ? payload.request_id : undefined,
        }
      }
  }
  throw new TypeError(`Invalid ${event} event`)
}

export async function* readChatStream(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
): AsyncGenerator<ChatStreamMessage> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let pending = ''
  let trailingCR = false
  let numbered: boolean | undefined
  let revision = 0
  let turnId: string | undefined

  try {
    while (true) {
      signal.throwIfAborted()
      const { value, done } = await reader.read()
      signal.throwIfAborted()
      if (done) return
      let text = decoder.decode(value, { stream: true })
      if (!text) continue
      if (trailingCR && text.startsWith('\n')) text = text.slice(1)
      trailingCR = text.endsWith('\r')
      pending += text.replace(/\r\n?/g, '\n')

      const frames = pending.split('\n\n')
      pending = frames.pop() ?? ''
      for (const frame of frames) {
        let event = ''
        let id: string | undefined
        const data: string[] = []
        for (const line of frame.split('\n')) {
          const separator = line.indexOf(':')
          const field = separator < 0 ? line : line.slice(0, separator)
          const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
          if (field === 'event') event = value
          if (field === 'id') id = value
          if (field === 'data') data.push(value)
        }
        if (!event || data.length === 0) continue

        numbered ??= id !== undefined
        revision += 1
        if (numbered ? id !== String(revision) : id !== undefined) {
          throw new TypeError('Unexpected SSE revision')
        }
        const message = parseMessage(event, data.join('\n'))
        if (!message) continue
        if (message.type === 'turn') {
          if (revision !== 1) throw new TypeError('Unexpected SSE turn')
          turnId = message.turn_id
        } else if (message.type === 'done' && turnId !== undefined) {
          const completedId = message.result.turn_id
          if ((numbered || completedId !== undefined) && completedId !== turnId) {
            throw new TypeError('SSE turn changed before completion')
          }
        }
        signal.throwIfAborted()
        yield message
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
