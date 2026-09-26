import { useEffect, useState } from 'react'
import { AppShell, type PageKey } from './components/AppShell'
import { ErrorState, LoadingState, Notice } from './components/Feedback'
import { FlightManagementPage } from './pages/FlightManagementPage'
import { OrderEntryPage } from './pages/OrderEntryPage'
import { OrderPickingPage } from './pages/OrderPickingPage'
import { api } from './services/api'
import type { SessionResponse, Stage } from './types/api'
import './App.css'

function App() {
  const [page, setPage] = useState<PageKey>('orders')
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [notice, setNotice] = useState('')

  async function loadSession() {
    setLoading(true); setError('')
    try { setSession(await api.getSession()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to connect to the API') }
    finally { setLoading(false) }
  }

  useEffect(() => { void loadSession() }, [])

  async function changeDataset(dataset: Stage | 'user') {
    setError(''); setNotice('')
    try {
      if (dataset === 'user') setSession(await api.selectMode('user'))
      else if (session?.active_mode === 'user') {
        await api.selectMode('challenge')
        setSession(await api.selectStage(dataset))
      } else setSession(await api.selectStage(dataset))
      setRefreshKey((key) => key + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to change active dataset') }
  }

  function importCompleted(nextSession: SessionResponse, summary: string) {
    setSession(nextSession)
    setRefreshKey((key) => key + 1)
    setNotice(summary + ' The same imported orders are now active for picking and flight management; previous plans were cleared.')
    window.setTimeout(() => setNotice(''), 6000)
  }

  async function resetSession() {
    setError('')
    try {
      setSession(await api.resetSession())
      setRefreshKey((key) => key + 1)
      setNotice('Planning session reset. Source orders and capacities are unchanged.')
      window.setTimeout(() => setNotice(''), 4000)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to reset session') }
  }

  return (
    <AppShell page={page} onNavigate={setPage} stage={session?.active_stage ?? 'stage1'} mode={session?.active_mode ?? 'challenge'} datasetName={session?.active_dataset_name ?? null} onDatasetChange={(dataset) => void changeDataset(dataset)} onReset={() => void resetSession()}>
      {notice && <Notice tone="success" title={session?.active_mode === 'user' ? 'User data imported' : 'Planning session'}>{notice}</Notice>}
      {error && <ErrorState message={error} onRetry={() => void loadSession()} />}
      {loading && !session ? <LoadingState label="Connecting to fulfillment API…" /> : session && <>
        {page === 'orders' && <OrderEntryPage stage={session.active_stage} mode={session.active_mode} refreshKey={refreshKey} onImported={importCompleted} />}
        {page === 'picking' && <OrderPickingPage stage={session.active_stage} refreshKey={refreshKey} />}
        {page === 'flights' && <FlightManagementPage stage={session.active_stage} mode={session.active_mode} refreshKey={refreshKey} />}
      </>}
    </AppShell>
  )
}

export default App
