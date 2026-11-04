/*
 * Browser smoke test for the platform shell and optional assessment workflow.
 * Start the backend and frontend first, then run: node tests/frontend/e2e-test.js
 * Set CONTENT_ASSESSMENT_E2E_FULL=1 with an authenticated storage state to run
 * the backend-dependent workflow assertions.
 */

const { chromium } = require('playwright')

const frontendUrl = process.env.CONTENT_ASSESSMENT_E2E_URL || 'https://www.nstl.gov.cn/ai/agent/content_assessment/'
const fullWorkflow = process.env.CONTENT_ASSESSMENT_E2E_FULL === '1'
const storageState = process.env.CONTENT_ASSESSMENT_E2E_STORAGE_STATE
const browserPath = process.env.CONTENT_ASSESSMENT_E2E_BROWSER_PATH

async function main() {
  const browser = await chromium.launch({ headless: true, ...(browserPath ? { executablePath: browserPath } : {}) })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
    ...(storageState ? { storageState } : {}),
  })
  const page = await context.newPage()
  try {
    await page.goto(frontendUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.getByRole('heading', { name: '面向科研场景的智能问答与专业能力服务' }).waitFor()
    await page.locator('.hl-pill').filter({ hasText: '智能选题智能体' }).waitFor()
    await page.locator('.hl-pill').filter({ hasText: '创新性评估智能体' }).click()
    await page.getByRole('heading', { name: '创新性评估智能体' }).waitFor()

    const modelControl = page.getByRole('button', { name: /可信评估模型/ })
    if (await modelControl.isEnabled()) throw new Error('model selector should be disabled until the capability is available')
    const voiceControl = page.getByRole('button', { name: '语音输入功能暂未开放' })
    if (await voiceControl.isEnabled()) throw new Error('voice input should be disabled until the capability is available')

    await page.getByRole('button', { name: '高级设置' }).click()
    const sampleCards = page.locator('.report-sample-card')
    if (await sampleCards.count() !== 3) throw new Error('expected three assessment samples')
    await sampleCards.first().click()
    await page.getByRole('textbox', { name: /输入评估对象/ }).fill('拟构建多器官芯片培养系统，目标是提高稳定性并在药物毒性场景验证。')

    await page.getByRole('button', { name: '收起侧边栏' }).click()
    if (!await page.locator('#platform-sidebar').evaluate((element) => element.classList.contains('is-collapsed'))) {
      throw new Error('desktop sidebar did not collapse')
    }
    await page.getByRole('button', { name: '展开侧边栏' }).click()

    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '打开平台导航' }).click()
    if (!await page.locator('#platform-sidebar').evaluate((element) => element.classList.contains('is-mobile-open'))) {
      throw new Error('mobile navigation drawer did not open')
    }
    await page.getByRole('button', { name: '关闭平台导航' }).click()
    await page.setViewportSize({ width: 1440, height: 900 })

    if (!fullWorkflow) {
      console.log('Platform homepage and responsive sidebar smoke test passed.')
      return
    }

    await page.getByRole('button', { name: '发送' }).click()
    await page.locator('.task-detail-page').waitFor()

    const stageCount = await page.locator('.modern-orbit__stage').count()
    if (stageCount !== 8) throw new Error(`expected 8 stages, got ${stageCount}`)
    await page.getByRole('button', { name: '执行过程' }).waitFor()
    if (await page.locator('.legacy-task-ui').count() !== 0) throw new Error('legacy task template should not render')
    await page.getByRole('button', { name: '结果概览' }).click()
    await page.locator('.modern-summary-card, .modern-running-card').first().waitFor({ timeout: 180000 })
    if (await page.locator('.modern-summary-card').count()) {
      await page.locator('.modern-result-grid').waitFor()
      await page.getByRole('button', { name: '站内报告' }).click()
      await page.locator('.modern-report-reader').waitFor()
    } else {
      await page.getByText(/报告正在生成中|报告正在排队|等待确认评估边界/).waitFor()
    }

    await page.getByRole('button', { name: /返回首页|返回历史任务/ }).click()
    await page.getByRole('heading', { name: '创新性评估智能体' }).waitFor()
    await page.getByText('历史任务', { exact: true }).first().click()
    await page.locator('.task-history-workspace').waitFor()
    await page.getByRole('heading', { name: '历史任务' }).waitFor()

    const taskItems = page.locator('.history-row')
    if (await taskItems.count() > 0) {
      await taskItems.first().click()
      await page.locator('.task-detail-page, .topic-run-page').first().waitFor()
      const detailKindCount = await page.locator('.modern-orbit__stage, .topic-stage-nav').count()
      if (![8, 9].includes(detailKindCount)) throw new Error(`history detail rendered an unexpected stage count: ${detailKindCount}`)
    }
    console.log('Content assessment task-page E2E smoke test passed.')
  } finally {
    await context.close()
    await browser.close()
  }
}

main().catch((error) => { console.error(error); process.exit(1) })
