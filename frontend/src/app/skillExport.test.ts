import assert from "node:assert/strict";
import test from "node:test";

import { buildSkillExportMarkdown } from "./skillExport.ts";
import type { SkillDetail } from "../services/configApi";

const detail: SkillDetail = {
  name: "refund-policy",
  description: "Hotel refund policy",
  source: "manual",
  content: "# Refund Policy\n\nFree cancellation.",
  references: {
    "faq.md": "How do I cancel?",
  },
  templates: {
    "reply.md": "Your cancellation was accepted.",
  },
  updated_at: "2026-08-13T00:00:00Z",
};

test("skill export includes body, references, and templates", () => {
  const markdown = buildSkillExportMarkdown(detail);

  assert.match(markdown, /^---\nname: refund-policy\n/);
  assert.match(markdown, /description: Hotel refund policy/);
  assert.match(markdown, /# Refund Policy/);
  assert.match(markdown, /## References/);
  assert.match(markdown, /faq\.md/);
  assert.match(markdown, /How do I cancel\?/);
  assert.match(markdown, /## Templates/);
  assert.match(markdown, /Your cancellation was accepted\./);
});

test("skill export does not silently drop content without references", () => {
  const markdown = buildSkillExportMarkdown({
    ...detail,
    references: {},
    templates: {},
  });

  assert.match(markdown, /# Refund Policy/);
  assert.doesNotMatch(markdown, /## References/);
  assert.doesNotMatch(markdown, /## Templates/);
});
