import {
  File,
  FileCode,
  FileCog,
  FileImage,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  Lock,
  type LucideIcon,
} from "lucide-react";

/**
 * Which icon a file or folder gets in the tree.
 *
 * By extension and, for the few files whose NAME is the type (`Dockerfile`, a lockfile), by name.
 * Eight shapes and no more: a tree where every language has its own glyph is a tree nobody can scan,
 * and the point is to tell code from data from prose from pictures at a glance. Icons already in
 * lucide, no new dependency.
 */

const CODE = new Set([
  "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "pyi", "rs", "go", "java", "kt", "c", "h", "cc",
  "cpp", "hpp", "cs", "rb", "php", "swift", "sh", "bash", "zsh", "ps1", "bat", "css", "scss",
  "html", "htm", "vue", "svelte", "sql", "lua", "r", "dart",
]);
const IMAGE = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif"]);
const PROSE = new Set(["md", "mdx", "markdown", "txt", "rst"]);
const CONFIG_EXT = new Set(["toml", "yaml", "yml", "ini", "cfg", "conf", "env", "properties"]);
const CONFIG_NAMES = new Set([
  "dockerfile", "makefile", ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
]);
const LOCK_NAMES = new Set(["package-lock.json", "pnpm-lock.yaml", "npm-shrinkwrap.json"]);

export function fileIconFor(name: string): LucideIcon {
  const lower = name.toLowerCase();
  // A lockfile before its extension: `package-lock.json` is a lock, not data.
  if (lower.endsWith(".lock") || LOCK_NAMES.has(lower)) return Lock;
  if (CONFIG_NAMES.has(lower) || lower.startsWith(".env")) return FileCog;
  const dot = lower.lastIndexOf(".");
  const ext = dot > 0 ? lower.slice(dot + 1) : "";
  if (ext === "json" || ext === "jsonc" || ext === "json5") return FileJson;
  if (CONFIG_EXT.has(ext)) return FileCog;
  if (PROSE.has(ext)) return FileText;
  if (IMAGE.has(ext)) return FileImage;
  if (CODE.has(ext)) return FileCode;
  return File;
}

export function folderIcon(open: boolean): LucideIcon {
  return open ? FolderOpen : Folder;
}
