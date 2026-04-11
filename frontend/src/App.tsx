import React, { useEffect, useState } from 'react'
import { LangProvider, useLang } from './LangContext'
import { ToastProvider } from './components/Toast'
import { ScanPage } from './pages/ScanPage'
import { CollectionPage } from './pages/CollectionPage'
import { SettingsPage } from './pages/SettingsPage'
import { CardDetailPage } from './pages/CardDetailPage'
import { PersonDetailPage } from './pages/PersonDetailPage'

const LAUNCHER = 'http://127.0.0.1:8001'
const HEALTH = '/api/v1/health'
const POLL_INTERVAL = 1500  // ms between health checks
const POLL_TIMEOUT = 30_000 // ms before giving up

async function startBackend(): Promise<void> {
  try {
    await fetch(`${LAUNCHER}/start`, { method: 'POST' })
  } catch {
    // Launcher not running — backend may already be up, proceed to health poll
  }
}

async function waitForBackend(): Promise<boolean> {
  const deadline = Date.now() + POLL_TIMEOUT
  while (Date.now() < deadline) {
    try {
      const res = await fetch(HEALTH)
      if (res.ok) return true
    } catch { /* not yet */ }
    await new Promise(r => setTimeout(r, POLL_INTERVAL))
  }
  return false
}

function useRoute() {
  const path = window.location.pathname
  if (path.startsWith('/scan')) return 'scan'
  if (path.startsWith('/settings')) return 'settings'
  if (path.startsWith('/cards/')) return 'card-detail'
  if (path.startsWith('/persons/')) return 'person-detail'
  return 'collection'
}

function Shell() {
  const route = useRoute()
  const { t, lang, setLang } = useLang()

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="border-b border-gray-200 bg-white sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 flex items-center gap-6 h-12">
          <span className="font-semibold text-gray-900 text-sm">🪪 {t.appName}</span>
          <a href="/collection" className={`text-sm ${route === 'collection' || route === 'card-detail' || route === 'person-detail' ? 'text-blue-600 font-medium' : 'text-gray-600 hover:text-gray-900'}`}>
            {t.navCollection}
          </a>
          <a href="/scan" className={`text-sm ${route === 'scan' ? 'text-blue-600 font-medium' : 'text-gray-600 hover:text-gray-900'}`}>
            {t.navScan}
          </a>
          <a href="/settings" className={`text-sm ${route === 'settings' ? 'text-blue-600 font-medium' : 'text-gray-600 hover:text-gray-900'}`}>
            {t.navSettings}
          </a>
          <div className="ml-auto">
            <button
              onClick={() => setLang(lang === 'ja' ? 'en' : 'ja')}
              className="text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
            >
              {t.langToggle}
            </button>
          </div>
        </div>
      </nav>

      <main>
        {route === 'scan' ? <ScanPage />
          : route === 'settings' ? <SettingsPage />
          : route === 'card-detail' ? <CardDetailPage />
          : route === 'person-detail' ? <PersonDetailPage />
          : <CollectionPage />}
      </main>
    </div>
  )
}

function BackendGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false)
  const [timedOut, setTimedOut] = useState(false)

  useEffect(() => {
    // Tell launcher to start Docker, then poll until the API responds
    startBackend().then(() => waitForBackend()).then(ok => {
      if (ok) setReady(true)
      else setTimedOut(true)
    })

    // Tell launcher to stop Docker when this tab closes
    const handleUnload = () => {
      navigator.sendBeacon(`${LAUNCHER}/stop`)
    }
    window.addEventListener('beforeunload', handleUnload)
    return () => window.removeEventListener('beforeunload', handleUnload)
  }, [])

  if (timedOut) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-gray-700 font-medium">Backend did not start in time.</p>
          <p className="text-gray-500 text-sm">Check that Docker is running, then refresh.</p>
          <button
            className="text-sm text-blue-600 underline"
            onClick={() => { setTimedOut(false); startBackend().then(() => waitForBackend()).then(ok => ok ? setReady(true) : setTimedOut(true)) }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!ready) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center space-y-3">
          <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-gray-500 text-sm">Starting backend…</p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}

export default function App() {
  return (
    <LangProvider>
      <ToastProvider>
        <BackendGate>
          <Shell />
        </BackendGate>
      </ToastProvider>
    </LangProvider>
  )
}
