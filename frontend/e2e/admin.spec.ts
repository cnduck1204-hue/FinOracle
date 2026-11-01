import { test, expect, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const backend = process.env.E2E_API_URL || 'http://127.0.0.1:8001/api/v1'
const prefix = `e2e_${randomUUID().replaceAll('-', '').slice(0, 8)}`
const names: string[] = []
const password = 'FinOracle123'
const records: { id: string; username: string; email: string }[] = []
let admin: { id: string; username: string }
let access: string
const name = () => {
  const value = prefix + (names.length + 1).toString(16).padStart(8, '0')
  names.push(value)
  return value
}
function helper(script: string, input?: string) {
  return execFileSync(
    'uv',
    [
      '--directory',
      fileURLToPath(new URL('../../backend', import.meta.url)),
      'run',
      '--locked',
      'python',
      fileURLToPath(new URL(script, import.meta.url)),
      ...(input ? [] : names),
    ],
    { input, encoding: 'utf8', timeout: 20_000 },
  )
}
async function signIn(page: Page, username: string) {
  await page.getByLabel('用户名或邮箱', { exact: true }).fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
}
async function openAdmin(page: Page) {
  await page.goto('/admin/users')
  await signIn(page, admin.username)
  await expect(page.getByRole('heading', { name: '用户管理', exact: true })).toBeVisible()
}
async function search(page: Page, value: string) {
  await page.getByLabel('用户名或邮箱搜索').fill(value)
  await page.getByRole('button', { name: '查询', exact: true }).click()
}

test.beforeAll(async ({ request }) => {
  admin = JSON.parse(helper('./create_admin.py', JSON.stringify({ username: name(), password })))
  const login = await request.post(`${backend}/auth/login`, {
    data: { identifier: admin.username, password },
  })
  expect(login.status()).toBe(200)
  access = (await login.json()).access_token
  for (let i = 0; i < 12; i++) {
    const username = name()
    const response = await request.post(`${backend}/auth/register`, {
      data: { username, email: `${username}@example.com`, password, confirm_password: password },
    })
    expect(response.status()).toBe(201)
    records.push(await response.json())
  }
  const disabled = await request.patch(`${backend}/admin/users/${records[11].id}/status`, {
    headers: { Authorization: `Bearer ${access}` },
    data: { status: 'disabled' },
  })
  expect(disabled.status()).toBe(200)
})
test.afterAll(() => {
  if (names.length) helper('./cleanup.py')
})

test('ordinary users cannot enter user management or issue administrator requests', async ({
  page,
}) => {
  const adminRequests: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/admin/')) adminRequests.push(request.url())
  })
  await page.goto('/admin/users')
  await signIn(page, records[0].username)
  await expect(page.getByRole('heading', { name: '没有访问权限' })).toBeVisible()
  await expect(page.getByRole('link', { name: '用户管理', exact: true })).toHaveCount(0)
  expect(adminRequests).toEqual([])
})

for (const mobile of [false, true]) {
  test(`administrator search, filters, pagination and details on ${mobile ? 'mobile' : 'desktop'}`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 375, height: 812 })
    await openAdmin(page)
    await page.getByLabel('每页条数').selectOption('10')
    await search(page, prefix)
    const table = page.getByRole('table', { name: '用户列表', exact: true })
    await expect(table.getByRole('row')).toHaveCount(11)
    await expect(page.getByText('共 13 位用户', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await expect(table.getByRole('row')).toHaveCount(4)
    await expect(page.getByText('第 2 / 2 页', { exact: true })).toBeVisible()
    await page.getByLabel('账号角色', { exact: true }).selectOption('user')
    await page.getByLabel('账号状态', { exact: true }).selectOption('disabled')
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await expect(table.getByRole('row')).toHaveCount(2)
    await expect(page.getByText('第 1 / 1 页', { exact: true })).toBeVisible()
    await table.getByRole('button', { name: '查看详情', exact: true }).click()
    const detail = page.getByRole('region', { name: '用户详情', exact: true })
    await expect(detail.getByText(records[11].id, { exact: true })).toBeVisible()
    await expect(detail.getByText('已禁用', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '关闭详情', exact: true }).click()
    await page.getByRole('button', { name: '重置', exact: true }).click()
    await expect(page.getByLabel('账号角色', { exact: true })).toHaveValue('')
    await expect(page.getByLabel('账号状态', { exact: true })).toHaveValue('')
    await search(page, admin.username)
    const row = table.getByRole('row').filter({ hasText: admin.username })
    await expect(row.getByText('管理员账号受保护')).toBeVisible()
    await expect(row.getByRole('button', { name: '禁用', exact: true })).toHaveCount(0)
    await row.getByRole('button', { name: '查看详情', exact: true }).click()
    await expect(detail.getByText(admin.id, { exact: true })).toBeVisible()
    await expect(detail.getByRole('button', { name: /^(启用|禁用)$/ })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({
      path: `test-results/preview-admin-${mobile ? 'mobile' : 'desktop'}.png`,
      fullPage: true,
    })
  })
}

test('cancel, disable and re-enable synchronize details and reject old logins', async ({
  page,
  request,
  browser,
}) => {
  const target = records[0]
  const oldLogin = await request.post(`${backend}/auth/login`, {
    data: { identifier: target.username, password },
  })
  const oldPair = await oldLogin.json()
  const userContext = await browser.newContext()
  const userPage = await userContext.newPage()
  try {
    await userPage.goto('/login')
    await signIn(userPage, target.username)
    await expect(userPage).toHaveURL(/\/chat$/)
    await openAdmin(page)
    await page.getByLabel('账号状态', { exact: true }).selectOption('active')
    await search(page, target.username)
    const table = page.getByRole('table', { name: '用户列表', exact: true })
    await table.getByRole('button', { name: '查看详情', exact: true }).click()
    const detail = page.getByRole('region', { name: '用户详情', exact: true })
    await expect(detail.getByText(target.id, { exact: true })).toBeVisible()
    const disable = table.getByRole('button', { name: '禁用', exact: true })
    await disable.focus()
    await disable.press('Enter')
    await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).press('Enter')
    await expect(disable).toBeFocused()
    await expect(table.getByText('已启用', { exact: true })).toBeVisible()
    await userPage.reload()
    await expect(userPage).toHaveURL(/\/chat$/)
    await table.getByRole('button', { name: '禁用', exact: true }).click()
    await page.getByRole('button', { name: '确认禁用', exact: true }).click()
    await expect(page.getByText('没有符合条件的用户。', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '账号列表', exact: true })).toBeFocused()
    await expect(detail.getByText('已禁用', { exact: true })).toBeVisible()
    await userPage.reload()
    await expect(userPage.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
    await page.getByLabel('账号状态', { exact: true }).selectOption('disabled')
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await table.getByRole('button', { name: '启用', exact: true }).click()
    await page.getByRole('button', { name: '确认启用', exact: true }).click()
    await expect(page.getByText('没有符合条件的用户。', { exact: true })).toBeVisible()
    const rejected = await request.get(`${backend}/users/me`, {
      headers: { Authorization: `Bearer ${oldPair.access_token}` },
    })
    expect(rejected.status()).toBe(401)
    const refresh = await request.post(`${backend}/auth/refresh`, {
      data: { refresh_token: oldPair.refresh_token },
    })
    expect(refresh.status()).toBe(401)
    await signIn(userPage, target.username)
    await expect(userPage).toHaveURL(/\/chat$/)
  } finally {
    await userContext.close()
  }
})

test('late details cannot replace a newer selection and list failures can be retried', async ({
  page,
}) => {
  await openAdmin(page)
  await search(page, prefix)
  let release!: () => void
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route(`**/api/v1/admin/users/${records[0].id}`, async (route) => {
    const response = await route.fetch()
    await held
    await route.fulfill({ response })
  })
  const table = page.getByRole('table', { name: '用户列表', exact: true })
  await table
    .getByRole('row')
    .filter({ hasText: records[0].username })
    .getByRole('button', { name: '查看详情' })
    .click()
  await table
    .getByRole('row')
    .filter({ hasText: records[1].username })
    .getByRole('button', { name: '查看详情' })
    .click()
  const detail = page.getByRole('region', { name: '用户详情', exact: true })
  await expect(detail.getByText(records[1].id, { exact: true })).toBeVisible()
  const late = page.waitForResponse((response) =>
    response.url().endsWith(`/admin/users/${records[0].id}`),
  )
  release()
  await late
  await expect(detail.getByText(records[1].id, { exact: true })).toBeVisible()
  await page.route('**/api/v1/admin/users?**', async (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'SERVICE_UNAVAILABLE',
        message: '服务暂时不可用',
        request_id: 'admin-e2e',
      }),
    }),
  )
  await page.getByRole('button', { name: '刷新列表', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('服务暂时不可用')
  await page.unroute('**/api/v1/admin/users?**')
  await page.getByRole('button', { name: '重试加载', exact: true }).click()
  await expect(table.getByRole('row')).toHaveCount(14)
})

test('a late list response cannot overwrite a newer search', async ({ page }) => {
  await openAdmin(page)
  let release!: () => void
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/v1/admin/users?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('q') !== records[0].username) {
      await route.continue()
      return
    }
    const response = await route.fetch()
    await held
    await route.fulfill({ response })
  })
  await search(page, records[0].username)
  await search(page, records[1].username)
  const table = page.getByRole('table', { name: '用户列表', exact: true })
  await expect(table.getByText(records[1].username, { exact: true })).toBeVisible()
  const late = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get('q') === records[0].username,
  )
  release()
  await late
  await expect(table.getByText(records[1].username, { exact: true })).toBeVisible()
  await expect(table.getByText(records[0].username, { exact: true })).toHaveCount(0)
})

test('a failed status change keeps the dialog and can be retried once', async ({
  page,
  request,
}) => {
  const target = records[2]
  await openAdmin(page)
  await search(page, target.username)
  const table = page.getByRole('table', { name: '用户列表', exact: true })
  await table.getByRole('button', { name: '禁用', exact: true }).click()
  const path = `**/api/v1/admin/users/${target.id}/status`
  await page.route(path, async (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'SERVICE_UNAVAILABLE',
        message: '请稍后重试',
        request_id: 'status-e2e',
      }),
    }),
  )
  await page.getByRole('button', { name: '确认禁用', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('请稍后重试')
  await expect(table.getByText('已启用', { exact: true })).toBeVisible()
  await page.unroute(path)
  let writes = 0
  page.on('request', (request) => {
    if (request.method() === 'PATCH' && request.url().endsWith(`/${target.id}/status`)) writes++
  })
  await page.getByRole('button', { name: '确认禁用', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(table.getByText('已禁用', { exact: true })).toBeVisible()
  expect(writes).toBe(1)
  const restored = await request.patch(`${backend}/admin/users/${target.id}/status`, {
    headers: { Authorization: `Bearer ${access}` },
    data: { status: 'active' },
  })
  expect(restored.status()).toBe(200)
})
