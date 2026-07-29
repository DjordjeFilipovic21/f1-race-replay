import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const workspaceRoot = fileURLToPath(new URL('..', import.meta.url))
const localSeasonsRoot = fileURLToPath(new URL('../artifacts/seasons/', import.meta.url))

export default defineConfig(({ command, mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const configuredSeasonsBaseUrl = process.env.VITE_REPLAY_SEASONS_BASE_URL ?? environment.VITE_REPLAY_SEASONS_BASE_URL
  const seasonsBaseUrl = configuredSeasonsBaseUrl
    || (command === 'serve' ? `/@fs${localSeasonsRoot}/` : '/replay-data/seasons/')

  return {
    plugins: [react()],
    base: process.env.VITE_WEB_BASE ?? '/',
    define: {
      'import.meta.env.VITE_REPLAY_SEASONS_BASE_URL': JSON.stringify(seasonsBaseUrl),
    },
    server: {
      fs: { allow: [workspaceRoot] },
    },
  }
})
