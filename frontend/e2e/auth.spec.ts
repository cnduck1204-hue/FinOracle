import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const users: string[] = []
const password = 'FinOracle123'
const username = () => {
  const name = `e2e_${randomUUID().replaceAll('-', '').slice(0, 16)}`
  users.push(name)
  return name
}
const backend = process.env.E2E_API_URL || 'http://127.0.0.1:8001/api/v1'

async function seed(request: APIRequestContext) {
  const name = username()
  const response = await request.post(`${backend}/auth/register`, {
    data: { username: name, email: `${name}@example.com`, password, confirm_password: password },
  })
  expect(response.status()).toBe(201)
  return name
}
async function login(page: Page, name: string, destination = 'chat') {
  await page.getByLabel('用户名或邮箱', { exact: true }).fill(name)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '进入工作台', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/${destination}$`))
}

test.afterAll(() => {
  if (!users.length) return
  execFileSync(
    'uv',
    [
      '--directory',
      fileURLToPath(new URL('../../backend', import.meta.url)),
      'run',
      '--locked',
      'python',
      fileURLToPath(new URL('./cleanup.py', import.meta.url)),
      ...users,
    ],
    { stdio: 'pipe', timeout: 20_000 },
  )
})

test('real registration, login, one-flight restore, draft and both logout flows', async ({
  page,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const name = username()
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
  await page.screenshot({
    path: 'test-results/preview-login.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('link', { name: /注册账号/ }).click()
  await page.getByLabel('用户名', { exact: true }).fill(name)
  await page.getByLabel('邮箱', { exact: true }).fill(`${name}@example.com`)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '创建账号', exact: true }).click()
  await expect(page.getByText('账号已创建，请使用新账号登录。')).toBeVisible()
  await login(page, name)
  await page.getByRole('button', { name: /先厘清制度范围/ }).click()
  await page.getByLabel('你想了解什么？').fill('我想梳理业务场景中的制度适用范围。')
  await page.getByRole('button', { name: '保存问题草稿' }).click()
  await expect(page.getByRole('status')).toContainText('草稿已保存在当前标签页')
  const refreshes: string[] = []
  page.on('request', (request) => {
    if (request.url().endsWith('/auth/refresh')) refreshes.push(request.url())
  })
  await page.reload()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(page.getByLabel('你想了解什么？')).toHaveValue('我想梳理业务场景中的制度适用范围。')
  expect(refreshes).toHaveLength(1)
  await page.screenshot({
    path: 'test-results/preview-workspace.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('link', { name: '打开我的账号', exact: true }).click()
  await expect(page.getByText(`${name}@example.com`, { exact: true })).toBeVisible()
  await page.screenshot({
    path: 'test-results/preview-account.png',
    fullPage: true,
    animations: 'disabled',
  })
  await page.getByRole('button', { name: '退出当前会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
  await login(page, name, 'account')
  await page.getByRole('link', { name: '打开我的账号', exact: true }).click()
  await page.getByRole('button', { name: '退出全部会话', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: '暂不退出', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('button', { name: '退出全部会话', exact: true }).click()
  await page.getByRole('button', { name: '确认全部退出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
  expect(errors).toEqual([])
})

test('validation and real incorrect-password response stay on the login page', async ({
  page,
  request,
}) => {
  const name = await seed(request)
  await page.goto('/login')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByText('请输入用户名或邮箱，至少 3 个字符。')).toBeVisible()
  await page.getByLabel('用户名或邮箱', { exact: true }).fill(name)
  await page.getByLabel('密码', { exact: true }).fill('Wrong123')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('alert')).toContainText('账号或密码错误')
  await expect(page).toHaveURL(/\/login$/)
})

test('mobile layouts, navigation and account operations have no horizontal overflow', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const name = await seed(request)
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
  await page.screenshot({
    path: 'test-results/preview-mobile-login.png',
    fullPage: true,
    animations: 'disabled',
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await login(page, name)
  await page.screenshot({
    path: 'test-results/preview-mobile-workspace.png',
    fullPage: true,
    animations: 'disabled',
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('link', { name: /制度库/ }).click()
  await expect(page.getByRole('heading', { name: '制度库，正在准备。' })).toBeVisible()
  await page.getByRole('link', { name: '打开我的账号', exact: true }).click()
  await expect(page.getByText(`${name}@example.com`, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.getByRole('button', { name: '退出当前会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
})

test('a copied tab requests a separate login without revoking the original session', async ({
  page,
  request,
}) => {
  const name = await seed(request)
  await page.goto('/login')
  await login(page, name)
  const opened = page.waitForEvent('popup')
  await page.evaluate(() => {
    window.open('/chat', '_blank')
  })
  const copied = await opened
  await expect(copied.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
  await expect(copied.getByText('此登录正在另一个标签页使用，请在本页重新登录。')).toBeVisible()
  await copied.close()
  await page.bringToFront()
  await page.reload()
  await expect(page.getByLabel('你想了解什么？')).toBeVisible()
  await page.getByRole('link', { name: '打开我的账号', exact: true }).click()
  await page.getByRole('button', { name: '退出当前会话', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来。' })).toBeVisible()
})
