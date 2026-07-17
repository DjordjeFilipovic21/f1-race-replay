interface ImportMetaEnv {
  readonly VITE_WEB_BASE?: string
  readonly VITE_REPLAY_DATA_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
