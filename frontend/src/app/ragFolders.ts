import type { RagDocument } from "../services/configApi";

export const UNFILED_FOLDER = "未分组";
export const NEW_FOLDER_CHOICE = "__new__";

export type RagFolderGroup = {
  folder: string;
  documents: RagDocument[];
};

export function normalizeFolderName(folder: string | null | undefined): string {
  const trimmed = (folder ?? "").trim();
  return trimmed || UNFILED_FOLDER;
}

export function groupDocumentsByFolder(documents: RagDocument[]): RagFolderGroup[] {
  const buckets = new Map<string, RagDocument[]>();
  for (const document of documents) {
    const folder = normalizeFolderName(document.folder);
    const bucket = buckets.get(folder);
    if (bucket) {
      bucket.push(document);
    } else {
      buckets.set(folder, [document]);
    }
  }
  const groups: RagFolderGroup[] = [...buckets.entries()]
    .filter(([folder]) => folder !== UNFILED_FOLDER)
    .sort(([a], [b]) => a.localeCompare(b, "zh-Hans-CN"))
    .map(([folder, folderDocuments]) => ({ folder, documents: folderDocuments }));
  const unfiled = buckets.get(UNFILED_FOLDER);
  if (unfiled) {
    groups.push({ folder: UNFILED_FOLDER, documents: unfiled });
  }
  return groups;
}

export function buildFolderOptions(documents: RagDocument[]): string[] {
  const names = new Set<string>();
  for (const document of documents) {
    const folder = normalizeFolderName(document.folder);
    if (folder !== UNFILED_FOLDER) {
      names.add(folder);
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
}

/** Resolve the folder <select> state (未分组/现有文件夹/新建) into a payload value. */
export function resolveFolderChoice(choice: string, newFolder: string): string {
  if (choice === NEW_FOLDER_CHOICE) {
    return newFolder.trim();
  }
  return choice;
}
