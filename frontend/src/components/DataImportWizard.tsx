import { useState } from 'react'
import { api } from '../services/api'
import type { ImportAnalysisFile, ImportDatasetType, ImportPreview, SessionResponse } from '../types/api'

const orderFields: Record<string, string> = {
  order_id: 'Order ID', quantity: 'Item quantity (optional; defaults to 1)', household_id: 'Household ID', batch_id: 'Batch ID (optional)',
  order_date: 'Order date', destination_community: 'Destination community', product_id: 'Product ID (optional)',
  product_name: 'Product name (optional)', weight_lb: 'Item weight in lb', length_in: 'Item length in inches',
  width_in: 'Item width in inches', height_in: 'Item height in inches',
}
const flightFields: Record<string, string> = {
  departure_id: 'Flight / departure ID', departure_date: 'Departure date', available_totes: 'Tote capacity',
  available_payload_lb: 'Payload capacity in lb', available_volume_cuft: 'Cargo volume in ft³ (optional / Unknown)',
  flight_cost: 'Flight cost (optional)', currency: 'Currency (optional; defaults to CAD)',
}
type FilePlan = { file: ImportAnalysisFile; datasetType: ImportDatasetType | 'unknown'; mapping: Record<string, string | null>; excluded: number[]; preview: ImportPreview | null }

export function DataImportWizard({ onImported }: { onImported: (session: SessionResponse, message: string) => void }) {
  const [files, setFiles] = useState<File[]>([])
  const [plans, setPlans] = useState<FilePlan[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  async function analyze() {
    if (!files.length) { setError('Select one or more CSV or XLSX files first.'); return }
    setBusy(true); setError(''); setMessage(''); setPlans([])
    try {
      const analyzed = await api.analyzeImport(files)
      setPlans(analyzed.map((file) => ({ file, datasetType: file.dataset_type, mapping: { ...file.suggested_mapping }, excluded: [], preview: null })))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to analyze selected files.') }
    finally { setBusy(false) }
  }

  async function previewOne(index: number, plan: FilePlan) {
    if (plan.datasetType === 'unknown') return
    setBusy(true); setError('')
    try {
      const preview = await api.previewImport({ draft_id: plan.file.draft_id, dataset_type: plan.datasetType, mapping: plan.mapping, excluded_row_numbers: plan.excluded })
      setPlans((current) => current.map((entry, i) => i === index ? { ...entry, preview } : entry))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to validate file mapping.') }
    finally { setBusy(false) }
  }

  async function changeDataset(index: number, datasetType: ImportDatasetType | 'unknown') {
    const current = plans[index]
    if (!current) return
    const fields = datasetType === 'orders' ? orderFields : flightFields
    const next = { ...current, datasetType, mapping: Object.fromEntries(Object.keys(fields).map((field) => [field, current.mapping[field] ?? null])), excluded: [], preview: null }
    setPlans((items) => items.map((entry, i) => i === index ? next : entry))
    if (datasetType !== 'unknown') await previewOne(index, next)
  }

  async function updateMapping(index: number, field: string, column: string) {
    const current = plans[index]
    if (!current || current.datasetType === 'unknown') return
    const next = { ...current, mapping: { ...current.mapping, [field]: column || null }, preview: null }
    setPlans((items) => items.map((entry, i) => i === index ? next : entry))
    await previewOne(index, next)
  }

  async function toggleExclude(index: number, plan: FilePlan, rowNumber: number, checked: boolean) {
    const excluded = checked ? [...new Set([...plan.excluded, rowNumber])] : plan.excluded.filter((row) => row !== rowNumber)
    const next = { ...plan, excluded, preview: null }
    setPlans((items) => items.map((entry, i) => i === index ? next : entry))
    await previewOne(index, next)
  }

  async function cancelImport() {
    setBusy(true); setError('')
    try { await Promise.all(plans.map((plan) => api.discardImportDraft(plan.file.draft_id))) }
    catch { /* The draft may already have expired; cancel remains local and safe. */ }
    setPlans([]); setFiles([]); setMessage('Import cancelled. Active planning data was not changed.')
    setBusy(false)
  }

  async function commit() {
    if (!plans.length || plans.some((plan) => !plan.preview?.valid || plan.datasetType === 'unknown')) {
      setError('Review each file, select its dataset type, correct its mapping, and validate it before importing.')
      return
    }
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await api.commitImport(plans.map((plan) => ({ draft_id: plan.file.draft_id, dataset_type: plan.datasetType as ImportDatasetType, mapping: plan.mapping, excluded_row_numbers: plan.excluded })))
      const summary = `Imported ${result.order_count} orders across ${result.household_count} households and ${result.flight_capacity_count} flight capacity record(s).`
      setMessage(summary)
      setFiles([]); setPlans([])
      onImported(result.session, summary)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Import could not be committed.') }
    finally { setBusy(false) }
  }

  return <section className="import-wizard" aria-labelledby="import-wizard-title">
    <div className="import-wizard-heading"><div><span className="eyebrow">USER DATA · INPUT</span><h2 id="import-wizard-title">Import orders and flight capacity</h2><p>Upload order and, optionally, flight capacity files. Review detection, map columns, preview errors, then explicitly commit the dataset to this planning session.</p></div></div>
    <div className="import-file-controls"><label className="import-file-picker"><span>Choose CSV or XLSX files</span><input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple disabled={busy} onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); setPlans([]); setError(''); setMessage('') }} /></label><span className="import-filenames">{files.length ? files.map((file) => file.name).join(', ') : 'No files selected'}</span><button className="button button-secondary" onClick={() => void analyze()} disabled={busy || !files.length}>{busy ? 'Working…' : 'Analyze files'}</button></div>
    {error && <div className="import-message import-error" role="alert">{error}</div>}{message && <div className="import-message import-success" role="status">{message}</div>}
    {plans.map((plan, index) => {
      const fields = plan.datasetType === 'orders' ? orderFields : flightFields
      return <article className="import-file-card" key={plan.file.draft_id}>
        <div className="import-card-title"><div><h3>{plan.file.filename}</h3><p>Detected: <strong>{plan.file.dataset_type.replace('_', ' ')}</strong> · {plan.file.confidence} confidence · {plan.file.headers.length} columns</p></div><label className="import-type-select"><span>DATASET TYPE</span><select value={plan.datasetType} disabled={busy} onChange={(event) => void changeDataset(index, event.target.value as ImportDatasetType | 'unknown')}><option value="unknown">Choose type…</option><option value="orders">Orders</option><option value="flight_capacity">Flight / capacity</option></select></label></div>
        {plan.file.parse_error && <p className="import-error">{plan.file.parse_error}</p>}{plan.file.note && <p className="import-warning">{plan.file.note}</p>}
        {plan.datasetType !== 'unknown' && <>
          <div className="mapping-grid">{Object.entries(fields).map(([field, label]) => <label key={field}><span>{label}</span><select value={plan.mapping[field] ?? ''} disabled={busy} onChange={(event) => void updateMapping(index, field, event.target.value)}><option value="">— Not mapped —</option>{plan.file.headers.map((header) => <option key={header} value={header}>{header}</option>)}</select></label>)}</div>
          <button className="button button-secondary import-preview-button" disabled={busy} onClick={() => void previewOne(index, plan)}>Validate / refresh preview</button>
          {plan.preview && <div className="import-preview">
            <div className={`preview-status ${plan.preview.valid ? 'is-valid' : 'is-invalid'}`}>{plan.preview.valid ? `Valid · ${plan.preview.valid_row_count} rows ready` : `Needs review · ${plan.preview.valid_row_count} valid of ${plan.preview.row_count} rows`}</div>
            {!!plan.preview.error && <p className="import-error">{plan.preview.error}</p>}
            {plan.preview.mapping_errors.map((issue) => <p className="import-error" key={issue}>{issue}</p>)}
            {plan.preview.warnings.map((warning) => <p className="import-warning" key={warning}>{warning}</p>)}
            {plan.preview.invalid_rows.length > 0 && <div className="invalid-row-list"><strong>Invalid rows · select each row you want to exclude</strong>{plan.preview.invalid_rows.filter((row) => row.row_number > 0).map((row) => <label key={row.row_number}><input type="checkbox" checked={plan.excluded.includes(row.row_number)} disabled={busy} onChange={(event) => void toggleExclude(index, plan, row.row_number, event.target.checked)} /><span>Row {row.row_number}: {row.issues.join('; ')}</span></label>)}</div>}
            {plan.file.rows.length > 0 && <details className="source-preview"><summary>Inspect source sample ({plan.file.rows.length} rows)</summary><div className="table-scroll"><table><thead><tr>{plan.file.headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{plan.file.rows.slice(0, 5).map((row, rowIndex) => <tr key={rowIndex}>{plan.file.headers.map((header) => <td key={header}>{row[header]}</td>)}</tr>)}</tbody></table></div></details>}
          </div>}
        </>}
      </article>
    })}
    {plans.length > 0 && <div className="import-commit-row"><p>Import replaces the active User Data dataset and clears its current tote, cart, and flight plans. Challenge data and challenge capacity assumptions remain separate.</p><div className="import-commit-actions"><button className="button button-secondary" onClick={() => void cancelImport()} disabled={busy}>Cancel import</button><button className="button button-primary" onClick={() => void commit()} disabled={busy || plans.some((plan) => !plan.preview?.valid || plan.datasetType === 'unknown')}>{busy ? 'Importing…' : 'Commit to User Data'}</button></div></div>}
  </section>
}
