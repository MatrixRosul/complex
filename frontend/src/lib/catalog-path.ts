import { api } from "./api";
import type { ApiLang, CategoryOut } from "./api/types";

/**
 * /uk/catalog/velyka-pobutova-tekhnika/kholodylnyky → external_id категорії.
 *
 * Раніше це робив резолвер із моків (жорстко зашите дерево). Тепер — по РЕАЛЬНОМУ дереву
 * з /api/v1/categories/tree (кешується ISR-тегом `categories:{lang}`, TTL 1 год,
 * тому це не запит на кожен рендер).
 *
 * ⚠️ SLUG'И ЛОКАЛІЗОВАНІ. uk: `velyka-pobutova-tekhnika/kholodylnyky`
 *                        ru: `krupnaia-bytovaia-tekhnyka/kholodylnyky`
 * Тому дерево тягнемо в МОВІ СТОРІНКИ. І тому ж тут є фолбек на іншу мову: перемикач
 * мови міняє лише сегмент локалі (/uk/catalog/… → /ru/catalog/… з uk-слагами), і без
 * фолбеку користувач після перемикання мови отримував би 404 на власній категорії.
 *
 * Третій рівень стійкості — пошук по ОСТАННЬОМУ сегменту в усьому дереві: /catalog/kholodylnyky
 * (без кореня) — валідне посилання, яке цілком може прилетіти ззовні.
 */

const OTHER: Record<ApiLang, ApiLang> = { uk: "ru", ru: "uk" };

function walkPath(tree: CategoryOut[], slugs: string[]): CategoryOut | null {
  let level = tree;
  let found: CategoryOut | null = null;

  for (const slug of slugs) {
    const match = level.find((c) => c.slug === slug);
    if (!match) return null;
    found = match;
    level = match.children ?? [];
  }

  return found;
}

function findBySlug(tree: CategoryOut[], slug: string): CategoryOut | null {
  for (const node of tree) {
    if (node.slug === slug) return node;
    const inChildren = findBySlug(node.children ?? [], slug);
    if (inChildren) return inChildren;
  }
  return null;
}

function resolveIn(tree: CategoryOut[], slugs: string[]): CategoryOut | null {
  if (slugs.length === 0) return null;
  return walkPath(tree, slugs) ?? findBySlug(tree, slugs[slugs.length - 1]);
}

/** external_id категорії або null (сторінка рендерить notFound()). */
export async function resolveCatalogSlug(
  slugs: string[],
  lang: ApiLang,
): Promise<string | undefined> {
  const tree = await api.getCategoryTree(lang);

  const direct = resolveIn(tree, slugs);
  if (direct) return direct.external_id;

  // Слаги іншої мови (після перемикача) — external_id мовонезалежний.
  const otherTree = await api.getCategoryTree(OTHER[lang]);
  return resolveIn(otherTree, slugs)?.external_id;
}

/** Зворотний бік: external_id → ["velyka-pobutova-tekhnika", "kholodylnyky"]. */
function findSlugPath(
  nodes: CategoryOut[],
  externalId: string,
  trail: string[] = [],
): string[] | null {
  for (const node of nodes) {
    const path = [...trail, node.slug];
    if (node.external_id === externalId) return path;

    const inChildren = findSlugPath(node.children ?? [], externalId, path);
    if (inChildren) return inChildren;
  }
  return null;
}

/**
 * external_id → канонічний шлях каталогу («/catalog/…»), або null.
 *
 * ⚠️ Потрібно для СТАРОГО формату посилань `/c/{external_id}`, яким заведені банери в CMS
 * (`link_url = "/uk/c/5609790"`). Такого роуту в застосунку немає й не буде — але й ламати
 * посилання, які контент-менеджер уже вбив у базу, не можна. Тому /c/{id} лишається живим
 * входом, який РЕДІРЕКТИТЬ на канонічну адресу категорії в потрібній мові.
 */
export async function resolveCategoryPathById(
  externalId: string,
  lang: ApiLang,
): Promise<string[] | null> {
  const tree = await api.getCategoryTree(lang);
  return findSlugPath(tree, externalId);
}
