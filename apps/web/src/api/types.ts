export type Role = 'viewer' | 'copywriter' | 'manager' | 'admin';

export interface ClientSummary {
  id: string;
  slug: string;
  name: string;
}

export interface ClientProfile {
  industry: string;
  description: string;
  audience: string;
  tone_of_voice: string;
  platforms: string[];
  content_pillars: string[];
  banned_topics: string[];
  hashtags: string[];
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  is_demo: boolean;
  clients: ClientSummary[];
}

export interface TokenResponse {
  access_token: string;
  expires_at: string;
  user: User;
}

export interface DemoAccount {
  role: Role;
  email: string;
  description: string;
}

export interface Conversation {
  id: string;
  client_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ContentPlanItem {
  date: string;
  platform: string;
  format: string;
  rubric: string;
  title: string;
  text: string;
  hashtags: string[];
  visual_idea: string;
}

export interface ContentPlan {
  title: string;
  period_start: string;
  period_end: string;
  goal: string;
  items: ContentPlanItem[];
}

export interface Post {
  platform: string;
  format: string;
  title: string;
  text: string;
  hashtags: string[];
  call_to_action: string;
  visual_idea: string;
}

export interface DesignerBrief {
  title: string;
  platform: string;
  format: string;
  dimensions: string;
  objective: string;
  key_message: string;
  text_on_visual: string;
  visual_description: string;
  mood: string[];
  colors: string[];
  do: string[];
  dont: string[];
  deliverables: string[];
}

export interface PublishResult {
  plan_id: string;
  title: string;
  items: number;
  approved_at: string;
  already_published: boolean;
}

export type Artifact =
  | { kind: 'content_plan'; data: { draft_id: string; plan: ContentPlan } }
  | { kind: 'post'; data: { post: Post } }
  | { kind: 'designer_brief'; data: { brief: DesignerBrief } }
  | { kind: 'publication'; data: PublishResult };

export interface BrandbookHit {
  document: string;
  section: string;
  text: string;
  score: number;
}

export interface StoredMessage {
  role: 'user' | 'assistant';
  content: string;
  artifacts: Artifact[];
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: StoredMessage[];
}

export interface DocumentInfo {
  id: string;
  filename: string;
  title: string;
  content_type: string;
  size_bytes: number;
  chunk_count: number;
  created_at: string;
}

export interface ApprovedPlan {
  id: string;
  client_id: string;
  title: string;
  period_start: string;
  period_end: string;
  payload: ContentPlan;
  approved_at: string;
}

export interface CallStats {
  calls: number;
  errors: number;
  error_rate: number;
  fallback_rate: number;
  cache_hits: number;
  cost_usd: string;
  cost_saved_usd: string;
  tokens_in: number;
  tokens_out: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
}

export interface MetricsReport {
  window_hours: number;
  since: string;
  totals: CallStats;
  by_model: (CallStats & { provider: string; model: string })[];
  by_purpose: Record<string, number>;
  daily: { day: string; calls: number; cost_usd: string; cost_saved_usd: string }[];
}

export const can = {
  generate: (role: Role) => role !== 'viewer',
  upload: (role: Role) => role !== 'viewer',
  publish: (role: Role) => role === 'manager' || role === 'admin',
  admin: (role: Role) => role === 'admin',
};
