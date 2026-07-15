"use client";

import { useState } from "react";
import { Fox } from "@/components/assistant/fox";
import { ChatPanel } from "@/components/assistant/chat-panel";
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

  if (!hydrated) return null;

  const foxState: FoxVisualState = isStreaming
    ? streamPhase === "talking"
      ? "talking"
      : "thinking"
    : hovered
      ? "hover"
      : "idle";

  return (
    <>
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
        )}
      >
        <Fox state={foxState} />
      </button>

      {isOpen ? <ChatPanel onClose={close} /> : null}
    </>
  );
}
