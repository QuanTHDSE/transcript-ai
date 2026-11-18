"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { PageTitle } from "../../components/shell";
import { api } from "../../lib/api";
import type { TemplateSummary } from "../../lib/types";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [existing, setExisting] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  async function refresh() { setTemplates(await api.listTemplates()); }
  useEffect(() => { let active = true; api.listTemplates().then(items => { if (active) setTemplates(items); }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, []);
  async function upload() {
    if (!file) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const version = await api.uploadTemplate(file, existing ? { template_id: existing } : { name: name.trim() || file.name.replace(/\.docx$/i, "") });
      setSuccess(`Uploaded version ${version.version}. Review protected-text candidates in the preview.`);
      setFile(null); await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Upload failed"); }
    finally { setBusy(false); }
  }
  return <><PageTitle title="Speaking templates" subtitle="Keep original DOCX files and inspect reusable versions." />
    <section className="card mb-6 space-y-4"><h2 className="text-lg font-semibold">Upload DOCX</h2>
      <label className="block text-sm">Document<input className="mt-1 block w-full" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <label className="block text-sm">Add to template<select className="mt-1 block w-full rounded border border-slate-300 bg-transparent p-2 dark:border-slate-700" value={existing} onChange={e => setExisting(e.target.value)}><option value="">New template</option>{templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
      {!existing && <label className="block text-sm">Template name<input className="mt-1 block w-full rounded border border-slate-300 bg-transparent p-2 dark:border-slate-700" value={name} onChange={e => setName(e.target.value)} placeholder="Defaults to filename" /></label>}
      <button className="rounded bg-brand px-4 py-2 text-white disabled:opacity-50" disabled={!file || busy || !file.name.toLowerCase().endsWith(".docx")} onClick={() => void upload()}>{busy ? "Inspecting…" : "Upload and inspect"}</button>
      {file && !file.name.toLowerCase().endsWith(".docx") && <p role="alert" className="text-red-600">Choose a DOCX file.</p>}
      {success && <p role="status" className="text-emerald-700">{success}</p>}{error && <p role="alert" className="text-red-600">{error}</p>}
    </section>
    <section><h2 className="mb-3 text-lg font-semibold">Template library</h2>{loading ? <p>Loading templates…</p> : !templates.length ? <p className="card text-slate-500">No templates uploaded yet.</p> : <div className="space-y-3">{templates.map(template => <article className="card" key={template.id}><h3 className="font-semibold">{template.name}</h3><div className="mt-3 space-y-2">{template.versions.map(version => <div key={version.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-200 p-3 text-sm dark:border-slate-700"><span>Version {version.version} · {version.original_filename}{version.warnings.length > 0 && ` · ${version.warnings.length} inspection warning(s)`}</span><Link className="text-indigo-700 dark:text-indigo-300" href={`/templates/${version.id}`}>Preview and confirm →</Link></div>)}</div></article>)}</div>}</section></>;
}
