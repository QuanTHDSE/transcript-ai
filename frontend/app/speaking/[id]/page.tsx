"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { AssessmentOverview, Panel, StatusPill } from "../../../components/assessment";
import { PageTitle } from "../../../components/shell";
import { api } from "../../../lib/api";
import { criterionLabels, secondsLabel, topPriorities } from "../../../lib/assessment-utils";
import type { AssessmentCriterion, AssessmentFinding, Job, SpeakingAssessment, TranscriptSnapshot } from "../../../lib/types";

type Interval = { start_ms: number; end_ms: number };
function intervals(value: unknown): Interval[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Interval => Boolean(item) && Number.isFinite((item as Interval).start_ms) && Number.isFinite((item as Interval).end_ms) && (item as Interval).end_ms > (item as Interval).start_ms);
}
function metric(analysis: Record<string, unknown> | null, key: string) {
  const value = analysis?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function highlightedSentence(text: string, evidence: string) {
  const index = text.indexOf(evidence);
  if (index < 0 || !evidence.trim()) return text;
  return <>{text.slice(0, index)}<mark className="rounded bg-amber-200 px-0.5 text-slate-900 dark:bg-amber-400">{text.slice(index, index + evidence.length)}</mark>{text.slice(index + evidence.length)}</>;
}

export default function SpeakingReportPage() {
  const { id } = useParams<{ id: string }>();
  const [assessment, setAssessment] = useState<SpeakingAssessment | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const player = useRef<HTMLAudioElement & HTMLVideoElement>(null);
  const hasLoadedJob = useRef(false);

  const load = useCallback(async () => {
    try {
      const assessmentResult = await api.getAssessment(id);
      setAssessment(assessmentResult);
      if (!hasLoadedJob.current) { setJob(await api.getJob(assessmentResult.job_id)); hasLoadedJob.current = true; }
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load the assessment report"); }
    finally { setLoading(false); }
  }, [id]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { if (!active) return; await load(); if (active) timer = setTimeout(poll, 3500); };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [load]);

  const snapshot = assessment?.transcript_snapshot ?? [];
  const findings = assessment?.criteria.flatMap((criterion) => criterion.feedback.map((finding) => ({ criterion, finding }))) ?? [];
  const bySegment = useMemo(() => {
    const groups = new Map<string, { criterion: AssessmentCriterion; finding: AssessmentFinding }[]>();
    for (const item of findings) groups.set(item.finding.segment_id, [...(groups.get(item.finding.segment_id) ?? []), item]);
    return groups;
  }, [assessment]);
  const audioCriteria = assessment?.criteria.filter((criterion) => criterion.source === "AUDIO") ?? [];
  const audioFluency = audioCriteria.find((criterion) => criterion.code === "fluency_coherence");
  const pronunciation = audioCriteria.find((criterion) => criterion.code === "pronunciation");
  const analysis = audioFluency?.analysis ?? null;
  const duration = metric(analysis, "duration_ms") ?? job?.segments?.reduce((end, segment) => Math.max(end, segment.end_ms), 0) ?? 0;
  const speechIntervals = intervals(analysis?.speech_intervals);
  const silenceIntervals = intervals(analysis?.silence_intervals);
  const priorities = topPriorities(assessment?.criteria ?? []);
  const recommendations = assessment?.criteria.flatMap((criterion) => criterion.recommendations.map((item) => ({ ...item, source: criterionLabels[criterion.code] }))) ?? [];
  const strengths = assessment?.criteria.flatMap((criterion) => {
    const values = criterion.analysis?.strengths;
    return Array.isArray(values) ? values.filter((item): item is string => typeof item === "string") : [];
  }) ?? [];

  async function retry() {
    if (!assessment) return;
    setRetrying(true); setError("");
    try { setAssessment(await api.retryAssessment(assessment.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not retry this assessment"); }
    finally { setRetrying(false); }
  }

  function seek(ms: number) {
    if (player.current) { player.current.currentTime = ms / 1000; void player.current.play(); }
  }

  if (loading) return <><PageTitle title="Speaking assessment" subtitle="Loading report…" /><p role="status">Loading assessment and transcript evidence…</p></>;
  if (!assessment || !job) return <><PageTitle title="Report unavailable" subtitle={error || "Assessment or recording not found"} /><Link href="/speaking">Back to speaking dashboard</Link></>;
  const isVideo = job.media_type.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(job.original_filename);

  return <>
    <PageTitle title={job.original_filename} subtitle={`Speaking assessment · Part ${assessment.part ?? "not specified"}`} action={<Link href="/speaking">← All assessments</Link>} />
    {error && <p role="alert" className="mb-4 text-sm text-rose-700 dark:text-rose-300">{error}</p>}
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"><p>{assessment.disclaimer}</p><div className="flex items-center gap-3"><StatusPill status={assessment.status} /><button onClick={() => void api.downloadAssessment(assessment, job.original_filename)} className="rounded-lg border border-current px-3 py-1.5">Export report JSON</button>{["FAILED", "PARTIAL", "NOT_ASSESSED"].includes(assessment.status) && <button disabled={retrying} onClick={() => void retry()}>{retrying ? "Retrying…" : "Retry failed analysis"}</button>}</div></div>
    <div className="space-y-6">
      <AssessmentOverview assessment={assessment} />

      <Panel title="Audio fluency and pronunciation">
        {isVideo ? <video ref={player} controls preload="metadata" className="mb-5 max-h-[50vh] w-full rounded-xl bg-black" src={api.mediaUrl(job.id)} /> : <audio ref={player} controls preload="metadata" className="mb-5 w-full" src={api.mediaUrl(job.id)} />}
        <div className="mb-4" aria-label="Speech and pause intervals">
          <div className="mb-2 flex flex-wrap gap-4 text-xs text-slate-500"><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-indigo-500" />Speech detected</span><span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-slate-300 dark:bg-slate-600" />Non-speech interval</span><span>Intervals measured from audio; not word alignment</span></div>
          {duration > 0 && (speechIntervals.length || silenceIntervals.length) ? <div className="relative h-8 overflow-hidden rounded-lg bg-slate-200 dark:bg-slate-800">{silenceIntervals.map((interval, index) => <button aria-label={`Replay non-speech interval ${index + 1}`} key={`silence-${index}`} title={`Non-speech ${secondsLabel(interval.start_ms)}–${secondsLabel(interval.end_ms)}`} onClick={() => seek(interval.start_ms)} className="absolute inset-y-0 bg-slate-400/60 hover:bg-slate-500/70 dark:bg-slate-600" style={{ left: `${interval.start_ms / duration * 100}%`, width: `${Math.max((interval.end_ms - interval.start_ms) / duration * 100, 0.4)}%` }} />)}{speechIntervals.map((interval, index) => <button aria-label={`Replay speech interval ${index + 1}`} key={`speech-${index}`} title={`Speech ${secondsLabel(interval.start_ms)}–${secondsLabel(interval.end_ms)}`} onClick={() => seek(interval.start_ms)} className="absolute inset-y-1 rounded bg-indigo-500 hover:bg-indigo-600" style={{ left: `${interval.start_ms / duration * 100}%`, width: `${Math.max((interval.end_ms - interval.start_ms) / duration * 100, 0.4)}%` }} />)}</div> : <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500 dark:bg-slate-900">{audioFluency?.status === "NOT_ASSESSED" || audioFluency?.status === "UNAVAILABLE" ? audioFluency.error ?? "Audio intervals were not assessed." : "Audio interval data is not available yet."}</p>}
        </div>
        <div className="mb-5 grid gap-3 sm:grid-cols-3">{[["Speaking duration", metric(analysis, "speech_duration_ms") === null ? null : `${(metric(analysis, "speech_duration_ms")! / 1000).toFixed(1)} sec`], ["Approx. speaking rate", metric(analysis, "approx_words_per_minute") === null ? null : `${metric(analysis, "approx_words_per_minute")} words/min`], ["Articulation rate", metric(analysis, "approx_articulation_words_per_minute") === null ? null : `${metric(analysis, "approx_articulation_words_per_minute")} words/min`]].map(([label, value]) => <div key={label} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-semibold">{value ?? "NOT_ASSESSED"}</p></div>)}</div>
        <h3 className="mb-2 font-semibold">Audio-based findings</h3>
        {audioCriteria.flatMap((criterion) => criterion.feedback.map((finding, index) => ({ criterion, finding, index }))).length ? <div className="space-y-3">{audioCriteria.flatMap((criterion) => criterion.feedback.map((finding, index) => ({ criterion, finding, index }))).map(({ criterion, finding, index }) => <article key={`${criterion.code}-${index}`} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><div className="flex flex-wrap items-center gap-2"><StatusPill status={finding.uncertain ? "UNCERTAIN" : "COMPLETED"} /><span className="text-sm font-medium">{finding.issue_type ?? finding.category ?? criterionLabels[criterion.code]}</span><button className="font-mono text-xs text-indigo-700 underline dark:text-indigo-300" onClick={() => seek(finding.start_ms)}>{secondsLabel(finding.start_ms)} – {secondsLabel(finding.end_ms)}</button></div><p className="mt-2 text-sm">{finding.evidence}</p><p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{finding.explanation_vi ?? finding.explanation}</p>{finding.suggested_practice && <p className="mt-2 text-sm"><strong>Practice:</strong> {finding.suggested_practice}</p>}{finding.uncertain && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">Potential cue; replay audio before treating it as a problem.</p>}</article>)}</div> : <p className="rounded-xl border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-700">No audio-based findings were returned for this recording.</p>}
        {pronunciation && <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300"><strong>Pronunciation:</strong> <StatusPill status={pronunciation.status} /> {pronunciation.status === "NOT_ASSESSED" || pronunciation.status === "UNAVAILABLE" ? pronunciation.error ?? "No supported audio pronunciation evidence is available." : pronunciation.feedback.length ? "Findings below are based on the configured audio evaluator." : "No evidence-backed pronunciation findings were returned."} {pronunciation.score != null && <span> Provider estimate: {pronunciation.score.toFixed(1)}.</span>}</div>}
        {audioCriteria.flatMap((criterion) => criterion.recommendations.map((item, index) => ({ criterion, item, index }))).length > 0 && <div className="mt-4"><h4 className="mb-2 text-sm font-semibold">Audio practice suggestions</h4><ul className="space-y-2">{audioCriteria.flatMap((criterion) => criterion.recommendations.map((item, index) => ({ criterion, item, index }))).map(({ criterion, item, index }) => <li key={`${criterion.code}-${index}`} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-900"><strong>{item.exercise}</strong> — {item.explanation}</li>)}</ul></div>}
      </Panel>

      <Panel title="Interactive transcript" aside={<span className="text-xs text-slate-500">Original immutable assessment snapshot</span>}>
        {assessment.question && <p className="mb-4 rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-900"><strong>Question:</strong> {assessment.question}</p>}
        {!snapshot.length ? <p className="text-sm text-slate-500">Transcript snapshot is not available.</p> : <div className="max-h-[65vh] space-y-3 overflow-y-auto">{snapshot.map((segment: TranscriptSnapshot) => {
          const items = bySegment.get(segment.id) ?? [];
          return <article key={segment.id} className="rounded-xl border border-slate-200 p-4 dark:border-slate-700"><div className="flex items-start gap-3"><button className="shrink-0 rounded-md bg-indigo-50 px-2 py-1 font-mono text-xs text-indigo-800 dark:bg-indigo-950 dark:text-indigo-200" onClick={() => seek(segment.start_ms)}>{secondsLabel(segment.start_ms)}</button><div className="min-w-0 flex-1"><p className="leading-7">{items.length ? highlightedSentence(segment.text, items[0].finding.evidence) : segment.text}</p><p className="mt-1 text-xs text-slate-500">Original sentence · {secondsLabel(segment.start_ms)}–{secondsLabel(segment.end_ms)}</p>
            {items.map(({ criterion, finding }, index) => <div key={`${criterion.source}-${criterion.code}-${index}`} className="mt-3 rounded-lg bg-amber-50 p-3 text-sm dark:bg-amber-950/30"><p className="font-medium">{criterionLabels[criterion.code]} · {criterion.source === "AUDIO" ? "audio" : "transcript"} evidence {finding.uncertain && <span className="text-amber-700 dark:text-amber-300">· uncertain</span>}</p>{finding.evidence !== segment.text && <p className="mt-1"><strong>Evidence:</strong> {finding.evidence}</p>}{finding.suggested_correction && <p className="mt-1"><strong>Suggested correction:</strong> {finding.suggested_correction}</p>}<p className="mt-1">{finding.explanation_vi ?? finding.explanation}</p><button className="mt-2 font-mono text-xs text-indigo-700 underline dark:text-indigo-300" onClick={() => seek(finding.start_ms)}>{secondsLabel(finding.start_ms)} · replay</button>{finding.suggested_practice && <p className="mt-2 text-xs"><strong>Practice:</strong> {finding.suggested_practice}</p>}</div>)}
          </div></div></article>;
        })}</div>}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2"><Panel title="Improvement plan"><h3 className="mb-2 text-sm font-semibold">Top priorities</h3>{priorities.length ? <ol className="list-inside list-decimal space-y-3">{priorities.map((finding, index) => <li key={`${finding.segment_id}-${finding.issue_type}-${index}`} className="text-sm"><span className="font-medium">{finding.suggested_practice ?? finding.issue_type ?? finding.category ?? "Review this example"}</span><span className="block pl-5 text-slate-500">{finding.evidence}</span>{finding.suggested_correction && <span className="block pl-5">Example: “{finding.suggested_correction}”</span>}</li>)}</ol> : <p className="text-sm text-slate-500">No evidence-backed priorities have been returned yet.</p>}<h3 className="mb-2 mt-5 text-sm font-semibold">Recommended exercises</h3>{recommendations.length ? <ul className="space-y-3">{recommendations.map((item, index) => <li key={`${item.source}-${index}`} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-900"><strong>{item.exercise}</strong><p className="mt-1 text-slate-500">{item.explanation}</p><span className="mt-1 block text-xs text-slate-400">{item.source}</span></li>)}</ul> : <p className="text-sm text-slate-500">No exercises were provided by the evaluators.</p>}</Panel>
        <Panel title="Areas going well">{strengths.length ? <ul className="list-inside list-disc space-y-2 text-sm">{strengths.map((strength, index) => <li key={`${index}-${strength}`}>{strength}</li>)}</ul> : <p className="text-sm text-slate-500">No explicit strengths were returned for this assessment; no positive findings are inferred.</p>}</Panel></div>
    </div>
  </>;
}
