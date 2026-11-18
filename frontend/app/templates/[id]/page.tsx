"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageTitle } from "../../../components/shell";
import { api } from "../../../lib/api";
import type { TemplateBlock, TemplateRun, TemplateVersion } from "../../../lib/types";

function Blocks({ blocks, decisions, onChange }: { blocks: TemplateBlock[]; decisions: Record<string, boolean>; onChange: (id: string, value: boolean) => void }) {
  return <div className="space-y-3">{blocks.map(block => block.type === "table"
    ? <div key={block.id} className="overflow-x-auto"><table className="w-full border-collapse text-sm"><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} className="border border-slate-300 p-2 align-top dark:border-slate-700"><Blocks blocks={cell} decisions={decisions} onChange={onChange} /></td>)}</tr>)}</tbody></table></div>
    : <div key={block.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"><div className="mb-1 flex flex-wrap gap-2 text-xs text-slate-500">{block.heading && <span>{block.heading}</span>}{block.part && <span>Speaking Part {block.part}</span>}{block.question && <span>Question candidate</span>}{block.style && <span>Style: {block.style}</span>}</div>
      <p className="whitespace-pre-wrap">{block.runs.length ? block.runs.map((run: TemplateRun) => <span key={run.id} title={`${run.color_source ?? "inherited"} color: ${run.color ?? "unspecified"}`} style={{ color: run.color ? `#${run.color}` : undefined, backgroundColor: run.highlight ? "#fef08a" : undefined, fontWeight: run.bold ? "bold" : undefined, fontStyle: run.italic ? "italic" : undefined }}>{run.text}</span>) : block.text}</p>
      {block.runs.filter(run => run.protected_candidate).map(run => <label key={run.id} className="mt-2 flex items-start gap-2 text-xs"><input type="checkbox" checked={decisions[run.id] ?? false} onChange={event => onChange(run.id, event.target.checked)} /><span>Protect “{run.text}” {run.red ? "· red text" : "· structure/question"}{run.id in decisions ? "" : " · needs confirmation"}</span></label>)}
    </div>)}</div>;
}

export default function TemplatePreview() {
  const { id } = useParams<{ id: string }>();
  const [version, setVersion] = useState<TemplateVersion | null>(null);
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  useEffect(() => { let active = true; api.getTemplateVersion(id).then(result => { if (active) { setVersion(result); setDecisions(result.protection_decisions); } }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [id]);
  useEffect(() => { if (!dirty) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [dirty]);
  function change(runId: string, protectedText: boolean) { setDecisions(current => ({ ...current, [runId]: protectedText })); setDirty(true); setSaved(""); }
  async function save() {
    if (!version) return;
    setBusy(true); setError("");
    try { const result = await api.confirmTemplateProtection(version.id, Object.entries(decisions).map(([run_id, protectedText]) => ({ run_id, protected: protectedText }))); setVersion(result); setDirty(false); setSaved("Protection choices saved. Original DOCX unchanged."); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save choices"); }
    finally { setBusy(false); }
  }
  if (loading) return <><PageTitle title="Template preview" subtitle="Inspecting document…" /><p role="status">Loading parsed structure…</p></>;
  if (!version) return <><PageTitle title="Template unavailable" subtitle={error || "Version not found"} /><Link href="/templates">Back to templates</Link></>;
  return <><PageTitle title={version.original_filename} subtitle={`Version ${version.version} · original SHA-256 ${version.sha256.slice(0, 12)}…`} action={<Link href="/templates">← Templates</Link>} />
    <div className="mb-5 flex flex-wrap items-center gap-3"><a className="rounded border border-slate-300 px-3 py-2 text-sm dark:border-slate-700" href={api.templateFileUrl(version.id)}>Download original DOCX</a><button className="rounded bg-brand px-3 py-2 text-sm text-white disabled:opacity-50" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "Saving…" : "Save protection choices"}</button></div>
    <p className="mb-4 text-sm text-slate-500">Preview is inspection metadata; use the original DOCX for exact layout, images and relationships. Red text is a protection candidate, not a speaking error.</p>
    {error && <p role="alert" className="mb-4 text-red-600">{error}</p>}{saved && <p role="status" className="mb-4 text-emerald-700">{saved}</p>}
    {version.parsed.warnings.length > 0 && <section className="card mb-5"><h2 className="font-semibold">Review in Word</h2><ul className="mt-2 list-inside list-disc text-sm text-amber-700 dark:text-amber-300">{version.parsed.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>}
    <section className="card mb-5"><h2 className="mb-3 text-lg font-semibold">Document body</h2><Blocks blocks={version.parsed.blocks} decisions={decisions} onChange={change} /></section>
    {version.parsed.sections.map(section => <section key={section.index} className="card mb-5"><h2 className="mb-3 font-semibold">Section {section.index + 1} · {section.start_type}</h2><h3 className="mb-2 text-sm">Header</h3><Blocks blocks={section.header} decisions={decisions} onChange={change} /><h3 className="my-2 text-sm">Footer</h3><Blocks blocks={section.footer} decisions={decisions} onChange={change} /></section>)}
    <p className="text-xs text-slate-500">{version.parsed.image_count} embedded image relationship(s) retained in the original DOCX.</p>
  </>;
}
