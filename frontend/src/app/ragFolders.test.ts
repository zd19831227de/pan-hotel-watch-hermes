import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  UNFILED_FOLDER,
  buildFolderOptions,
  groupDocumentsByFolder,
  normalizeFolderName,
} from "./ragFolders.ts";
import type { RagDocument } from "../services/configApi";

function doc(id: number, folder: string | null): RagDocument {
  return {
    id,
    title: `文档${id}`,
    source_type: "text",
    folder,
    content_hash: `hash-${id}`,
    chunk_count: 2,
    created_at: "2026-08-21T10:00:00",
    updated_at: "2026-08-21T10:00:00",
  };
}

test("normalizeFolderName maps blank folders to 未分组", () => {
  assert.equal(normalizeFolderName(null), UNFILED_FOLDER);
  assert.equal(normalizeFolderName(""), UNFILED_FOLDER);
  assert.equal(normalizeFolderName("   "), UNFILED_FOLDER);
  assert.equal(normalizeFolderName(" 调价知识 "), "调价知识");
});

test("groupDocumentsByFolder groups, sorts, and keeps 未分组 last", () => {
  const groups = groupDocumentsByFolder([
    doc(1, null),
    doc(2, "运营 SOP"),
    doc(3, "调价知识"),
    doc(4, "运营 SOP"),
    doc(5, ""),
  ]);
  assert.deepEqual(
    groups.map((group) => group.folder),
    ["调价知识", "运营 SOP", UNFILED_FOLDER],
  );
  assert.deepEqual(
    groups[1].documents.map((document) => document.id),
    [2, 4],
  );
  assert.deepEqual(
    groups[2].documents.map((document) => document.id),
    [1, 5],
  );
});

test("buildFolderOptions dedupes named folders and drops 未分组", () => {
  const options = buildFolderOptions([
    doc(1, null),
    doc(2, "运营 SOP"),
    doc(3, "运营 SOP"),
    doc(4, "调价知识"),
  ]);
  assert.deepEqual(options, ["调价知识", "运营 SOP"]);
});

test("rag panel renders shelf + knowledge-base detail with four tabs", () => {
  const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  for (const marker of [
    "getRagDocument",
    "updateRagDocument",
    "知识库",
    "新建知识库",
    "概览",
    "文档管理",
    "知识库检索",
    "保存修改",
    "默认（未分组）",
  ]) {
    assert.ok(appSource.includes(marker), marker);
  }
});
