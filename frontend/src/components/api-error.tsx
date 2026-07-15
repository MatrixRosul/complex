"use client";

import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/provider";
import { cn } from "@/lib/utils";

/**
 * Стан «API не відповів».
 *
 * ⚠️ Раніше помилка bulk-запиту в кошику/бажаннях/порівнянні мовчки давала порожній
 * масив — і людина бачила «Кошик порожній» замість «сервіс тимчасово недоступний».
 * Це найгірший з можливих варіантів: користувач вважає, що втратив свій кошик,
 * і йде. Порожньо ≠ зламано, і ці два стани мають виглядати по-різному.
 */
export function ApiErrorState({
  onRetry,
  className,
}: {
  onRetry?: () => void;
  className?: string;
}) {
  const t = useT();

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-3 rounded-lg border border-border bg-muted/40 p-12 text-center",
        className,
      )}
    >
      <AlertTriangle aria-hidden className="size-6 text-stock-out" />
      <p className="text-h3 text-foreground">{t("common.error")}</p>

      {onRetry && (
        <Button variant="secondary" size="xl" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      )}
    </div>
  );
}
