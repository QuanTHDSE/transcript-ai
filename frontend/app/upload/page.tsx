"use client";
import { useState } from "react";
import Link from "next/link";
import { api } from "../../lib/api";
import type { UploadResult } from "../../lib/types";
import { PageTitle } from "../../components/shell";

export default function UploadPage() {
  const [files,setFiles] = useState<File[]>([]);
  const [language,setLanguage] = useState("auto");
  const [busy,setBusy] = useState(false);
  const [results,setResults] = useState<UploadResult[]>([]);
  const [error,setError] = useState("");
  async function upload() {
    setBusy(true); setError(""); setResults([]);
    try { setResults(await api.upload(files, {language,engine:"faster-whisper"})); }
    catch(e) { setError(e instanceof Error ? e.message : "Upload failed"); }
    finally { setBusy(false); }
  }
  return <><PageTitle title="Upload media" subtitle="Each file becomes an independent transcription job." />
    <div className="card space-y-5"><label className="block">Audio or video files<input className="mt-2 block w-full" type="file" multiple disabled={busy} accept=".mp3,.wav,.m4a,.mp4,.mov,.webm" onChange={event=>setFiles(Array.from(event.target.files ?? []))}/></label>
    <label className="block">Spoken language <select className="rounded border p-2 dark:bg-slate-900" value={language} disabled={busy} onChange={event=>setLanguage(event.target.value)}><option value="auto">Detect automatically</option><option value="en">English</option><option value="vi">Vietnamese</option></select></label>
    <ul>{files.map((file,i)=><li className="break-all" key={i}>{file.name}</li>)}</ul>
    <button className="rounded bg-brand px-4 py-2 text-white disabled:opacity-50" disabled={busy || !files.length} onClick={()=>void upload()}>{busy ? "Uploading…" : "Upload and queue"}</button>
    {error && <p role="alert">{error}</p>}<ul aria-live="polite">{results.map((result,i)=><li key={i}>{result.filename}: {result.error ?? "Queued"}</li>)}</ul><Link className="block" href="/jobs">View live queue →</Link></div></>;
}
