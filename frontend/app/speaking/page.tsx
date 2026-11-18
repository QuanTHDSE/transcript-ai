"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageTitle } from "../../components/shell";
import { StatusPill } from "../../components/assessment";
import { api } from "../../lib/api";
import type { Job, SpeakingAssessment, GradingPreset } from "../../lib/types";

export default function SpeakingDashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [assessments, setAssessments] = useState<SpeakingAssessment[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [part, setPart] = useState<"" | "1" | "2" | "3" | "4">("");
  const [question, setQuestion] = useState("");
  const [presets, setPresets] = useState<GradingPreset[]>([]);
  const [ruleVersion, setRuleVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [jobList, assessmentList] = await Promise.all([api.listJobs(), api.listAssessments()]);
    setJobs(jobList);
    setAssessments(assessmentList);
    setSelected((current) => current.filter((id) => jobList.some((job) => job.id === id && job.status === "COMPLETED" && job.language !== "vi" && Boolean(job.segments?.length))));
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(); if (active) setError(""); }
      catch (e) { if (active) setError(e instanceof Error ? e.message : "Could not load speaking assessments"); }
      finally { if (active) { setLoading(false); timer = setTimeout(poll, 4000); } }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [refresh]);

  useEffect(() => { api.listGradingPresets().then(setPresets).catch(() => setError("Could not load grading presets.")); }, []);

  const jobById = useMemo(() => new Map(jobs.map((job) => [job.id, job])), [jobs]);
  const eligibleJobs = jobs.filter((job) => job.status === "COMPLETED" && job.language !== "vi" && Boolean(job.segments?.length));

  function toggle(id: string) { setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]); }

  async function createBatch() {
    setBusy(true); setError("");
    const results = await Promise.allSettled(selected.map((job_id) => api.createAssessment({ job_id, language: "en", ...(part ? { part: Number(part) as 1 | 2 | 3 | 4 } : {}), ...(question.trim() ? { question: question.trim() } : {}), ...(ruleVersion ? { grading_rule_version_id: ruleVersion } : {}) })));
    const failures = results.filter((result) => result.status === "rejected");
    setSelected([]);
    try { await refresh(); } catch { /* the next poll will refresh the list */ }
    if (failures.length) setError(`${failures.length} recording(s) could not be assessed. ${failures[0].status === "rejected" ? String(failures[0].reason) : ""}`);
    setBusy(false);
  }

  async function retry(assessment: SpeakingAssessment) {
    setRetrying(assessment.id); setError("");
    try {
      const updated = await api.retryAssessment(assessment.id);
      setAssessments((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (e) { setError(e instanceof Error ? e.message : "Retry failed"); }
    finally { setRetrying(null); }
  }

  return <>
    <PageTitle title="Speaking feedback" subtitle="Review independent English speaking assessments and practice evidence." action={<Link href="/upload">Upload recording →</Link>} />
    <p className="mb-5 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">All feedback and any provider estimates are for practice only. They are not official IELTS results.</p>
    {error && <p role="alert" className="mb-4 text-sm text-rose-700 dark:text-rose-300">{error}</p>}
    <section className="card mb-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">Assess completed recordings</h2><p className="mt-1 text-sm text-slate-500">Select one or more transcripts. Each recording is submitted as its own assessment.</p></div><Link href="/jobs" className="text-sm text-indigo-700 dark:text-indigo-300">Transcription queue →</Link></div>
      <div className="mb-4 grid gap-3 sm:grid-cols-2"><label className="text-sm">Speaking part (optional)<select className="mt-1 block w-full rounded-lg border border-slate-300 bg-transparent p-2 dark:border-slate-700" value={part} onChange={(event) => setPart(event.target.value as typeof part)}><option value="">Not specified</option><option value="1">Part 1</option><option value="2">Part 2</option><option value="3">Part 3</option><option value="4">Part 4 (custom rules)</option></select></label><label className="text-sm">Original question (optional)<input className="mt-1 block w-full rounded-lg border border-slate-300 bg-transparent p-2 dark:border-slate-700" maxLength={4000} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Paste the speaking prompt" /></label></div>
      <label className="mb-4 block text-sm">Grading rules version (optional)<select className="mt-1 block w-full rounded-lg border border-slate-300 bg-transparent p-2 dark:border-slate-700" value={ruleVersion} onChange={event => setRuleVersion(event.target.value)}><option value="">None</option>{presets.flatMap(p => p.versions.map(v => <option key={v.id} value={v.id}>{p.name} · v{v.version}</option>))}</select><span className="text-slate-500">The selected version is recorded for this assessment. Check duration separately in Grading rules.</span></label>
      {loading ? <p className="text-sm text-slate-500">Loading completed recordings…</p> : eligibleJobs.length === 0 ? <p className="text-sm text-slate-500">No completed English recordings with transcript segments yet. Upload and transcribe a recording first.</p> : <div className="max-h-64 space-y-2 overflow-y-auto">{eligibleJobs.map((job) => <div key={job.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700"><label className="flex min-w-0 flex-1 cursor-pointer items-center gap-3"><input type="checkbox" checked={selected.includes(job.id)} onChange={() => toggle(job.id)} /><span className="min-w-0"><span className="block break-all font-medium">{job.original_filename}</span><span className="text-xs text-slate-500">{job.segments?.length ?? 0} transcript segments · language {job.language}</span></span></label><Link href={`/transcripts/${job.id}`} className="text-sm">Transcript</Link></div>)}</div>}
      <button className="mt-4 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" disabled={busy || selected.length === 0 || (part === "4" && !ruleVersion)} onClick={() => void createBatch()}>{busy ? "Submitting assessments…" : `Assess ${selected.length || "selected"} recording${selected.length === 1 ? "" : "s"}`}</button>
    </section>

    <section aria-labelledby="history-title"><div className="mb-3 flex items-center justify-between"><h2 id="history-title" className="text-lg font-semibold">Assessment history</h2><span className="text-sm text-slate-500">Refreshes every 4 seconds</span></div>
      {!assessments.length && !loading && !error ? <div className="card text-sm text-slate-500">No speaking assessments yet.</div> : <div className="space-y-3">{assessments.map((assessment) => {
        const job = jobById.get(assessment.job_id);
        const canRetry = ["FAILED", "PARTIAL", "NOT_ASSESSED"].includes(assessment.status) || assessment.criteria.some((criterion) => ["FAILED", "UNAVAILABLE", "NOT_ASSESSED"].includes(criterion.status));
        return <article key={assessment.id} className="card flex flex-wrap items-center justify-between gap-4"><div className="min-w-0"><h3 className="break-all font-semibold">{job?.original_filename ?? `Recording ${assessment.job_id.slice(0, 8)}`}</h3><p className="mt-1 text-xs text-slate-500">Part {assessment.part ?? "not specified"} · {assessment.criteria.filter((criterion) => criterion.status === "COMPLETED").length} of {assessment.criteria.length} analyses complete</p><div className="mt-2"><StatusPill status={assessment.status} /></div></div><div className="flex flex-wrap items-center gap-3"><Link href={`/speaking/${assessment.id}`} className="text-sm font-medium text-indigo-700 dark:text-indigo-300">View report →</Link><button className="text-sm" onClick={() => void api.downloadAssessment(assessment, job?.original_filename ?? "speaking-feedback")}>Export JSON</button>{canRetry && <button className="text-sm" disabled={retrying !== null} onClick={() => void retry(assessment)}>{retrying === assessment.id ? "Retrying…" : "Retry failed analysis"}</button>}</div></article>;
      })}</div>}
    </section>
  </>;
}
