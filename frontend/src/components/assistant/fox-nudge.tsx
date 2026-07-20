"use client";

import { X } from "lucide-react";
import { useAssistantStore } from "@/store/assistant";
import { useT } from "@/i18n/provider";
import { useNudgeContext } from "@/components/assistant/use-nudge";
import { cn } from "@/lib/utils";

/**
 * Проактивна бульбашка над лисичкою: «🦊 ШІ-помічник. Допомогти обрати?».
 *
 * Дизайн-дисципліна (DESIGN_SYSTEM §2.3 «акцент = тут гроші / тут дія»): бульбашка на
 * НЕЙТРАЛЬНІЙ поверхні (`bg-card`), акцент — лише крихітна крапка біля лейбла «ШІ-помічник».
 * Це не промо-попап: жодного заклику «ЗНИЖКА!», лише чесна пропозиція допомоги + чесна
 * рамка «це ШІ» (саме те, що просив Max — щоб люди бачили, що лисичка = асистент).
 *
 * Клік по тілу → відкриває чат (store.open виставляє hasOpenedChat, тож нудж більше не
 * з'явиться). Хрестик → dismiss із паузою на 7 днів. Анімація появи гаситься в
 * prefers-reduced-motion (як у fox.tsx), але сама бульбашка лишається — функція не залежить від руху.
 */
export function FoxNudge() {
  const t = useT();
  const context = useNudgeContext();
  const nudgeVisible = useAssistantStore((s) => s.nudgeVisible);
  const open = useAssistantStore((s) => s.open);
  const dismiss = useAssistantStore((s) => s.dismissNudge);

  if (!nudgeVisible) return null;

  return (
    <>
      <style>{NUDGE_KEYFRAMES}</style>
      <div
        className={cn(
          // Над кнопкою-лисичкою (size-14 = 56px + відступ). Той самий z-40, що й кнопка:
          // нижче за Dialog/Sheet, щоб не пробиватись над відкритим кошиком/фільтрами.
          "fixed right-4 bottom-20 z-40 max-w-[16rem] sm:right-6 sm:bottom-24",
          "cx-nudge-in",
        )}
      >
        <div className="relative rounded-2xl bg-card p-3 pr-9 shadow-lg ring-1 ring-border">
          <button
            type="button"
            onClick={dismiss}
            aria-label={t("assistant.nudge.close")}
            className={cn(
              "absolute top-1.5 right-1.5 flex size-6 items-center justify-center rounded-full",
              "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
              "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            )}
          >
            <X className="size-3.5" aria-hidden="true" />
          </button>

          <button
            type="button"
            onClick={open}
            className="block text-left focus-visible:outline-none"
          >
            {/* Чесна рамка «це ШІ» — крапка акцентом, текст ні, щоб не розмивати сигнал акценту. */}
            <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
              {t("assistant.nudge.ai_label")}
            </span>
            <span className="mt-1 block text-sm text-foreground">
              {t(`assistant.nudge.${context}`)}
            </span>
          </button>
        </div>
      </div>
    </>
  );
}

// Поява: підйом + прояв. Scoped-клас `cx-nudge-*`, як у fox.tsx (Tailwind 4 не генерує
// @keyframes для довільного animate-[...]). У reduced-motion — миттєва поява без руху.
const NUDGE_KEYFRAMES = `
  @keyframes cx-nudge-rise {
    from { opacity: 0; transform: translateY(8px) scale(0.96); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
  }
  .cx-nudge-in { animation: cx-nudge-rise 0.22s ease-out both; transform-origin: bottom right; }
  @media (prefers-reduced-motion: reduce) {
    .cx-nudge-in { animation: none; }
  }
`;
