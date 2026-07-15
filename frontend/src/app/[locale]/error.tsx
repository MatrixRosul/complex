"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/provider";

/**
 * Межа помилок для всіх сторінок локалі.
 *
 * ⚠️ Ловить рівно те, що зараз найімовірніше: бекенд не відповів (ApiError зі статусом 0),
 * віддав 500, або next/image отримав хост, якого немає в remotePatterns. Без цього файлу
 * будь-який із цих випадків = БІЛА СТОРІНКА (у проді Next не показує ні тексту, ні стека).
 *
 * `reset()` перемонтовує сегмент — на транзієнтній помилці (перезапуск gunicorn) сторінка
 * оживає без перезавантаження вкладки.
 */
export default function LocaleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useT();

  useEffect(() => {
    console.error("[complex] page error:", error);
  }, [error]);

  return (
    <div className="container-complex flex flex-col items-center gap-4 py-24 text-center">
      <AlertTriangle aria-hidden className="size-8 text-stock-out" />

      <h1 className="text-h1 text-foreground">{t("common.error")}</h1>

      <p className="max-w-md text-sm text-muted-foreground">
        {t("errorPage.hint")}
      </p>

      <Button size="xl" onClick={reset} className="mt-2">
        {t("common.retry")}
      </Button>
    </div>
  );
}
