import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { createInterface } from 'node:readline'
import { test } from 'node:test'
import { Readable } from 'node:stream'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const session = '4ea54f11-b976-48a5-be51-c29857d88a04'
const firstTurn = '39b55d57-3059-4f6b-99c8-ad8aeb782ae9'
const secondTurn = '10014efe-03bb-4cc3-9152-eba3c263a558'
const token = 'private-test-token'
const toolName = 'lookup_financial_sample'
const contract = {
  system_prompt: 'FinOracle contract test prompt.',
  tools: [{
    type: 'function',
    function: {
      name: toolName,
      description: 'Read the sample rule.',
      parameters: {
        type: 'object',
        properties: { article: { type: 'string', maxLength: 30 } },
        required: ['article'],
        additionalProperties: false,
      },
    },
  }],
  max_model_calls: 3,
}

test('dsh streams reply deltas, executes tools, and uses the saved conversation projection', async (t) => {
  const requests = []
  const provider = createServer(async (req, res) => {
    const body = JSON.parse(await read(req))
    requests.push(body)
    const last = body.messages.at(-1)
    const tool = last.role === 'user'
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    const frame = (delta, finish_reason) => `data: ${JSON.stringify({
      id: 'mock', object: 'chat.completion.chunk', created: 1, model: body.model,
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`
    if (tool) {
      res.write(frame({ role: 'assistant', tool_calls: [{
        index: 0, id: `call_${requests.length}`, type: 'function',
        function: { name: toolName, arguments: '{"article":"第一条"}' },
      }] }, null))
    } else {
      res.write(frame({ role: 'assistant', content: '这里是尚未交付的' }, null))
      res.write(frame({ content: '模型候选。[[e123]]' }, null))
    }
    res.end(frame({}, tool ? 'tool_calls' : 'stop') + 'data: [DONE]\n\n')
  })
  provider.listen(0, '127.0.0.1')
  await once(provider, 'listening')
  t.after(() => provider.close())
  const home = await mkdtemp(join(tmpdir(), 'finoracle-dsh-test-'))
  t.after(() => rm(home, { recursive: true, force: true }))
  const env = {
    ...process.env,
    DSH_HOME: home,
    DSH_TELEMETRY_DISABLED: '1',
    FINORACLE_DSH_TOKEN: token,
    FINORACLE_DSH_PORT: '0',
    FINORACLE_DSH_HOST: '127.0.0.1',
    DEEPSEEK_API_KEY: 'mock-key',
    CHAT_BASE_URL: `http://127.0.0.1:${provider.address().port}`,
    CHAT_MODEL: 'deepseek-flash',
    CHAT_DSH_CONTEXT_WINDOW: '8192',
  }
  const first = await startHost(t, env)
  const unauthorized = await fetch(`${first.url}/turns`, { method: 'POST', body: '{}' })
  assert.equal(unauthorized.status, 401)
  const events = await turn(first.url, firstTurn, '第一条是什么？')
  assert.deepEqual(
    events.map(({ type, attempt, state }) => [type, attempt ?? null, state ?? null]),
    [
      ['start', 1, null], ['end', 1, 'completed'], ['tool', null, null],
      ['start', 2, null], ['text', null, null], ['text', null, null],
      ['end', 2, 'completed'], ['done', null, null],
    ],
  )
  assert.ok(events.some((item) => item.type === 'tool' && item.name === toolName))
  assert.deepEqual(events.filter((item) => item.type === 'text').map((item) => item.text), [
    '这里是尚未交付的', '模型候选。[[e123]]',
  ])
  assert.deepEqual(events.at(-1), { type: 'done', text: '这里是尚未交付的模型候选。[[e123]]' })
  assert.deepEqual(requests[0].tools.map((tool) => tool.function.name), [toolName])
  assert.equal(requests[0].tools[0].function.parameters.properties.article.maxLength, 30)
  assert.ok(requests[0].messages.some((message) =>
    ['system', 'developer'].includes(message.role) && message.content.includes(contract.system_prompt)))
  assert.ok(!requests[0].messages.some((message) => String(message.content).includes('Current working directory')))
  assert.ok(!('dsh_session_log' in requests[0]))
  assert.deepEqual(requests[0].thinking, { type: 'disabled' })
  first.child.kill()
  await once(first.child, 'exit')

  const second = await startHost(t, env)
  const approved = { turn_id: firstTurn, question: '第一条是什么？', answer_excerpt: '这是已经交付的正文。', references: [] }
  const removedContext = '其他会话的旧上下文不再传给模型。'
  const secondEvents = await collectTurn(await openTurn(second.url, secondTurn, '继续追问', {
    history: [approved],
    references: removedContext,
  }), second.url, secondTurn)
  assert.equal(secondEvents.at(-1).type, 'done')
  assert.ok(requests[2].messages.some((message) =>
    message.role === 'user' && message.content.includes('第一条是什么')))
  assert.ok(requests[2].messages.some((message) => String(message.content).includes(approved.answer_excerpt)))
  assert.ok(!requests[2].messages.some((message) => String(message.content).includes('尚未交付的模型候选')))
  assert.ok(!requests[2].messages.some((message) => String(message.content).includes(removedContext)))
  const calls = requests.length
  const replay = await turn(second.url, secondTurn, '继续追问')
  assert.equal(replay.at(-1).type, 'error')
  assert.equal(requests.length, calls)

  const removed = await fetch(`${second.url}/review`, {
    method: 'POST', headers,
  })
  assert.equal(removed.status, 404)
  assert.equal(requests.length, calls)

  const exhaustedId = randomUUID()
  const exhausted = await openTurn(second.url, exhaustedId, '汇总已查到的条文')
  const exhaustedEvents = await collectTurn(exhausted, second.url, exhaustedId, 0)
  assert.equal(exhaustedEvents.at(-1).type, 'done')
  assert.equal(requests[calls].tools.length, 1)
  assert.equal(requests[calls + 1].tools?.length ?? 0, 0)

  const cancelled = await openTurn(second.url, randomUUID(), '停止这一轮')
  const reader = cancelled.body.getReader()
  const decoder = new TextDecoder()
  let wire = ''
  while (!wire.includes('"type":"tool"')) {
    const part = await reader.read()
    assert.ok(!part.done, wire)
    wire += decoder.decode(part.value, { stream: true })
  }
  await reader.cancel()
  const nextId = randomUUID()
  let next
  for (let remaining = 10; remaining > 0; remaining--) {
    next = await openTurn(second.url, nextId, '停止后可以续问')
    if (next.status !== 409) break
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  assert.equal((await collectTurn(next, second.url, nextId)).at(-1).type, 'done')
})

async function read(stream) {
  const parts = []
  for await (const part of stream) parts.push(part)
  return Buffer.concat(parts).toString('utf8')
}

async function startHost(t, env) {
  const child = spawn(process.env.DSH_BIN || join(root, 'node_modules/.bin/dsh'), [
    'sdk-minimal', '--patch', join(root, 'profile.yml'),
  ], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] })
  t.after(async () => {
    if (child.exitCode !== null || child.signalCode !== null) return
    const exited = once(child, 'exit')
    child.kill()
    await exited
  })
  let stderr = ''
  child.stderr.on('data', (value) => { stderr += value })
  return await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`dsh startup timed out: ${stderr}`)), 10000)
    child.once('error', reject)
    child.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`dsh exited early (${code}): ${stderr}`))
    })
    createInterface({ input: child.stdout }).on('line', (line) => {
      if (!line.startsWith('FinOracle dsh listening on ')) return
      clearTimeout(timer)
      resolve({ child, url: `http://127.0.0.1:${line.split(' ').at(-1)}` })
    })
  })
}

async function turn(base, turnId, question) {
  return await collectTurn(await openTurn(base, turnId, question), base, turnId)
}

const headers = { authorization: `Bearer ${token}`, 'content-type': 'application/json' }

async function openTurn(base, turnId, question, extra = {}) {
  return await fetch(`${base}/turns`, {
    method: 'POST', headers,
    body: JSON.stringify({ session_id: session, turn_id: turnId, question, contract, ...extra }),
  })
}

async function collectTurn(response, base, turnId, remainingToolCalls = 3) {
  assert.equal(response.status, 200)
  const events = []
  for await (const line of createInterface({ input: Readable.fromWeb(response.body) })) {
    const item = JSON.parse(line)
    events.push(item)
    if (item.type === 'tool') {
      const reply = await fetch(`${base}/turns/${turnId}/tools/${item.id}`, {
        method: 'POST', headers,
        body: JSON.stringify({ evidence: [{ id: 'e123' }], remaining_tool_calls: remainingToolCalls }),
      })
      assert.equal(reply.status, 204)
    }
  }
  return events
}
