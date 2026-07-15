"use client";

import {
  AirVent,
  Blend,
  Bot,
  Coffee,
  CupSoda,
  Fan,
  Flame,
  Headphones,
  LayoutGrid,
  Microwave,
  Package,
  Plug,
  Refrigerator,
  Shirt,
  Snowflake,
  Speaker,
  ThermometerSun,
  Tv,
  Utensils,
  WashingMachine,
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
};

export function CategoryIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] ?? Package;
  return <Icon aria-hidden className={cn("size-5 stroke-[1.5]", className)} />;
}
