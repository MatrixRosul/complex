import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";
import type { ProductDetail } from "@/lib/api/types";

import { AvailabilityBadge } from "./availability-badge";
import { ConditionBadge } from "./product-badges";

/**
 * ПЕРША ПЛИТКА правої колонки: бренд, модель, артикул, статус наявності.
 *
 * Задача «Сайт §3» дослівно: «справа винести першу плитку (модель, артикул)». До цього
 * артикул був дрібним сірим рядком у шапці <BuyPanel>, над свічером варіантів, а бейдж
 * наявності — там же нижче. Тобто «що це за прилад» і «скільком це коштує» жили в одному
 * блоці, і око не мало де зупинитись. Тепер це дві окремі плитки: ідентифікація тут,
 * гроші й дії — у <BuyPanel>.
 *
 * ⚠️ МОДЕЛЬ = `mpn`, АРТИКУЛ = `sku`, і плутати їх не можна. `mpn` (manufacturer part
 * number) — заводське позначення, яке покупець гуглить і зіставляє з іншими магазинами
 * («SMV4HVX00E»). `sku` — НАШ внутрішній код із прайсу, за яким товар шукає менеджер.
 * Замовник назвав їх у порядку «модель, артикул» — цей порядок і збережено.
 *
 * ⚠️ Порожній рядок НЕ рендериться. `mpn` у прайсі Артура заповнений далеко не всюди;
 * «Модель: —» на пів каталогу виглядало б як поломка, а не як брак даних.
 *
 * Серверний компонент: тут нема жодного інтеракту, тягти заради трьох рядків клієнтський
 * JS немає сенсу. Бейджі всередині — клієнтські ("use client"), це нормально: серверний
 * компонент може їх рендерити, контекст i18n їм дає <Providers> з layout.
 */
export function IdentityPanel({
  product,
  locale,
}: {
  product: ProductDetail;
  locale: Locale;
}) {
  const t = getT(locale);

  const rows = [
    { label: t("product.model"), value: product.mpn },
    { label: t("product.article"), value: product.sku },
  ].filter((row) => row.value?.trim());

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      {product.brand && (
        <span className="text-h3 text-foreground">{product.brand.name}</span>
      )}

      {rows.length > 0 && (
        <dl className="flex flex-col gap-1.5">
          {rows.map((row) => (
            <div key={row.label} className="flex items-baseline gap-2 text-sm">
              <dt className="text-muted-foreground">{row.label}:</dt>
              {/* product-code → tabular-nums: артикули майже завжди цифрові, і в парі
                  «Модель / Артикул» вони мусять вирівнюватись по розряду. */}
              <dd className="product-code font-semibold text-foreground">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <AvailabilityBadge
          availability={product.availability}
          leadDays={product.order_lead_days}
          variant="badge"
        />
        <ConditionBadge condition={product.condition} />
      </div>
    </div>
  );
}
