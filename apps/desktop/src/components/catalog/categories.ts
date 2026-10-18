/**
 * The 26 category ids the three public catalogues share, and the table that folds every raw label
 * the app meets today into one of them (study 04, §4).
 *
 * The ids are the ones in `categories.yaml` of chimera-plugins, chimera-skills and
 * chimera-connectors, mirrored by `chimera/catalogs/categories.py`; the order is the grid's order.
 * The public catalogues already speak these ids. Everything else does not: a skill in the bundled
 * catalogue has a `topic` ("productivity", "github"), a card of the library a `topic` from its own
 * vocabulary ("software-dev"), a curated MCP entry nothing but its id ("supabase"), and a plugin
 * whatever its marketplace wrote. This file is where those meet the grid.
 *
 * Anything that does not match lands in `other`, which has no tile: the item stays in its section
 * and in search, it just is not filed under a picture the app would be guessing at. The test beside
 * this file reads the bundled catalogues from the repository and fails if any value they carry
 * today falls through to `other` — so this table cannot go stale in silence.
 */
import type { CatalogItem } from "@/components/catalog/model";

export const CATEGORY_IDS = [
  "engineering",
  "code",
  "pull-requests",
  "testing-quality",
  "files-documents",
  "reference-docs",
  "design-media",
  "sites-frontend",
  "pipelines-jobs",
  "servers-cloud",
  "databases",
  "apis-integrations",
  "logs-errors",
  "security",
  "tickets-tasks",
  "notes-memory",
  "email-messaging",
  "contacts-leads",
  "sales",
  "marketing",
  "finance-payments",
  "product",
  "research",
  "operations",
  "education",
  "ai-models-agents",
] as const;

export type CategoryId = (typeof CATEGORY_IDS)[number];

export const OTHER_CATEGORY = "other";
export type CategoryOrOther = CategoryId | typeof OTHER_CATEGORY;

/** Literal keys, so the dictionary's reachability check can see each one. */
export const CATEGORY_LABEL_KEY: Record<CategoryId, string> = {
  engineering: "catalogUi.category.engineering",
  code: "catalogUi.category.code",
  "pull-requests": "catalogUi.category.pull-requests",
  "testing-quality": "catalogUi.category.testing-quality",
  "files-documents": "catalogUi.category.files-documents",
  "reference-docs": "catalogUi.category.reference-docs",
  "design-media": "catalogUi.category.design-media",
  "sites-frontend": "catalogUi.category.sites-frontend",
  "pipelines-jobs": "catalogUi.category.pipelines-jobs",
  "servers-cloud": "catalogUi.category.servers-cloud",
  databases: "catalogUi.category.databases",
  "apis-integrations": "catalogUi.category.apis-integrations",
  "logs-errors": "catalogUi.category.logs-errors",
  security: "catalogUi.category.security",
  "tickets-tasks": "catalogUi.category.tickets-tasks",
  "notes-memory": "catalogUi.category.notes-memory",
  "email-messaging": "catalogUi.category.email-messaging",
  "contacts-leads": "catalogUi.category.contacts-leads",
  sales: "catalogUi.category.sales",
  marketing: "catalogUi.category.marketing",
  "finance-payments": "catalogUi.category.finance-payments",
  product: "catalogUi.category.product",
  research: "catalogUi.category.research",
  operations: "catalogUi.category.operations",
  education: "catalogUi.category.education",
  "ai-models-agents": "catalogUi.category.ai-models-agents",
};

const KNOWN: ReadonlySet<string> = new Set(CATEGORY_IDS);

export function isCategoryId(value: string): value is CategoryId {
  return KNOWN.has(value);
}

/**
 * Raw label → id, keyed by the normalised label (lower case, no accents, words joined by "-").
 *
 * Grouped by where each label comes from, because that is what a reviewer needs to check: the
 * bundled skill catalogue (`chimera/skills/catalog.json`), the library's topic vocabulary
 * (`chimera/skills/skill_md.py`), the two kinds the owner adds by hand, and then the words plugin
 * marketplaces and older catalogues use, in English and Portuguese. A judgement call is a judgement
 * call: `productivity` in the bundled catalogue is mostly PDF, Word, Excel and Drive skills, so it
 * files under documents; `apple` is Notes, Reminders and iMessage, so notes.
 */
const SYNONYMS: Readonly<Record<string, CategoryId>> = {
  // chimera/skills/catalog.json — `topic`
  productivity: "files-documents",
  creative: "design-media",
  "software-development": "code",
  github: "pull-requests",
  research: "research",
  "autonomous-ai-agents": "ai-models-agents",
  mlops: "ai-models-agents",
  apple: "notes-memory",
  media: "design-media",
  email: "email-messaging",
  devops: "pipelines-jobs",
  "note-taking": "notes-memory",
  "smart-home": "operations",
  "social-media": "marketing",
  // chimera/skills/skill_md.py — TOPICS, the library's vocabulary
  "software-dev": "code",
  "ai-agents": "ai-models-agents",
  security: "security",
  "data-science": "research",
  networking: "servers-cloud",
  "vision-ai": "ai-models-agents",
  translation: "files-documents",
  // what the owner adds by hand (connectors' own category ids)
  mcp: "apis-integrations",
  openapi: "apis-integrations",
  // marketplace and older-catalogue words
  dev: "code",
  development: "code",
  coding: "code",
  programming: "code",
  engineering: "engineering",
  architecture: "engineering",
  refactoring: "code",
  git: "pull-requests",
  "code-review": "pull-requests",
  review: "pull-requests",
  "pull-request": "pull-requests",
  testing: "testing-quality",
  test: "testing-quality",
  tests: "testing-quality",
  quality: "testing-quality",
  "code-quality": "testing-quality",
  qa: "testing-quality",
  documents: "files-documents",
  document: "files-documents",
  docs: "files-documents",
  files: "files-documents",
  pdf: "files-documents",
  writing: "files-documents",
  documentation: "reference-docs",
  reference: "reference-docs",
  design: "design-media",
  ui: "design-media",
  "ui-ux": "design-media",
  video: "design-media",
  audio: "design-media",
  image: "design-media",
  images: "design-media",
  frontend: "sites-frontend",
  "front-end": "sites-frontend",
  web: "sites-frontend",
  browser: "sites-frontend",
  automation: "pipelines-jobs",
  "ci-cd": "pipelines-jobs",
  ci: "pipelines-jobs",
  workflow: "pipelines-jobs",
  workflows: "pipelines-jobs",
  cloud: "servers-cloud",
  deployment: "servers-cloud",
  deploy: "servers-cloud",
  infrastructure: "servers-cloud",
  infra: "servers-cloud",
  hosting: "servers-cloud",
  database: "databases",
  databases: "databases",
  data: "databases",
  sql: "databases",
  api: "apis-integrations",
  apis: "apis-integrations",
  integration: "apis-integrations",
  integrations: "apis-integrations",
  monitoring: "logs-errors",
  observability: "logs-errors",
  logging: "logs-errors",
  logs: "logs-errors",
  debugging: "logs-errors",
  tasks: "tickets-tasks",
  "project-management": "tickets-tasks",
  issues: "tickets-tasks",
  notes: "notes-memory",
  memory: "notes-memory",
  knowledge: "notes-memory",
  communication: "email-messaging",
  messaging: "email-messaging",
  chat: "email-messaging",
  crm: "contacts-leads",
  contacts: "contacts-leads",
  leads: "contacts-leads",
  sales: "sales",
  marketing: "marketing",
  seo: "marketing",
  finance: "finance-payments",
  payments: "finance-payments",
  billing: "finance-payments",
  product: "product",
  analytics: "research",
  ai: "ai-models-agents",
  "machine-learning": "ai-models-agents",
  ml: "ai-models-agents",
  agents: "ai-models-agents",
  operations: "operations",
  ops: "operations",
  education: "education",
  learning: "education",
  // Portuguese
  desenvolvimento: "code",
  codigo: "code",
  testes: "testing-quality",
  documentos: "files-documents",
  arquivos: "files-documents",
  pesquisa: "research",
  seguranca: "security",
  financas: "finance-payments",
  vendas: "sales",
  educacao: "education",
  produtividade: "files-documents",
  "banco-de-dados": "databases",
  "bancos-de-dados": "databases",
  nuvem: "servers-cloud",
  operacoes: "operations",
};

/**
 * The curated MCP catalogue (`chimera/integrations/mcp_catalog.py`) carries no category, only ids.
 * Each id is filed here; the database entries share the `db-` prefix and all file under databases.
 */
const MCP_CATALOG: Readonly<Record<string, CategoryId>> = {
  github: "pull-requests",
  "github-binary": "pull-requests",
  firebase: "servers-cloud",
  supabase: "databases",
  stripe: "finance-payments",
  notion: "notes-memory",
  sentry: "logs-errors",
  hostinger: "servers-cloud",
  // Reverse engineering of shipped binaries: analysis for security work.
  rea: "security",
};

function key(raw: string): string {
  return raw
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim()
    .replace(/[\s_/&]+/g, "-")
    .replace(/-+/g, "-");
}

/** A raw `category` / `topic` as one of the 26, or `other` when nothing matches. */
export function canonicalCategory(raw: string | null | undefined): CategoryOrOther {
  if (!raw) return OTHER_CATEGORY;
  const k = key(raw);
  if (isCategoryId(k)) return k;
  return SYNONYMS[k] ?? OTHER_CATEGORY;
}

/** A curated MCP catalogue entry, by its id. */
export function mcpCatalogCategory(id: string): CategoryOrOther {
  if (id.startsWith("db-")) return "databases";
  return MCP_CATALOG[id] ?? OTHER_CATEGORY;
}

/** The id an item files under: the adapter's own answer when it has one, else its raw label folded. */
export function categoryOfItem(item: Pick<CatalogItem, "category" | "canonicalCategory">): CategoryOrOther {
  const own = item.canonicalCategory;
  if (own && (own === OTHER_CATEGORY || isCategoryId(own))) return own;
  return canonicalCategory(item.category);
}
