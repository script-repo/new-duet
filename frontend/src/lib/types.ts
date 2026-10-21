// Entities and events of the Tandem API (SPEC §10.2, §10.3).

export type Role = 'user' | 'agent' | 'system';
export type CardType = 'approval' | 'task' | 'artifact' | 'brief' | 'notification';
export type Urgency = 'low' | 'normal' | 'high' | 'critical';

export interface Card {
  type: CardType;
  ref: string;
  urgency?: string;
}

export interface Message {
  id: number;
  role: Role;
  text: string;
  card: Card | null;
  created_at: string;
}

export type ApprovalState = 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';

export interface Approval {
  id: string;
  tool: string;
  title: string;
  summary: string;
  reason: string;
  args: Record<string, unknown>;
  editable: string[];
  tags: string[];
  state: ApprovalState;
  task_id: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  note: string | null;
  result: unknown;
}

export type TaskState = 'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'stopped';

export interface Task {
  id: string;
  title: string;
  instructions: string;
  harness: string;
  origin: string;
  state: TaskState;
  summary: string | null;
  error: string | null;
  steps: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  artifact_ids: string[];
}

export type TaskLogKind = 'info' | 'thought' | 'tool' | 'result' | 'steer' | 'error';

export interface TaskLog {
  id: number;
  task_id: string;
  ts: string;
  kind: TaskLogKind;
  text: string;
}

export type ActivityLevel = 'info' | 'notify' | 'warn' | 'error';

export interface Activity {
  id: number;
  ts: string;
  kind: string;
  title: string;
  detail: string | null;
  task_id: string | null;
  level: ActivityLevel;
}

export interface Artifact {
  id: string;
  task_id: string | null;
  kind: string;
  title: string;
  filename: string;
  size: number;
  created_at: string;
}

export interface BriefItem {
  topic: string;
  summary: string;
  needs_you: boolean;
  approval_id?: string;
}

export interface Brief {
  id: string;
  date: string;
  greeting: string;
  items: BriefItem[];
  created_at: string;
}

export interface TEvent {
  id: string;
  source: string;
  kind: string;
  title: string;
  sender: string | null;
  occurred_at: string;
  status: string;
  urgency: string;
  triage: { reason?: string } | null;
}

/** Not typed in the spec; a calendar entry as the `upcoming` list is expected to carry it. */
export interface CalendarItem {
  id?: string;
  title?: string;
  summary?: string;
  start: string;
  end?: string | null;
  location?: string | null;
  all_day?: boolean;
}

export interface ThreadSnapshot {
  messages: Message[];
  approvals: Record<string, Approval>;
  tasks: Record<string, Task>;
  artifacts: Record<string, Artifact>;
  briefs: Record<string, Brief>;
}

export interface TodayStats {
  events_today: number;
  ignored: number;
  notified: number;
  tasks_done: number;
}

export interface Today {
  date: string;
  brief: Brief | null;
  needs_you: Approval[];
  in_progress: Task[];
  done: Task[];
  upcoming: CalendarItem[];
  notifications: TEvent[];
  stats: TodayStats;
}

export interface ModelRole {
  provider: string;
  model: string;
  has_api_key: boolean;
}

export interface HarnessInfo {
  name: string;
  display_name: string;
  available: boolean;
  reason: string | null;
  default: boolean;
}

export interface ConnectorInfo {
  name: string;
  type: string;
  status: string | Record<string, unknown>;
  tools: string[];
}

export interface PolicyRule {
  match: Record<string, unknown>;
  action: 'allow' | 'ask' | 'deny';
  reason?: string | null;
}

export interface AppConfig {
  mode: string;
  timezone: string;
  models: Record<string, ModelRole>;
  harnesses: HarnessInfo[];
  connectors: ConnectorInfo[];
  policy: { default: string; timeout_s: number; rules: PolicyRule[] };
  schedule: Record<string, unknown>;
  notify: Record<string, unknown>;
  voice: { server: boolean };
  auth: unknown;
}

export type MemoryKind = 'core' | 'preference' | 'fact' | 'thread_summary' | 'note';

export interface MemoryEntry {
  id: string;
  kind: MemoryKind | string;
  key: string | null;
  content: string;
  source: string | null;
  created_at: string;
  updated_at: string;
}

export interface Skill {
  name: string;
  description: string;
}

export interface AuditRow {
  id: number;
  ts: string;
  actor: string;
  tool: string;
  args: Record<string, unknown> | null;
  decision: 'allow' | 'approved' | 'rejected' | 'expired' | 'denied' | 'edited' | string;
  approval_id: string | null;
  task_id: string | null;
  ok: boolean | null;
  detail: string | null;
}

export interface NotifyAction {
  action: 'approve' | 'open' | string;
  title: string;
}

export interface NotifyPayload {
  title: string;
  body: string;
  urgency: string;
  message_id: number | null;
  approval_id?: string | null;
  url?: string;
  tag?: string | null;
  actions?: NotifyAction[];
}

/** `GET /api/push/vapid` */
export interface VapidInfo {
  public_key: string;
  enabled: boolean;
}

/** One device that turned on notifications (`/api/push/subscriptions`); never its keys or full endpoint. */
export interface PushSubscriptionInfo {
  id: string;
  host: string | null;
  label: string | null;
  created_at: string;
  last_ok_at: string | null;
  last_error: string | null;
  failures: number;
}

/** `GET/PUT /api/settings/notify` (PUT takes any subset; `quiet_hours: null` turns them off) */
export interface NotifySettings {
  min_urgency: 'low' | 'normal' | 'high' | 'critical';
  quiet_hours: string | null; // "HH:MM-HH:MM"
  max_per_hour: number;
  push_enabled: boolean;
}

/** `AppConfig.notify.push` */
export interface PushConfig {
  enabled: boolean;
  subscriptions: number;
  vapid_public_key: string;
}

export type StreamEvent =
  | { type: 'hello'; data: { server_time: string; version: string } }
  | { type: 'message.created'; data: Message }
  | { type: 'approval.created' | 'approval.updated'; data: Approval }
  | { type: 'task.created' | 'task.updated'; data: Task }
  | { type: 'task.log'; data: TaskLog }
  | { type: 'activity.created'; data: Activity }
  | { type: 'artifact.created'; data: Artifact }
  | { type: 'brief.created'; data: Brief }
  | { type: 'event.created'; data: TEvent }
  | { type: 'notify'; data: NotifyPayload }
  | { type: 'typing'; data: { on: boolean } };

export type StreamEventType = StreamEvent['type'];
