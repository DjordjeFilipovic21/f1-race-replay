interface ImportMetaEnv {
  readonly VITE_WEB_BASE?: string
  readonly VITE_REPLAY_SEASONS_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
