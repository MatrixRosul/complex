import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import {
  CatalogResults,
  parseCatalogParams,
  type SearchParams,
} from "@/components/catalog/catalog-results";
import { api } from "@/lib/api";
import { resolveCatalogSlug } from "@/lib/catalog-path";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";

/** ISR: ADR-023 — TTL для fetch-кешу лістингу (сама сторінка динамічна через searchParams). */
export const revalidate = 600;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale; slug: string[] }>;
}): Promise<Metadata> {
  const { locale, slug } = await params;
  const lang = localeToApiLang[locale];

  const externalId = await resolveCatalogSlug(slug, lang);
  if (!externalId) return {};

  const data = await api.getCatalog({ category: externalId, page_size: 1 }, lang);

  return {
    title: data.category?.name,
    alternates: { canonical: localePath(locale, `/catalog/${slug.join("/")}`) },
  };
}

export default async function CatalogPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: Locale; slug: string[] }>;
  searchParams: Promise<SearchParams>;
}) {
  const [{ locale, slug }, sp] = await Promise.all([params, searchParams]);

  const t = getT(locale);
  const lang = localeToApiLang[locale];

  // Слаг → external_id по РЕАЛЬНОМУ дереву категорій (lib/catalog-path.ts), а не по мокам.
  // Слаги локалізовані (uk: velyka-pobutova-tekhnika / ru: krupnaia-bytovaia-tekhnyka),
  // тому резолвер має фолбек на іншу мову — інакше перемикач мови давав би 404.
  const externalId = await resolveCatalogSlug(slug, lang);
  if (!externalId) notFound();

  const data = await api.getCatalog(parseCatalogParams(sp, externalId), lang);
  if (!data.category) notFound();

  return (
    <div className="container-complex flex flex-col gap-5 py-6">
      <Breadcrumbs
        locale={locale}
        items={data.breadcrumbs.map((c, i) => ({
          label: c.name,
          href: localePath(
            locale,
            `/catalog/${data.breadcrumbs
              .slice(0, i + 1)
              .map((x) => x.slug)
              .join("/")}`,
          ),
        }))}
      />

      <h1 className="text-h1 text-foreground">{data.category.name}</h1>

      {/* Підкатегорії — швидкі посилання вглиб дерева. */}
      {data.subcategories.length > 0 && (
        <nav aria-label={t("catalog.subcategories")} className="flex flex-wrap gap-2">
          {data.subcategories.map((sub) => (
            <Link
              key={sub.id}
              href={localePath(locale, `/catalog/${slug.join("/")}/${sub.slug}`)}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-sm text-foreground transition-colors hover:border-input"
            >
              {sub.name}
              <span className="text-xs text-muted-foreground tnum">{sub.products_count}</span>
            </Link>
          ))}
        </nav>
      )}

      <CatalogResults data={data} locale={locale} />
    </div>
  );
}
