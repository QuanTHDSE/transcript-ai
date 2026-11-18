"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "../../lib/api";
import type { Job } from "../../lib/types";
import { PageTitle } from "../../components/shell";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { const result = await api.listJobs(); if (active) { setJobs(result); setError(""); } }
      catch (e) { if (active) setError(e instanceof Error ? e.message : "Queue unavailable"); }
      finally { if (active) { setLoading(false); timer = setTimeout(poll, 3000); } }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  async function action(job: Job, type: "retry" | "cancel") {
    setBusy(job.id);
    try { const changed = await (type === "retry" ? api.retryJob(job.id) : api.cancelJob(job.id)); setJobs(current => current.map(item => item.id === changed.id ? changed : item)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Action failed"); }
    finally { setBusy(null); }
  }
  async function remove(job: Job) {
    if (!window.confirm(`Delete ${job.original_filename} and its transcript? This cannot be undone.`)) return;
    setBusy(job.id);
    try { await api.deleteJob(job.id); setJobs(current => current.filter(item => item.id !== job.id)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Delete failed"); }
    finally { setBusy(null); }
  }
  return <><PageTitle title="Transcription queue" subtitle="Latest 100 jobs · refreshed every 3 seconds" action={<Link href="/upload">Upload files →</Link>} />
    <p className="mb-4 text-sm text-slate-500">Cancellation prevents saved results. Running inference may finish before resources are released.</p>
    {error && <p role="alert" className="mb-4 text-red-600">{error} Status may be out of date.</p>}
    {loading ? <p>Loading queue…</p> : !jobs.length && !error ? <p>No jobs yet.</p> : null}
    <div className="space-y-3">{jobs.map(job => <article className="card flex flex-wrap items-center justify-between gap-4" key={job.id}>
      <div className="min-w-0"><h2 className="break-all font-semibold">{job.original_filename}</h2><p className="text-sm text-slate-500">{job.status} · {job.stage.replaceAll("_", " ")} · Attempts: {job.attempts}</p>{job.error_message && <p className="mt-2 text-sm text-red-600">{job.error_message}</p>}</div>
      <div className="flex gap-3">{job.status === "COMPLETED" && <Link href={`/transcripts/${job.id}`}>Edit transcript →</Link>}{job.status === "FAILED" && <button disabled={busy !== null} onClick={() => void action(job,"retry")}>Retry</button>}{["QUEUED","PROCESSING"].includes(job.status) && <button disabled={busy !== null} onClick={() => void action(job,"cancel")}>Cancel</button>}{["COMPLETED", "FAILED", "CANCELED"].includes(job.status) && <button disabled={busy !== null} onClick={() => void remove(job)}>Delete</button>}</div>
    </article>)}</div></>;
}
