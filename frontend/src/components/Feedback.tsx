import type { ReactNode } from 'react'

export function LoadingState({ label = 'Loading data…' }: { label?: string }) {
  return <div className="feedback" role="status"><span className="spinner" />{label}</div>
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="notice notice-error" role="alert">
      <div><strong>Couldn’t load this view</strong><p>{message}</p></div>
      {onRetry && <button className="button button-subtle" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon">↗</span><strong>{title}</strong>{children && <p>{children}</p>}</div>
}

export function Notice({ tone = 'info', title, children }: { tone?: 'info' | 'warning' | 'error' | 'success'; title: string; children?: ReactNode }) {
  return <div className={`notice notice-${tone}`}><div><strong>{title}</strong>{children && <p>{children}</p>}</div></div>
}

export function MetricCard({ label, value, detail, icon }: { label: string; value: string; detail?: string; icon: string }) {
  return <article className="metric-card"><span className="metric-icon">{icon}</span><div><span className="eyebrow">{label}</span><strong className="metric-value">{value}</strong>{detail && <span className="metric-detail">{detail}</span>}</div></article>
}

export function ProgressBar({ label, percent, value }: { label: string; percent: number | null; value: string }) {
  const shown = percent === null ? null : Math.max(0, Math.min(100, percent))
  return (
    <div className="progress-group">
      <div className="progress-heading"><span>{label}</span><strong>{value}</strong></div>
      <div className="progress-track" aria-label={`${label}: ${shown === null ? 'unknown' : `${shown.toFixed(1)} percent`}`}>
        {shown !== null && <span className="progress-fill" style={{ width: `${shown}%` }} />}
        {shown === null && <span className="progress-unknown">Not specified</span>}
      </div>
    </div>
  )
}
