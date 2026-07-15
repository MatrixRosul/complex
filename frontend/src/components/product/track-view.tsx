"use client";

import { useEffect } from "react";

import { useRecentlyViewedStore } from "@/store/recently-viewed";

/**
 * Записує перегляд товару в історію (localStorage) — блок «Ви переглядали» на головній.
 *
 * ⚠️ НІЧОГО НЕ РЕНДЕРИТЬ. Сторінка товару лишається серверною (RSC): це найдорожча сторінка
 *    сайту для SEO, і робити її client-компонентом заради одного запису в localStorage було б
 *    дорого. Тому — крихітний клієнтський «острівець» на нуль пікселів.
 *
 * ⚠️ Пишемо в useEffect, а не під час рендера: localStorage на сервері не існує, а запис
 *    у сторі під час рендера — це side effect у фазі рендеру (React його виконає двічі
 *    у StrictMode). Ефект з [id] у депсах спрацьовує рівно раз на товар.
 *
 * ⚠️ Ніякої аналітики й трекінгу користувача тут немає: id нікуди не відправляється,
 *    він лишається в браузері.
 */
export function TrackView({ id }: { id: number }) {
  const push = useRecentlyViewedStore((s) => s.push);

  useEffect(() => {
    push(id);
  }, [id, push]);

  return null;
}
