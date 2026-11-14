/**
 * The browser entry point: checks the configuration, then renders the app or the configuration error page.
 *
 * Purpose: The app must never start with missing configuration (the Supabase client would be built
 * without its keys), and an error no route catches must still end on the crash page instead of a
 * blank screen.
 * How it fits: Loaded by `index.html`; imports `index.css`, reads `lib/env.ts` and lazily loads
 * `App.tsx`; layer: config.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import React from 'react'
import ReactDOM from 'react-dom/client'
import { envResult } from './lib/env'
import { ConfigErrorPage, CrashPage } from './components/ErrorPages'
import './index.css'

const root = ReactDOM.createRoot(document.getElementById('root')!, {
  // The last resort for an error no route caught (e.g. in a provider outside the router): React
  // unmounts the app, so log it and show the crash page in its place.
  onUncaughtError: (error, errorInfo) => {
    console.error('Unhandled application error', error, errorInfo.componentStack)
    root.render(<CrashPage />)
  },
})

if (envResult.ok) {
  // Loaded lazily so the Supabase client is never constructed with missing config.
  void import('./App').then(({ default: App }) => {
    root.render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    )
  })
} else {
  console.error('Missing or invalid configuration', envResult.problems)
  root.render(<ConfigErrorPage problems={envResult.problems} />)
}
