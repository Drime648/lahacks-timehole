import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { BlockCategory } from "./types.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const blacklistDirectory = path.join(__dirname, "blacklists");

function parseBlacklistFile(contents: string): string[] {
  return contents
    .split(/\r?\n/)
    .map((line) => line.trim().toLowerCase())
    .filter((line) => line.length > 0 && !line.startsWith("#"));
}

export function loadCategoryBlacklist(category: BlockCategory): string[] {
  const filePath = path.join(blacklistDirectory, `${category}.txt`);
  if (!fs.existsSync(filePath)) {
    return [];
  }

  return parseBlacklistFile(fs.readFileSync(filePath, "utf8"));
}

export function buildEffectiveBlacklist(
  blockedCategories: BlockCategory[],
  manualBlacklist: string[]
): { manualBlacklist: string[]; categoryBlacklist: string[]; effectiveBlacklist: string[] } {
  const normalizedManual = manualBlacklist
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);

  const categoryBlacklist = blockedCategories.flatMap((category) =>
    loadCategoryBlacklist(category)
  );

  const effectiveBlacklist = [...new Set([...normalizedManual, ...categoryBlacklist])];

  return {
    manualBlacklist: [...new Set(normalizedManual)],
    categoryBlacklist: [...new Set(categoryBlacklist)],
    effectiveBlacklist
  };
}
