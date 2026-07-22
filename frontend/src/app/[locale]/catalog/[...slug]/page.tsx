import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { CategoryTiles } from "@/components/home/category-tiles";
import {
  CatalogResults,
  parseCatalogParams,
  type SearchParams,
} from "@/components/catalog/catalog-results";
import { api } from "@/lib/api";
import { resolveCatalogSlug } from "@/lib/catalog-path";
import type { CategoryOut } from "@/lib/api/types";
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

      {/* Підкатегорії — ПЛИТКИ З ФОТО, а не текстові чіпи.
          ⚠️ Раніше тут був рядок чіпів, і завантажена в адмінці плитка підгрупи нікуди
          не потрапляла: поле є, API його віддає, а сайт малював саму назву. Замовниця
          спитала прямо — «для підгруп фото-плитки можна зробити в адмінці?». Можна було
          й тоді, просто результату не було видно. Тепер той самий компонент, що й на
          головній: своя картинка виграє, немає — фолбек на фото товару з підгрупи. */}
      {data.subcategories.length > 0 && (
        <CategoryTiles
          categories={data.subcategories}
          locale={locale}
          title={t("catalog.subcategories")}
          hrefFor={(sub: CategoryOut) => localePath(locale, `/catalog/${slug.join("/")}/${sub.slug}`)}
          showCount
        />
      )}

      <CatalogResults data={data} locale={locale} />
    </div>
  );
}
