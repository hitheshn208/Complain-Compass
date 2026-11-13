export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface Envelope<T> {
  data: T;
  meta: Record<string, unknown> & { page?: number; page_size?: number; total?: number; pages?: number };
}

export interface Filters {
  date_from?: string;
  date_to?: string;
  country?: string[];
  state?: string[];
  age_group?: string[];
  student_status?: string[];
  income_bracket?: string[];
  source?: string[];
  severity?: string[];
}

export interface FilterOptions {
  country: string[];
  state: string[];
  age_group: string[];
  student_status: string[];
  income_bracket: string[];
  source: string[];
  date_range: string[];
}

export interface CountRow {
  key: string;
  reports: number;
  unique_customers: number;
}

export interface IssueSummary {
  issue_id: string;
  title: string;
  category: string | null;
  severity: Severity | null;
  status: string;
  priority_score: number;
  unique_affected_customers: number | null;
  total_reports: number | null;
  total_incidents: number | null;
  impact_score: number | null;
  severity_score: number | null;
  recurrence_score: number | null;
  growth_score: number | null;
  business_impact_score: number | null;
  label_source: string;
  explanation: string | null;
  is_emerging?: boolean;
  z_score?: number | null;
}

export interface Overview {
  total_reports: number;
  total_incidents: number;
  duplicate_reports: number;
  unique_affected_customers: number;
  verified_customers: number;
  anonymous_incidents: number;
  issue_clusters: number;
  noise_complaints: number;
  critical_high_issues: number;
  emerging_issues: number;
  emerging_limited_data: boolean;
  severity_distribution: Record<string, number>;
  by_source: CountRow[];
  by_country: CountRow[];
  by_category: CountRow[];
  top_issues: IssueSummary[];
  llm_enabled: boolean;
  filtered: boolean;
  /** "category": small dataset, issues are complaint categories; "semantic": HDBSCAN clustering */
  grouping_mode: "category" | "semantic";
  semantic_min_complaints: number;
  processed_complaints: number;
}

export interface SeriesPoint {
  period: string;
  reports: number;
  incidents: number;
  unique_customers: number;
  CRITICAL: number;
  HIGH: number;
  MEDIUM: number;
  LOW: number;
}

export interface EmergingIssue {
  issue_id: string;
  title: string;
  recent_incidents: number;
  recent_daily_mean: number;
  baseline_daily_mean: number;
  baseline_std: number;
  z_score: number;
  is_emerging: boolean;
  limited_data: boolean;
}

export interface Emerging {
  method: string;
  params: Record<string, number>;
  limited_data: boolean;
  history_days: number;
  as_of: string | null;
  note?: string;
  issues: EmergingIssue[];
}

export interface Trends {
  granularity: string;
  series: SeriesPoint[];
  emerging: Emerging;
}

export interface SegmentRow {
  segment: string;
  unique_customers: number;
  complaints: number;
  incidents: number;
  complaints_per_customer: number | null;
  top_issues: { issue_id: string; title: string; unique_customers: number }[];
  population: number | null;
  affected_rate_per_1000: number | null;
  complaint_rate_per_1000: number | null;
}

export interface SegmentBreakdown {
  dimension: string;
  metric_type: "count" | "rate";
  rate_note?: string | null;
  segments: SegmentRow[];
  suppressed_segments: number;
  min_customers: number;
}

export interface SegmentsResponse {
  segments: Record<string, SegmentBreakdown>;
  issue_matrix: {
    dimension: string;
    issues: { issue_id: string; title: string }[];
    segments: string[];
    cells: { issue_id: string; segment: string; unique_customers: number | null }[];
  };
}

export interface ScoreComponent {
  label: string;
  score: number;
  weight: number;
  contribution: number;
}

export interface Breakdown {
  weights: Record<string, number>;
  components: Record<string, ScoreComponent>;
  inputs: Record<string, number | string | boolean>;
}

export interface RepresentativeComplaint {
  complaint_id: string;
  text: string;
  source: string;
  timestamp: string;
  severity: Severity | null;
  category: string | null;
  classification_evidence: string | null;
  classification_source: string | null;
  assignment_confidence: number | null;
}

export interface IssueDetail extends IssueSummary {
  description: string | null;
  cluster_id: string;
  symptoms: string[];
  root_cause_hypotheses: string[];
  supporting_complaint_ids: string[];
  keywords: string[];
  breakdown: Breakdown | null;
  representative_complaints: RepresentativeComplaint[];
  trend: SeriesPoint[];
  severity_distribution: Record<string, number>;
  by_source: CountRow[];
  demographics: Record<string, SegmentBreakdown>;
  emerging: EmergingIssue | null;
  evidence: {
    label_source: string;
    supporting_complaint_ids: string[];
    keywords: string[];
    classification_sources: number;
    category_distribution: CountRow[];
  };
  created_at: string;
  updated_at: string;
}

export interface RowError {
  row_number: number;
  field: string | null;
  message: string;
}

export interface UploadResult {
  batch_id: string;
  file_hash: string;
  total_rows: number;
  accepted_rows: number;
  rejected_rows: number;
  row_errors: RowError[];
  timestamps_inferred: number;
  processing_started: boolean;
  channel?: string;
  /** accepted items per identity link method: explicit | directory | pseudonymous | anonymous */
  customer_links?: Record<string, number>;
  items?: ItemReport[];
}

export interface ItemReport {
  item: number;
  label: string;
  accepted: boolean;
  customer_link: string;
  detail: string | null;
}

export interface ChannelInfo {
  channel: string;
  source: string;
  extensions: string[];
  multiple_files: boolean;
  available: boolean;
  endpoint: string;
  description: string;
  transcriber?: { backend: string; available: boolean; reason: string | null; model?: string; device?: string; loaded?: boolean };
}

export interface DirectoryStats {
  customers_with_contacts: number;
  by_type: Record<string, { contacts: number; customers: number }>;
}

export interface DirectoryResult {
  rows: number;
  customers: number;
  contacts_stored: number;
  complaints_relinked: number;
  recluster_started: boolean;
  row_errors: RowError[];
  stats: DirectoryStats;
}

export interface Batch {
  batch_id: string;
  filename: string;
  channel?: string;
  total_rows: number;
  accepted_rows: number;
  rejected_rows: number;
  status: "UPLOADED" | "PROCESSING" | "PROCESSED" | "FAILED";
  stage: string | null;
  progress: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  row_errors?: RowError[];
}

export interface Complaint {
  complaint_id: string;
  customer_id: string | null;
  source: string;
  original_text: string;
  normalized_text: string | null;
  timestamp: string;
  batch_id: string;
  processing_status: string;
  category: string | null;
  severity: Severity | null;
  summary: string | null;
  issue_id: string | null;
  issue_title: string | null;
  incident_id: string | null;
  link_type: string | null;
}

export interface SimilarComplaint {
  complaint_id: string;
  similarity: number;
  text: string;
  customer_id: string | null;
  source: string | null;
  timestamp: string | null;
}
