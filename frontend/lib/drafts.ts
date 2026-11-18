export function settleSavedDraft(drafts: Record<string, string>, id: string, submittedText: string): Record<string, string> {
  const next = { ...drafts };
  if (next[id] === submittedText) delete next[id];
  return next;
}
