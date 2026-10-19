import assert from "node:assert/strict";
import test from "node:test";

import {
  RAG_ALLOWED_EXTENSIONS,
  deriveRagTitleFromFilename,
  isAllowedRagFile,
  prepareRagDocumentFromFile,
  readRagTextFile,
} from "./ragFileImport.ts";

test("isAllowedRagFile accepts only text-based extensions, case-insensitive", () => {
  for (const name of ["a.txt", "b.md", "c.csv", "d.json", "e.log", "SOP.MD", "x.JSON"]) {
    assert.ok(isAllowedRagFile(name), `expected ${name} to be allowed`);
  }
  for (const name of ["a.docx", "b.pdf", "c.xlsx", "noext", "readme.doc"]) {
    assert.ok(!isAllowedRagFile(name), `expected ${name} to be rejected`);
  }
  assert.deepEqual([...RAG_ALLOWED_EXTENSIONS], [".txt", ".md", ".csv", ".json", ".log"]);
});

test("deriveRagTitleFromFilename strips the extension and survives paths", () => {
  assert.equal(deriveRagTitleFromFilename("SOP.md"), "SOP");
  assert.equal(deriveRagTitleFromFilename("check-in-rules.TXT"), "check-in-rules");
  assert.equal(deriveRagTitleFromFilename("a/b/notes.txt"), "notes");
  assert.equal(deriveRagTitleFromFilename("noext"), "noext");
  assert.equal(deriveRagTitleFromFilename(""), "导入文档");
});

test("readRagTextFile reads file text and rejects unsupported or empty files", async () => {
  const text = await readRagTextFile(new File(["酒店 SOP 正文"], "sop.md", { type: "text/markdown" }));
  assert.equal(text, "酒店 SOP 正文");

  await assert.rejects(
    readRagTextFile(new File(["x"], "secret.docx")),
    /不支持的文件类型/,
  );
  await assert.rejects(
    readRagTextFile(new File(["   \n  "], "empty.txt")),
    /文件内容为空/,
  );
});

test("prepareRagDocumentFromFile keeps an explicit title, otherwise derives it", async () => {
  const kept = await prepareRagDocumentFromFile(
    new File(["body"], "sop.md"),
    "已有标题",
  );
  assert.equal(kept.title, "已有标题");
  assert.equal(kept.content, "body");

  const derived = await prepareRagDocumentFromFile(new File(["body"], "policy.txt"));
  assert.equal(derived.title, "policy");
  assert.equal(derived.content, "body");
});
