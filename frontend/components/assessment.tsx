import type { ReactNode } from "react";
import { criterionForOverview, criterionLabels, scoreLabel } from "../lib/assessment-utils";
import type { AssessmentCriterion, SpeakingAssessment } from "../lib/types";

export function StatusPill({ status }: { status: string }) {
  const color = status === "COMPLETED" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
    : status === "FAILED" || status === "UNAVAILABLE" ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200"
      : status === "NOT_ASSESSED" ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
        : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${color}`}>{status.replaceAll("_", " ")}</span>;
}

export function AssessmentOverview({ assessment }: { assessment: SpeakingAssessment }) {
  const codes: AssessmentCriterion["code"][] = ["fluency_coherence", "lexical_resource", "grammar_accuracy", "pronunciation"];
  return <section aria-labelledby="criteria-title" className="card">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 id="criteria-title" className="text-lg font-semibold">IELTS assessment overview</h2><p className="mt-1 text-sm text-slate-500">Completion: <StatusPill status={assessment.status} /></p></div><p className="text-xs text-slate-500">Practice estimates only · not official IELTS results</p></div>
    <div className="grid gap-3 sm:grid-cols-2">{codes.map((code) => {
      const criterion = criterionForOverview(assessment, code);
      return <article className="rounded-xl border border-slate-200 p-4 dark:border-slate-700" key={code}>
        <div className="flex items-start justify-between gap-3"><h3 className="font-medium">{criterionLabels[code]}</h3><StatusPill status={criterion?.status ?? "NOT_ASSESSED"} /></div>
        <p className="mt-3 text-sm font-semibold">{scoreLabel(criterion)}</p>
        {criterion?.score != null && <p className="mt-1 text-xs text-slate-500">Estimate returned by {criterion.provider ?? "configured evaluator"}; no score range is provided by the API.</p>}
        {criterion?.error && <p className="mt-2 text-sm text-rose-700 dark:text-rose-300">{criterion.error}</p>}
        {code === "fluency_coherence" && criterion && <p className="mt-2 text-xs text-slate-500">Evidence source: {criterion.source === "AUDIO" ? "audio analysis" : "transcript analysis"}</p>}
      </article>;
    })}</div>
  </section>;
}

export function Panel({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return <section className="card"><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{title}</h2>{aside}</div>{children}</section>;
}
