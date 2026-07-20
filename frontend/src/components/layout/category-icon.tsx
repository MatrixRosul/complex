"use client";

import {
  AirVent,
  Beef,
  Blend,
  Bot,
  Citrus,
  Coffee,
  CookingPot,
  CupSoda,
  Droplets,
  Fan,
  Flame,
  Headphones,
  LayoutGrid,
  Microwave,
  Package,
  Plug,
  Refrigerator,
  Sandwich,
  Shirt,
  Snowflake,
  Sparkles,
  Speaker,
  ThermometerSun,
  Tv,
  Utensils,
  WashingMachine,
  Waves,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Міні-емблеми категорій для мегаменю (DESIGN_SYSTEM §4.9).
 *
 * ⚠️ Векторні іконки з currentColor, а НЕ кольорові PNG. Причина:
 * у темній темі кольоровий PNG на темному фоні перетворюється на пляму,
 * а stroke-емблема просто успадковує колір тексту й працює в обох темах.
 */
const ICONS: Record<string, LucideIcon> = {
  refrigerator: Refrigerator,
  "washing-machine": WashingMachine,
  utensils: Utensils,
  flame: Flame,
  snowflake: Snowflake,
  shirt: Shirt,
  microwave: Microwave,
  bot: Bot,
  coffee: Coffee,
  blend: Blend,
  "cup-soda": CupSoda,
  tv: Tv,
  speaker: Speaker,
  headphones: Headphones,
  "layout-grid": LayoutGrid,
  oven: Microwave,
  fan: Fan,
  "air-vent": AirVent,
  "thermometer-sun": ThermometerSun,
  plug: Plug,
  package: Package,
  sparkles: Sparkles,
  droplets: Droplets,
  "cooking-pot": CookingPot,
  wrench: Wrench,
  beef: Beef,
  waves: Waves,
  citrus: Citrus,
  sandwich: Sandwich,
};

export function CategoryIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] ?? Package;
  return <Icon aria-hidden className={cn("size-5 stroke-[1.5]", className)} />;
}

/**
 * КРУГЛИЙ БЕЙДЖ КАТЕГОРІЇ — єдине місце, де вирішується «що саме намалювати».
 * Ним користуються мегаменю, бургер і сайдбар головної, тож правило одне на всіх.
 *
 * ⚠️ Чому SVG йде через mask-image, а не <img>. Емблема з адмінки має ВЛАСНИЙ колір
 * (замовниця малює їх у колір лого — темно-синій). На темній темі темно-синя емблема
 * на темному фоні зникає — саме через це `icon_url` раніше не використовувався взагалі.
 * Маска викидає кольори файлу й лишає тільки його ФОРМУ, яку ми заливаємо currentColor:
 * емблема поводиться як lucide-іконка і читається в обох темах.
 *
 * Растр (PNG/WebP) замаскувати не можна — напівпрозорі краї дадуть брудний силует.
 * Тому він рендериться як є: це усвідомлений компроміс, і саме тому в адмінці
 * для емблем рекомендований SVG.
 */
export function CategoryEmblem({
  emblem,
  className,
}: {
  emblem?: { icon: string; url: string | null };
  className?: string;
}) {
  const url = emblem?.url ?? null;
  const isSvg = url ? new URL(url, "http://x").pathname.toLowerCase().endsWith(".svg") : false;

  return (
    <span
      aria-hidden
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-subtle text-primary",
        className,
      )}
    >
      {url && isSvg ? (
        <span
          className="size-5 bg-current"
          style={{
            maskImage: `url(${JSON.stringify(url)})`,
            WebkitMaskImage: `url(${JSON.stringify(url)})`,
            maskSize: "contain",
            WebkitMaskSize: "contain",
            maskPosition: "center",
            WebkitMaskPosition: "center",
            maskRepeat: "no-repeat",
            WebkitMaskRepeat: "no-repeat",
          }}
        />
      ) : url ? (
        // Емблема — файл до 64 КБ з /media, який тут малюється розміром 20×20.
        // next/image дав би на нього окремий раунд оптимізації й запис у кеш —
        // дорожче за сам файл, тому свідомо звичайний <img>.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" className="size-5 object-contain" />
      ) : (
        <CategoryIcon name={emblem?.icon ?? "package"} />
      )}
    </span>
  );
}
