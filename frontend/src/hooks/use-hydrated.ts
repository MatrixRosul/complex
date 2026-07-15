"use client";

import { useSyncExternalStore } from "react";

/** Стор, який ніколи не змінюється — підписка порожня. */
const subscribe = () => () => {};

/**
 * true лише після гідратації.
 *
 * Навіщо: zustand/persist читає localStorage тільки в браузері. Якщо відрендерити
 * лічильник кошика одразу, SSR віддасть 0, клієнт — 3, і React зарепортить
 * hydration mismatch. Тому компоненти, що читають persist-стори, до гідратації
 * рендерять скелет.
 *
 * ⚠️ Реалізовано через useSyncExternalStore, а не useState+useEffect: у другому
 * варіанті компонент рендериться двічі (false → ефект → true) і React 19 справедливо
 * лається react-hooks/set-state-in-effect. Тут серверний снапшот — false,
 * клієнтський — true, зайвого рендера немає.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true, // клієнт
    () => false, // сервер / перший рендер до гідратації
  );
}
