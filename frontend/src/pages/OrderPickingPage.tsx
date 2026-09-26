import { useEffect, useMemo, useRef, useState } from 'react'
import { EmptyState, ErrorState, LoadingState, MetricCard, Notice, ProgressBar } from '../components/Feedback'
import { api } from '../services/api'
import type { CartOptimizationResponse, CartResponse, OrderPickingProgress, PickingProgressResponse, PickingSummaryResponse, Stage, ToteOptimizationResponse, ToteResponse } from '../types/api'

const num = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 1 })
const precise = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 2 })
const statusLabel: Record<OrderPickingProgress['picking_status'], string> = {
  ENTERED: 'Entered', READY_FOR_PICKING: 'Ready for picking', IN_PROGRESS: 'In progress', PICKED: 'Picked',
}

export function OrderPickingPage({ stage, refreshKey }: { stage: Stage; refreshKey: number }) {
  const [totes, setTotes] = useState<ToteResponse[]>([])
  const [carts, setCarts] = useState<CartResponse[]>([])
  const [toteResult, setToteResult] = useState<ToteOptimizationResponse | null>(null)
  const [cartResult, setCartResult] = useState<CartOptimizationResponse | null>(null)
  const [summary, setSummary] = useState<PickingSummaryResponse | null>(null)
  const [progress, setProgress] = useState<PickingProgressResponse>({ orders: [] })
  const [view, setView] = useState<'standard' | 'handheld'>('standard')
  const [selectedCartId, setSelectedCartId] = useState('')
  const [selectedToteId, setSelectedToteId] = useState('')
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(null)
  const [scanValue, setScanValue] = useState('')
  const [scanFeedback, setScanFeedback] = useState<{ tone: 'success' | 'warning' | 'error'; text: string } | null>(null)
  const scanRef = useRef<HTMLInputElement>(null)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState<'totes' | 'carts' | 'scan' | null>(null)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true); setError('')
    try {
      const [nextSummary, nextTotes, nextCarts, nextProgress] = await Promise.all([
        api.getPickingSummary(), api.getTotes().catch(() => []), api.getCarts(), api.getPickingProgress(),
      ])
      setSummary(nextSummary); setTotes(nextTotes); setCarts(nextCarts); setProgress(nextProgress)
      const currentChoiceStillExists = nextCarts.some((cart) => cart.cart_id === selectedCartId)
      const nextCartChoice = currentChoiceStillExists ? selectedCartId : nextCarts[0]?.cart_id ?? '__unassigned__'
      setSelectedCartId(nextCartChoice)
      const nextChoiceTotes = nextCartChoice === '__unassigned__'
        ? nextTotes.filter((tote) => !tote.cart_id)
        : nextCarts.find((cart) => cart.cart_id === nextCartChoice)?.tote_ids.map((id) => nextTotes.find((tote) => tote.tote_id === id)).filter(Boolean) ?? []
      if (!nextChoiceTotes.some((tote) => tote?.tote_id === selectedToteId)) setSelectedToteId(nextChoiceTotes[0]?.tote_id ?? '')
      setCartResult(null)
      if (nextTotes.length) setToteResult({
        total_totes: nextTotes.length, total_orders: nextProgress.orders.length,
        complete_orders: nextSummary.fully_allocated_orders, complete_order_ids: nextSummary.fully_allocated_order_ids,
        split_orders: nextSummary.split_orders.length, split_order_ids: nextSummary.split_orders,
        oversized_order_ids: [], unallocated_item_count: 0,
        total_weight_lb: nextTotes.reduce((sum, tote) => sum + tote.weight_lb, 0),
        total_volume_ft3: nextTotes.reduce((sum, tote) => sum + tote.volume_ft3, 0), average_fill_percent: 0,
        warnings: nextSummary.warnings, totes: nextTotes,
      })
      else setToteResult(null)
      if (nextCarts.length) setCartResult({
        feasible: nextSummary.cart_feasible ?? true, total_carts: nextCarts.length, total_totes: nextTotes.length,
        totes_per_cart: nextCarts[0]?.tote_capacity ?? 5, orders_kept_together: 0, orders_kept_together_ids: [],
        split_order_ids: nextSummary.split_orders, warnings: nextSummary.warnings, errors: nextSummary.errors,
        unassigned_tote_ids: nextTotes.filter((tote) => !tote.cart_id).map((tote) => tote.tote_id),
        carts: nextCarts, per_cart_metrics: nextCarts,
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unexpected API error') }
    finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [stage, refreshKey])

  async function runTotes() {
    setWorking('totes'); setError('')
    try {
      const result = await api.optimizeTotes(stage)
      setToteResult(result); setTotes(result.totes); setCarts([]); setCartResult(null)
      setSummary(await api.getPickingSummary()); setProgress(await api.getPickingProgress())
      setSelectedCartId('__unassigned__'); setSelectedToteId(result.totes[0]?.tote_id ?? '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Tote optimization failed') }
    finally { setWorking(null) }
  }

  async function runCarts() {
    setWorking('carts'); setError('')
    try {
      const result = await api.optimizeCarts()
      setCartResult(result); setCarts(result.carts); setSummary(await api.getPickingSummary()); setTotes(await api.getTotes())
      setSelectedCartId(result.carts[0]?.cart_id ?? '__unassigned__')
      setSelectedToteId(result.carts[0]?.tote_ids[0] ?? '')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Cart optimization failed') }
    finally { setWorking(null) }
  }

  async function scan(orderId: number, toteId: string, identifier: string) {
    setWorking('scan'); setError(''); setScanFeedback(null)
    try {
      const result = await api.scanPickItem({ order_id: orderId, tote_id: toteId, identifier, quantity: 1 })
      const tone = ['scanned', 'quantity_complete'].includes(result.outcome) ? 'success' : result.outcome === 'already_complete' ? 'warning' : 'error'
      setScanFeedback({ tone, text: result.message })
      setProgress(await api.getPickingProgress())
      setScanValue('')
      requestAnimationFrame(() => scanRef.current?.focus())
    } catch (reason) { setScanFeedback({ tone: 'error', text: reason instanceof Error ? reason.message : 'Scan could not be recorded.' }) }
    finally { setWorking(null) }
  }

  const splitIds = new Set([...(toteResult?.split_order_ids ?? []), ...(cartResult?.split_order_ids ?? [])])
  const orderProgress = useMemo(() => new Map(progress.orders.map((order) => [order.order_id, order])), [progress])
  const itemProgress = useMemo(() => new Map(progress.orders.flatMap((order) => order.items.map((item) => [item.order_item_id, item] as const))), [progress])
  const canOptimizeCarts = totes.length > 0
  const unassignedTotes = totes.filter((tote) => !tote.cart_id)
  const cartSelection = carts.find((cart) => cart.cart_id === selectedCartId)
  const currentToteIds = selectedCartId === '__unassigned__' ? unassignedTotes.map((tote) => tote.tote_id) : cartSelection?.tote_ids ?? []
  const currentToteId = currentToteIds.includes(selectedToteId) ? selectedToteId : currentToteIds[0] ?? ''
  const currentTote = totes.find((tote) => tote.tote_id === currentToteId)
  const currentOrderId = currentTote?.order_ids.includes(selectedOrderId ?? -1) ? selectedOrderId : currentTote?.order_ids[0] ?? null
  const currentOrder = currentOrderId === null ? undefined : orderProgress.get(currentOrderId)
  const currentItems = currentOrder?.items.filter((item) => item.tote_ids.includes(currentToteId)) ?? []

  function renderToteLines(tote: ToteResponse) {
    const grouped = new Map<number, typeof tote.items>()
    tote.items.forEach((item) => grouped.set(item.order_id, [...(grouped.get(item.order_id) ?? []), item]))
    return <div className="tote-orders">{[...grouped.entries()].map(([orderId, items]) => <div className="tote-order" key={orderId}>
      <div className="order-group-heading"><span className="order-group-icon">⌂</span><strong>Order #{orderId}</strong><span>HH-{items[0]?.household_id}</span>{splitIds.has(orderId) && <em className="split-chip">SPLIT</em>}<span className={`status-chip ${orderProgress.get(orderId)?.picking_status === 'PICKED' ? 'status-good' : 'status-neutral'}`}><i />{statusLabel[orderProgress.get(orderId)?.picking_status ?? 'ENTERED']}</span></div>
      <ul>{items.map((item) => {
        const itemState = itemProgress.get(item.order_item_id)
        return <li className="pick-item-line" key={item.order_item_id}>
          <span className="pick-item-name"><strong>{item.product_name}</strong><small>{item.product_id} · This tote: {item.quantity} · Order required: {itemState?.ordered_quantity ?? item.quantity}</small></span>
          <span className="pick-item-measures"><span><small>PICKED / REMAINING</small>{itemState?.picked_quantity ?? 0} / {itemState?.remaining_quantity ?? item.quantity}</span><span><small>ALLOCATED WT</small>{precise.format(item.weight_lb)} lb</span></span>
          <button className="button button-small button-secondary pick-one-button" disabled={working !== null || (itemState?.remaining_quantity ?? 0) <= 0} onClick={() => void scan(orderId, tote.tote_id, item.order_item_id)}>Pick 1</button>
        </li>
      })}</ul>
    </div>)}</div>
  }

  return (
    <section className="page-stack">
      <div className="page-heading"><div><div className="kicker"><span className="kicker-line" />PICK & PACK</div><h1>Order Picking</h1><p>Pack household orders into returnable totes, keep their cart plan intact, then record each picked quantity.</p></div><span className="tote-size-tag">TOTE · 23.5 × 14 × 11 IN</span></div>
      {error && <ErrorState message={error} onRetry={() => void load()} />}
      {loading ? <LoadingState label="Loading picking plan…" /> : <>
        <div className="action-strip"><div><span className="eyebrow">PICKING WORKFLOW</span><strong>1. Pack orders <span>→</span> 2. Assign carts <span>→</span> 3. Pick items</strong></div><div className="action-buttons"><button className="button button-primary" onClick={() => void runTotes()} disabled={working !== null}><span>◫</span>{working === 'totes' ? 'Packing…' : 'Optimize totes'}</button><button className="button button-secondary" onClick={() => void runCarts()} disabled={working !== null || !canOptimizeCarts}><span>▦</span>{working === 'carts' ? 'Assigning…' : 'Optimize carts'}</button></div></div>
        <div className="pick-view-toggle" role="group" aria-label="Picking view"><span className="eyebrow">PICKER VIEW</span><button className={`button ${view === 'standard' ? 'button-primary' : 'button-secondary'}`} onClick={() => setView('standard')}>Standard View</button><button className={`button ${view === 'handheld' ? 'button-primary' : 'button-secondary'}`} onClick={() => { setView('handheld'); window.setTimeout(() => scanRef.current?.focus(), 0) }}>Handheld Scan View</button></div>
        {cartResult && !cartResult.feasible && <Notice tone="error" title="Cart plan is infeasible">{cartResult.errors.join(' ') || 'The current order-linked tote groups do not fit the configured cart capacity. Tote groups remain intact.'}</Notice>}
        {toteResult?.warnings.map((warning) => <Notice key={warning} tone="warning" title="Packing note">{warning}</Notice>)}
        {cartResult?.warnings.map((warning) => <Notice key={warning} tone="warning" title="Cart note">{warning}</Notice>)}
        {scanFeedback && <div className={`scan-feedback scan-feedback-${scanFeedback.tone}`} role="status" aria-live="polite">{scanFeedback.text}</div>}
        <div className="metric-grid metric-grid-4"><MetricCard icon="◫" label="TOTES" value={num.format(toteResult?.total_totes ?? summary?.total_totes ?? 0)} detail="Canonical containers" /><MetricCard icon="⌂" label="ORDERS PICKED" value={num.format(progress.orders.filter((order) => order.picking_status === 'PICKED').length)} detail={`${progress.orders.filter((order) => order.picking_status === 'IN_PROGRESS').length} in progress`} /><MetricCard icon="⤴" label="SPLIT ORDERS" value={num.format(splitIds.size)} detail="Orders spanning totes" /><MetricCard icon="▦" label="CARTS" value={num.format(cartResult?.total_carts ?? summary?.total_carts ?? 0)} detail={cartResult ? `${cartResult.totes_per_cart} totes per cart` : 'Not assigned yet'} /></div>

        {view === 'handheld' && <section className="panel handheld-panel" aria-label="Handheld scan picking">
          <div className="panel-heading"><div><span className="eyebrow">KEYBOARD-WEDGE SCANNER</span><h2>Scan the item in the current tote</h2><p>Focus stays in the scan box after each result. Scan a product ID or the stable source item ID.</p></div></div>
          {!totes.length ? <EmptyState title="No tote plan yet">Optimize totes before picking items.</EmptyState> : <>
            <div className="handheld-context-grid"><label>Current cart<select value={selectedCartId} onChange={(event) => { setSelectedCartId(event.target.value); setSelectedToteId(''); setSelectedOrderId(null); setScanFeedback(null) }}><option value="__unassigned__">Unassigned totes</option>{carts.map((cart) => <option key={cart.cart_id} value={cart.cart_id}>{cart.cart_id}</option>)}</select></label><label>Current tote<select value={currentToteId} onChange={(event) => { setSelectedToteId(event.target.value); setSelectedOrderId(null); setScanFeedback(null) }}>{currentToteIds.map((id) => <option key={id} value={id}>{id}</option>)}</select></label><label>Household order<select value={currentOrderId ?? ''} onChange={(event) => setSelectedOrderId(Number(event.target.value))}>{currentTote?.order_ids.map((id) => <option key={id} value={id}>HH-{currentOrder?.household_id ?? orderProgress.get(id)?.household_id} · Order #{id}</option>)}</select></label></div>
            {currentTote && <div className="handheld-current-tote"><strong>{currentTote.tote_id}</strong><span>{precise.format(currentTote.weight_lb)} lb · {precise.format(currentTote.volume_ft3)} ft³</span><span>Cart {currentTote.cart_id ?? 'unassigned'}</span></div>}
            <form className="scanner-form" onSubmit={(event) => { event.preventDefault(); if (currentOrderId !== null && currentToteId && scanValue.trim()) void scan(currentOrderId, currentToteId, scanValue.trim()) }}><label htmlFor="scanner-input">Scan / enter product ID or source item ID</label><div><input id="scanner-input" ref={scanRef} autoComplete="off" autoFocus value={scanValue} onChange={(event) => setScanValue(event.target.value)} placeholder="Scan barcode, then press Enter" disabled={!currentTote || currentOrderId === null || working !== null} /><button className="button button-primary" disabled={!scanValue.trim() || !currentTote || currentOrderId === null || working !== null}>{working === 'scan' ? 'Recording…' : 'Record scan'}</button></div></form>
            {!currentItems.length ? <EmptyState title="Choose an allocated order">This tote has no item lines for the selected household order.</EmptyState> : <div className="handheld-item-list">{currentItems.map((item) => <div className="handheld-item" key={item.order_item_id}><div><strong>{item.product_name}</strong><small>{item.product_id} · source {item.order_item_id}</small></div><div className="handheld-quantities"><span>Required <b>{item.ordered_quantity}</b></span><span>Picked <b>{item.picked_quantity}</b></span><span>Remaining <b>{item.remaining_quantity}</b></span></div><button className="button button-secondary" disabled={item.remaining_quantity <= 0 || working !== null} onClick={() => void scan(item.order_id, currentToteId, item.order_item_id)}>Pick one</button></div>)}</div>}
          </>}
        </section>}

        <div className="picking-columns">
          <section className="panel tote-panel"><div className="panel-heading"><div><h2>Tote plan</h2><p>Household boundaries and source item progress stay visible.</p></div><span className="count-pill">{totes.length} TOTES</span></div>
            {!totes.length ? <EmptyState title="No tote plan yet">Run Optimize totes to build a plan from the active order batch.</EmptyState> : <div className="tote-list">{totes.map((tote) => <article className="tote-card" key={tote.tote_id}><header><div className="tote-title"><span className="tote-glyph">◫</span><div><strong>{tote.tote_id}</strong><small>{tote.cart_id ? `Assigned to ${tote.cart_id}` : 'Not assigned to cart'}</small></div></div><div className="tote-stats"><strong>{precise.format(tote.weight_lb)} lb</strong><span>{precise.format(tote.volume_ft3)} ft³ · {tote.volume_fill_percent.toFixed(0)}% full</span></div></header>{renderToteLines(tote)}</article>)}</div>}
          </section>
          <aside className="panel cart-panel"><div className="panel-heading"><div><h2>Cart pick lists</h2><p>Cart → tote → household/order → item.</p></div><span className={`status-chip ${cartResult?.feasible ? 'status-good' : cartResult && !cartResult.feasible ? 'status-warn' : 'status-neutral'}`}><i />{cartResult ? cartResult.feasible ? 'Feasible' : 'Infeasible' : 'Pending'}</span></div>
            {!cartResult?.carts.length ? <EmptyState title={cartResult && !cartResult.feasible ? 'No valid cart assignment' : 'No carts assigned'}>{cartResult && !cartResult.feasible ? 'Review the infeasibility details above. Orders are not split to force a fit.' : 'Run Optimize carts after the tote plan is ready.'}</EmptyState> : <div className="cart-list">{cartResult.carts.map((cart) => <article className="cart-card cart-pick-list" key={cart.cart_id}><div className="cart-card-heading"><div><span className="eyebrow">PICKER CART</span><strong>{cart.cart_id}</strong></div><span>{cart.tote_count}/{cart.tote_capacity} totes</span></div><ProgressBar label="Capacity" percent={cart.utilization_percent} value={`${cart.utilization_percent.toFixed(0)}%`} /><div className="cart-orders"><span className="eyebrow">ORDERS</span><p>{cart.order_ids.map((id) => <span className="order-number-chip" key={id}>#{id}{cart.split_order_ids.includes(id) && ' ↗'}</span>)}</p></div><div className="cart-tote-pick-groups">{cart.tote_ids.map((toteId) => { const tote = totes.find((item) => item.tote_id === toteId); return tote && <details key={toteId}><summary>{tote.tote_id} · {precise.format(tote.weight_lb)} lb · {tote.order_ids.length} order(s)</summary>{renderToteLines(tote)}</details> })}</div></article>)}</div>}
            {cartResult && !cartResult.feasible && <div className="error-detail-list">{cartResult.errors.map((item) => <p key={item}>• {item}</p>)}</div>}
          </aside>
        </div>
        <div className="fine-print">Volume is the sum of item-box dimensions. It is an estimate and does not prove physical packing fit. No tote weight limit is applied. Pick progress does not change tote allocations.</div>
      </>}
    </section>
  )
}
