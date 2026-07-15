"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { ChevronLeft, ChevronRight, ImageOff, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import type { ImageOut } from "@/lib/api/types";

/**
 * Галерея товару (DESIGN_SYSTEM §4.3).
 *
 * Desktop: вертикальна стрічка мініатюр 64px зліва + головне фото справа.
 * Mobile:  горизонтальний свайп-карусель з точками й лічильником «3/8».
 *
 * ⚠️ Зум — по КЛІКУ в лайтбокс, а не hover-лупа: лупа просто не існує на тач-екранах,
 * а половина покупців побутової техніки заходить з телефона.
 */
export function ProductGallery({
  images,
  name,
  className,
}: {
  images: ImageOut[];
  name: string;
  className?: string;
}) {
  const t = useT();
  const [active, setActive] = useState(0);
  const [lightbox, setLightbox] = useState(false);

  const total = images.length;

  const go = useCallback(
    (delta: number) => setActive((i) => (i + delta + total) % total),
    [total],
  );

  // Esc закриває лайтбокс, стрілки гортають.
  useEffect(() => {
    if (!lightbox) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(false);
      if (e.key === "ArrowRight") go(1);
      if (e.key === "ArrowLeft") go(-1);
    };

    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [lightbox, go]);

  // Порожній стан — заглушка, ніколи не битий <img>.
  if (total === 0) {
    return (
      <div
        className={cn(
          "flex aspect-square w-full items-center justify-center rounded-lg bg-muted",
          className,
        )}
      >
        <div className="flex flex-col items-center gap-2 text-muted-foreground">
          <ImageOff aria-hidden className="size-10" />
          <span className="text-sm">{t("product.noPhoto")}</span>
        </div>
      </div>
    );
  }

  const current = images[active];

  return (
    <div className={cn("flex flex-col gap-3 lg:flex-row", className)}>
      {/* ── Мініатюри: зліва на ПК, приховані на мобільному ─────────── */}
      <div className="order-2 hidden shrink-0 flex-col gap-2 lg:order-1 lg:flex">
        {images.slice(0, 6).map((img, i) => (
          <button
            key={img.id}
            type="button"
            onClick={() => setActive(i)}
            aria-label={`${name} — ${i + 1}`}
            aria-current={i === active}
            className={cn(
              "relative size-16 overflow-hidden rounded-md border bg-card transition-colors",
              i === active
                ? "border-transparent ring-2 ring-primary"
                : "border-border hover:border-input",
            )}
          >
            <Image
              src={img.url}
              alt=""
              fill
              sizes="64px"
              className="object-contain p-1"
            />
          </button>
        ))}
        {total > 6 && (
          <button
            type="button"
            onClick={() => setLightbox(true)}
            className="flex size-16 items-center justify-center rounded-md border border-border bg-muted text-xs font-medium text-muted-foreground hover:border-input tnum"
          >
            +{total - 6}
          </button>
        )}
      </div>

      {/* ── Головне фото ─────────────────────────────────────────────── */}
      <div className="relative order-1 flex-1 lg:order-2">
        <button
          type="button"
          onClick={() => setLightbox(true)}
          className="relative block aspect-square w-full cursor-zoom-in rounded-lg bg-card"
          aria-label={name}
        >
          <Image
            src={current.url}
            alt={current.alt || name}
            fill
            sizes="(max-width: 1024px) 100vw, 50vw"
            className="object-contain p-4"
            priority
          />
        </button>

        {/* Лічильник — правий верхній кут, тільки на мобільному. */}
        {total > 1 && (
          <span className="absolute right-3 top-3 rounded-full bg-foreground/70 px-2 py-0.5 text-xs font-medium text-background tnum lg:hidden">
            {t("product.photoCount", { current: active + 1, total })}
          </span>
        )}

        {/* Точкові індикатори — мобільний. */}
        {total > 1 && (
          <div className="mt-3 flex justify-center gap-1.5 lg:hidden">
            {images.map((img, i) => (
              <button
                key={img.id}
                type="button"
                onClick={() => setActive(i)}
                aria-label={`${i + 1}/${total}`}
                className={cn(
                  "size-2 rounded-full transition-colors",
                  i === active ? "bg-primary" : "bg-border",
                )}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Лайтбокс ─────────────────────────────────────────────────── */}
      {lightbox && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={name}
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 backdrop-blur"
          onClick={() => setLightbox(false)}
        >
          <Button
            variant="ghost"
            size="icon-lg"
            aria-label={t("common.close")}
            className="absolute right-4 top-4"
            onClick={() => setLightbox(false)}
          >
            <X className="size-5" />
          </Button>

          {total > 1 && (
            <>
              <Button
                variant="ghost"
                size="icon-lg"
                aria-label={t("catalog.prev")}
                className="absolute left-4"
                onClick={(e) => {
                  e.stopPropagation();
                  go(-1);
                }}
              >
                <ChevronLeft className="size-6" />
              </Button>
              <Button
                variant="ghost"
                size="icon-lg"
                aria-label={t("catalog.next")}
                className="absolute right-4 top-1/2"
                onClick={(e) => {
                  e.stopPropagation();
                  go(1);
                }}
              >
                <ChevronRight className="size-6" />
              </Button>
            </>
          )}

          <div
            className="relative size-full max-h-[85vh] max-w-[90vw]"
            onClick={(e) => e.stopPropagation()}
          >
            <Image
              src={current.url}
              alt={current.alt || name}
              fill
              sizes="90vw"
              className="object-contain"
            />
          </div>

          <span className="absolute bottom-6 rounded-full bg-muted px-3 py-1 text-sm text-muted-foreground tnum">
            {t("product.photoCount", { current: active + 1, total })}
          </span>
        </div>
      )}
    </div>
  );
}
