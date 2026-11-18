"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "../../../lib/api";
import { settleSavedDraft } from "../../../lib/drafts";
import type { Job, Segment } from "../../../lib/types";
import { PageTitle } from "../../../components/shell";

type ExportFormat = "txt" | "srt" | "vtt" | "json";
const formats: ExportFormat[] = ["txt", "srt", "vtt", "json"];

function displayTime(ms: number) {
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  const tenths = Math.floor((ms % 1000) / 100);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

export default function TranscriptPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const player = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const storageKey = `transcripto:drafts:${id}`;

  const load = useCallback(async () => {
    try {
      const result = await api.getJob(id);
      setJob(result);
      setDrafts(previous => {
        const savedText = new Map((result.segments ?? []).map(segment => [segment.id, segment.text]));
        const next = Object.fromEntries(Object.entries(previous).filter(([segmentId, text]) => savedText.get(segmentId) !== text));
        try { Object.keys(next).length ? localStorage.setItem(storageKey, JSON.stringify(next)) : localStorage.removeItem(storageKey); } catch { /* keep in-memory drafts */ }
        return next;
      });
      setError("");
      setLoading(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load transcript");
      setLoading(false);
    }
  }, [id, storageKey]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) setDrafts(JSON.parse(saved) as Record<string, string>);
    } catch { localStorage.removeItem(storageKey); }
    const poll = async () => {
      if (!active) return;
      await load();
      if (active) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [load, storageKey]);

  const segments = job?.segments ?? [];
  const dirty = useMemo(() => segments.some(segment => drafts[segment.id] !== undefined && drafts[segment.id] !== segment.text), [drafts, segments]);
  const activeId = segments.find(segment => currentTime >= segment.start_ms / 1000 && currentTime < segment.end_ms / 1000)?.id;
  const filtered = segments.filter(segment => (drafts[segment.id] ?? segment.text).toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function changeDraft(segment: Segment, text: string) {
    setDrafts(previous => {
      const next = { ...previous, [segment.id]: text };
      try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { setError("Local draft storage is unavailable; save edits before leaving this page."); }
      return next;
    });
  }

  async function save(segment: Segment) {
    const text = drafts[segment.id];
    if (text === undefined || text === segment.text) return;
    setBusy(segment.id); setError("");
    try {
      const saved = await api.updateSegment(id, segment.id, text);
      setJob(previous => previous ? { ...previous, segments: previous.segments?.map(item => item.id === saved.id ? saved : item) } : previous);
      setDrafts(previous => {
        const next = settleSavedDraft(previous, segment.id, text);
        try { Object.keys(next).length ? localStorage.setItem(storageKey, JSON.stringify(next)) : localStorage.removeItem(storageKey); } catch { /* the saved database value remains authoritative */ }
        return next;
      });
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save edit. Your draft is kept on this device."); }
    finally { setBusy(null); }
  }

  async function download(format: ExportFormat) {
    if (dirty) { setError("Save your unsaved edits before downloading."); return; }
    setBusy("export"); setError("");
    try { await api.downloadExport(id, format); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not download transcript"); }
    finally { setBusy(null); }
  }

  function seek(segment: Segment) {
    if (player.current) { player.current.currentTime = segment.start_ms / 1000; void player.current.play(); }
  }

  if (loading) return <><PageTitle title="Transcript" subtitle="Loading job and transcript…" />{error && <p role="alert">{error}</p>}</>;
  if (!job) return <><PageTitle title="Transcript unavailable" subtitle={error || "Job not found"} /><Link href="/jobs">Back to jobs</Link></>;
  const isVideo = job.media_type.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(job.original_filename);

  return <>
    <PageTitle title={job.original_filename} subtitle={`${job.status} · ${segments.length} segments`} action={<Link href="/jobs">← Jobs</Link>} />
    {error && <p role="alert" className="mb-4 text-red-600">{error}</p>}
    {job.status !== "COMPLETED" && <p className="card mb-5">Transcript is {job.status.toLowerCase().replaceAll("_", " ")}. {job.error_message}</p>}
    {isVideo
      ? <video ref={player} controls preload="metadata" className="mb-6 max-h-[55vh] w-full rounded-xl bg-black" src={api.mediaUrl(id)} onTimeUpdate={event => setCurrentTime(event.currentTarget.currentTime)} />
      : <audio ref={player} controls preload="metadata" className="mb-6 w-full" src={api.mediaUrl(id)} onTimeUpdate={event => setCurrentTime(event.currentTarget.currentTime)} />}
    <section className="card mb-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">Transcript</h2><label className="text-sm">Search <input className="ml-2 rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find in transcript" /></label></div>
      {dirty && <p className="mb-3 text-sm text-amber-700 dark:text-amber-300">Unsaved edits are kept in this browser. Save them before exporting.</p>}
      {!segments.length && job.status === "COMPLETED" && <p>No speech segments were found.</p>}
      <div className="max-h-[65vh] space-y-3 overflow-y-auto">{filtered.map(segment => <article key={segment.id} className={`rounded-xl border p-3 ${activeId === segment.id ? "border-brand bg-indigo-50 dark:bg-indigo-950/40" : "border-slate-200 dark:border-slate-700"}`}>
        <div className="flex items-start gap-3"><button type="button" className="shrink-0 rounded bg-slate-100 px-2 py-1 font-mono text-sm text-indigo-700 dark:bg-slate-800 dark:text-indigo-300" aria-label={`Seek to ${displayTime(segment.start_ms)}`} onClick={() => seek(segment)}>{displayTime(segment.start_ms)}</button>
          <div className="min-w-0 flex-1"><textarea aria-label={`Transcript segment ${segment.ordinal + 1}`} className="min-h-16 w-full resize-y rounded-lg border border-slate-200 bg-transparent p-2 dark:border-slate-700" value={drafts[segment.id] ?? segment.text} disabled={job.status !== "COMPLETED"} onChange={event => changeDraft(segment, event.target.value)} />
            <div className="mt-2 flex items-center justify-between text-xs text-slate-500"><span>{displayTime(segment.start_ms)} – {displayTime(segment.end_ms)}</span>{drafts[segment.id] !== undefined && drafts[segment.id] !== segment.text && <button disabled={busy !== null} onClick={() => void save(segment)}>{busy === segment.id ? "Saving…" : "Save edit"}</button>}</div>
          </div></div>
      </article>)}</div>
      {query && !filtered.length && <p className="mt-4 text-slate-500">No matching transcript text.</p>}
    </section>
    <section className="card"><h2 className="mb-3 text-lg font-semibold">Export edited transcript</h2><div className="flex flex-wrap gap-2">{formats.map(format => <button key={format} className="rounded-lg border border-slate-300 px-4 py-2 uppercase hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800" disabled={job.status !== "COMPLETED" || dirty || busy !== null} onClick={() => void download(format)}>{busy === "export" ? "Preparing…" : format}</button>)}</div></section>
  </>;
}
