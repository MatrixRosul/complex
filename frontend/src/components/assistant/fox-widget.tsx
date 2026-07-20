"use client";

import { useState } from "react";
import { Fox } from "@/components/assistant/fox";
import { ChatPanel } from "@/components/assistant/chat-panel";
import { FoxNudge } from "@/components/assistant/fox-nudge";
import { useNudgeTrigger } from "@/components/assistant/use-nudge";
import { useAssistantStore } from "@/store/assistant";
import { useHydrated } from "@/hooks/use-hydrated";
import { useT } from "@/i18n/provider";
import { cn } from "@/lib/utils";

type FoxVisualState = "idle" | "hover" | "thinking" | "talking";

/**
 * Плаваюча кнопка-лисичка (fixed bottom-right) + чат-панель.
 *
 * ⚠️ z-index (PLAN §3). Кнопка — `z-40`, свідомо НИЖЧЕ за Dialog/Sheet (`z-50`):
 * якщо користувач відкрив кошик чи фільтри, той шар важливіший за виклик асистента,
 * і лисичка мусить під ним ховатись, а не пробиватись зверху. Панель чату — `z-50`,
 * той самий поверх, що й діалоги, але вона НЕ Dialog і не блокує сторінку (немає
 * backdrop) — двом речам на z-50 нема з чим конфліктувати, бо панель ніколи не
 * накладається на модалку: поки модалка відкрита, кнопка виклику чату вже схована.
 * Нових z-поверхів не заводимо — найвищий у проєкті лишається 50.
 *
 * ⚠️ useHydrated(). І `messages`, і `sessionToken` — persist-стан (store/assistant.ts).
 * Поки localStorage не прочитаний, віджет узагалі не рендериться: `isOpen` на сервері
 * й до гідратації завжди `false` (він поза partialize), тож технічно hydration
 * mismatch тут малоймовірний, але це єдине місце, де монтується ChatPanel — і саме
 * вона читає persisted-історію, тож гейт ставимо тут, а не розкидаємо по дітях.
 */
export function FoxWidget() {
  const hydrated = useHydrated();
  const t = useT();
  const [hovered, setHovered] = useState(false);

  const isOpen = useAssistantStore((s) => s.isOpen);
  const isStreaming = useAssistantStore((s) => s.isStreaming);
  const streamPhase = useAssistantStore((s) => s.streamPhase);
  const toggle = useAssistantStore((s) => s.toggle);
  const close = useAssistantStore((s) => s.close);
  const nudgeVisible = useAssistantStore((s) => s.nudgeVisible);
  const hasOpenedChat = useAssistantStore((s) => s.hasOpenedChat);
  const nudgeDismissedAt = useAssistantStore((s) => s.nudgeDismissedAt);

  // Тригер бульбашки (скрол/затримка) — хук завжди викликається; усередині сам вирішує,
  // чи ставити слухачі. Гейтимо параметром, а не умовним викликом (правила хуків).
  useNudgeTrigger(hydrated);

  if (!hydrated) return null;

  const foxState: FoxVisualState = isStreaming
    ? streamPhase === "talking"
      ? "talking"
      : "thinking"
    : hovered
      ? "hover"
      : "idle";

  // Тиха крапка на кнопці ПІСЛЯ того, як бульбашку закрили, але чат так і не відкрили:
  // лишається натяк «тут щось є», без повторного нудіння. Зникає назавжди по відкритті чату.
  const showDot = !isOpen && !hasOpenedChat && nudgeDismissedAt !== null && !nudgeVisible;

  return (
    <>
      {/* Одноразовий «махнути» кнопкою в момент появи бульбашки — зв'язує бульбашку з лисичкою. */}
      <style>{WIGGLE_KEYFRAMES}</style>
      <button
        type="button"
        onClick={toggle}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
        aria-label={t(isOpen ? "assistant.widget.closeLabel" : "assistant.widget.openLabel")}
        aria-expanded={isOpen}
        className={cn(
          // size-14 = 56px — з запасом над мінімальним тач-таргетом 44px (PLAN §3).
          "fixed right-4 bottom-4 z-40 flex size-14 items-center justify-center rounded-full",
          "bg-card shadow-lg ring-1 ring-border transition-transform duration-150",
          "hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none",
          "motion-reduce:transition-none motion-reduce:hover:translate-y-0",
          "sm:right-6 sm:bottom-6",
          nudgeVisible && "cx-fox-wiggle",
        )}
      >
        <Fox state={foxState} />
        {showDot ? (
          <span
            aria-hidden="true"
            className="absolute top-0.5 right-0.5 size-3 rounded-full bg-primary ring-2 ring-card"
          />
        ) : null}
      </button>

      <FoxNudge />

      {isOpen ? <ChatPanel onClose={close} /> : null}
    </>
  );
}

// Один короткий «махок» при появі бульбашки. Не цикл (це дратувало б і ламало б дисципліну
// акценту). У reduced-motion — нічого.
const WIGGLE_KEYFRAMES = `
  @keyframes cx-fox-wiggle {
    0%, 100% { transform: rotate(0deg); }
    25%      { transform: rotate(-9deg); }
    60%      { transform: rotate(7deg); }
    80%      { transform: rotate(-3deg); }
  }
  .cx-fox-wiggle { animation: cx-fox-wiggle 0.6s ease-in-out 1; }
  @media (prefers-reduced-motion: reduce) {
    .cx-fox-wiggle { animation: none; }
  }
`;
