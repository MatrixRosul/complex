import type { Metadata } from "next";

import { SearchBar } from "@/components/layout/search-bar";
import {
  CatalogResults,
  parseCatalogParams,
  type SearchParams,
} from "@/components/catalog/catalog-results";
import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localeToApiLang, type Locale } from "@/i18n/config";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}): Promise<Metadata> {
  const { locale } = await params;
  return {
    title: getT(locale)("search.submit"),
    // Сторінки пошуку індексувати не треба: нескінченний простір URL,
    // з якого краулер не вибереться, а цінності для індексу нуль.
    robots: { index: false, follow: true },
  };
}

/**
 * Пошук — GET /api/v1/search: FTS по `search_vector_{lang}` + trigram-фолбек на одруківки
 * («холодильнек» → 9 товарів, «Gorenge» → 8). Фасети працюють і тут: пошук — просто
 * інший спосіб звузити базовий набір, тому рендеримо той самий <CatalogResults>.
 */
export default async function SearchPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: Locale }>;
  searchParams: Promise<SearchParams>;
}) {
  const [{ locale }, sp] = await Promise.all([params, searchParams]);
  const t = getT(locale);

  const query = parseCatalogParams(sp);
  const q = query.q ?? "";

  if (!q) {
    return (
      <div className="container-complex flex flex-col gap-6 py-6">
        <h1 className="text-h1 text-foreground">{t("search.submit")}</h1>
        <SearchBar />
        <p className="text-sm text-muted-foreground">{t("search.emptyQuery")}</p>
      </div>
    );
  }

  const data = await api.getCatalog(query, localeToApiLang[locale]);

  return (
    <div className="container-complex flex flex-col gap-5 py-6">
      <h1 className="text-h1 text-foreground">
        {t("search.resultsFor")}: <span className="text-muted-foreground">{q}</span>
      </h1>

      <SearchBar initialQuery={q} className="max-w-2xl" />

      {data.total === 0 ? (
        <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
          <p className="text-h3 text-foreground">{t("search.nothingFound")}</p>
          <p className="mt-2 text-sm text-muted-foreground">{t("search.nothingFoundHint")}</p>
        </div>
      ) : (
        <CatalogResults data={data} locale={locale} />
      )}
    </div>
  );
}
