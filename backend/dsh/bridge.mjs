import { createHash, randomUUID, timingSafeEqual } from 'node:crypto'
import { createServer } from 'node:http'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

export const name = 'finoracle-bridge'
export const inject = ['agents', 'agentLoop', 'sessions', 'sessionPersistence', 'systemPrompt', 'tools']

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/
const MAX_BODY_BYTES = 256 * 1024
const model = process.env.CHAT_MODEL || 'deepseek-flash'
const modelRoute = { provider: 'finoracle', model }

function digest(text) {
  return createHash('sha256').update(text).digest()
}

async function readJson(req) {
  let length = 0
  const parts = []
  for await (const chunk of req) {
    length += chunk.length
    if (length > MAX_BODY_BYTES) throw new Error('body limit')
    parts.push(chunk)
  }
  return JSON.parse(Buffer.concat(parts).toString('utf8'))
}

function writeFrame(res, value) {
  if (!res.destroyed && !res.writableEnded) res.write(JSON.stringify(value) + '\n')
}

function userInput(id, content) {
  const message = createUserMessage({ content, source: { kind: 'user' } })
  return { ...message, id }
}

function isContract(value) {
  return value && typeof value.system_prompt === 'string' && value.system_prompt.trim()
    && Number.isSafeInteger(value.max_model_calls) && value.max_model_calls > 0
    && Array.isArray(value.tools) && value.tools.every((tool) =>
      tool?.type === 'function' && typeof tool.function?.name === 'string'
      && typeof tool.function.description === 'string' && tool.function.parameters?.type === 'object')
}

function isTurn(value) {
  return value && UUID.test(value.turn_id) && UUID.test(value.session_id)
    && typeof value.question === 'string'
    && value.question.trim().length > 0 && value.question.length <= 4000
    && isContract(value.contract)
    && (value.history === undefined || Array.isArray(value.history) && value.history.length <= 12
      && value.history.every((item) => item && UUID.test(item.turn_id)
        && typeof item.question === 'string' && Array.isArray(item.references)))
}

function configureAgent(ctx, contract, turn) {
  ctx.systemPrompt.section({
    name: 'deployment:persona-prefix',
    order: ctx.systemPrompt.getSectionOrder('DEPLOYMENT_PERSONA_PREFIX'),
    text: contract.system_prompt,
  })
  ctx.tools.restrict({ allow: [] })
  for (const { function: { name, description, parameters } } of contract.tools) {
    ctx.tools.register({
      name,
      description,
      parameters,
      output: {
        schema: {},
        render: (_args, result) => [{ type: 'text', text: JSON.stringify(result) }],
      },
      async execute(args, { signal }) {
        const id = randomUUID()
        const { promise, resolve, reject } = Promise.withResolvers()
        const abort = () => reject(signal.reason)
        signal.throwIfAborted()
        signal.addEventListener('abort', abort, { once: true })
        turn.pending.set(id, resolve)
        writeFrame(turn.res, { type: 'tool', id, name, arguments: args })
        try {
          return await promise
        } finally {
          signal.removeEventListener('abort', abort)
          turn.pending.delete(id)
        }
      },
    }, {
      when: () => !turn.toolsExhausted && turn.attempts < contract.max_model_calls - 1,
    })
  }
}

export function apply(ctx) {
  const token = process.env.FINORACLE_DSH_TOKEN
  if (!token) throw new Error('FINORACLE_DSH_TOKEN is required')
  const authorization = digest(`Bearer ${token}`)
  const activeSessions = new Set()
  const byTurn = new Map()

  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const turn = byTurn.get(String(agent.session.id))
    if (turn && turn.attempts >= turn.maxModelCalls && !turn.hasFinalMessage) return { kind: 'reject' }
    return await next()
  })
  ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    const turn = byTurn.get(String(agent.session.id))
    if (!turn) return
    if (frame.type === 'start') {
      turn.attempts++
      turn.interrupted = false
      writeFrame(turn.res, { type: 'start', attempt: turn.attempts })
    } else if (frame.type === 'end') {
      const completed = frame.outcome.kind === 'committed'
        && frame.outcome.eventType === 'assistant/message'
      const state = turn.interrupted ? 'interrupted' : completed ? 'completed' : 'failed'
      writeFrame(turn.res, { type: 'end', attempt: turn.attempts, state })
    } else if (frame.type === 'chunk' && frame.chunk.type === 'text-delta' && frame.chunk.text) {
      writeFrame(turn.res, { type: 'text', text: frame.chunk.text })
    }
  })
  ctx.on('session/event', (session, event) => {
    const turn = byTurn.get(String(session.id))
    if (!turn) return
    if (event.type === 'assistant/message') {
      const content = event.data.message.content
      turn.interrupted = event.data.interrupted === true
      turn.hasFinalMessage = content.every((block) => block.type !== 'tool-call')
      turn.text = content.filter((block) => block.type === 'text').map((block) => block.text).join('')
    } else if (event.type === 'turn/end') {
      turn.reason = event.data.reason.kind
    }
  })

  async function run(req, res) {
    const data = await readJson(req)
    if (!isTurn(data)) {
      res.writeHead(422).end()
      return
    }
    if (activeSessions.has(data.session_id) || byTurn.has(data.turn_id)) {
      res.writeHead(409).end()
      return
    }
    const turn = {
      res, pending: new Map(), attempts: 0, maxModelCalls: data.contract.max_model_calls,
      hasFinalMessage: false, interrupted: false, toolsExhausted: false, text: '', reason: null,
    }
    activeSessions.add(data.session_id)
    byTurn.set(data.turn_id, turn)
    const cancellation = new AbortController()
    let handle
    res.once('close', () => {
      if (!res.writableEnded) {
        cancellation.abort()
        try {
          handle?.agent.cancel({ kind: 'user' })
        } catch (error) {
          console.error('FinOracle dsh cancellation failed', error?.name)
        }
      }
    })
    try {
      res.writeHead(200, { 'content-type': 'application/x-ndjson', 'cache-control': 'no-store' })
      res.flushHeaders()
      if (await ctx.sessionPersistence.stat(data.turn_id)) {
        writeFrame(res, { type: 'error', code: 'TURN_ALREADY_SUBMITTED' })
        return
      }
      // Each runtime session sees the approved business history and this turn's tool results.
      handle = await ctx.agents.create({
        agentOptions: { ...modelRoute, maxTokens: 2048 },
        signal: cancellation.signal,
        setup: (agentCtx) => configureAgent(agentCtx, data.contract, turn),
        sessionId: data.turn_id,
        meta: { cwd: process.cwd() },
      })
      cancellation.signal.throwIfAborted()
      const { agent } = handle
      agent.model.select(modelRoute)
      const content = []
      if (data.history?.length) {
        content.push({ type: 'text', text: 'FinOracle 本会话已交付的历史，仅作为本轮上下文。\n' + JSON.stringify(data.history) })
      }
      content.push({ type: 'text', text: data.question })
      agent.followup(userInput(data.turn_id, content))
      await agent.whenIdle()
      await ctx.sessions.flush(agent.session)
      if (turn.reason === 'completed') writeFrame(res, { type: 'done', text: turn.text })
      else writeFrame(res, { type: 'error', code: 'TURN_INCOMPLETE' })
    } catch (error) {
      if (!cancellation.signal.aborted) {
        console.error('FinOracle dsh turn failed', error?.name)
        writeFrame(res, { type: 'error', code: 'TURN_FAILED' })
      }
    } finally {
      try { await handle?.dispose() } finally {
        byTurn.delete(data.turn_id)
        activeSessions.delete(data.session_id)
        res.end()
      }
    }
  }

  const server = createServer(async (req, res) => {
    try {
      if (!timingSafeEqual(digest(req.headers.authorization ?? ''), authorization)) {
        res.writeHead(401).end()
      } else if (req.method === 'GET' && req.url === '/health') {
        res.writeHead(204).end()
      } else if (req.method === 'POST' && req.url === '/turns') {
        await run(req, res)
      } else {
        const [, resource, turnId, operation, callId, extra] = req.url.split('/')
        const turn = byTurn.get(turnId)
        const finishTool = turn?.pending.get(callId)
        if (req.method !== 'POST' || resource !== 'turns' || operation !== 'tools' || extra !== undefined || !finishTool) {
          res.writeHead(404).end()
          return
        }
        const result = await readJson(req)
        if (!turn.pending.delete(callId)) {
          res.writeHead(409).end()
          return
        }
        if (result?.remaining_tool_calls === 0) turn.toolsExhausted = true
        finishTool(result)
        res.writeHead(204).end()
      }
    } catch {
      if (!res.headersSent) res.writeHead(400)
      res.end()
    }
  })
  server.listen(Number(process.env.FINORACLE_DSH_PORT ?? 8091), process.env.FINORACLE_DSH_HOST ?? '127.0.0.1', () => {
    console.info('FinOracle dsh listening on', server.address().port)
  })
  ctx.effect(() => () => {
    server.closeAllConnections()
    server.close()
  })
}
