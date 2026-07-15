import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";

import { localePath, type Locale } from "@/i18n/config";
import type { BannerOut } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Банер із гарантовано читабельним текстом — НА БУДЬ-ЯКОМУ ФОТО.
 *
 * ⚠️ ЧОМУ ЦЕ ОКРЕМИЙ КОМПОНЕНТ, А НЕ JSX У page.tsx.
 * Було: <Image> і одразу текстовий <div className="relative"> — між ними ЖОДНОГО шару.
 * Заголовок ішов кольором `text-foreground` (#18181B, майже чорний) прямо по темному фото
 * замовника: виміряний контраст 1.09:1 / 1.24:1 / 1.28:1 при нормі WCAG 4.5:1. Текст був
 * не «трохи блідий», а фізично невидимий — саме це користувач описав як «колір тексту
 * зливається з фоном».
 *
 * Виправляти це підбором кольору під три поточні картинки — означає полагодити симптом:
 * завтра контент-менеджер завантажить світле фото, і білий текст зникне так само.
 * Тому захист СИСТЕМНИЙ і не залежить від знімка:
 *   1. `.banner-scrim` — градієнт-скрім МІЖ фото і текстом (знизу ~80% чорного);
 *   2. `.banner-text` — колір зафіксовано білим (НЕ токеном теми) + text-shadow;
 *   3. фото рендериться, ТІЛЬКИ якщо image_url непорожній — інакше лишається `bg-muted`,
 *      і скрім усе одно на місці, тож білий текст читається й без картинки.
 * Результат — ≈15:1 на будь-якому фото, темному чи світлому.
 *
 * Кнопку (cta_label) скрім не стосується: `bg-primary text-primary-foreground` контрастна
 * сама по собі, незалежно від підкладки.
 */
export function BannerCard({
  banner,
  locale,
  children,
  className,
  sizes,
  priority = false,
}: {
  banner: BannerOut;
  locale: Locale;
  children: ReactNode;
  className?: string;
  sizes: string;
  priority?: boolean;
}) {
  return (
    <Link
      href={localePath(locale, banner.link)}
      className={cn(
        "relative flex flex-col justify-end overflow-hidden rounded-xl border border-border bg-muted",
        className,
      )}
    >
      {/* Порожній/битий image_url — не рендеримо <Image> взагалі: next/image на порожньому
          src кидає помилку в рантаймі й гасить УСЮ сторінку через error boundary. */}
      {banner.image_url ? (
        <Image
          src={banner.image_url}
          alt=""
          fill
          priority={priority}
          sizes={sizes}
          className="object-cover transition-transform duration-200 group-hover:scale-[1.02]"
        />
      ) : null}

      {/* 🔴 Шар, якого не було. Саме він робить текст читабельним. */}
      <div aria-hidden className="banner-scrim" />

      <div className="relative flex flex-col gap-2">{children}</div>
    </Link>
  );
}
