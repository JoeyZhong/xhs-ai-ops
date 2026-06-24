/**
 * API client。
 * - setToken/getToken/clearToken 运行时 JWT 管理（localStorage）
 * - 401/403 自动 clearToken + 跳 /login
 * - SSE 端点 token 走 query string
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const TOKEN_KEY = "spider_xhs_jwt";
const GOALS_STORAGE_KEY = "spider-xhs-goals";

// ── token 管理 ─────────────────────────────────────────────────

export function setToken(jwt: string): void {
  if (typeof window !== "undefined") {
    const prev = window.localStorage.getItem(TOKEN_KEY) || "";
    if (prev !== jwt) {
      window.localStorage.removeItem(GOALS_STORAGE_KEY);
    }
    window.localStorage.setItem(TOKEN_KEY, jwt);
  }
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(TOKEN_KEY) || "";
}

export function clearToken(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(GOALS_STORAGE_KEY);
  }
}

// ── error ──────────────────────────────────────────────────────

interface ApiErrorOptions {
  code?: string;
  detail?: string | null;
  field?: string | null;
  current_rev?: number | null;
  request_id?: string | null;
}

export class ApiError extends Error {
  public code: string;
  public detail: string | null;
  public field: string | null;
  public current_rev: number | null;
  public request_id: string | null;

  constructor(
    public status: number,
    message: string,
    options: ApiErrorOptions = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code ?? String(status);
    this.detail = options.detail ?? null;
    this.field = options.field ?? null;
    this.current_rev = options.current_rev ?? null;
    this.request_id = options.request_id ?? null;
  }
}

function stringifyDetail(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.length > 0) {
    const first = value[0];
    if (first && typeof first === "object" && "msg" in first && typeof first.msg === "string") {
      return first.msg;
    }
  }
  if (value && typeof value === "object") {
    if ("message" in value && typeof value.message === "string") return value.message;
    if ("detail" in value && typeof value.detail === "string") return value.detail;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

async function parseError(res: Response): Promise<ApiErrorOptions & { message: string }> {
  const fallbackMessage = res.statusText || `HTTP ${res.status}`;

  try {
    const data = await res.clone().json();
    if (
      data &&
      typeof data === "object" &&
      "error" in data &&
      data.error &&
      typeof data.error === "object"
    ) {
      const error = data.error as Record<string, unknown>;
      const detail = stringifyDetail(error.detail) ?? null;
      return {
        code: typeof error.code === "string" ? error.code : String(res.status),
        message: stringifyDetail(error.message) ?? detail ?? fallbackMessage,
        detail,
        field: typeof error.field === "string" ? error.field : null,
        current_rev: readNumber(error.current_rev),
        request_id: typeof error.request_id === "string" ? error.request_id : null,
      };
    }
    if (data && typeof data === "object" && "detail" in data) {
      const detail = stringifyDetail(data.detail) ?? fallbackMessage;
      return {
        code: String(res.status),
        message: detail,
        detail,
      };
    }
  } catch {
    // Fall back to text below.
  }

  const text = await res.text().catch(() => fallbackMessage);
  return {
    code: String(res.status),
    message: text || fallbackMessage,
    detail: text || null,
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const error = await parseError(res);

    if (res.status === 401 || res.status === 403) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login?error=token";
      }
      throw new ApiError(res.status, error.message || "认证失败，请重新登录", error);
    }

    throw new ApiError(res.status, error.message, error);
  }
  return res.json() as Promise<T>;
}

function authHeaders(): HeadersInit {
  const token = getToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...authHeaders(),
      ...(options?.headers ?? {}),
    },
  });
  return handleResponse<T>(res);
}

export function sseUrl(path: string): string {
  const url = new URL(`${API_BASE}${path}`);
  const token = getToken();
  if (token) url.searchParams.set("token", token);
  return url.toString();
}

// ── content lifecycle API ──────────────────────────────────────

type QueryValue = string | number | boolean | null | undefined;

function qs(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as [string, QueryValue][]) {
    if (value !== undefined && value !== null) {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

export interface IdempotencyOptions {
  idempotencyKey?: string;
}

export function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `key-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function writeRequest(
  method: "POST" | "PUT" | "DELETE",
  body?: unknown,
  options?: IdempotencyOptions
): RequestInit {
  return {
    method,
    headers: {
      "Idempotency-Key": options?.idempotencyKey ?? generateIdempotencyKey(),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number | null;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface Ref {
  type: string;
  id: string;
  label?: string;
}

export type FunnelStage = "traffic" | "trust" | "conversion";
export type CreatedBy =
  | "user"
  | "orchestrator"
  | "intel"
  | "analyst"
  | "content"
  | "scheduler"
  | "system";

export type TopicStatus =
  | "idea"
  | "planned"
  | "drafting"
  | "drafted"
  | "scheduled"
  | "published"
  | "archived";

export interface Topic {
  topic_id: string;
  goal_id: string | null;
  persona_id: string | null;
  title: string;
  angle: string | null;
  funnel_stage: FunnelStage | null;
  source: "ai" | "manual" | "market_insight" | "memory";
  source_refs: Ref[];
  status: TopicStatus;
  created_by: CreatedBy;
  rev: number;
  created_at: string;
  updated_at: string;
}

export interface TopicCreate {
  title: string;
  goal_id?: string | null;
  persona_id?: string | null;
  angle?: string | null;
  funnel_stage?: FunnelStage | null;
  source?: Topic["source"];
  source_refs?: Ref[];
}

export type TopicUpdate = Partial<
  Omit<TopicCreate, "source_refs"> & Pick<Topic, "status"> & { source_refs: Ref[] }
> & {
  rev: number;
};

export interface TopicListParams {
  goal_id?: string;
  status?: TopicStatus;
  page?: number;
  page_size?: number;
  sort?: string;
}

export interface ContentStrategy {
  strategy_id: string;
  topic_id: string | null;
  manual_input_hint: string | null;
  target_reader: string | null;
  funnel_stage: FunnelStage | null;
  angle: string | null;
  hook: string | null;
  key_points: unknown[];
  cta: string | null;
  avoid_points: unknown[];
  evidence_refs: Ref[];
  memory_refs: Ref[];
  knowledge_refs: Ref[];
  created_by: CreatedBy;
  rev: number;
  created_at: string;
  updated_at: string;
}

export interface StrategyCreate {
  topic_id?: string | null;
  manual_input_hint?: string | null;
  target_reader?: string | null;
  funnel_stage?: FunnelStage | null;
  angle?: string | null;
  hook?: string | null;
  key_points?: unknown[];
  cta?: string | null;
  avoid_points?: unknown[];
  evidence_refs?: Ref[];
  memory_refs?: Ref[];
  knowledge_refs?: Ref[];
}

export type StrategyUpdate = Partial<StrategyCreate> & {
  rev: number;
};

export interface StrategyListParams {
  topic_id?: string;
  page?: number;
  page_size?: number;
  sort?: string;
}

export type CalendarStatus = "planned" | "drafted" | "scheduled" | "published" | "cancelled";
export type CalendarDeleteMode = "soft" | "hard";

export interface CalendarItem {
  calendar_item_id: string;
  topic_id: string | null;
  content_id: string | null;
  scheduled_date: string;
  scheduled_time: string | null;
  funnel_stage: FunnelStage | null;
  status: CalendarStatus;
  delete_mode: CalendarDeleteMode;
  deleted_at: string | null;
  created_by: CreatedBy;
  rev: number;
  created_at: string;
  updated_at: string;
}

export interface CalendarItemCreate {
  topic_id?: string | null;
  content_id?: string | null;
  scheduled_date: string;
  scheduled_time?: string | null;
  funnel_stage?: FunnelStage | null;
}

export type CalendarItemUpdate = Partial<CalendarItemCreate & Pick<CalendarItem, "status">> & {
  rev: number;
};

export interface CalendarListParams {
  from?: string;
  to?: string;
  status?: CalendarStatus;
  include_deleted?: boolean;
  page?: number;
  page_size?: number;
  sort?: string;
}

export type DraftStatus = "draft" | "edited" | "scheduled" | "published" | "rejected";

export interface ContentDraft {
  content_id: string;
  goal_id: string | null;
  persona_id: string | null;
  topic_id: string | null;
  strategy_id: string | null;
  calendar_item_id: string | null;
  title: string | null;
  body: string | null;
  hashtags: string[];
  publish_at: string | null;
  status: DraftStatus;
  knowledge_refs: Ref[];
  memory_refs: Ref[];
  meta: Record<string, unknown> | null;
  rev?: number;
  created_at: string;
  updated_at: string;
}

export type DraftUpdate = Partial<
  Pick<
    ContentDraft,
    | "goal_id"
    | "persona_id"
    | "topic_id"
    | "strategy_id"
    | "calendar_item_id"
    | "title"
    | "body"
    | "hashtags"
    | "publish_at"
    | "status"
    | "knowledge_refs"
    | "memory_refs"
    | "meta"
  >
> & {
  rev?: number;
};

export interface DraftListParams {
  goal_id?: string;
  persona_id?: string;
  status?: DraftStatus;
  topic_id?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
  sort?: string;
}

export interface GenerateContentRequest {
  strategy_id?: string | null;
  count?: number;
  persist?: boolean;
}

export interface TopicGenerateContentResponse {
  items: ContentDraft[];
  total?: number;
  strategy: ContentStrategy | null;
  partial_failures?: unknown[];
  task_id?: string | null;
}

export interface CalendarGenerateContentResponse {
  items: ContentDraft[];
  total?: number;
  calendar_item: CalendarItem;
  partial_failures?: unknown[];
  task_id?: string | null;
}

export const topicsApi = {
  list: (params: TopicListParams = {}) =>
    apiFetch<PaginatedResponse<Topic>>(`/api/v1/topics${qs(params)}`),

  get: (topic_id: string) => apiFetch<Topic>(`/api/v1/topics/${segment(topic_id)}`),

  create: (data: TopicCreate, options?: IdempotencyOptions) =>
    apiFetch<Topic>("/api/v1/topics", writeRequest("POST", data, options)),

  update: (topic_id: string, data: TopicUpdate, options?: IdempotencyOptions) =>
    apiFetch<Topic>(`/api/v1/topics/${segment(topic_id)}`, writeRequest("PUT", data, options)),

  delete: (topic_id: string, rev: number, options?: IdempotencyOptions) =>
    apiFetch<{ topic_id: string; status: "archived" }>(
      `/api/v1/topics/${segment(topic_id)}${qs({ rev })}`,
      writeRequest("DELETE", undefined, options)
    ),

  generateContent: (
    topic_id: string,
    data: GenerateContentRequest = {},
    options?: IdempotencyOptions
  ) =>
    apiFetch<TopicGenerateContentResponse>(
      `/api/v1/topics/${segment(topic_id)}/generate-content`,
      writeRequest("POST", data, options)
    ),
};

export const calendarApi = {
  list: (params: CalendarListParams = {}) =>
    apiFetch<PaginatedResponse<CalendarItem>>(`/api/v1/calendar${qs(params)}`),

  get: (calendar_item_id: string) =>
    apiFetch<CalendarItem>(`/api/v1/calendar/${segment(calendar_item_id)}`),

  create: (data: CalendarItemCreate, options?: IdempotencyOptions) =>
    apiFetch<CalendarItem>("/api/v1/calendar", writeRequest("POST", data, options)),

  update: (calendar_item_id: string, data: CalendarItemUpdate, options?: IdempotencyOptions) =>
    apiFetch<CalendarItem>(
      `/api/v1/calendar/${segment(calendar_item_id)}`,
      writeRequest("PUT", data, options)
    ),

  delete: (
    calendar_item_id: string,
    rev: number,
    mode: CalendarDeleteMode = "soft",
    options?: IdempotencyOptions
  ) =>
    apiFetch<CalendarItem | { deleted: true }>(
      `/api/v1/calendar/${segment(calendar_item_id)}${qs({ rev, mode })}`,
      writeRequest("DELETE", undefined, options)
    ),

  softDelete: (calendar_item_id: string, rev: number, options?: IdempotencyOptions) =>
    calendarApi.delete(calendar_item_id, rev, "soft", options),

  hardDelete: (calendar_item_id: string, rev: number, options?: IdempotencyOptions) =>
    calendarApi.delete(calendar_item_id, rev, "hard", options),

  generateContent: (
    calendar_item_id: string,
    data: GenerateContentRequest = {},
    options?: IdempotencyOptions
  ) =>
    apiFetch<CalendarGenerateContentResponse>(
      `/api/v1/calendar/${segment(calendar_item_id)}/generate-content`,
      writeRequest("POST", data, options)
    ),
};

export const strategiesApi = {
  list: (params: StrategyListParams = {}) =>
    apiFetch<PaginatedResponse<ContentStrategy>>(`/api/v1/strategies${qs(params)}`),

  get: (strategy_id: string) =>
    apiFetch<ContentStrategy>(`/api/v1/strategies/${segment(strategy_id)}`),

  create: (data: StrategyCreate, options?: IdempotencyOptions) =>
    apiFetch<ContentStrategy>("/api/v1/strategies", writeRequest("POST", data, options)),

  update: (strategy_id: string, data: StrategyUpdate, options?: IdempotencyOptions) =>
    apiFetch<ContentStrategy>(
      `/api/v1/strategies/${segment(strategy_id)}`,
      writeRequest("PUT", data, options)
    ),

  delete: (strategy_id: string, options?: IdempotencyOptions) =>
    apiFetch<{ deleted: boolean; strategy_id: string }>(
      `/api/v1/strategies/${segment(strategy_id)}`,
      writeRequest("DELETE", undefined, options)
    ),
};

export const draftsApi = {
  list: (params: DraftListParams = {}) =>
    apiFetch<PaginatedResponse<ContentDraft>>(`/api/v1/drafts${qs(params)}`),

  get: (content_id: string) => apiFetch<ContentDraft>(`/api/v1/drafts/${segment(content_id)}`),

  update: (content_id: string, data: DraftUpdate, options?: IdempotencyOptions) =>
    apiFetch<ContentDraft>(
      `/api/v1/drafts/${segment(content_id)}`,
      writeRequest("PUT", data, options)
    ),

  duplicate: (
    content_id: string,
    data: { title_suffix?: string | null } = {},
    options?: IdempotencyOptions
  ) =>
    apiFetch<ContentDraft>(
      `/api/v1/drafts/${segment(content_id)}/duplicate`,
      writeRequest("POST", data, options)
    ),

  schedule: (
    content_id: string,
    data: {
      scheduled_date: string;
      scheduled_time?: string | null;
      funnel_stage?: FunnelStage | null;
    },
    options?: IdempotencyOptions
  ) =>
    apiFetch<{ draft: ContentDraft; calendar_item: CalendarItem }>(
      `/api/v1/drafts/${segment(content_id)}/schedule`,
      writeRequest("POST", data, options)
    ),

  reject: (
    content_id: string,
    data: { reason?: string | null } = {},
    options?: IdempotencyOptions
  ) =>
    apiFetch<ContentDraft>(
      `/api/v1/drafts/${segment(content_id)}/reject`,
      writeRequest("POST", data, options)
    ),
};

// ── 线索雷达 leads API (lead-intent-radar V1) ──────────────────

export type TriggerType = "loan" | "bid" | "hitech" | "foreign" | "cancel";
export type LeadStatus =
  | "detected" | "qualified" | "drafted" | "pending" | "touched" | "skipped";
export type LeadOutcome = "replied" | "converted";
export type LeadSource = "xhs" | "zhihu" | "zhubajie";

// 一键发送结果（V2）。业务态走 200 + status。
export type SendStatus =
  | "sent" | "dryrun" | "blocked_checks"
  | "engine_not_ready" | "rate_limited" | "source_unsupported";

export interface SendResult {
  status: SendStatus;
  sent: boolean;
  engine: string; // dryrun | reajason
  platform_id: string | null;
  reason: string;
  count_today: number;
  daily_limit: number;
  next_available_minutes: number | null;
  lead: Lead | null;
}

export interface Lead {
  lead_id: string;
  tenant_id: string;
  goal_id: string | null;
  persona_id: string | null;
  source: string;
  source_url: string | null;
  signal_key: string;
  author: string | null;
  posted_at: string | null;
  post_text: string | null;
  excerpt: string | null;
  detected_at: string;
  is_intent: boolean;
  match_score: number | null;
  trigger_type: TriggerType | null;
  judge_reason: string | null;
  draft_text: string | null;
  check_lure_pass: boolean;
  check_dup_pass: boolean;
  lead_status: LeadStatus;
  touched_at: string | null;
  outcome: LeadOutcome | null;
  sent_at: string | null;
  send_platform_id: string | null;
  send_engine: string | null;
  meta: Record<string, unknown> | null;
  rev: number;
  created_at: string;
  updated_at: string;
}

export interface LeadListParams {
  goal_id?: string;
  status?: LeadStatus;
  trigger_type?: TriggerType;
  limit?: number;
}

export interface LeadStats {
  pending: number;
  today_qualified: number;
  week_opportunities: number; // 北极星：沟通机会
  week_conversions: number;
}

export interface LeadUpdate {
  draft_text?: string;
  check_lure_pass?: boolean;
  check_dup_pass?: boolean;
  lead_status?: LeadStatus;
  outcome?: LeadOutcome;
  rev: number;
}

export interface ScanStats {
  scanned: number;
  qualified: number;
  created: number;
  duplicate: number;
  noise: number;
  errors: number;
  by_source: Record<string, Record<string, number>>;
}

export interface ScanResponse {
  ok: boolean;
  error?: string;
  stats: ScanStats;
  created_lead_ids: string[];
}

export const leadsApi = {
  list: (params: LeadListParams = {}) =>
    apiFetch<{ items: Lead[]; total: number }>(`/api/v1/leads${qs(params)}`),

  stats: (goalId: string | null) =>
    apiFetch<LeadStats>(`/api/v1/leads/stats${qs({ goal_id: goalId })}`),

  get: (lead_id: string) => apiFetch<Lead>(`/api/v1/leads/${segment(lead_id)}`),

  update: (lead_id: string, data: LeadUpdate, options?: IdempotencyOptions) =>
    apiFetch<Lead>(`/api/v1/leads/${segment(lead_id)}`, writeRequest("PUT", data, options)),

  touch: (
    lead_id: string,
    data: { outcome?: LeadOutcome; rev: number },
    options?: IdempotencyOptions
  ) =>
    apiFetch<Lead>(`/api/v1/leads/${segment(lead_id)}/touch`, writeRequest("POST", data, options)),

  // 一键发送（V2，仅小红书）。返回业务态 SendResult（默认 dryrun 不真发）。
  send: (
    lead_id: string,
    data: { account_id?: string } = {},
    options?: IdempotencyOptions
  ) =>
    apiFetch<SendResult>(`/api/v1/leads/${segment(lead_id)}/send`, writeRequest("POST", data, options)),

  // 手动触发一次雷达扫描（V2）。返回采集→判定→入库全流程统计。
  scan: (goalId: string, limitPerKeyword = 20, options?: IdempotencyOptions) =>
    apiFetch<ScanResponse>(
      `/api/v1/leads/scan`,
      writeRequest("POST", { goal_id: goalId, limit_per_keyword: limitPerKeyword }, options),
    ),
};

// ── Orchestrator 主助手 API (V1.3) ──────────────────────────────

// 契约 §A：本期落点枚举。旧值（gathering/planned/dispatched）保留为迁移期兼容，迁完删。
export type OrchStatus =
  | "thinking"
  | "awaiting_user"
  | "awaiting_decision"
  | "done"
  | "cancelled"
  | "gathering"
  | "planned"
  | "dispatched";

export interface OrchPlanNode {
  id: string;
  type: string;
  prompt: string;
  blocked_by: string[];
}

export interface OrchCard {
  card_id: string;
  kind: "plan_approval" | "high_risk_step" | string;
  title: string;
  detail: string;
  options: string[];
  status: "pending" | "approved" | "rejected" | string;
}

export interface OrchMessage {
  role: string;
  text: string;
}

// 契约 §B：SSE 事件 = trace 元素。8 型，done 为每轮唯一终止符。
export type OrchEvent =
  | { type: "thinking"; seq: number; summary: string }
  // 用户本轮提问：仅存在于 trace（恢复用），不经 SSE 实时推送（实时端本地已显示）。
  | { type: "user_message"; seq: number; content: string }
  // 心跳：子 agent 执行期的存活/进度信号。仅用于喂活前端空闲计时器 + 显示进度，
  // 不入 trace、不渲染气泡（见 page.tsx appendEvent 过滤）。
  | { type: "heartbeat"; seq: number; archetype?: string; stage?: string; iteration?: number; detail?: string }
  | { type: "subagent_start"; seq: number; archetype: string; task: string }
  | { type: "subagent_result"; seq: number; archetype: string; ok: boolean; summary: string }
  | { type: "decision_card"; seq: number; card: OrchCard }
  | { type: "awaiting_user"; seq: number; question: string }
  // 最终回答的增量 token（真流式）：seq=0、不入 trace（传输层信号，类 heartbeat）；
  // 前端累积成 live 最终气泡，随后由 final（完整文本）定稿。见 page.tsx appendEvent。
  | { type: "final_delta"; seq: number; text: string }
  | { type: "final"; seq: number; summary: string }
  | { type: "error"; seq: number; message: string }
  | { type: "done"; seq: number; status: OrchStatus; session_id: string };

// 契约 §A/§D：会话待答态。null=无待答；question=追问；decision=决策卡。
export type OrchPending =
  | null
  | { kind: "question"; question: string }
  | { kind: "decision"; card: OrchCard };

export interface OrchConverseResponse {
  session_id: string;
  status: OrchStatus;
  reply?: string;
  missing?: string[];
  proposed_plan?: OrchPlanNode[];
  decision_cards?: OrchCard[];
}

export interface OrchSessionView {
  session_id: string;
  status: OrchStatus;
  goal_id: string | null;
  messages: OrchMessage[];
  // 契约 §D：trace 是前端主渲染源；pending 用于刷新/续接恢复待答态。
  trace: OrchEvent[];
  pending: OrchPending;
  proposed_plan: OrchPlanNode[];
  decision_cards: OrchCard[];
  dag_id: string | null;
}

export interface OrchSessionListItem {
  session_id: string;
  goal_id: string | null;
  title: string;
  status: OrchStatus;
  updated_at: string;
  message_count: number;
}

export const orchestratorApi = {
  converse: (
    data: { message: string; goal_id?: string | null; session_id?: string | null },
    options?: IdempotencyOptions
  ) =>
    apiFetch<OrchConverseResponse>(
      "/api/v1/orchestrator/converse",
      writeRequest("POST", data, options)
    ),

  confirm: (
    data: { session_id: string; plan_card_decision: "approve" | "reject" },
    options?: IdempotencyOptions
  ) =>
    apiFetch<{ session_id: string; status: OrchStatus; dag_id?: string }>(
      "/api/v1/orchestrator/plan/confirm",
      writeRequest("POST", data, options)
    ),

  decision: (
    data: { session_id: string; card_id: string; decision: "approve" | "reject" },
    options?: IdempotencyOptions
  ) =>
    apiFetch<{ session_id: string; decision_cards: OrchCard[] }>(
      "/api/v1/orchestrator/decision",
      writeRequest("POST", data, options)
    ),

  getSession: (session_id: string) =>
    apiFetch<OrchSessionView>(`/api/v1/orchestrator/session/${segment(session_id)}`),

  listSessions: (goalId: string | null) =>
    apiFetch<{ sessions: OrchSessionListItem[] }>(
      `/api/v1/orchestrator/sessions${qs({ goal_id: goalId, limit: 20 })}`
    ),

  deleteSession: (session_id: string) =>
    apiFetch<{ deleted: boolean }>(
      `/api/v1/orchestrator/session/${segment(session_id)}`,
      writeRequest("DELETE")
    ),
};
