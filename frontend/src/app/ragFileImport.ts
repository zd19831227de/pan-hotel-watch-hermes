// RAG document file import (text-based files only).
// Reads the file as text in the browser and hands it to the existing
// POST /api/v1/rag/documents JSON endpoint — no backend file-parsing surface.

export const RAG_ALLOWED_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".log"] as const;

export function isAllowedRagFile(name: string): boolean {
  const lower = (name ?? "").toLowerCase();
  return RAG_ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export function deriveRagTitleFromFilename(name: string): string {
  const base = ((name ?? "").replace(/[\\/]/g, "/").split("/").pop() ?? "").trim();
  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  return stem.trim() || "导入文档";
}

export async function readRagTextFile(file: File): Promise<string> {
  if (!isAllowedRagFile(file.name)) {
    throw new Error(`不支持的文件类型。仅支持 ${RAG_ALLOWED_EXTENSIONS.join("、")}。`);
  }
  const text = await file.text();
  if (!text.trim()) {
    throw new Error("文件内容为空。");
  }
  return text;
}

export async function prepareRagDocumentFromFile(
  file: File,
  currentTitle?: string,
): Promise<{ title: string; content: string }> {
  const content = await readRagTextFile(file);
  const title = (currentTitle ?? "").trim() || deriveRagTitleFromFilename(file.name);
  return { title, content };
}
