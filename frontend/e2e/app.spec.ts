import { expect, test } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// End-to-end flows against the prod-mode server (backend serving built frontend).
// The link flow uses the bundled 1k-row test files via the "test data" links; the
// dedupe flow uploads the small generated sample (run `make sample` first).

const SAMPLE = path.resolve(__dirname, '../../sample_data')

test.describe('two-file matching', () => {
  test('full link flow: upload → map → run → review → decide', async ({ page }) => {
    await page.goto('/')
    await page.getByText('Compare two files').click()

    // one-click bundled test data into both dropzones
    const links = page.getByText('test data', { exact: true })
    await links.nth(0).click()
    await page.getByText('companies_file_a.csv').waitFor()
    await links.nth(1).click()
    await page.getByText('companies_file_b.csv').waitFor()
    await page.getByRole('button', { name: /map columns/i }).click()

    // auto-guessed mapping is accepted as-is
    await page.getByRole('button', { name: /configure/i }).click()
    await page.getByRole('button', { name: /^Next: run/i }).click()

    await page.getByRole('button', { name: /start matching/i }).click()
    await page.getByRole('button', { name: /view results/i }).waitFor({ timeout: 90_000 })
    await page.getByRole('button', { name: /view results/i }).click()

    // reconciliation bars render with real totals
    await expect(page.locator('.reconcile-row')).toHaveCount(2)
    await expect(page.locator('.reconcile-row').first()).toContainText('1,000 rows')

    // provenance: at least one pair card shows a match reason
    await expect(page.locator('.reason').first()).toBeVisible()

    // review workflow: confirm the first review pair if any exist
    const reviewTab = page.getByRole('button', { name: /^Review/ })
    await reviewTab.click()
    const reviewCards = page.locator('.pair-card.review')
    if ((await reviewCards.count()) > 0) {
      await reviewCards.first().getByRole('button', { name: /Confirm match/i }).click()
      await expect(page.locator('.pair-card.review').first()).not.toBeVisible({
        timeout: 10_000,
      })
    }

    // CSV download responds
    const download = page.waitForEvent('download')
    await page.getByRole('link', { name: /Download CSV/i }).click()
    expect((await download).suggestedFilename()).toContain('matches_')
  })
})

test.describe('single-file dedupe', () => {
  test('dedupe flow: upload → run → clusters + possible duplicates', async ({ page }) => {
    await page.goto('/')
    await page.getByText('Deduplicate one file').click()

    await page
      .locator('input[type=file]')
      .first()
      .setInputFiles(path.join(SAMPLE, 'file_dedupe.csv'))
    await page.getByText('file_dedupe.csv').waitFor()
    await page.getByRole('button', { name: /configure/i }).click()
    await page.getByRole('button', { name: /^Next: run/i }).click()

    await page.getByRole('button', { name: /start deduplication/i }).click()
    await page.getByRole('button', { name: /view results/i }).waitFor({ timeout: 90_000 })
    await page.getByRole('button', { name: /view results/i }).click()

    // master-ID clusters render
    await expect(page.locator('.badge.master').first()).toBeVisible()

    // the sample ships one borderline pair -> possible-duplicates review works
    await page.getByRole('button', { name: /Possible duplicates/ }).click()
    const cand = page.locator('.pair-card').first()
    await expect(cand).toBeVisible()
    await cand.getByRole('button', { name: /Same company/i }).click()
    await expect(page.getByText('No possible duplicates in this view.')).toBeVisible({
      timeout: 10_000,
    })
  })
})
