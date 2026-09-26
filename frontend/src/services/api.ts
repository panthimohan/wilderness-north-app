import type {
  CartOptimizationResponse,
  DataMode,
  ImportAnalysisFile,
  ImportCommitResponse,
  ImportDatasetType,
  ImportPreview,
  PickingProgressResponse,
  PickScanResponse,
  FlightCapacityResponse,
  FlightCostUpdateResponse,
  FlightOptimizationResponse,
  FlightResponse,
  FlightSummaryResponse,
  OrderResponse,
  OrderSummaryResponse,
  PickingSummaryResponse,
  SessionResponse,
  Stage,
  ToteOptimizationResponse,
  ToteResponse,
} from '../types/api'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function fileBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
      else if (body.detail && typeof body.detail === 'object') {
        const detail = body.detail as { filename?: string; error?: string | null; mapping_errors?: string[]; invalid_rows?: { row_number: number; issues: string[] }[] }
        const parts = [detail.filename, detail.error, ...(detail.mapping_errors ?? []), ...(detail.invalid_rows ?? []).map((row) => `Row ${row.row_number}: ${row.issues.join('; ')}`)].filter(Boolean)
        message = parts.length ? parts.join(' · ') : JSON.stringify(body.detail)
      }
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message)
  }

  return (await response.json()) as T
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

export const api = {
  getSession: () => request<SessionResponse>('/api/session'),
  selectStage: (stage: Stage) => postJson<SessionResponse>('/api/session/stage', { stage }),
  selectMode: (mode: DataMode) => postJson<SessionResponse>('/api/session/mode', { mode }),
  resetSession: () => postJson<SessionResponse>('/api/session/reset', {}),
  getOrders: (stage: Stage) => request<OrderResponse[]>(`/api/orders?stage=${stage}`),
  getOrderSummary: (stage: Stage) => request<OrderSummaryResponse>(`/api/orders/summary?stage=${stage}`),
  analyzeImport: async (files: File[]) => postJson<ImportAnalysisFile[]>('/api/import/analyze', { files: await Promise.all(files.map(async (file) => ({ filename: file.name, content_base64: await fileBase64(file) }))) }),
  discardImportDraft: (draftId: string) => request<{ message: string }>(`/api/import/drafts/${encodeURIComponent(draftId)}`, { method: 'DELETE' }),
  previewImport: (input: { draft_id: string; dataset_type: ImportDatasetType; mapping: Record<string, string | null>; excluded_row_numbers: number[] }) => postJson<ImportPreview>('/api/import/preview', input),
  commitImport: (files: { draft_id: string; dataset_type: ImportDatasetType; mapping: Record<string, string | null>; excluded_row_numbers: number[] }[]) => postJson<ImportCommitResponse>('/api/import/commit', { files }),
  optimizeTotes: (stage: Stage) => postJson<ToteOptimizationResponse>('/api/picking/optimize-totes', { stage }),
  getTotes: () => request<ToteResponse[]>('/api/picking/totes'),
  getCarts: () => request<CartOptimizationResponse['carts']>('/api/picking/carts'),
  getPickingSummary: () => request<PickingSummaryResponse>('/api/picking/summary'),
  getPickingProgress: () => request<PickingProgressResponse>('/api/picking/progress'),
  scanPickItem: (input: { order_id: number; tote_id: string; identifier: string; quantity?: number }) => postJson<PickScanResponse>('/api/picking/scan', input),
  optimizeCarts: () => postJson<CartOptimizationResponse>('/api/picking/optimize-carts', {}),
  getFlightCapacities: (stage: Stage) => request<FlightCapacityResponse[]>(`/api/flights/capacities?stage=${stage}`),
  optimizeFlights: (stage: Stage) => postJson<FlightOptimizationResponse>('/api/flights/optimize', { stage }),
  getFlights: () => request<FlightResponse[]>('/api/flights'),
  updateFlightCost: (flightId: number, input: { flight_cost: number | null; currency: string | null }) => request<FlightCostUpdateResponse>(`/api/flights/${flightId}/cost`, { method: 'PATCH', body: JSON.stringify(input) }),
  getFlightSummary: () => request<FlightSummaryResponse>('/api/flights/summary'),
}
