import type { Metadata } from "next";
import Link from "next/link";

import { CategoryIcon } from "@/components/layout/category-icon";
import {
  CatalogResults,
  parseCatalogParams,
  type SearchParams,
} from "@/components/catalog/catalog-results";
import { api } from "@/lib/api";
import { categoryIcon } from "@/lib/category-icons";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";

/**
 * Корінь каталогу — увесь асортимент із фасетами.
 *
 * ⚠️ Чому сторінка взагалі з'явилась. «Перейти до каталогу» з порожнього кошика,
 * бажань, порівняння й checkout вело на ЗАШИТИЙ слаг категорії з моків
 * (`/catalog/velyka-pobutova-tehnika`). У реальній БД слаг інший
 * (`velyka-pobutova-tekhnika`), та ще й локалізований — усі чотири кнопки давали б 404.
 * `/catalog` мовонезалежний і не залежить від того, які категорії заведені.
 */
export const revalidate = 600;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("nav.catalog"),
    alternates: { canonical: localePath(locale, "/catalog") },
  };
}

export default async function CatalogRootPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: Locale }>;
  searchParams: Promise<SearchParams>;
}) {
  const [{ locale }, sp] = await Promise.all([params, searchParams]);

  const t = getT(locale);
  const lang = localeToApiLang[locale];

  const [data, categories] = await Promise.all([
    api.getCatalog(parseCatalogParams(sp), lang),
    api.getCategoryTree(lang),
  ]);

  return (
    <div className="container-complex flex flex-col gap-5 py-6">
      <h1 className="text-h1 text-foreground">{t("nav.catalog")}</h1>

      <nav aria-label={t("home.categories")} className="flex flex-wrap gap-2">
        {categories.map((cat) => (
          <Link
            key={cat.id}
            href={localePath(locale, `/catalog/${cat.slug}`)}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-sm text-foreground transition-colors hover:border-input"
          >
            <CategoryIcon name={categoryIcon(cat.slug)} className="size-4 text-muted-foreground" />
            {cat.name}
            <span className="text-xs text-muted-foreground tnum">{cat.products_count}</span>
          </Link>
        ))}
      </nav>

      <CatalogResults data={data} locale={locale} />
    </div>
  );
}
