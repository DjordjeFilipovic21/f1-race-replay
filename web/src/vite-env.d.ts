interface ImportMetaEnv {
  readonly VITE_WEB_BASE?: string
  readonly VITE_REPLAY_SEASONS_BASE_URL?: string
  readonly VITE_REPLAY_SEASON_YEARS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
