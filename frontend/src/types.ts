export type Status = 'idle' | 'running' | 'waiting' | 'completed' | 'failed' | 'skipped' | 'stopped' | 'stopping' | 'interrupted';
export type Article = {
  title: string; slug: string; description: string; date: string;
  tags: string[]; authors: string[]; draft: boolean; body: string;
};
export type Finding = {
  id: string; risk: 'high' | 'medium' | 'low'; line_start: number; line_end: number;
  issue: string; reason: string; suggestion: string; original_excerpt: string;
};
export type Target = { path?: string; exists?: boolean; fingerprint?: string | null; error?: string };
export type Pending = {
  id: string; node: string; kind: 'suggestion_review' | 'redaction_review' | 'diff_review' | 'article_question' | 'final_review';
  findings?: Finding[]; llm_enabled?: boolean; diff?: string; candidate_text?: string;
  question?: string; question_number?: number; article?: Article; document?: string;
  revision?: number; publish_preview?: Target; review_path?: string; no_publish?: boolean; degraded?: boolean;
};
export type Execution = {
  id: string; node: string; iteration: number; status: Status; started_at: string; finished_at?: string;
  input: Record<string, unknown>; output: Record<string, unknown>;
};
export type RunEvent = {
  seq: number; time: string; type: string; node?: string; execution_id?: string;
  iteration?: number; status: Status; summary: string;
};
export type RunSummary = { id: string; name: string; status: Status; created_at: string; updated_at: string; workflow_id: string; workflow_name?: string; editor_source?: { workspace: string; path: string; revision: string; text_hash: string } | null; pending_count?: number };
export type Run = RunSummary & {
  editor_source?: { workspace: string; path: string; revision: string; text_hash: string } | null;
  feedback_history?: { suggestion_id: string; feedback: string }[];
  annotations: import('./EditorWorkbench').Suggestion[]; annotation_snapshot: string;
  editor_base?: { revision: string; text_hash: string };
  document_result: WorkflowDocumentResult | null;
  workflow: Workflow;
  event_cursor: number; current_node: string | null; pending: Pending | null; events: RunEvent[];
  executions: Execution[]; traversed_edges: { source: string; target: string; time: string }[];
  artifacts: { id: string; label: string; filename: string; source_path: string; revision: number }[];
  state: {
    raw_text: string; checked_text?: string; redacted_text?: string; clean_text?: string; source_path?: string; warnings?: string[]; failure?: string; degraded?: boolean;
    article_data?: Article; final_document?: string; revision?: number; review_status?: string; blog_path?: string;
  };
};
export type WorkflowDocumentResult = { id: string; text: string; kind: string; label: string };
export type WorkflowImport = { run_id: string; workflow_id: string; result_id: string; kind: string; label: string };
export type WorkflowApplyRequest = { runId: string; path: string; revision: string; text: string; result_id: string; confirm_conflict: boolean };
export type WorkflowSummary = { id: string; name: string; description: string };
export type Workflow = WorkflowSummary & {
  nodes: { id: string; label: string; description: string; position: { x: number; y: number }; human: boolean }[];
  edges: { id: string; source: string; target: string }[];
};
export type Settings = {
  llm: { enabled: boolean; provider: string; model: string; base_url: string; api_key_env: string;
    temperature: number; timeout_seconds: number; max_tokens: number };
  blog: { repo_path: string; content_dir: string }; review: { dir: string }; report: { dir: string };
  api_key_configured: boolean; local_key_saved: boolean; platform: string; config_directory: string;
};
export type ActionHandler = (action: string, payload?: Record<string, unknown>) => Promise<void>;
export const terminal = (status: Status) => ['completed', 'failed', 'stopped', 'interrupted'].includes(status);
