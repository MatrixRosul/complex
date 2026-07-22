"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";

/**
 * СЛАЙДЕР ПРОМО-БАНЕРІВ у правій зоні каталогу.
 *
 * ⚠️ НАВІЩО. Досі в слоті показувався ПЕРШИЙ банер, а решта мовчки чекали черги: замовник
 * заводив кілька промо і бачив лише одне. Тепер вони чергуються, а стрілки дають ручне
 * гортання. Один банер → стрілок і крапок немає взагалі (нема чим гортати).
 *
 * ⚠️ Автопрокрутка ЗУПИНЯЄТЬСЯ на ховері й фокусі. Банер — це посилання: якщо він поїде
 * з-під курсора, людина клікне не туди, куди цілилась. З тієї ж причини інтервал 6 с, а
 * не 3 — встигнути прочитати й натиснути.
 *
 * ⚠️ Слайди рендеряться ВСІ й перемикаються прозорістю, а не умовним рендером: так
 * next/image не перезавантажує картинку щоразу і не блимає білим при поверненні назад.
 */
const INTERVAL_MS = 6000;

export function BannerCarousel({ slides }: { slides: ReactNode[] }) {
  const t = useT();
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  const count = slides.length;
  const go = useCallback((next: number) => setIndex(((next % count) + count) % count), [count]);

  useEffect(() => {
    if (count < 2 || paused) return;
    const timer = setInterval(() => setIndex((i) => (i + 1) % count), INTERVAL_MS);
    return () => clearInterval(timer);
  }, [count, paused]);

  if (count === 0) return null;
  if (count === 1) return <>{slides[0]}</>;

  return (
    <div
      className="group/carousel relative flex min-w-0 flex-1"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      role="region"
      aria-roledescription="carousel"
      aria-label={t("home.promoCarousel")}
    >
      {slides.map((slide, i) => (
        <div
          key={i}
          // Неактивні слайди прибрані з потоку читання й з табуляції: інакше скрінрідер
          // зачитував би всі банери підряд, а Tab провалювався б у невидимі посилання.
          aria-hidden={i !== index}
          inert={i !== index ? true : undefined}
          className={cn(
            "inset-0 transition-opacity duration-500",
            i === index ? "relative flex flex-1 opacity-100" : "absolute opacity-0",
          )}
        >
          {slide}
        </div>
      ))}

      {/* Стрілки: з'являються на ховері, але на фокусі — завжди (клавіатура). */}
      <button
        type="button"
        onClick={() => go(index - 1)}
        aria-label={t("home.promoPrev")}
        className="absolute left-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-background/85 p-2 text-foreground opacity-0 shadow-md transition-opacity hover:bg-background focus-visible:opacity-100 group-hover/carousel:opacity-100"
      >
        <ChevronLeft aria-hidden className="size-5" />
      </button>
      <button
        type="button"
        onClick={() => go(index + 1)}
        aria-label={t("home.promoNext")}
        className="absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-background/85 p-2 text-foreground opacity-0 shadow-md transition-opacity hover:bg-background focus-visible:opacity-100 group-hover/carousel:opacity-100"
      >
        <ChevronRight aria-hidden className="size-5" />
      </button>

      {/* Крапки — і індикатор «скільки їх», і швидкий перехід. */}
      <div className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 gap-1.5">
        {slides.map((_, i) => (
          <button
            key={i}
            type="button"
            onClick={() => go(i)}
            aria-label={`${i + 1}`}
            aria-current={i === index}
            className={cn(
              "size-2 rounded-full transition-colors",
              i === index ? "bg-primary" : "bg-foreground/25 hover:bg-foreground/40",
            )}
          />
        ))}
      </div>
    </div>
  );
}
