import type { Job, Segment, UploadResult, SpeakingAssessment, TemplateSummary, TemplateVersion, GradingRules, GradingVersion, GradingPreset, MappingBatch } from "./types";

const base = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "") + "/api";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, init);
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json() as Promise<T>;
}
export const api = {
  deleteJob: async (id: string): Promise<void> => {
    const response = await fetch(`${base}/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!response.ok) throw new Error(`Delete failed (${response.status})`);
  },
  listJobs: () => request<Job[]>("/jobs"),
  getJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  retryJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  cancelJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  upload: async (files: File[], options: { language: string; engine: string }): Promise<UploadResult[]> => {
    const form = new FormData(); files.forEach(file => form.append("files", file));
    form.set("language", options.language); form.set("engine", options.engine);
    return request<UploadResult[]>("/jobs", { method: "POST", body: form });
  },
  updateSegment: (jobId: string, segmentId: string, text: string) => request<Segment>(`/jobs/${encodeURIComponent(jobId)}/segments/${encodeURIComponent(segmentId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }),
  exportUrl: (id: string, format: "txt" | "srt" | "vtt" | "json") => `${base}/jobs/${encodeURIComponent(id)}/export?format=${format}`,
  mediaUrl: (id: string) => `${base}/jobs/${encodeURIComponent(id)}/media`,
  listTemplates: () => request<TemplateSummary[]>("/v1/templates"),
  listMappingBatches: () => request<MappingBatch[]>("/v1/mapping-batches"),
  getMappingBatch: (id: string) => request<MappingBatch>(`/v1/mapping-batches/${encodeURIComponent(id)}`),
  createMappingBatch: (body: { template_version_id: string; rule_version_id: string; duration_metric: string }) => request<MappingBatch>("/v1/mapping-batches", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}),
  addMappingRecording: (id: string, job_id: string) => request<MappingBatch>(`/v1/mapping-batches/${encodeURIComponent(id)}/recordings`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({job_id})}),
  confirmMapping: (id: string, mapping: string, body: {student_id: string; part: number; question_id: string; confirmed: true}) => request<MappingBatch>(`/v1/mapping-batches/${encodeURIComponent(id)}/recordings/${encodeURIComponent(mapping)}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}),
  prepareMapping: (id: string, mapping: string) => request<{status: string}>(`/v1/mapping-batches/${encodeURIComponent(id)}/recordings/${encodeURIComponent(mapping)}/prepare`, {method:"POST"}),
  parseGradingRules: (instructions: string) => request<{rules: GradingRules; review_required: boolean; review_notes: string[]}>("/v1/grading-presets/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instructions }) }),
  listGradingPresets: () => request<GradingPreset[]>("/v1/grading-presets"),
  saveGradingPreset: (body: { name: string; template_version_id: string; instructions: string; rules: GradingRules; reviewed: true }, presetId?: string) => request<GradingVersion>(presetId ? `/v1/grading-presets/${encodeURIComponent(presetId)}/versions` : "/v1/grading-presets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(presetId ? { instructions: body.instructions, rules: body.rules, reviewed: true } : body) }),
  evaluateDuration: (id: string, part: number, duration_seconds: number) => request<{status: string; min_seconds: number | null; max_seconds: number | null}>(`/v1/grading-presets/versions/${encodeURIComponent(id)}/duration-results`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ part, duration_seconds }) }),
  getTemplateVersion: (id: string) => request<TemplateVersion>(`/v1/templates/versions/${encodeURIComponent(id)}`),
  templateFileUrl: (id: string) => `${base}/v1/templates/versions/${encodeURIComponent(id)}/file`,
  uploadTemplate: (file: File, options: { name?: string; template_id?: string }) => {
    const form = new FormData(); form.append("file", file);
    if (options.name) form.set("name", options.name);
    if (options.template_id) form.set("template_id", options.template_id);
    return request<TemplateVersion>("/v1/templates", { method: "POST", body: form });
  },
  confirmTemplateProtection: (id: string, decisions: { run_id: string; protected: boolean }[]) => request<TemplateVersion>(`/v1/templates/versions/${encodeURIComponent(id)}/protection`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decisions }) }),
  listAssessments: () => request<SpeakingAssessment[]>("/v1/speaking-assessments?limit=200"),
  getAssessment: (id: string) => request<SpeakingAssessment>(`/v1/speaking-assessments/${encodeURIComponent(id)}`),
  createAssessment: (input: { job_id: string; language: "en"; part?: 1 | 2 | 3 | 4; question?: string; grading_rule_version_id?: string }) => request<SpeakingAssessment>("/v1/speaking-assessments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }),
  retryAssessment: (id: string) => request<SpeakingAssessment>(`/v1/speaking-assessments/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  downloadAssessment: async (assessment: SpeakingAssessment, filename: string) => {
    const blob = new Blob([JSON.stringify(assessment, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a"); anchor.href = href; anchor.download = `${filename.replace(/[^a-z0-9._-]+/gi, "_") || "speaking-feedback"}.json`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  },
  downloadExport: async (id: string, format: "txt" | "srt" | "vtt" | "json") => {
    const response = await fetch(api.exportUrl(id, format));
    if (!response.ok) throw new Error(`Export failed (${response.status})`);
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a"); anchor.href = href; anchor.download = `transcript.${format}`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  },
};
