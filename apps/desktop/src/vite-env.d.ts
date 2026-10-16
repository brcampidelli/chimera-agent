/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CHIMERA_SERVER_TOKEN?: string;
}

interface Window {
  __CHIMERA_SERVER_TOKEN__?: string;
}
