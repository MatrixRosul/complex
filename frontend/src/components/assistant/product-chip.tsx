"use client";

import Image from "next/image";
import Link from "next/link";
import { ImageOff } from "lucide-react";
import { AvailabilityBadge } from "@/components/product/availability-badge";
import { Price } from "@/components/product/price";
import { mediaUrl } from "@/lib/media";
import { localePath, type Locale } from "@/i18n/config";
import type { AssistantProduct } from "@/lib/api/assistant";

/**
 * Міні-картка товару всередині повідомлення асистента.
 *
 * ⚠️ Клік — ЗВИЧАЙНА навігація через `<Link>`, БЕЗ onClick-обробника: чат-панель не
 * Dialog/Sheet і свідомо не блокує сторінку (PLAN §3), тож людина спокійно переходить
 * на картку товару, а розмова лишається позаду в localStorage.
 *
 * ⚠️ Ціна й наявність — рівно ті самі компоненти, що й у каталозі (`Price`,
 * `AvailabilityBadge`): токен `--price` (tabular-nums) і WCAG-безпечний статус
 * (колір + крапка + текст) тут так само важливі, як на картці товару.
 *
 * ⚠️ `mediaUrl()` — навіть якщо бекенд-асистент уже віддав абсолютний URL,
 * функція ідемпотентна для http(s)/data/blob. Але якщо колись image прийде
 * відносним шляхом (`/media/…`) — це той самий кейс, що й банери/іконки категорій
 * (lib/media.ts), і без нормалізації next/image впав би в SSRF-guard.
 */
export function ProductChip({
  product,
  locale,
}: {
  product: AssistantProduct;
  locale: Locale;
}) {
  const imageUrl = mediaUrl(product.image);

  return (
    <Link
      href={localePath(locale, product.url)}
      className="flex items-center gap-3 rounded-lg border border-border bg-card p-2 transition-colors hover:border-primary/40 hover:bg-muted"
    >
      <div className="relative size-14 shrink-0 overflow-hidden rounded-md bg-muted">
        {imageUrl ? (
          <Image
            src={imageUrl}
            alt={product.name}
            fill
            sizes="56px"
            className="object-contain p-1"
          />
        ) : (
          <div className="flex size-full items-center justify-center">
            <ImageOff aria-hidden className="size-5 text-muted-foreground" />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-sm font-medium text-foreground">{product.name}</p>
        <AvailabilityBadge availability={product.availability} className="mt-0.5" />
      </div>

      <Price
        price={product.price}
        oldPrice={product.old_price}
        locale={locale}
        size="lg"
        reserveOldPriceSpace={false}
        className="shrink-0 items-end text-right"
      />
    </Link>
  );
}
