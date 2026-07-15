"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n/provider";
import { useHydrated } from "@/hooks/use-hydrated";

/**
 * Перемикач теми (DESIGN_SYSTEM §6).
 *
 * Дефолт — світла. Клас .dark ставиться на <html> ДО гідратації інлайн-скриптом
 * next-themes, тому спалаху білим при завантаженні темної теми немає.
 */
export function ThemeToggle() {
  const t = useT();
  const { theme, setTheme } = useTheme();
  const hydrated = useHydrated();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="icon-lg" aria-label={t("theme.toggle")}>
            {/* До гідратації тема невідома — показуємо нейтральну іконку,
                інакше React зарепортить mismatch. */}
            {!hydrated ? (
              <Sun className="size-5" />
            ) : theme === "dark" ? (
              <Moon className="size-5" />
            ) : theme === "system" ? (
              <Monitor className="size-5" />
            ) : (
              <Sun className="size-5" />
            )}
          </Button>
        }
      />

      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun className="size-4" />
          {t("theme.light")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon className="size-4" />
          {t("theme.dark")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor className="size-4" />
          {t("theme.system")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
