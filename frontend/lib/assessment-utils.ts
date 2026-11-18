import type { AssessmentCriterion, AssessmentFinding, SpeakingAssessment } from "./types";

export const criterionLabels: Record<AssessmentCriterion["code"], string> = {
  fluency_coherence: "Fluency and Coherence",
  lexical_resource: "Lexical Resource",
  grammar_accuracy: "Grammatical Range and Accuracy",
  pronunciation: "Pronunciation",
};

export function criterionForOverview(assessment: SpeakingAssessment, code: AssessmentCriterion["code"]) {
  const matches = assessment.criteria.filter((criterion) => criterion.code === code);
  if (code === "fluency_coherence") {
    const audio = matches.find((criterion) => criterion.source === "AUDIO");
    const text = matches.find((criterion) => criterion.source === "TEXT");
    return [audio, text].find((criterion) => criterion?.status === "COMPLETED" && criterion.score !== null)
      ?? [text, audio].find((criterion) => criterion?.status === "COMPLETED")
      ?? audio ?? text;
  }
  return matches.find((criterion) => criterion.source === (code === "pronunciation" ? "AUDIO" : "TEXT"));
}

export function scoreLabel(criterion: AssessmentCriterion | undefined) {
  return criterion?.score != null && criterion.status === "COMPLETED"
    ? `${criterion.score.toFixed(1)} provider estimate`
    : "No score provided";
}

const severityRank: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function topPriorities(criteria: AssessmentCriterion[], limit = 3): AssessmentFinding[] {
  return criteria.flatMap((criterion) => criterion.feedback)
    .filter((finding) => Boolean(finding.suggested_practice || finding.suggested_correction))
    .sort((a, b) => (severityRank[(a.severity ?? "").toLowerCase()] ?? 3) - (severityRank[(b.severity ?? "").toLowerCase()] ?? 3))
    .filter((finding, index, all) => all.findIndex((item) => item.segment_id === finding.segment_id && item.evidence === finding.evidence && item.issue_type === finding.issue_type) === index)
    .slice(0, limit);
}

export function secondsLabel(milliseconds: number) {
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1000);
  const tenths = Math.floor((milliseconds % 1000) / 100);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${tenths}`;
}
