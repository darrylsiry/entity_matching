import { defineConfig } from '@playwright/test'

// E2E runs against the PRODUCTION-mode server: the FastAPI backend serving the built
// frontend from one origin (exactly how the Docker image runs). Start it with:
//   cd backend && FRONTEND_DIST=../frontend/dist .venv/bin/uvicorn app.main:app --port 8100
// then: npm run e2e   (or set E2E_BASE_URL to point elsewhere)
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8100',
    trace: 'retain-on-failure',
  },
})
