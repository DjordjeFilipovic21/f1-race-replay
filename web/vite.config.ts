import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const workspaceRoot = fileURLToPath(new URL('..', import.meta.url))
const localReplayRoot = fileURLToPath(new URL('../artifacts/seasons/2024/browser/2024-round-21-sao-paulo-grand-prix/', import.meta.url))

export default defineConfig(({ command, mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const configuredReplayBase = process.env.VITE_REPLAY_DATA_BASE_URL ?? environment.VITE_REPLAY_DATA_BASE_URL
  const replayDataBaseUrl = configuredReplayBase || (command === 'serve' ? `/@fs${localReplayRoot}/` : '/replay-data/')

  return {
    plugins: [react()],
    base: process.env.VITE_WEB_BASE ?? '/',
    define: {
      'import.meta.env.VITE_REPLAY_DATA_BASE_URL': JSON.stringify(replayDataBaseUrl),
    },
    server: {
      fs: { allow: [workspaceRoot] },
    },
  }
})
