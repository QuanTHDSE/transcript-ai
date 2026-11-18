import { test } from "node:test";
import assert from "node:assert/strict";
import { settleSavedDraft } from "../lib/drafts.ts";
test("save completion preserves edits typed during the request", () => {
  assert.deepEqual(settleSavedDraft({ a: "newer edit", b: "other" }, "a", "submitted edit"), { a: "newer edit", b: "other" });
  assert.deepEqual(settleSavedDraft({ a: "submitted edit", b: "other" }, "a", "submitted edit"), { b: "other" });
});
