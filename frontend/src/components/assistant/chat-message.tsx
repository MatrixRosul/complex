"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { ProductChip } from "@/components/assistant/product-chip";
import { useT } from "@/i18n/provider";
import { localePath, type Locale } from "@/i18n/config";
import { useAssistantStore, type AssistantMessage } from "@/store/assistant";
import { cn } from "@/lib/utils";

/**
 * Бульбашка чату. Повідомлення асистента дописується токен за токеном (стор просто
 * подовжує `text` — тут ЖОДНОЇ окремої логіки анімації набору, ре-рендер по ходу
 * стріму сам і є ефектом «друкує»).
 *
 * Товари й посилання-CTA — окремі блоки під бульбашкою, а не всередині тексту:
 * модель повертає markdown-подібний текст ОКРЕМО від структурованих `products`/`link`
 * (PLAN §2.8 — «products» подія малює картки, а не маркдаун).
 */
export function ChatMessage({
  message,
  locale,
}: {
  message: AssistantMessage;
  locale: Locale;
}) {
  const closeChat = useAssistantStore((state) => state.close);
  const t = useT();
  const isUser = message.role === "user";
  const isEmptyPending = !isUser && !message.isError && message.text.length === 0;

  return (
    <div className={cn("flex flex-col gap-2", isUser ? "items-end" : "items-start")}>
      <div
        aria-live={isEmptyPending ? "polite" : undefined}
        className={cn(
          "max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap",
          isUser
            ? "rounded-br-sm bg-primary text-primary-foreground"
            : message.isError
              ? "rounded-bl-sm bg-destructive/10 text-destructive"
              : "rounded-bl-sm bg-muted text-foreground",
        )}
      >
        {isEmptyPending ? t("assistant.typingIndicator") : message.text}
      </div>

      {message.products && message.products.length > 0 ? (
        <div className="flex w-full max-w-[95%] flex-col gap-2">
          {message.products.map((product) => (
            <ProductChip key={product.id} product={product} locale={locale} />
          ))}
        </div>
      ) : null}

      {message.link ? (
        <Link
          href={localePath(locale, message.link.url)}
          // ⚠️ ЗАКРИВАЄМО ЧАТ ПРИ ПЕРЕХОДІ. Панель — оверлей поверх сторінки: без цього
          // перехід відбувався, але людина далі бачила чат і не розуміла, чи її взагалі
          // кудись перекинуло. Тепер клік = видимий результат.
          onClick={closeChat}
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          {message.link.label ?? t("assistant.panel.viewCatalog")}
          <ArrowRight className="size-3.5" />
        </Link>
      ) : null}
    </div>
  );
}
