export type JobStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED" | "CANCELED";
export interface Segment { id: string; ordinal: number; start_ms: number; end_ms: number; text: string }
export interface Job { id: string; original_filename: string; media_type: string; status: JobStatus; stage: string; attempts: number; language: string; error_message: string | null; segments?: Segment[] }
export interface UploadResult { filename: string; job_id?: string; error?: string }
export type AssessmentStatus = "QUEUED" | "PROCESSING" | "COMPLETED" | "PARTIAL" | "FAILED" | "NOT_ASSESSED";
export type CriterionStatus = AssessmentStatus | "UNAVAILABLE";
export interface AssessmentFinding {
  segment_id: string; start_ms: number; end_ms: number; start_time: number; end_time: number;
  evidence: string; original_text: string; suggested_correction: string | null; corrected_text: string | null;
  explanation: string; explanation_en: string; explanation_vi: string | null; category: string | null;
  issue_type: string | null; severity: string | null; confidence: number | null; uncertain: boolean;
  suggested_practice: string | null; recommended_practice: string | null;
}
export interface AssessmentRecommendation { exercise: string; explanation: string }
export interface AssessmentCriterion {
  code: "fluency_coherence" | "lexical_resource" | "grammar_accuracy" | "pronunciation";
  source: "TEXT" | "AUDIO"; status: CriterionStatus; provider: string | null; error: string | null;
  score: number | null; analysis: Record<string, unknown> | null;
  feedback: AssessmentFinding[]; recommendations: AssessmentRecommendation[];
}
export interface TranscriptSnapshot { id: string; start_ms: number; end_ms: number; text: string }
export interface SpeakingAssessment {
  id: string; job_id: string; status: AssessmentStatus; part: number | null; question: string | null;
  grading_rule_version_id: string | null;
  criteria: AssessmentCriterion[]; disclaimer: string; transcript_snapshot?: TranscriptSnapshot[];
}
export interface TemplateRun { id: string; text: string; color: string | null; color_source: string | null; highlight: string | null; bold: boolean | null; italic: boolean | null; red: boolean; protected_candidate: boolean }
export interface TemplateParagraph { type: "paragraph"; id: string; text: string; style: string | null; heading: string | null; part: number | null; question: boolean; runs: TemplateRun[] }
export interface TemplateTable { type: "table"; id: string; rows: TemplateBlock[][][] }
export type TemplateBlock = TemplateParagraph | TemplateTable;
export interface TemplateParsed { blocks: TemplateBlock[]; sections: { index: number; start_type: string; header: TemplateBlock[]; footer: TemplateBlock[] }[]; warnings: string[]; image_count: number }
export interface TemplateVersion { id: string; template_id: string; version: number; original_filename: string; sha256: string; size_bytes: number; created_at: string; parsed: TemplateParsed; protection_decisions: Record<string, boolean> }
export interface TemplateSummary { id: string; name: string; created_at: string; versions: { id: string; version: number; original_filename: string; created_at: string; warnings: string[] }[] }
export interface GradingRules { duration_limits: { part: number; min_seconds: number; max_seconds: number }[]; correction_styles: { category: string; color: string; correction_placement: string }[]; preserve_original_red_text: boolean; required_structures: string[] }
export interface GradingVersion { id: string; preset_id: string; version: number; instructions: string; rules: GradingRules }
export interface GradingPreset { id: string; name: string; template_version_id: string; versions: GradingVersion[] }
export interface MappingBatch { id: string; template_id: string; template_version_id: string; rule_version_id: string; duration_metric: string; questions: { id: string; part: number | null; text: string }[]; recordings: { id: string; recording_id: string; transcript_id: string | null; filename: string; transcription_status: string; student_id: string | null; part: number | null; question_id: string | null; status: string; analysis_status: string; assessment_ready: boolean; duration: Record<string, unknown> | null; error: string | null; suggestions: { question_id: string; part: number | null; reason: string }[] }[]; missing: { student_id: string; question_id: string; part: number | null }[] }
