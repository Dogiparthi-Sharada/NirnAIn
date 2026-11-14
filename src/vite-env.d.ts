/**
 * Types of the build-time `VITE_*` variables the app reads.
 *
 * Purpose: Lets TypeScript check every use of `import.meta.env`, so a misspelled variable fails
 * the type-check instead of the running app.
 * How it fits: Read by the compiler only; the values are validated at startup by `lib/env.ts`;
 * layer: config.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
