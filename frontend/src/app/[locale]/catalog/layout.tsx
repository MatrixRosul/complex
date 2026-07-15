import { notFound } from "next/navigation";
import { headers } from "next/headers";

import { resolveCatalogSlug } from "@/lib/catalog-path";
import { isLocale, localeToApiLang, type Locale } from "@/i18n/config";

/**
 * ⚠️ ЯК І В /p/[id]/[slug]/layout.tsx — цей layout існує заради HTTP-СТАТУСУ, не заради розмітки.
 *
 * `catalog/loading.tsx` створює Suspense-межу, Next стрімить відповідь і фіксує 200 ще до
 * того, як `catalog/[...slug]/page.tsx` дізнався, що такої категорії немає. notFound()
 * у сторінці вже не міг змінити статус → неіснуюча категорія віддавала 200 OK (soft-404).
 *
 * Layout рендериться ДО першого флаша, тому перевірка існування категорії живе тут.
 *
 * ⚠️ Сегменти шляху беремо із заголовка, бо layout не отримує `params` вкладеного роуту
 * ([...slug] належить дочірньому сегменту). Дерево категорій кешоване ISR-тегом
 * (`categories:{lang}`, TTL 1 год), тож це не запит на кожен рендер.
 */
export default async function CatalogLayout({ children }: { children: React.ReactNode }) {
  const pathname = (await headers()).get("x-pathname") ?? "";

  // "/uk/catalog/velyka-pobutova-tekhnika/kholodylnyky" → ["velyka-…", "kholodylnyky"]
  const segments = pathname.split("/").filter(Boolean);
  const localeSegment = segments[0] ?? "";

  // /catalog без сегментів — це весь каталог, він існує завжди.
  if (!isLocale(localeSegment) || segments[1] !== "catalog" || segments.length < 3) {
    return children;
  }

  const slugs = segments.slice(2);
  const externalId = await resolveCatalogSlug(slugs, localeToApiLang[localeSegment as Locale]);
  if (!externalId) notFound();

  return children;
}
