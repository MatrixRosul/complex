"use client";

import { useEffect, useRef, useState } from "react";
import { Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Fox } from "@/components/assistant/fox";
import { ChatMessage } from "@/components/assistant/chat-message";
import { streamChat, type AssistantEvent } from "@/lib/api/assistant";
import { ApiError } from "@/lib/api/types";
import { useAssistantStore } from "@/store/assistant";
import { useVisualViewport } from "@/hooks/use-visual-viewport";
import { useLocale, useT } from "@/i18n/provider";
import { localeToApiLang } from "@/i18n/config";
import type { TranslationKey } from "@/i18n/dictionary";
import { cn } from "@/lib/utils";

/**
 * Дзеркалить `ASSISTANT_MAX_INPUT_CHARS` (backend/assistant/conf.py, PLAN §2.2) —
 * лише щоб людина побачила ліміт одразу в полі, а не після 400 з бекенда. Джерело
 * правди все одно бекенд: якщо конфіг там зміниться, це число просто розсинхронізується
 * з реальним лімітом на кілька символів, не зламає нічого критичного.
 */
const MAX_INPUT_LENGTH = 1000;

const EXAMPLE_PROMPT_KEYS: TranslationKey[] = [
  "assistant.examples.fridges",
  "assistant.examples.compare",
  "assistant.examples.delivery",
  "assistant.examples.budget",
];

/**
 * Чат-панель. СВІДОМО НЕ Dialog і НЕ Sheet (PLAN §3) — обидва в проєкті модальні
 * (backdrop + focus-trap), а користувач має могти клікнути товар у чаті й піти на
 * картку, не закриваючи розмову. Тому звичайний fixed-блок: на десктопі ~380×560
 * прив'язаний до кнопки-лисички, на мобільному — на всю ширину/висоту (`inset-0`).
 *
 * Ця панель — ЄДИНЕ місце, що кличе `streamChat()`: приймає SSE-подію за подією
 * і транслює їх у зміни `store/assistant.ts`, крок за кроком, як описано в PLAN §2.8.
 */
export function ChatPanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const locale = useLocale();
  const apiLang = localeToApiLang[locale];

  const messages = useAssistantStore((s) => s.messages);
  const isStreaming = useAssistantStore((s) => s.isStreaming);
  const streamPhase = useAssistantStore((s) => s.streamPhase);
  const sessionToken = useAssistantStore((s) => s.sessionToken);

  const addUserMessage = useAssistantStore((s) => s.addUserMessage);
  const addAssistantPlaceholder = useAssistantStore((s) => s.addAssistantPlaceholder);
  const appendAssistantToken = useAssistantStore((s) => s.appendAssistantToken);
  const setAssistantProducts = useAssistantStore((s) => s.setAssistantProducts);
  const setAssistantLink = useAssistantStore((s) => s.setAssistantLink);
  const setAssistantError = useAssistantStore((s) => s.setAssistantError);
  const setSessionToken = useAssistantStore((s) => s.setSessionToken);
  const setStreaming = useAssistantStore((s) => s.setStreaming);
  const setStreamPhase = useAssistantStore((s) => s.setStreamPhase);

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const viewport = useVisualViewport();

  // Автоскрол донизу — і на нове повідомлення, і на кожен токен, що дописується
  // в останнє (масив messages — нова референція на кожен `appendAssistantToken`).
  //
  // ⚠️ Скролимо ВИКЛЮЧНО вьюпорт історії, руками через scrollTop. Тут раніше стояв
  // `bottomRef.scrollIntoView()`, і це був баг: scrollIntoView скролить УСІХ
  // scrollable-предків, включно з документом. На мобільному з відкритою клавіатурою
  // браузер і так підскролює сторінку до сфокусованого поля — і на кожен токен стріму
  // сюди прилітав ще один скрол документа. Сторінка сіпалась під пальцем, а панель
  // «їздила». Пряме присвоєння scrollTop фізично не може зачепити нічого, крім історії.
  useEffect(() => {
    const view = scrollRef.current?.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]',
    );
    if (view) view.scrollTop = view.scrollHeight;
  }, [messages]);

  // Автофокус — ТІЛЬКИ на десктопі. На мобільному `autoFocus` (який тут і стояв)
  // піднімав клавіатуру ще до того, як людина щось прочитала: панель відкривалась
  // уже наполовину з'їденою, з прикладами питань під клавіатурою. На телефоні поле
  // фокусується тапом — тоді клавіатура доречна й очікувана.
  useEffect(() => {
    if (window.matchMedia("(min-width: 640px)").matches) inputRef.current?.focus();
  }, []);

  function applyEvent(event: AssistantEvent, assistantId: string) {
    switch (event.type) {
      case "session":
        setSessionToken(event.token);
        break;
      case "thinking":
      case "tool":
        setStreamPhase("thinking");
        break;
      case "token":
        setStreamPhase("talking");
        appendAssistantToken(assistantId, event.text);
        break;
      case "products":
        setAssistantProducts(assistantId, event.items);
        break;
      case "link":
        setAssistantLink(assistantId, { url: event.url, label: event.label });
        break;
      case "error":
        setAssistantError(assistantId, event.message || t("assistant.error.generic"));
        break;
      case "done":
        break;
    }
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    setDraft("");
    addUserMessage(trimmed);
    const assistantId = addAssistantPlaceholder();
    setStreaming(true);
    setStreamPhase("thinking");

    try {
      for await (const event of streamChat({
        message: trimmed,
        locale: apiLang,
        sessionToken,
      })) {
        applyEvent(event, assistantId);
      }
    } catch (cause) {
      // Мережа впала / бекенд відповів не 2xx ще до початку стріму — ApiError.
      // Помилки ВСЕРЕДИНІ стріму (бюджет, refusal) приходять подією `error`,
      // сюди не потрапляють (applyEvent обробляє їх окремо, без throw).
      const message =
        cause instanceof ApiError ? t("assistant.error.network") : t("assistant.error.generic");
      setAssistantError(assistantId, message);
    } finally {
      setStreaming(false);
      setStreamPhase("idle");
    }
  }

  const headerFoxState = isStreaming ? (streamPhase === "talking" ? "talking" : "thinking") : "idle";

  return (
    <div
      role="region"
      aria-label={t("assistant.panel.title")}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      style={
        {
          // Фолбек 100dvh/0px тримає перший рендер і випадок, коли VisualViewport
          // недоступний: поводиться рівно як старий inset-0, не гірше.
          "--chat-h": viewport ? `${viewport.height}px` : "100dvh",
          "--chat-top": viewport ? `${viewport.offsetTop}px` : "0px",
        } as React.CSSProperties
      }
      className={cn(
        "fixed z-50 flex flex-col bg-popover text-popover-foreground",
        // Мобільний: розпираємо по візуальному вьюпорту, а не по лейаут-вьюпорту.
        // Сторони задані явно (не inset-0), щоб кожну можна було перебити sm:-аналогом —
        // inset-shorthand і top-longhand конфліктують у каскаді непередбачувано.
        "top-[var(--chat-top)] left-0 h-[var(--chat-h)] w-full",
        "sm:top-auto sm:left-auto sm:right-6 sm:bottom-24 sm:h-[560px] sm:w-[380px]",
        "sm:rounded-xl sm:border sm:border-border sm:shadow-2xl",
      )}
    >
      {/* ── Хедер ─────────────────────────────────────────────────────── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Fox state={headerFoxState} className="size-8 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">
            {t("assistant.panel.title")}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {t("assistant.panel.subtitle")}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t("common.close")}
          onClick={onClose}
        >
          <X />
        </Button>
      </div>

      {/* ── Історія ───────────────────────────────────────────────────── */}
      {/* overscroll-contain: догортавши історію до краю, палець не починає тягнути
          сторінку ПІД панеллю (scroll chaining). На мобільному це читалось як
          «панель поїхала разом із сайтом». */}
      <ScrollArea
        ref={scrollRef}
        className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]]:overscroll-contain"
      >
        <div className="flex flex-col gap-3 p-4">
          {messages.length === 0 ? (
            <EmptyState onPick={send} />
          ) : (
            messages.map((message) => (
              <ChatMessage key={message.id} message={message} locale={locale} />
            ))
          )}
        </div>
      </ScrollArea>

      {/* ── Ввід ──────────────────────────────────────────────────────── */}
      <form
        className="flex shrink-0 items-center gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        {/* ⚠️ Поле СВІДОМО не disabled під час стріму (раніше було `disabled={isStreaming}`).
            disabled знімає фокус — а на мобільному втрата фокуса закриває клавіатуру.
            Тобто клавіатура згорталась після КОЖНОГО відправленого повідомлення, і щоб
            дописати наступне, треба було тапати по полю заново. Захист від подвійної
            відправки лишається там, де й був: `send()` виходить на `isStreaming`, а
            кнопка Send disabled — блокувати ще й саме поле було зайвим. */}
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value.slice(0, MAX_INPUT_LENGTH))}
          placeholder={t("assistant.panel.placeholder")}
          aria-label={t("assistant.panel.placeholder")}
          maxLength={MAX_INPUT_LENGTH}
        />
        <Button
          type="submit"
          size="icon"
          aria-label={t("assistant.panel.send")}
          disabled={isStreaming || draft.trim().length === 0}
        >
          <Send />
        </Button>
      </form>
    </div>
  );
}

/** Порожній стан — 3-4 приклади питань, які реально відповідають каталогу (PLAN §3). */
function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  const t = useT();

  return (
    <div className="flex flex-col items-center gap-4 py-6 text-center">
      <Fox state="idle" className="size-16" />
      <div>
        <p className="text-sm font-medium text-foreground">{t("assistant.empty.title")}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t("assistant.empty.subtitle")}</p>
      </div>
      <div className="flex w-full flex-col gap-2">
        {EXAMPLE_PROMPT_KEYS.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => onPick(t(key))}
            className="rounded-lg border border-border bg-card px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-muted"
          >
            {t(key)}
          </button>
        ))}
      </div>
    </div>
  );
}
