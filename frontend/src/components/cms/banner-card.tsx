import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";

import { localePath, type Locale } from "@/i18n/config";
import type { BannerOut } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Банер: фото + опційний overlay-контент (заголовок/кнопка) поверх нього.
 *
 * ⚠️ ПРО СКРІМ (`scrim`). Це темний градієнт МІЖ фото і текстом. За замовчуванням його
 * НЕМАЄ — замовник попросив прибрати градієнти: у його банерах увесь текст ВШИТИЙ у саму
 * картинку (поле `image` перекладне, підпис лежить усередині зображення), а `title`/
 * `subtitle` порожні, тож накладного тексту тут нема взагалі й затемнювати нічого.
 *
 * Скрім лишився ОПЦІЄЮ (`scrim`), а не викинутий, бо колись розв'язав реальну проблему:
 * `title` кольором `text-foreground` по темному фото давав контраст 1.09:1 (норма 4.5:1) —
 * текст був фізично невидимий. Якщо колись банер знову ітиме зі СПРАВЖНІМ накладним
 * текстом (`.banner-text`), увімкни `scrim` — і він читатиметься на будь-якому фото.
 * З порожнім title (як зараз) скрім не потрібен: рендерити нічого.
 */
export function BannerCard({
  banner,
  locale,
  children,
  className,
  sizes,
  priority = false,
  scrim = false,
}: {
  banner: BannerOut;
  locale: Locale;
  children: ReactNode;
  className?: string;
  sizes: string;
  priority?: boolean;
  /** Темний градієнт під накладним текстом. За замовчуванням вимкнено (див. коментар). */
  scrim?: boolean;
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
          // Слот майже завжди інших пропорцій, ніж картинка → `cover` її обрізає.
          // ЯКУ САМЕ частину лишити, вирішує замовник в адмінці, рухаючи кадр у
          // відсотках; `zoom` наближає, щоб можна було прибрати зайве по краях.
          style={{
            objectPosition: `${banner.focus_x ?? 50}% ${banner.focus_y ?? 50}%`,
            ...((banner.zoom ?? 100) > 100 ? { scale: `${(banner.zoom ?? 100) / 100}` } : {}),
          }}
          className="object-cover transition-transform duration-200 group-hover:scale-[1.02]"
        />
      ) : null}

      {/* Скрім лише коли явно ввімкнений (є накладний текст). Типово вимкнено — жодного
          градієнта на банерах, які бачить замовник. */}
      {scrim && <div aria-hidden className="banner-scrim" />}

      <div className="relative flex flex-col gap-2">{children}</div>
    </Link>
  );
}
