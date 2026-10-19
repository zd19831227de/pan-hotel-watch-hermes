import type { SkillDetail } from "../services/configApi";

function frontMatter(detail: SkillDetail): string {
  return [
    "---",
    `name: ${detail.name}`,
    `description: ${detail.description || ""}`,
    "---",
    "",
  ].join("\n");
}

function namedEntries(
  entries: Record<string, string>,
  heading: string,
): string {
  const blocks = Object.entries(entries).map(([name, body]) => {
    return `### ${name}\n\n${body}`;
  });
  return blocks.length ? ["", `## ${heading}`, "", ...blocks].join("\n") : "";
}

export function buildSkillExportMarkdown(detail: SkillDetail): string {
  return [
    frontMatter(detail),
    detail.content?.trim() || "",
    namedEntries(detail.references || {}, "References"),
    namedEntries(detail.templates || {}, "Templates"),
  ]
    .filter(Boolean)
    .join("\n");
}
