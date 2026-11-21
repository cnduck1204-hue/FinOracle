import { expect, test, type Page } from '@playwright/test'
import type { ChatTurn } from '../src/lib/types'

const testUserId = 'c4c001ba-7718-41ff-963a-afcdf40a38de'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/chat/sessions?**', (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  )
})

async function login(page: Page) {
  const user = {
    id: testUserId,
    username: 'test_user',
    email: 'test@example.com',
    role: 'user',
    status: 'active',
    created_at: '2026-10-28T00:00:00Z',
    updated_at: '2026-10-28T00:00:00Z',
  }
  await page.route('**/api/v1/users/me', (route) => route.fulfill({ json: user }))
  await page.route('**/api/v1/auth/{login,refresh}', (route) =>
    route.fulfill({
      json: {
        access_token: 'test-access',
        refresh_token: 'test-refresh',
        token_type: 'bearer',
        expires_in: 1800,
        user,
      },
    }),
  )
  await page.goto('/login')
  await page.getByLabel('用户名或邮箱', { exact: true }).fill('test_user')
  await page.getByLabel('密码', { exact: true }).fill('Password123')
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
  await expect(page.getByRole('link', { name: '打开我的账号', exact: true })).toBeVisible()
}

test('manages a session from its row menu in the sidebar and directory', async ({ page }) => {
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const session = {
    id,
    title: '待整理会话',
    archived: false,
    pinned: false,
    updated_at: '2026-11-18T00:00:00Z',
  }
  const newer = {
    ...session,
    id: '22dd7e68-e623-4f4a-b971-a994e76c081a',
    title: '较新会话',
    updated_at: '2026-11-19T00:00:00Z',
  }
  const alreadyPinned = {
    ...session,
    id: 'cf15dca1-4adb-4a29-b097-e92cba1b94fe',
    title: '已有置顶会话',
    pinned: true,
    updated_at: '2026-11-17T00:00:00Z',
  }
  let deleted = false
  await page.route('**/api/v1/chat/sessions?**', (route) => {
    const pinned = new URL(route.request().url()).searchParams.get('pinned')
    const sessions = deleted ? [alreadyPinned, newer] : [session, alreadyPinned, newer]
    const items = sessions
      .filter((item) => pinned === null || String(item.pinned) === pinned)
      .sort(
        (a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at),
      )
    return route.fulfill({ json: { items, next_cursor: null } })
  })
  await page.route(`**/api/v1/chat/sessions/${id}`, async (route) => {
    const request = route.request()
    if (request.method() === 'PATCH') {
      Object.assign(session, request.postDataJSON())
      await route.fulfill({ json: session })
    } else if (request.method() === 'DELETE') {
      deleted = true
      await route.fulfill({ status: 204 })
    } else {
      await route.fulfill({
        json: { ...session, turns: [], through_seq: 0, next_before_seq: null },
      })
    }
  })
  await page.route(`**/api/v1/chat/sessions/${alreadyPinned.id}`, (route) => {
    Object.assign(alreadyPinned, route.request().postDataJSON())
    return route.fulfill({ json: alreadyPinned })
  })
  await login(page)
  const sidebar = page.getByRole('complementary', { name: '主导航' })
  const recent = sidebar.getByRole('region', { name: '最近会话' })
  const pinned = sidebar.getByRole('region', { name: '已置顶', exact: true })
  const pinnedItem = pinned.getByRole('button', { name: alreadyPinned.title, exact: true })
  const name = recent.getByRole('button', { name: '待整理会话', exact: true })
  const trigger = recent.getByRole('button', { name: '会话操作 待整理会话' })
  const viewAll = sidebar.getByRole('link', { name: '查看全部' })

  await expect(pinnedItem).toBeVisible()
  await expect(name).toBeVisible()
  await expect(recent.getByRole('button', { name: alreadyPinned.title, exact: true })).toHaveCount(
    0,
  )
  await pinned.locator('summary').click()
  await expect(pinnedItem).toBeHidden()
  await expect(name).toBeVisible()
  await recent.locator('summary').click()
  await expect(name).toBeHidden()
  await expect(viewAll).toBeVisible()
  await pinned.locator('summary').click()
  await expect(pinnedItem).toBeVisible()
  await expect(name).toBeHidden()
  await recent.locator('summary').click()
  await expect(name).toBeVisible()
  await pinned.locator('summary').click()
  await page.mouse.move(0, 0)
  await expect(trigger).toHaveCSS('opacity', '0')
  await name.hover()
  await expect(trigger).toHaveCSS('opacity', '1')
  await trigger.click()
  const menu = page.getByRole('group', { name: '会话操作', exact: true })
  await expect(menu.getByRole('button')).toHaveText(['置顶', '重命名', '删除'])
  await menu.getByRole('button', { name: '置顶', exact: true }).click()
  await expect(name).toHaveCount(0)
  await expect(pinned.locator('details')).not.toHaveAttribute('open', '')
  await pinned.locator('summary').click()
  await expect(pinned.getByRole('button', { name: session.title, exact: true })).toBeVisible()

  await viewAll.click()
  const directory = page.getByRole('region', { name: '全部会话' })
  await directory.getByRole('button', { name: '待整理会话', exact: true }).hover()
  await directory.getByRole('button', { name: '会话操作 待整理会话' }).click()
  await expect(menu.getByRole('button', { name: '取消置顶' })).toBeVisible()
  await menu.getByRole('button', { name: '重命名' }).click()
  const rename = page.getByRole('dialog', { name: '重命名会话' })
  await rename.getByLabel('会话标题').fill('已整理会话')
  await rename.getByRole('button', { name: '保存标题' }).click()
  await expect(directory.getByRole('button', { name: '已整理会话', exact: true })).toBeVisible()
  await directory.getByRole('button', { name: '已整理会话', exact: true }).click()
  await expect(page.getByRole('banner').getByText('已整理会话')).toBeVisible()

  await pinned.getByRole('button', { name: '已整理会话', exact: true }).hover()
  await pinned.getByRole('button', { name: '会话操作 已整理会话' }).click()
  await menu.getByRole('button', { name: '删除', exact: true }).click()
  const confirmation = page.getByRole('dialog', { name: '删除会话' })
  await confirmation.getByRole('button', { name: '取消' }).click()
  await expect(pinned.getByRole('button', { name: '已整理会话', exact: true })).toBeVisible()
  await pinned.getByRole('button', { name: '会话操作 已整理会话' }).focus()
  await page.keyboard.press('Enter')
  await menu.getByRole('button', { name: '删除', exact: true }).click()
  await confirmation.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByRole('heading', { name: '查制度，核出处' })).toBeVisible()
  await expect(pinned.getByRole('button', { name: '已整理会话', exact: true })).toHaveCount(0)
  expect(deleted).toBe(true)

  await pinned.locator('summary').click()
  await sidebar.getByRole('link', { name: /制度库/ }).click()
  await expect(page.getByRole('heading', { name: '制度库，正在准备。' })).toBeVisible()
  await expect(pinned.locator('details')).toBeVisible()
  await expect(pinned.locator('details')).not.toHaveAttribute('open', '')
  await expect(recent.getByRole('button', { name: newer.title, exact: true })).toBeVisible()
  await pinned.locator('summary').click()
  await expect(pinnedItem).toBeVisible()
  await pinnedItem.hover()
  await pinned.getByRole('button', { name: `会话操作 ${alreadyPinned.title}` }).click()
  await menu.getByRole('button', { name: '取消置顶' }).click()
  await expect(pinned).toHaveCount(0)
  await expect(recent.getByRole('button', { name: alreadyPinned.title, exact: true })).toBeVisible()
  await recent.locator('summary').click()
  await viewAll.click()
  await expect(directory).toBeVisible()
  await expect(recent.locator('details')).toBeVisible()
  await expect(recent.locator('details')).not.toHaveAttribute('open', '')
})

test('finds an old chat, reads older turns, and archives and restores it', async ({ page }) => {
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const summary = { id, title: '制度讨论', archived: false, updated_at: '2026-11-19T00:00:00Z' }
  const olderSummary = {
    ...summary,
    id: '10728b1f-793e-458f-9500-e92c59758349',
    title: '较早的制度会话',
  }
  await page.route('**/api/v1/chat/sessions**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'PATCH') {
      Object.assign(summary, route.request().postDataJSON())
      await route.fulfill({ json: summary })
    } else if (url.pathname.endsWith(id)) {
      const older = url.searchParams.has('before_seq')
      await route.fulfill({
        json: {
          ...summary,
          through_seq: 3,
          next_before_seq: older ? null : 2,
          turns: [
            {
              id: older ? 'old' : 'new',
              question: older ? '第一条是什么' : '第二条是什么',
              state: 'completed',
              result: null,
            },
          ],
        },
      })
    } else {
      const archived = url.searchParams.get('archived') === 'true'
      const query = url.searchParams.get('q') || ''
      const canLoadMore = !archived && !summary.archived && query === '制度'
      const more = canLoadMore && url.searchParams.get('cursor') === 'next'
      await route.fulfill({
        json: {
          items: more
            ? [olderSummary]
            : archived === summary.archived &&
                summary.title.includes(query) &&
                url.searchParams.get('pinned') !== 'true'
              ? [summary]
              : [],
          next_cursor: canLoadMore && !more ? 'next' : null,
        },
      })
    }
  })
  await login(page)
  const sidebar = page.getByRole('complementary', { name: '主导航' })
  const recent = sidebar.getByRole('region', { name: '最近会话' })
  const viewAll = sidebar.getByRole('link', { name: '查看全部' })
  const main = page.getByRole('main')
  const question = page.getByLabel('你想了解什么？')
  await expect(recent.getByRole('button', { name: summary.title, exact: true })).toBeVisible()
  await expect(sidebar.getByRole('region', { name: '已置顶', exact: true })).toHaveCount(0)
  await question.fill('还未发送的问题')
  await viewAll.click()
  await expect(main.getByRole('heading', { name: '全部会话' })).toBeVisible()
  await expect(question).toBeHidden()
  const search = main.getByRole('searchbox', { name: '搜索会话' })
  await expect(search).toBeHidden()
  await main.getByRole('button', { name: '查找会话' }).click()
  await expect(search).toBeFocused()
  const submitSearch = main.getByRole('button', { name: '搜索', exact: true })
  const toolbar = main.getByRole('group', { name: '会话目录操作' })
  const [fieldBox, iconBox] = await Promise.all([search.boundingBox(), submitSearch.boundingBox()])
  expect(fieldBox!.x + fieldBox!.width).toBeLessThan(iconBox!.x)
  expect(
    Math.abs(fieldBox!.y + (fieldBox!.height - iconBox!.height) / 2 - iconBox!.y),
  ).toBeLessThan(2)
  await expect(toolbar.getByRole('searchbox', { name: '搜索会话' })).toBeVisible()
  await search.fill('制度')
  await submitSearch.click()
  await main.getByRole('button', { name: '更多会话' }).click()
  await expect(main.getByRole('button', { name: olderSummary.title, exact: true })).toBeVisible()
  await expect(recent.getByRole('button', { name: olderSummary.title, exact: true })).toHaveCount(0)
  await main.getByRole('button', { name: summary.title, exact: true }).click()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(question).toHaveValue('')
  await expect(page.getByRole('banner').getByText(summary.title, { exact: true })).toBeVisible()
  await expect(page.getByText('第二条是什么')).toBeVisible()
  await page.getByRole('button', { name: '查看更早的问答' }).click()
  await expect(page.getByText('第一条是什么')).toBeVisible()
  await page.getByRole('button', { name: '归档会话', exact: true }).click()
  await expect(page.getByRole('button', { name: '恢复会话', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await viewAll.click()
  const archived = main.getByRole('checkbox', { name: '已归档' })
  await expect(archived).toBeHidden()
  await main.getByRole('button', { name: '筛选会话' }).click()
  await expect(archived).toBeFocused()
  await archived.check()
  await main.getByRole('button', { name: summary.title, exact: true }).click()
  await page.getByRole('button', { name: '恢复会话', exact: true }).click()
  await page.getByLabel('你想了解什么？').fill('第三条是什么')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled()
  await sidebar.getByRole('button', { name: '新会话', exact: true }).click()
  await expect(question).toHaveValue('还未发送的问题')
  await viewAll.click()
  await expect(main.getByRole('button', { name: summary.title, exact: true })).toBeVisible()
  await expect(main.getByRole('button', { name: /引用会话/ })).toHaveCount(0)
  await main.getByRole('button', { name: summary.title, exact: true }).click()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(page.getByText('第二条是什么')).toBeVisible()
  await expect(question).toHaveValue('第三条是什么')
  await page.reload()
  await expect(page.getByText('第二条是什么')).toBeVisible()
  await expect(question).toHaveValue('第三条是什么')

  await sidebar.getByRole('link', { name: /制度库/ }).click()
  await expect(page).toHaveURL(/\/library$/)
  await expect(main.getByRole('heading', { name: '制度库，正在准备。' })).toBeVisible()
  await recent.getByRole('button', { name: summary.title, exact: true }).click()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(page.getByText('第二条是什么')).toBeVisible()
  await sidebar.getByRole('link', { name: /制度库/ }).click()
  await expect(main.getByRole('heading', { name: '制度库，正在准备。' })).toBeVisible()
  const newSession = sidebar.getByRole('button', { name: '新会话', exact: true })
  await expect(newSession).toBeVisible()
  await expect(sidebar.getByRole('link', { name: '智能问答', exact: true })).toHaveCount(0)
  await newSession.click()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(main.getByRole('heading', { name: '查制度，核出处' })).toBeVisible()
  await expect(recent.getByRole('button', { name: summary.title, exact: true })).toBeVisible()
})

test('loads older history using the new head after a recovered turn', async ({ page }) => {
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const summary = { id, title: '恢复测试', archived: false, updated_at: '2026-11-19T00:00:00Z' }
  const completed = (id: string, question: string) => ({
    id,
    question,
    state: 'completed',
    result: null,
  })
  let recovered = false
  await page.route('**/api/v1/chat/sessions**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.endsWith(id)) {
      await route.fulfill({
        json: {
          items: url.searchParams.get('pinned') === 'true' ? [] : [summary],
          next_cursor: null,
        },
      })
      return
    }
    const before = url.searchParams.get('before_seq')
    const page =
      before === null
        ? recovered
          ? {
              turns: [completed('3', '原最后一轮'), completed('4', '恢复的新一轮')],
              next_before_seq: 4,
              through_seq: 7,
            }
          : {
              turns: [completed('2', '被移出首页的一轮'), completed('3', '原最后一轮')],
              next_before_seq: 2,
              through_seq: 5,
            }
        : {
            turns: before === '4' ? [completed('2', '被移出首页的一轮')] : [],
            next_before_seq: null,
            through_seq: Number(url.searchParams.get('through_seq')),
          }
    await route.fulfill({ json: { ...summary, ...page } })
  })
  await page.route('**/api/v1/chat/stream', async (route) => {
    recovered = true
    await route.abort()
  })
  await login(page)
  await page.getByRole('link', { name: '查看全部', exact: true }).click()
  await page.getByRole('main').getByRole('button', { name: '恢复测试', exact: true }).click()
  await expect(page.getByText('被移出首页的一轮', { exact: true })).toBeVisible()
  await page.getByLabel('你想了解什么？').fill('恢复的新一轮')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByLabel('会话记录').getByText('恢复的新一轮', { exact: true })).toBeVisible()
  await expect(page.getByText('被移出首页的一轮', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '查看更早的问答' }).click()
  await expect(page.getByText('被移出首页的一轮', { exact: true })).toBeVisible()
})

test('renders tool progress and Markdown without closing an open citation on completion', async ({
  page,
}) => {
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const turnId = 'a7a55e43-8a63-4ed4-bb35-d01155d39dab'
  const text =
    '发现重大风险隐患时，可以进行**专项检查**。[1]\n\n' +
    '其他说法&#91;1&#93;或\\[1\\]。\n\n' +
    '代码为 \x60arr[1]\x60。\n\n\x60\x60\x60python\nprint(arr[1])\n\x60\x60\x60\n\n' +
    '| 项目 | 结论 |\n| --- | --- |\n| 检查 | 可开展 |[2]'
  const citation = {
    kind: 'chunk',
    chunk_id: 17,
    number: 1,
    document: { id, title: '合成制度样本', issuer: '测试' },
    version: { id, version_note: '', document_number: '', validity_status: 'effective' },
    chapter: '',
    section: '',
    article: '第六条',
    heading: '',
    page_start: 1,
    page_end: 1,
  }
  await page.addInitScript(
    ({ turnId, text, citations }) => {
      const fetch = window.fetch.bind(window)
      window.fetch = (input, init) => {
        if (!String(input).endsWith('/chat/stream')) return fetch(input, init)
        const encoder = new TextEncoder()
        const frame = (id: number, event: string, data: object) =>
          encoder.encode(
            'id: ' + id + '\nevent: ' + event + '\ndata: ' + JSON.stringify(data) + '\n\n',
          )
        return Promise.resolve(
          new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(frame(1, 'turn', { turn_id: turnId }))
                const step = { id: '1:0', tool: 'get_article' }
                controller.enqueue(frame(2, 'tool', { ...step, state: 'running' }))
                window.addEventListener(
                  'test-tool-finished',
                  () => {
                    controller.enqueue(frame(3, 'tool', { ...step, state: 'completed' }))
                    controller.enqueue(frame(4, 'paragraph', { text, citations }))
                  },
                  { once: true },
                )
                window.addEventListener(
                  'test-stream-finished',
                  () => {
                    controller.enqueue(
                      frame(5, 'done', {
                        turn_id: turnId,
                        status: 'partial',
                        answer: text + '\n\n部分问题暂缺少可核对的制度依据。',
                        citations,
                      }),
                    )
                    controller.close()
                  },
                  { once: true },
                )
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        )
      }
    },
    { turnId, text, citations: [citation, { ...citation, number: 2, chunk_id: 18 }] },
  )
  await page.route('**/api/v1/chat/sessions', (route) =>
    route.fulfill({ status: 201, json: { id } }),
  )
  await page.route('**/api/v1/chat/references/chunk/17', (route) =>
    route.fulfill({ json: { ...citation, content: '仅用于验收的合成条文。' } }),
  )
  await page.route('**/api/v1/chat/references/chunk/18', (route) =>
    route.fulfill({ json: { ...citation, chunk_id: 18, content: '表格引用对应的合成条文。' } }),
  )
  await login(page)
  await page.getByLabel('你想了解什么？').fill('发现重大风险时怎么处理？')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('读取制度条文')).toBeVisible()
  await expect(page.getByText('进行中')).toBeVisible()
  await page.evaluate(() => {
    window.dispatchEvent(new Event('test-tool-finished'))
  })
  await expect(page.getByText('已完成', { exact: true })).toBeVisible()
  const answer = page.getByRole('region', { name: '制度问答结果' })
  await expect(answer.locator('strong')).toHaveText('专项检查')
  await expect(answer.locator('.chat-reply-caret')).toBeVisible()
  await expect(answer.getByRole('table')).toBeVisible()
  await expect(answer.locator('code')).toHaveText(['arr[1]', 'print(arr[1])'])
  await expect(answer.getByText('其他说法或。')).toBeVisible()
  await expect(answer.getByRole('button', { name: '查看引用 1' })).toHaveCount(1)
  await answer.getByRole('button', { name: '查看引用 1' }).click()
  await expect(page.getByText('仅用于验收的合成条文。')).toBeVisible()
  const citationButton = answer.getByRole('button', { name: '查看引用 2' })
  await citationButton.click()
  const source = page.getByText('表格引用对应的合成条文。')
  await expect(source).toBeVisible()
  await page.evaluate(() => {
    window.dispatchEvent(new Event('test-stream-finished'))
  })
  await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible()
  await expect(answer.locator('.chat-reply-caret')).toHaveCount(0)
  await expect(source).toBeVisible()
  await expect(citationButton).toHaveAttribute('aria-expanded', 'true')
  await expect(citationButton).toBeFocused()
})

test('streams native reply text and restores the saved answer and execution after reload', async ({
  page,
}) => {
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const tool = { id: 'lookup', tool: 'get_article', target: '示例制度 · 第六条' }
  const completedTool = { ...tool, state: 'completed', summary: '返回 1 段条文' }
  await page.route('**/api/v1/chat/sessions', (route) =>
    route.fulfill({ status: 201, json: { id } }),
  )
  await page.route(`**/api/v1/chat/sessions/${id}`, (route) =>
    route.fulfill({
      json: {
        id,
        title: '第六条说什么？',
        through_seq: 2,
        next_before_seq: null,
        turns: [
          {
            id: 'live-turn',
            question: '第六条说什么？',
            state: 'completed',
            result: { status: 'answered', answer: '可以进行专项检查。', citations: [] },
            tool_steps: [completedTool],
            execution: [
              { attempt: 1, state: 'completed' },
              completedTool,
              { attempt: 2, state: 'completed' },
            ],
          },
        ],
      },
    }),
  )
  await page.addInitScript(
    ({ tool, completedTool }) => {
      const fetch = window.fetch.bind(window)
      window.fetch = (input, init) => {
        if (!String(input).endsWith('/chat/stream')) return fetch(input, init)
        return Promise.resolve(
          new Response(
            new ReadableStream({
              start(controller) {
                let revision = 0
                const encoder = new TextEncoder()
                const push = (event: string, data: object) =>
                  controller.enqueue(
                    encoder.encode(
                      `id: ${++revision}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
                    ),
                  )
                push('turn', { turn_id: 'live-turn' })
                push('model', { attempt: 1, state: 'running' })
                push('answer_delta', { text: '我先查一下。' })
                window.addEventListener(
                  'test-tool-and-next-model',
                  () => {
                    push('model', { attempt: 1, state: 'completed' })
                    push('tool', { ...tool, state: 'running' })
                    push('tool', completedTool)
                    push('model', { attempt: 2, state: 'running' })
                    push('progress', { stage: 'generating' })
                    push('answer_delta', { text: '可以进行' })
                  },
                  { once: true },
                )
                window.addEventListener(
                  'test-continue-answer',
                  () => push('answer_delta', { text: '专项检查；示例暂定。' }),
                  { once: true },
                )
                window.addEventListener(
                  'test-commit-answer',
                  () => {
                    const text = '可以进行专项检查。'
                    push('model', { attempt: 2, state: 'completed' })
                    push('paragraph', { text, citations: [] })
                    push('done', {
                      turn_id: 'live-turn',
                      answer: text,
                      status: 'answered',
                      citations: [],
                    })
                    controller.close()
                  },
                  { once: true },
                )
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        )
      }
    },
    { tool, completedTool },
  )
  await login(page)
  const question = page.getByLabel('你想了解什么？')
  await question.fill('第六条说什么？')
  await question.press('Shift+Enter')
  await expect(question).toHaveValue('第六条说什么？\n')
  await question.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled()
  await question.press('Enter')
  const reply = page.getByRole('region', { name: '制度问答结果' })
  await expect(reply.getByText('我先查一下。')).toBeVisible()
  await expect(question).toHaveValue('')
  await expect(page.getByText('第六条说什么？', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: '查看全部' }).click()
  await expect(page.getByRole('heading', { name: '全部会话' })).toBeVisible()
  await page.getByRole('link', { name: '返回问答' }).click()
  await expect(reply.getByText('我先查一下。')).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new Event('test-tool-and-next-model')))
  const steps = page.getByRole('list', { name: '执行过程' }).getByRole('listitem')
  await expect(steps).toHaveCount(3)
  await expect(steps.nth(0)).toContainText('第 1 次模型调用')
  await expect(steps.nth(1)).toContainText('示例制度 · 第六条')
  await expect(steps.nth(1)).toContainText('返回 1 段条文')
  await expect(steps.nth(2)).toContainText('第 2 次模型调用')
  await expect(reply.getByText('我先查一下。')).toHaveCount(0)
  await expect(page.getByText('正在生成回答…')).toBeVisible()
  await expect(reply.getByText('可以进行', { exact: true })).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new Event('test-continue-answer')))
  await expect(reply.getByText('可以进行专项检查；示例暂定。', { exact: true })).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new Event('test-commit-answer')))
  await expect(reply.getByText('可以进行专项检查。', { exact: true })).toHaveCount(1)
  await page.evaluate((id) => {
    sessionStorage.setItem(
      `finoracle.draft.v1:${id}`,
      JSON.stringify({ text: '之前保存的草稿', savedAt: Date.now() }),
    )
  }, testUserId)
  await page.reload()
  await expect(reply.getByText('可以进行专项检查。', { exact: true })).toBeVisible()
  await expect(question).toHaveValue('')
  await expect(steps).toHaveCount(3)
  await expect(steps.nth(0)).toContainText('第 1 次模型调用')
  await expect(steps.nth(1)).toContainText('示例制度 · 第六条')
  await expect(steps.nth(2)).toContainText('第 2 次模型调用')
  await page.getByRole('button', { name: '新会话', exact: true }).click()
  await expect(question).toHaveValue('')
})

test('follows live reply growth until the reader scrolls away', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 650 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const id = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
  const turnId = 'a7a55e43-8a63-4ed4-bb35-d01155d39dab'
  const first = '追踪中的制度正文。'.repeat(180)
  const second = '读者离开底部后收到的新内容。'.repeat(80)
  await page.addInitScript(
    ({ turnId, first, second }) => {
      const fetch = window.fetch.bind(window)
      window.fetch = (input, init) => {
        if (!String(input).endsWith('/chat/stream')) return fetch(input, init)
        const encoder = new TextEncoder()
        const frame = (id: number, event: string, data: object) =>
          encoder.encode(`id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        return Promise.resolve(
          new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(frame(1, 'turn', { turn_id: turnId }))
                controller.enqueue(frame(2, 'answer_delta', { text: first }))
                window.addEventListener(
                  'test-next-paragraph',
                  () => {
                    controller.enqueue(frame(3, 'answer_delta', { text: '\n\n' + second }))
                  },
                  { once: true },
                )
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        )
      }
    },
    { turnId, first, second },
  )
  await page.route('**/api/v1/chat/sessions', (route) =>
    route.fulfill({ status: 201, json: { id } }),
  )
  await login(page)
  await page.getByLabel('你想了解什么？').fill('请解释制度。')
  await page.getByRole('button', { name: '发送问题' }).click()
  const answer = page.getByRole('region', { name: '制度问答结果' })
  await expect(answer).toBeAttached()
  const messages = page.getByRole('region', { name: '问答内容' })
  const stop = page.getByRole('button', { name: '停止生成' })
  await expect
    .poll(() => messages.evaluate((el) => el.scrollHeight - el.clientHeight - el.scrollTop))
    .toBeLessThanOrEqual(1)
  await expect(stop).toBeInViewport()
  await expect(answer.locator('.chat-reply-caret')).toHaveCSS('animation-name', 'none')
  await messages.evaluate(async (el) => {
    el.scrollTo({ top: 0, behavior: 'instant' })
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    )
    window.dispatchEvent(new Event('test-next-paragraph'))
  })
  await expect(answer.getByText(second)).toBeAttached()
  await expect.poll(() => messages.evaluate((el) => el.scrollTop)).toBe(0)
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0)
  await expect(stop).toBeInViewport()
})

for (const renewed of [false, true]) {
  const title = renewed
    ? 'reconciles a completed turn while a newer turn is running'
    : 'keeps verified paragraphs while a disconnected turn is reconciled'

  test(title, async ({ page }) => {
    const sessionId = '98b46289-fb96-4bc9-b5e7-2ffd18d759de'
    const turnId = 'a7a55e43-8a63-4ed4-bb35-d01155d39dab'
    const question = '发现重大风险隐患时可以采取什么措施？'
    const paragraph = '发现重大风险隐患时，可以进行专项检查。'
    const turns: ChatTurn[] = [{ id: turnId, question, state: 'running', result: null }]

    await page.route('**/api/v1/chat/sessions', (route) =>
      route.fulfill({ status: 201, json: { id: sessionId } }),
    )
    await page.route(`**/api/v1/chat/sessions/${sessionId}`, (route) =>
      route.fulfill({
        json: { id: sessionId, turns },
      }),
    )
    await page.route('**/api/v1/chat/stream', (route) =>
      route.fulfill({
        contentType: 'text/event-stream',
        body:
          `id: 1\nevent: turn\ndata: ${JSON.stringify({ turn_id: turnId })}\n\n` +
          'id: 2\nevent: model\ndata: {"attempt":1,"state":"running"}\n\n' +
          'id: 3\nevent: answer_delta\ndata: {"text":"没有写入会话的预览"}\n\n' +
          `id: 4\nevent: paragraph\ndata: ${JSON.stringify({ text: paragraph, citations: [] })}\n\n`,
      }),
    )

    await login(page)
    await page.getByLabel('你想了解什么？').fill(question)
    const send = page.getByRole('button', { name: '发送问题' })
    await send.click()

    await expect(page.getByText('上一轮正在收尾…')).toBeVisible()
    await expect(page.getByText(paragraph)).toBeVisible()
    await expect(page.getByText('没有写入会话的预览')).toHaveCount(0)
    await expect(page.getByRole('list', { name: '执行过程' })).toContainText('第 1 次模型调用')
    await expect(send).toBeDisabled()
    if (renewed) {
      turns[0] = {
        id: turnId,
        question,
        state: 'completed',
        result: { status: 'answered', answer: '根据第六条，可进行专项检查。', citations: [] },
      }
      turns.push({ id: 'newer-turn', question: '另一个问题', state: 'running', result: null })
      await expect(page.getByText('根据第六条，可进行专项检查。')).toBeVisible()
      await expect(page.getByLabel('你想了解什么？')).toHaveValue('')
      await expect(page.getByText('回答连接已中断', { exact: false })).toHaveCount(0)
      await expect(send).toBeDisabled()
    } else {
      turns[0].state = 'interrupted'
      await expect(page.getByText('本轮回答未完成。')).toBeVisible()
      await expect(page.getByText(paragraph)).toBeVisible()
      await expect(page.getByLabel('你想了解什么？')).toHaveValue(question)
      await expect(send).toBeEnabled()
      await page.reload()
      await expect(page.getByLabel('你想了解什么？')).toHaveValue(question)
      await expect(send).toBeEnabled()
    }
  })
}
