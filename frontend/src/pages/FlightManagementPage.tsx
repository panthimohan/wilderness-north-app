import { useEffect, useState } from 'react'
import { EmptyState, ErrorState, LoadingState, MetricCard, Notice, ProgressBar } from '../components/Feedback'
import { api } from '../services/api'
import type { DataMode, FlightCapacityResponse, FlightOptimizationResponse, FlightResponse, FlightSummaryResponse, Stage } from '../types/api'

const one = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 1 })
const two = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 2 })

function capacityDisplay(value: number | null, unit: string) {
  return value === null ? 'Unknown' : `${one.format(value)} ${unit}`
}

export function FlightManagementPage({ stage, mode, refreshKey }: { stage: Stage; mode: DataMode; refreshKey: number }) {
  const [capacities, setCapacities] = useState<FlightCapacityResponse[]>([])
  const [flights, setFlights] = useState<FlightResponse[]>([])
  const [summary, setSummary] = useState<FlightSummaryResponse | null>(null)
  const [result, setResult] = useState<FlightOptimizationResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [costValues, setCostValues] = useState<Record<number, string>>({})
  const [currencyValues, setCurrencyValues] = useState<Record<number, string>>({})
  const [costMessages, setCostMessages] = useState<Record<number, string>>({})
  const [costErrors, setCostErrors] = useState<Record<number, string>>({})

  async function load() {
    setLoading(true); setError('')
    try {
      const [nextCapacities, nextFlights, nextSummary] = await Promise.all([
        api.getFlightCapacities(stage), api.getFlights(), api.getFlightSummary(),
      ])
      setCapacities(nextCapacities); setFlights(nextFlights); setSummary(nextSummary); setResult(null)
      setCostValues(Object.fromEntries(nextCapacities.map((item) => [item.departure_id, item.flight_cost == null ? '' : String(item.flight_cost)])))
      setCurrencyValues(Object.fromEntries(nextCapacities.map((item) => [item.departure_id, item.currency ?? 'CAD'])))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unexpected API error') }
    finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [stage, refreshKey])

  async function runOptimization() {
    setWorking(true); setError('')
    try {
      const nextResult = await api.optimizeFlights(stage)
      setResult(nextResult); setFlights(nextResult.flight_plans)
      setSummary({
        total_flights: nextResult.total_flights, assigned_totes: nextResult.assigned_tote_ids.length,
        unassigned_totes: nextResult.unassigned_tote_ids.length, complete_orders: nextResult.complete_order_ids,
        rollover_orders: nextResult.rollover_order_ids, warnings: nextResult.warnings, errors: nextResult.errors,
        per_flight_metrics: nextResult.per_flight_metrics,
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Flight optimization failed') }
    finally { setWorking(false) }
  }

  async function saveFlightCost(capacity: FlightCapacityResponse) {
    const rawCost = costValues[capacity.departure_id]?.trim() ?? ''
    const currency = (currencyValues[capacity.departure_id] ?? 'CAD').trim().toUpperCase()
    setCostErrors((current) => ({ ...current, [capacity.departure_id]: '' }))
    setCostMessages((current) => ({ ...current, [capacity.departure_id]: '' }))
    try {
      const flight_cost = rawCost === '' ? null : Number(rawCost)
      const updated = await api.updateFlightCost(capacity.departure_id, { flight_cost, currency: currency || null })
      const [nextCapacities, nextFlights] = await Promise.all([api.getFlightCapacities(stage), api.getFlights()])
      setCapacities(nextCapacities); setFlights(nextFlights)
      setCostMessages((current) => ({ ...current, [capacity.departure_id]: updated.flight_cost === null ? 'Cost cleared.' : 'Flight cost saved.' }))
    } catch (reason) {
      setCostErrors((current) => ({ ...current, [capacity.departure_id]: reason instanceof Error ? reason.message : 'Unable to save flight cost.' }))
    }
  }

  function printManifests() {
    window.print()
  }

  const totalCapacity = capacities.reduce((sum, item) => sum + item.available_totes, 0)
  const unknownVolume = capacities.some((item) => item.available_volume_cuft === null)
  const totalVolume = capacities.reduce((sum, item) => sum + (item.available_volume_cuft ?? 0), 0)

  return (
    <section className="page-stack">
      <div className="page-heading"><div><div className="kicker"><span className="kicker-line" />AIR CARGO PLANNING</div><h1>Flight Management</h1><p>Load the canonical picking totes within each departure’s aircraft limits.</p></div><button className="button button-primary" onClick={() => void runOptimization()} disabled={working || loading}><span>◒</span>{working ? 'Planning flights…' : 'Optimize flights'}</button></div>
      {error && <ErrorState message={error} onRetry={() => void load()} />}
      {loading ? <LoadingState label="Loading flight capacities…" /> : <>
        {result && !result.feasible && <Notice tone="error" title="Flight plan is infeasible">{result.errors.join(' ') || 'Some totes could not be assigned to the available departures.'}</Notice>}
        {result?.warnings.map((warning) => <Notice key={warning} tone="warning" title="Scheduling note">{warning}</Notice>)}
        <div className="capacity-section-heading"><div><h2>Available departures</h2><p>{mode === 'user' ? 'Limits imported from User Data' : stage === 'stage1' ? 'Challenge base-case aircraft limit' : 'Capacity assigned to each Stage 2 departure'}</p></div><span className="source-tag">{mode === 'user' ? 'USER PROVIDED' : stage === 'stage1' ? 'CHALLENGE WRITEUP' : 'CAPACITY CSV'}</span></div>
        <div className="capacity-grid">{capacities.map((capacity) => <article className="capacity-card" key={capacity.departure_id}><div className="capacity-card-top"><div><span className="eyebrow">DEPARTURE {String(capacity.departure_id).padStart(2, '0')}</span><strong>{new Date(`${capacity.departure_date}T00:00:00`).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' })}</strong></div><span className="plane-mark">✈</span></div><div className="capacity-facts"><div><span>AVAILABLE TOTES</span><strong>{capacity.available_totes}</strong></div><div><span>PAYLOAD LIMIT</span><strong>{one.format(capacity.available_payload_lb)} <small>lb</small></strong></div><div><span>CARGO VOLUME</span><strong>{capacityDisplay(capacity.available_volume_cuft, 'ft³')}</strong></div></div><div className="flight-cost-editor"><label><span>FLIGHT COST</span><input aria-label={`Flight ${capacity.departure_id} cost`} type="number" min="0" step="0.01" value={costValues[capacity.departure_id] ?? ''} placeholder="Not provided" onChange={(event) => setCostValues((current) => ({ ...current, [capacity.departure_id]: event.target.value }))} /></label><label><span>CURRENCY</span><input aria-label={`Flight ${capacity.departure_id} currency`} value={currencyValues[capacity.departure_id] ?? 'CAD'} maxLength={3} placeholder="CAD" onChange={(event) => setCurrencyValues((current) => ({ ...current, [capacity.departure_id]: event.target.value }))} /></label><button className="button button-secondary" onClick={() => void saveFlightCost(capacity)}>Save Cost</button></div>{costErrors[capacity.departure_id] && <p className="import-error" role="alert">{costErrors[capacity.departure_id]}</p>}{costMessages[capacity.departure_id] && <p className="import-success" role="status">{costMessages[capacity.departure_id]}</p>}</article>)}</div>
        {mode === 'user' && capacities.length === 0 && <Notice tone="warning" title="No user flight capacity loaded">Upload a flight-capacity dataset in Order Entry before scheduling. No challenge or default limits will be substituted.</Notice>}
        {mode !== 'user' && stage === 'stage1' && <Notice title="Cargo-volume capacity is unspecified">The challenge states a 90-tote ceiling and an estimated 2,877 lb payload. Usable cargo volume has not been established, so volume capacity and utilization are shown as Unknown.</Notice>}
        {result && <div className="metric-grid metric-grid-4"><MetricCard icon="✈" label="DEPARTURES" value={one.format(summary?.total_flights ?? 0)} detail="In current plan" /><MetricCard icon="◫" label="TOTES ASSIGNED" value={`${one.format(summary?.assigned_totes ?? 0)} / ${one.format(totalCapacity)}`} detail={`${summary?.unassigned_totes ?? 0} unassigned`} /><MetricCard icon="⌂" label="ORDERS COMPLETE" value={one.format(summary?.complete_orders.length ?? 0)} detail={`${summary?.rollover_orders.length ?? 0} rollover`} /><MetricCard icon="◒" label="AVAILABLE VOLUME" value={unknownVolume ? 'Unknown' : `${one.format(totalVolume)} ft³`} detail={unknownVolume ? mode === 'user' ? 'One or more limits are unspecified' : 'Not specified by challenge' : 'Across listed departures'} /></div>}

        <div className="manifest-print-region">
          <div className="manifest-heading"><div><h2>Flight manifests</h2><p>Flight → tote → household → order → items, from canonical tote allocations.</p></div><button className="button button-secondary print-manifest-button" onClick={printManifests} disabled={!flights.length}>Print Manifest</button>{result && <span className={`status-chip ${result.feasible ? 'status-good' : 'status-warn'}`}><i />{result.feasible ? 'Plan feasible' : 'Review plan'}</span>}</div>
          <div className="manifest-print-title"><h1>Wilderness North · Flight Manifest</h1><p>Nakina → {flights.flatMap((flight) => flight.destination_communities).filter((value, index, all) => all.indexOf(value) === index).join(', ') || 'Unknown destination'}</p></div>
          {!flights.length ? <div className="panel"><EmptyState title="No flight plan yet">Optimize flights to assign the current picking totes to available departures.</EmptyState></div> : <div className="flight-list">{flights.map((flight) => {
            const households = new Set(flight.manifest_totes.flatMap((tote) => tote.households.map((household) => household.household_id)))
            return <article className="flight-card" key={flight.flight_id}><div className="flight-card-header"><div className="flight-title"><span className="flight-icon">✈</span><div><span className="eyebrow">DEPARTURE {String(flight.flight_id).padStart(2, '0')}</span><h3>{new Date(`${flight.departure_date}T00:00:00`).toLocaleDateString('en-CA', { weekday: 'short', month: 'long', day: 'numeric' })}</h3><small>Destination: {flight.destination_communities.join(', ') || 'Unknown'}</small></div></div><div className="tightest"><small>TIGHTEST CAPACITY</small><strong>{flight.tightest_capacity_dimension ?? '—'}</strong></div></div>
              <div className="flight-utilization"><ProgressBar label="Tote count" percent={flight.tote_utilization_percent} value={`${flight.tote_count}/${flight.capacity.available_totes} · ${flight.tote_utilization_percent.toFixed(1)}%`} /><ProgressBar label="Payload" percent={flight.payload_utilization_percent} value={`${two.format(flight.total_weight_lb)} / ${two.format(flight.capacity.available_payload_lb)} lb · ${flight.payload_utilization_percent.toFixed(1)}%`} /><ProgressBar label="Cargo volume" percent={flight.volume_utilization_percent} value={flight.capacity.available_volume_cuft === null ? 'Unknown' : `${two.format(flight.total_volume_cuft)} / ${two.format(flight.capacity.available_volume_cuft)} ft³ · ${flight.volume_utilization_percent?.toFixed(1)}%`} /></div>
              <div className="flight-cost-summary"><div><small>FLIGHT COST</small><strong>{flight.flight_cost === null ? 'Not provided' : `${flight.currency ?? 'CAD'} ${two.format(flight.flight_cost)}`}</strong></div><div><small>COMPLETED ORDERS CARRIED</small><strong>{flight.completed_order_count}</strong></div><div><small>COST PER ORDER</small><strong>{flight.cost_per_order === null ? 'Unknown' : `${flight.currency ?? 'CAD'} ${two.format(flight.cost_per_order)}`}</strong>{flight.cost_per_order_unknown_reason && <small>{flight.cost_per_order_unknown_reason}</small>}</div></div>
              <div className="flight-remaining"><span><small>TOTES</small><strong>{flight.tote_count}</strong></span><span><small>REMAINING TOTES</small><strong>{flight.remaining_totes}</strong></span><span><small>REMAINING PAYLOAD</small><strong>{two.format(flight.remaining_payload_lb)} lb</strong></span><span><small>REMAINING VOLUME</small><strong>{flight.remaining_volume_cuft === null ? 'Unknown' : `${two.format(flight.remaining_volume_cuft)} ft³`}</strong></span><span><small>ORDERS</small><strong>{flight.order_ids.length}</strong></span><span><small>HOUSEHOLDS</small><strong>{households.size}</strong></span></div>
              <div className="manifest-order-columns"><div><span className="eyebrow">ROLLOVER</span><p className="order-number-list">{flight.rollover_order_ids.length ? flight.rollover_order_ids.map((id) => <span className="rollover-number" key={id}>#{id}</span>) : <span className="muted">None</span>}</p></div><div><span className="eyebrow">COMPLETE THIS FLIGHT</span><p className="order-number-list">{flight.complete_order_ids.length ? flight.complete_order_ids.map((id) => <span className="complete-number" key={id}>#{id}</span>) : <span className="muted">None</span>}</p></div></div>
              <details className="manifest-tree" open><summary>Manifest contents · {flight.manifest_totes.length} totes</summary><div className="manifest-tote-list">{flight.manifest_totes.map((tote) => <section className="manifest-tote" key={tote.tote_id}><header><strong>{tote.tote_id}</strong><span>{two.format(tote.weight_lb)} lb</span><span>{two.format(tote.volume_cuft)} ft³ · {tote.volume_fill_percent.toFixed(1)}% nominal tote fill</span><span>{tote.cart_id ? `Cart ${tote.cart_id}` : 'No cart assigned'}</span></header>{tote.households.map((household) => <section className="manifest-household" key={household.household_id}><h4>Household HH-{household.household_id}</h4>{household.orders.map((order) => <section className="manifest-order" key={order.order_id}><h5>Order #{order.order_id}</h5><ul>{order.items.map((item) => <li key={item.source_item_id}><span><strong>{item.product_name}</strong><small>Product {item.product_id} · Source item {item.source_item_id}</small></span><b>× {item.quantity}</b></li>)}</ul></section>)}</section>)}</section>)}</div></details>
            </article>
          })}</div>}
        </div>
        {summary?.errors.map((item) => <Notice key={item} tone="error" title="Planning issue">{item}</Notice>)}
        <div className="fine-print">Volume is a summed item-box estimate, not verified aircraft packing geometry. No tote weight limit or cabin geometry is invented. Unknown capacity remains Unknown.</div>
      </>}
    </section>
  )
}
