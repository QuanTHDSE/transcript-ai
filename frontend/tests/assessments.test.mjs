import test from "node:test";
import assert from "node:assert/strict";
import { criterionForOverview, scoreLabel, topPriorities } from "../lib/assessment-utils.ts";

const finding = (overrides = {}) => ({
  segment_id: "segment-1", evidence: "I is", issue_type: "grammar", severity: "medium",
  suggested_practice: "Practise subject agreement", suggested_correction: "I am", ...overrides,
});
const criterion = (code, source, overrides = {}) => ({ code, source, status: "COMPLETED", score: null, feedback: [], ...overrides });

test("fluency overview uses only a score actually returned by an evaluator", () => {
  const assessment = { criteria: [
    criterion("fluency_coherence", "AUDIO", { score: null, status: "COMPLETED" }),
    criterion("fluency_coherence", "TEXT", { score: 6.5 }),
  ] };
  assert.equal(criterionForOverview(assessment, "fluency_coherence").source, "TEXT");
  assert.equal(scoreLabel(criterionForOverview(assessment, "fluency_coherence")), "6.5 provider estimate");
});

test("missing and unassessed scores are never derived or displayed as estimates", () => {
  assert.equal(scoreLabel(criterion("pronunciation", "AUDIO", { status: "NOT_ASSESSED", score: null })), "No score provided");
  assert.equal(scoreLabel(criterion("lexical_resource", "TEXT", { status: "FAILED", score: 7 })), "No score provided");
});

test("top priorities are evidence-backed, ordered by severity and deduplicated", () => {
  const criteria = [criterion("grammar_accuracy", "TEXT", { feedback: [finding(), finding({ severity: "high", evidence: "she go" }), finding({ severity: "high", evidence: "she go" })] })];
  const result = topPriorities(criteria, 3);
  assert.equal(result.length, 2);
  assert.equal(result[0].severity, "high");
});

test("findings without an actionable correction or practice do not become priorities", () => {
  assert.deepEqual(topPriorities([criterion("fluency_coherence", "AUDIO", { feedback: [finding({ suggested_practice: null, suggested_correction: null })] })]), []);
});
