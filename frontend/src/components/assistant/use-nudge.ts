"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { NUDGE_COOLDOWN_MS, useAssistantStore } from "@/store/assistant";

/**
 * Логіка проактивної бульбашки-нуджа: КОЛИ показати і ЯКИЙ текст.
 *
 * Правила (узгоджено з Max):
 *   • показуємо ЗА СИГНАЛОМ ЗАЛУЧЕНОСТІ, не миттєво: людина проскролила перший екран АБО
 *     затрималась ~15с. Той, хто одразу закриває вкладку, нуджа не побачить — і добре;
 *   • ОДИН РАЗ на вкладку (`shownThisTab`), не блимаємо на кожній навігації;
 *   • НІКОЛИ, якщо чат уже відкривали (`hasOpenedChat`) — людина й так знає про асистента;
 *   • після закриття бульбашки — пауза NUDGE_COOLDOWN_MS (persist);
 *   • текст — КОНТЕКСТНИЙ під тип сторінки (товар/категорія/кошик/пошук/інше).
 */

const DWELL_MS = 15_000;
/** Частка висоти екрана, після якої скрол вважаємо «людина читає, а не пробігає повз». */
const SCROLL_TRIGGER_RATIO = 0.6;

export type NudgeContext = "product" | "category" | "cart" | "search" | "generic";

/** Тип сторінки з URL. Шлях — `/{locale}/{section}/…`, локаль відкидаємо. */
export function nudgeContextFromPath(pathname: string): NudgeContext {
  const section = pathname.split("/").filter(Boolean)[1] ?? "";
  switch (section) {
    case "p":
      return "product";
    case "c":
    case "catalog":
      return "category";
    case "cart":
      return "cart";
    case "search":
      return "search";
    default:
      return "generic";
  }
}

function cooldownPassed(dismissedAt: number | null): boolean {
  return dismissedAt === null || Date.now() - dismissedAt >= NUDGE_COOLDOWN_MS;
}

/**
 * Ставить тригери (скрол + таймер) і показує бульбашку, коли спрацював перший з них.
 * Викликається один раз із FoxWidget (після гідратації).
 */
export function useNudgeTrigger(hydrated: boolean): void {
  const pathname = usePathname();
  const isOpen = useAssistantStore((s) => s.isOpen);
  const hasOpenedChat = useAssistantStore((s) => s.hasOpenedChat);
  const nudgeVisible = useAssistantStore((s) => s.nudgeVisible);
  const nudgeDismissedAt = useAssistantStore((s) => s.nudgeDismissedAt);
  const showNudge = useAssistantStore((s) => s.showNudge);

  // Ref, а не стан: «уже показали в цій вкладці» має пережити ре-арм на навігації,
  // але НЕ повинен викликати зайвий ре-рендер.
  const shownThisTab = useRef(false);

  useEffect(() => {
    if (!hydrated) return;
    const canArm =
      !isOpen &&
      !hasOpenedChat &&
      !nudgeVisible &&
      !shownThisTab.current &&
      cooldownPassed(nudgeDismissedAt);
    if (!canArm) return;

    let done = false;
    const fire = () => {
      if (done) return;
      done = true;
      shownThisTab.current = true;
      showNudge();
    };

    const timer = window.setTimeout(fire, DWELL_MS);
    const onScroll = () => {
      if (window.scrollY > window.innerHeight * SCROLL_TRIGGER_RATIO) fire();
    };
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("scroll", onScroll);
    };
  }, [hydrated, pathname, isOpen, hasOpenedChat, nudgeVisible, nudgeDismissedAt, showNudge]);
}

/** Контекст поточної сторінки — для вибору тексту бульбашки. */
export function useNudgeContext(): NudgeContext {
  return nudgeContextFromPath(usePathname());
}
