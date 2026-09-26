import { useEffect, useMemo, useState } from 'react'
import { EmptyState, ErrorState, LoadingState, MetricCard, Notice } from '../components/Feedback'
import { DataImportWizard } from '../components/DataImportWizard'
import { api } from '../services/api'
import type { DataMode, OrderResponse, OrderSummaryResponse, SessionResponse, Stage } from '../types/api'

const number = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 1 })
const weight = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 2 })

export function OrderEntryPage({ stage, mode, refreshKey, onImported }: { stage: Stage; mode: DataMode; refreshKey: number; onImported: (session: SessionResponse, message: string) => void }) {
  const [orders, setOrders] = useState<OrderResponse[]>([])
  const [summary, setSummary] = useState<OrderSummaryResponse | null>(null)
  const [query, setQuery] = useState('')
  const [dateFilter, setDateFilter] = useState('')
  const [selected, setSelected] = useState<OrderResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true); setError('')
    try {
      const [nextOrders, nextSummary] = await Promise.all([
        api.getOrders(stage), api.getOrderSummary(stage),
      ])
      setOrders(nextOrders); setSummary(nextSummary)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unexpected API error') }
    finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [stage, refreshKey])

  const visibleOrders = useMemo(() => {
    const term = query.trim().toLowerCase()
    return orders.filter((order) => {
      const matchesText = !term || [order.order_id, order.household_id, order.destination_community].some((value) => String(value).toLowerCase().includes(term))
      return matchesText && (!dateFilter || order.order_date === dateFilter)
    })
  }, [orders, query, dateFilter])

  return (
    <section className="page-stack">
      <div className="page-heading"><div><div className="kicker"><span className="kicker-line" />ORDER INTAKE</div><h1>Order Entry</h1><p>Review household orders before they move into picking and flight planning.</p></div><div className="heading-tag"><span className="live-dot" />{mode === 'user' ? 'USER DATA' : stage === 'stage1' ? 'BASE CASE' : 'MULTI-DEPARTURE'}</div></div>
      <DataImportWizard onImported={onImported} />
      {error && <ErrorState message={error} onRetry={() => void load()} />}
      {loading ? <LoadingState label="Loading order data…" /> : <>
        <div className="metric-grid metric-grid-4">
          <MetricCard icon="▤" label="ORDERS" value={number.format(summary?.order_count ?? 0)} detail="Unique order IDs" />
          <MetricCard icon="⌂" label="HOUSEHOLDS" value={number.format(summary?.household_count ?? 0)} detail="Customer accounts" />
          <MetricCard icon="↗" label="TOTAL WEIGHT" value={`${weight.format(summary?.total_weight_lb ?? 0)} lb`} detail="Across all order lines" />
          <MetricCard icon="◫" label="EST. VOLUME" value={`${number.format(summary?.total_volume_ft3 ?? 0)} ft³`} detail="Summed item-box estimate" />
        </div>
        <div className="table-panel">
          <div className="panel-heading"><div><h2>Household orders</h2><p>{summary?.date_min ?? '—'} to {summary?.date_max ?? '—'} <span className="muted-dot">·</span> {summary?.destination_communities.join(', ') || 'No destinations'}</p></div><span className="count-pill">{visibleOrders.length} ORDERS</span></div>
          <div className="filter-row"><label className="search-box"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search order, household, or community" /></label><label className="date-filter"><span>DATE</span><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /></label></div>
          {visibleOrders.length === 0 ? <EmptyState title="No matching orders">Try changing the search or date filter.</EmptyState> : <div className="table-scroll"><table><thead><tr><th>ORDER</th><th>HOUSEHOLD</th><th>ORDER DATE</th><th>DESTINATION</th><th>WEIGHT</th><th>VOLUME</th><th>STATUS</th><th /></tr></thead><tbody>
            {visibleOrders.map((order) => {
              const statusLabels = { ENTERED: 'Entered', READY_FOR_PICKING: 'Ready', IN_PROGRESS: 'In progress', PICKED: 'Picked' } as const
              const tone = order.picking_status === 'PICKED' ? 'status-good' : order.picking_status === 'IN_PROGRESS' ? 'status-warn' : 'status-neutral'
              return <tr key={order.order_id}><td><strong className="mono">#{order.order_id}</strong><small className="cell-sub">{order.items.length} item lines</small></td><td>HH-{order.household_id}</td><td>{order.order_date}</td><td><span className="destination"><span />{order.destination_community}</span></td><td>{weight.format(order.total_weight_lb)} lb</td><td>{number.format(order.total_volume_ft3)} ft³</td><td><span className={`status-chip ${tone}`}><i />{statusLabels[order.picking_status]}</span><small className="cell-sub">{order.packing_status.replaceAll('_', ' ').toLowerCase()}</small></td><td><button className="text-button" onClick={() => setSelected(order)}>View <span>↗</span></button></td></tr>
            })}
          </tbody></table></div>}
          <div className="table-foot"><span>Showing {visibleOrders.length} of {orders.length} orders</span><span>One row represents one household order</span></div>
        </div>
        <Notice title="Order traceability stays intact">Every order keeps its own ID through tote packing and flight manifests. Status here is derived from the current packing plan; retailer-side status is not yet tracked.</Notice>
      </>}

      {selected && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null) }}><section className="detail-modal" role="dialog" aria-modal="true" aria-labelledby="order-detail-title"><div className="modal-heading"><div><span className="eyebrow">HOUSEHOLD ORDER</span><h2 id="order-detail-title">#{selected.order_id}</h2></div><button className="icon-button" aria-label="Close order details" onClick={() => setSelected(null)}>×</button></div><div className="detail-facts"><span><small>HOUSEHOLD</small>HH-{selected.household_id}</span><span><small>DATE</small>{selected.order_date}</span><span><small>DESTINATION</small>{selected.destination_community}</span><span><small>BATCH</small>{selected.batch_id}</span></div><h3>Items <span>{selected.items.length} lines</span></h3><div className="item-list">{selected.items.map((item) => <div key={item.order_item_id} className="order-item-row"><div><strong>{item.product_name}</strong><small>Product {item.product_id} · Required {item.quantity} · Picked {item.picked_quantity} · Remaining {item.remaining_quantity} · Box {item.length_in} × {item.width_in} × {item.height_in} in</small></div><div><strong>Item wt {weight.format(item.weight_lb)} lb</strong><small>Box vol {number.format(item.volume_ft3)} ft³ · {item.picking_status.replace('_', ' ').toLowerCase()}</small></div></div>)}</div><div className="modal-totals"><span>Picking status <strong>{selected.picking_status.replaceAll('_', ' ')}</strong></span><span>Packing status <strong>{selected.packing_status.replaceAll('_', ' ')}</strong></span><span>Total weight <strong>{weight.format(selected.total_weight_lb)} lb</strong></span><span>Estimated volume <strong>{number.format(selected.total_volume_ft3)} ft³</strong></span></div></section></div>}
    </section>
  )
}
