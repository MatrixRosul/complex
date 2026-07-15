"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * «Ви нещодавно переглядали» — історія переглядів у браузері.
 *
 * ⚠️ У localStorage — ТІЛЬКИ id (як у cart.ts / wishlist.ts). Ціни, назви й наявність тягне
 *    POST /products/bulk на головній. Це не стиль, а захист: історія переглядів живе МІСЯЦЯМИ,
 *    а синк міняє ціни 4×/добу. Зберегти тут ціну = гарантовано показати людині суму, якої
 *    вже немає (а вона ще й пам'ятає, що бачила саме її).
 *
 * ⚠️ Особистого кабінету в проєкті НЕМАЄ — історія існує лише в цьому браузері.
 */

/** Скільки товарів пам'ятаємо. Більше — це вже не «нещодавно». */
export const RECENTLY_VIEWED_LIMIT = 12;

type RecentlyViewedState = {
  /** Найновіші СПЕРЕДУ (ids[0] — останній переглянутий). */
  ids: number[];
  push: (id: number) => void;
  /** Прибрати id, який бекенд більше не віддає (товар зняли з продажу). */
  remove: (id: number) => void;
  clear: () => void;
};

export const useRecentlyViewedStore = create<RecentlyViewedState>()(
  persist(
    (set) => ({
      ids: [],

      /**
       * Записує перегляд товару.
       *
       * ⚠️ ПОВТОРНИЙ перегляд не дублює запис, а ПІДНІМАЄ товар нагору: спершу викидаємо id
       *    з масиву, потім кладемо спереду. Без фільтра список із 12 позицій швидко
       *    перетворився б на дванадцять копій одного холодильника, який людина відкривала
       *    дванадцять разів.
       *
       * ⚠️ Обрізаємо ЗАВЖДИ (slice), а не «коли переповниться»: інакше історія росла б
       *    безмежно й у localStorage лежала б стрічка на тисячу id.
       */
      push: (id) =>
        set((state) => {
          if (state.ids[0] === id) return state; // вже зверху — не смикаємо сховище
          return {
            ids: [id, ...state.ids.filter((x) => x !== id)].slice(0, RECENTLY_VIEWED_LIMIT),
          };
        }),

      /**
       * ⚠️ Потрібен, бо товар можуть зняти з продажу. Тоді /products/bulk просто не поверне
       *    його (він піде в `unavailable_items`), а id інакше лежав би в localStorage вічно,
       *    з'їдаючи один із 12 слотів історії назавжди.
       */
      remove: (id) => set((state) => ({ ids: state.ids.filter((x) => x !== id) })),

      clear: () => set({ ids: [] }),
    }),
    {
      name: "complex.recently-viewed",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ ids: state.ids }),
    },
  ),
);
