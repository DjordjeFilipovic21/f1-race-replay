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
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            minSize: 20_000,
            maxSize: 450_000,
            groups: [
              { name: 'react-vendor', test: /node_modules\/(?:react|react-dom|scheduler)\// },
              { name: 'geo-vendor', test: /node_modules\/(?:d3-geo|topojson-client|world-atlas)\// },
              { name: 'dnd-vendor', test: /node_modules\/@dnd-kit\// },
            ],
          },
        },
      },
    },
    server: {
      fs: { allow: [workspaceRoot] },
    },
  }
})
