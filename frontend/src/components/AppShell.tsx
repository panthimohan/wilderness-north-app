import type { ReactNode } from 'react'
import type { DataMode, Stage } from '../types/api'

export type PageKey = 'orders' | 'picking' | 'flights'

const tabs: { id: PageKey; label: string; icon: string; number: string }[] = [
  { id: 'orders', label: 'Order Entry', icon: '▤', number: '01' },
  { id: 'picking', label: 'Order Picking', icon: '▦', number: '02' },
  { id: 'flights', label: 'Flight Management', icon: '◒', number: '03' },
]

export function AppShell({
  page, onNavigate, stage, mode, datasetName, onDatasetChange, onReset, children,
}: {
  page: PageKey
  onNavigate: (page: PageKey) => void
  stage: Stage
  mode: DataMode
  datasetName: string | null
  onDatasetChange: (dataset: Stage | "user") => void
  onReset: () => void
  children: ReactNode
}) {
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand-lockup"><div className="brand-mark">W<span>N</span></div><div><strong>Wilderness North</strong><small>FULFILLMENT CONTROL</small></div></div>
        <div className="sidebar-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main sections">
          {tabs.map((tab) => (
            <button key={tab.id} className={`nav-item ${page === tab.id ? 'active' : ''}`} onClick={() => onNavigate(tab.id)}>
              <span className="nav-icon">{tab.icon}</span><span>{tab.label}</span><small>{tab.number}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-route"><span className="route-dot" /><div><strong>NAKINA → WEBEQUIE</strong><small>REMOTE GROCERY LOGISTICS</small></div></div>
        <div className="sidebar-footer">HackGB 2026 <span>·</span> Planning workspace</div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>Operations</span><b>/</b><strong>{tabs.find((tab) => tab.id === page)?.label}</strong></div>
          <div className="top-actions">
            <label className="stage-picker"><span>ACTIVE DATASET</span><select value={mode === "user" ? "user" : stage} onChange={(event) => onDatasetChange(event.target.value as Stage | "user")}><option value="stage1">Challenge · Stage 1</option><option value="stage2">Challenge · Stage 2</option><option value="user">User Data{datasetName ? ` · ${datasetName}` : ""}</option></select></label>
            <button className="icon-button" title="Reset planning session" aria-label="Reset planning session" onClick={onReset}>↺</button>
            <div className="user-avatar" title="Local planning session">WN</div>
          </div>
        </header>
        <div className="page-content">{children}</div>
        <footer className="app-footer"><span>Wilderness North <b>·</b> Zamiigo fulfillment planning</span><span>Session data is held in memory</span></footer>
      </main>
    </div>
  )
}
