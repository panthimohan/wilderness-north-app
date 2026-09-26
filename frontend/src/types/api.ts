export type Stage = 'stage1' | 'stage2'
export type DataMode = 'challenge' | 'user'

export interface SessionResponse {
  active_stage: Stage
  active_mode: DataMode
  active_dataset_name: string | null
  active_dataset_source: string | null
  user_imported_at: string | null
  order_count: number
  tote_count: number
  cart_count: number
  flight_count: number
  picking_completed: boolean
  cart_optimization_completed: boolean
  flight_optimization_completed: boolean
}

export interface OrderItemResponse {
  order_item_id: string
  product_id: number | string
  product_name: string
  quantity: number
  picked_quantity: number
  remaining_quantity: number
  picking_status: 'NOT_PICKED' | 'PARTIAL' | 'PICKED'
  tote_ids: string[]
  length_in: number
  width_in: number
  height_in: number
  weight_lb: number
  volume_in3: number
  volume_ft3: number
}

export interface OrderResponse {
  order_id: number
  household_id: number
  batch_id: number
  order_date: string
  destination_community: string
  picking_status: 'ENTERED' | 'READY_FOR_PICKING' | 'IN_PROGRESS' | 'PICKED'
  packing_status: 'NOT_PACKED' | 'PARTIALLY_ALLOCATED' | 'FULLY_ALLOCATED'
  picked_quantity: number
  remaining_quantity: number
  items: OrderItemResponse[]
  total_weight_lb: number
  total_volume_ft3: number
}

export interface OrderSummaryResponse {
  order_count: number
  household_count: number
  total_weight_lb: number
  total_volume_ft3: number
  oversized_order_ids: number[]
  date_min: string | null
  date_max: string | null
  destination_communities: string[]
}

export interface ToteItemResponse {
  order_item_id: string
  order_id: number
  household_id: number
  quantity: number
  product_id: number | string
  product_name: string
  weight_lb: number
  volume_in3: number
  volume_ft3: number
}

export interface ToteResponse {
  tote_id: string
  cart_id: string | null
  flight_id: number | null
  order_ids: number[]
  household_ids: number[]
  items: ToteItemResponse[]
  weight_lb: number
  volume_in3: number
  volume_ft3: number
  volume_fill_percent: number
}

export interface ToteOptimizationResponse {
  total_totes: number
  total_orders: number
  complete_orders: number
  complete_order_ids: number[]
  split_orders: number
  split_order_ids: number[]
  oversized_order_ids: number[]
  unallocated_item_count: number
  total_weight_lb: number
  total_volume_ft3: number
  average_fill_percent: number
  warnings: string[]
  totes: ToteResponse[]
}


export interface PickingItemProgress {
  order_item_id: string
  order_id: number
  household_id: number
  product_id: number | string
  product_name: string
  ordered_quantity: number
  picked_quantity: number
  remaining_quantity: number
  allocated_quantity: number
  tote_ids: string[]
  picking_status: 'NOT_PICKED' | 'PARTIAL' | 'PICKED'
}
export interface OrderPickingProgress {
  order_id: number
  household_id: number
  ordered_quantity: number
  picked_quantity: number
  remaining_quantity: number
  picking_status: 'ENTERED' | 'READY_FOR_PICKING' | 'IN_PROGRESS' | 'PICKED'
  packing_status: 'NOT_PACKED' | 'PARTIALLY_ALLOCATED' | 'FULLY_ALLOCATED'
  items: PickingItemProgress[]
}
export interface PickingProgressResponse { orders: OrderPickingProgress[] }
export interface PickScanResponse {
  outcome: 'scanned' | 'quantity_complete' | 'already_complete' | 'unknown_item' | 'wrong_order' | 'wrong_tote' | 'unknown_order' | 'ambiguous_item'
  message: string
  order_id: number
  order_item_id: string | null
  product_id: number | string | null
  tote_id: string
  picked_quantity: number
  ordered_quantity: number
  remaining_quantity: number
  progress: OrderPickingProgress | null
}

export interface CartResponse {
  cart_id: string
  tote_ids: string[]
  tote_count: number
  tote_capacity: number
  utilization_percent: number
  order_ids: number[]
  split_order_ids: number[]
}

export interface CartOptimizationResponse {
  feasible: boolean
  total_carts: number
  total_totes: number
  totes_per_cart: number
  orders_kept_together: number
  orders_kept_together_ids: number[]
  split_order_ids: number[]
  warnings: string[]
  errors: string[]
  unassigned_tote_ids: string[]
  carts: CartResponse[]
  per_cart_metrics: CartResponse[]
}

export interface PickingSummaryResponse {
  total_totes: number
  total_carts: number
  tote_capacity_in3: number
  fully_allocated_orders: number
  fully_allocated_order_ids: number[]
  split_orders: number[]
  cart_feasible: boolean | null
  warnings: string[]
  errors: string[]
}

export interface FlightCapacityResponse {
  departure_id: number
  departure_date: string
  available_totes: number
  available_payload_lb: number
  available_volume_cuft: number | null
  flight_cost: number | null
  currency: string | null
}

export interface FlightMetricResponse {
  flight_id: number
  departure_date: string
  tote_ids: string[]
  tote_count: number
  tote_capacity: number
  used_payload_lb: number
  remaining_payload_lb: number
  used_volume_cuft: number
  remaining_volume_cuft: number | null
  payload_utilization_percent: number
  volume_utilization_percent: number | null
  tote_utilization_percent: number
  order_ids: number[]
  household_ids: number[]
  complete_order_ids: number[]
  rollover_order_ids: number[]
  tightest_capacity_dimension: string | null
}


export interface ManifestItemResponse {
  source_item_id: string
  product_id: number | string
  product_name: string
  quantity: number
}
export interface ManifestOrderResponse { order_id: number; items: ManifestItemResponse[] }
export interface ManifestHouseholdResponse { household_id: number; orders: ManifestOrderResponse[] }
export interface ManifestToteResponse {
  tote_id: string
  cart_id: string | null
  weight_lb: number
  volume_cuft: number
  volume_fill_percent: number
  households: ManifestHouseholdResponse[]
}

export interface FlightCostUpdateResponse {
  flight_id: number
  flight_cost: number | null
  currency: string | null
  completed_order_count: number
  cost_per_order: number | null
  cost_per_order_unknown_reason: string | null
}

export interface FlightResponse {
  flight_id: number
  flight_cost: number | null
  currency: string | null
  completed_order_count: number
  cost_per_order: number | null
  cost_per_order_unknown_reason: string | null
  departure_date: string
  destination_communities: string[]
  capacity: FlightCapacityResponse
  tote_ids: string[]
  manifest_totes: ManifestToteResponse[]
  order_ids: number[]
  household_ids: number[]
  complete_order_ids: number[]
  rollover_order_ids: number[]
  total_weight_lb: number
  total_volume_cuft: number
  tote_count: number
  payload_utilization_percent: number
  volume_utilization_percent: number | null
  tote_utilization_percent: number
  remaining_totes: number
  remaining_payload_lb: number
  remaining_volume_cuft: number | null
  tightest_capacity_dimension: string | null
}

export interface FlightOptimizationResponse {
  feasible: boolean
  total_flights: number
  assigned_tote_ids: string[]
  unassigned_tote_ids: string[]
  complete_order_ids: number[]
  rollover_order_ids: number[]
  warnings: string[]
  errors: string[]
  flight_plans: FlightResponse[]
  per_flight_metrics: FlightMetricResponse[]
}

export interface FlightSummaryResponse {
  total_flights: number
  assigned_totes: number
  unassigned_totes: number
  complete_orders: number[]
  rollover_orders: number[]
  warnings: string[]
  errors: string[]
  per_flight_metrics: FlightMetricResponse[]
}

export type ImportDatasetType = 'orders' | 'flight_capacity'
export interface ImportAnalysisFile {
  draft_id: string
  filename: string
  dataset_type: ImportDatasetType | 'unknown'
  confidence: string
  signals: string[]
  headers: string[]
  rows: Record<string, string>[]
  suggested_mapping: Record<string, string | null>
  parse_error: string | null
  note: string | null
}
export interface ImportPreview {
  dataset_type: ImportDatasetType
  valid: boolean
  error: string | null
  mapping_errors: string[]
  mapping: Record<string, string | null>
  valid_row_numbers: number[]
  invalid_rows: { row_number: number; issues: string[] }[]
  valid_row_count: number
  row_count: number
  column_count: number
  headers?: string[]
  sample_rows?: Record<string, string>[]
  excluded_row_numbers?: number[]
  warnings: string[]
}
export interface ImportCommitResponse {
  mode: 'user'
  order_count: number
  household_count: number
  flight_capacity_count: number
  imported_files: string[]
  session: SessionResponse
}
