import { chromium } from 'playwright-core'

const investigation = process.argv[2]
if (!/^inv_[A-Za-z0-9_-]+$/.test(investigation || '')) throw new Error('用法: node scripts/smoke-chain-rest.mjs <investigation_id>')
const base = process.env.FRONTEND_URL || 'http://127.0.0.1:5190'
const browser = await chromium.launch({ channel: 'msedge', headless: true })
try {
  const page = await browser.newPage()
  await page.goto(`${base}/#/reports/${investigation}`, { waitUntil: 'domcontentloaded' })
  if (await page.getByRole('dialog', { name: '登录' }).isVisible().catch(() => false)) {
    await page.getByLabel('邮箱').fill('admin@sense2act.local')
    await page.getByLabel('密码').fill(process.env.SEED_ADMIN_PASSWORD || 'admin123')
    await page.getByRole('button', { name: '登录' }).click()
  }
  const link = page.getByRole('link', { name: /查看证据链/ })
  await link.waitFor({ state: 'visible', timeout: 30_000 })
  await link.click()
  await page.waitForURL(url => url.hash.includes(`investigation=${investigation}`), { timeout: 15_000 })
  await page.waitForSelector('.react-flow__node', { state: 'visible', timeout: 30_000 })
  const nodes = await page.locator('.react-flow__node').count()
  const eventId = (await page.locator('.chain-toolbar-id strong').first().textContent())?.trim()
  console.log(`PASS investigation=${investigation} event=${eventId} rendered_nodes=${nodes}`)
} finally {
  await browser.close()
}
