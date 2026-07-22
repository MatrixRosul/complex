import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { BuyPanel } from "@/components/product/buy-panel";
import { ProductGallery } from "@/components/product/product-gallery";
import { RelatedProducts } from "@/components/product/related-products";
import { SpecsTable } from "@/components/product/specs-table";
import { TrackView } from "@/components/product/track-view";
import { VariantSwitcher } from "@/components/product/variant-switcher";
import { DeliveryCalculator } from "@/components/delivery/delivery-calculator";
import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";

type Params = Promise<{ locale: Locale; id: string; slug: string }>;

/** ISR: ADR-023 — `revalidateTag('product:{id}:{lang}')` після синку + TTL як самозагоєння. */
export const revalidate = 600;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { locale, id, slug } = await params;
  const product = await api.getProduct(Number(id), localeToApiLang[locale]);
  if (!product) return {};

  return {
    title: product.seo_title || product.name,
    description: product.seo_description || product.short_description,
    // Канонічний URL — по id + slug. Роут іде по id, slug декоративний,
    // тому канонікал обов'язковий: інакше /p/5601/будь-що дасть дублі.
    alternates: { canonical: localePath(locale, `/p/${id}/${slug}`) },
    openGraph: {
      title: product.name,
      description: product.short_description,
      images: product.main_image_url ? [product.main_image_url] : undefined,
    },
  };
}

export default async function ProductPage({ params }: { params: Params }) {
  const { locale, id } = await params;
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  const product = await api.getProduct(Number(id), lang);
  if (!product) notFound();

  /**
   * ДВА РІЗНІ БЛОКИ, А НЕ ОДИН З ФОЛБЕКОМ (вимога замовника).
   *
   * Раніше «З цим товаром купують» підмінявся товарами тієї ж категорії, коли RelatedGroup
   * порожня, — і заголовок брехав: під «купують разом» лежали конкуренти тієї ж моделі.
   *
   *   crossSell — ЛИШЕ те, що контент-менеджер завів руками (RelatedGroup). Немає — блока немає.
   *   similar   — автопідбір по категорії. Це те, що блок робив і досі, але тепер під чесною
   *               назвою «Схожі товари» і показується завжди.
   *
   * Порядок на сторінці: «З цим товаром купують» → «Схожі товари».
   */
  const crossSell = await api.getRelatedProducts(product.id, lang);

  const crossSellIds = new Set(crossSell.map((p) => p.id));
  const similar = (
    await api.getCatalog(
      // ⚠️ Беремо з запасом: із видачі відсіюється сам товар + усі, що вже стоять
      //    у «купують разом», інакше та сама модель світиться двічі поспіль.
      { category: product.category.external_id, page_size: 16, sort: "popular" },
      lang,
    )
  ).items
    .filter((p) => p.id !== product.id && !crossSellIds.has(p.id))
    .slice(0, 8);

  /**
   * Кнопка «← Назад до <категорія>».
   *
   * ⚠️ Саме ПОСИЛАННЯ В КАТЕГОРІЮ, а не `router.back()`. Історія браузера порожня, коли на
   * картку зайшли напряму — з реклами, пошуковика, месенджера чи збереженого посилання, —
   * і `back()` у такому разі або нікуди не веде, або викидає з сайту зовсім. Посилання
   * працює завжди й веде туди, куди людина насправді хоче: у список схожих товарів.
   *
   * Хлібні крихти лишаються (SEO + повний шлях), але вони text-xs, приглушені й на вузькому
   * екрані переносяться в 2–3 рядки — як навігаційна кнопка не читаються.
   */
  const parent = product.breadcrumbs.at(-1);
  const backHref = parent
    ? localePath(locale, `/catalog/${product.breadcrumbs.map((x) => x.slug).join("/")}`)
    : localePath(locale, "/catalog");

  return (
    <div className="container-complex flex flex-col gap-10 py-6">
      {/* ⚠️ Нуль пікселів: записує id у localStorage для блоку «Ви переглядали» на головній.
          Сама сторінка лишається серверною (RSC) — це найдорожча сторінка сайту для SEO,
          і робити її клієнтською заради одного запису було б безглуздо. */}
      <TrackView id={product.id} />

      <div className="flex flex-col gap-3">
        <Link
          href={backHref}
          className="flex w-fit items-center gap-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronLeft aria-hidden className="size-4" />
          {parent ? t("nav.backTo", { category: parent.name }) : t("nav.back")}
        </Link>

        <Breadcrumbs
          locale={locale}
          items={[
            ...product.breadcrumbs.map((c, i) => ({
              label: c.name,
              href: localePath(
                locale,
                `/catalog/${product.breadcrumbs.slice(0, i + 1).map((x) => x.slug).join("/")}`,
              ),
            })),
            { label: product.name },
          ]}
        />
      </div>

      <h1 className="text-h1 text-foreground">{product.name}</h1>

      <div className="grid gap-8 lg:grid-cols-2">
        <ProductGallery images={product.images} name={product.name} />

        <div className="flex flex-col gap-6">
          {/* Перемикачі варіантів — узагальнені: кнопки АБО кружечки. */}
          {product.variant_groups.map((group) => (
            <VariantSwitcher
              key={group.id}
              group={group}
              currentProductId={product.id}
            />
          ))}

          <BuyPanel product={product} />

          <DeliveryCalculator product={product} />
        </div>
      </div>

      {/* ── «Характеристики | Опис» — жорсткий формат замовника ─────── */}
      <SpecsTable specs={product.specs} description={product.description} />

      {crossSell.length > 0 && (
        <RelatedProducts title={t("product.related")} products={crossSell} />
      )}

      {similar.length > 0 && (
        <RelatedProducts title={t("product.similar")} products={similar} />
      )}
    </div>
  );
}
