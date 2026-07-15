"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * ⚠️ У localStorage — ТІЛЬКИ id (див. пояснення у store/cart.ts).
 * Характеристики, ціни й наявність тягне GET /catalog/products/bulk на сторінці порівняння.
 */

/** Ліміт з DESIGN_SYSTEM §4.7: більше 4 колонок не влазить у горизонтальний скрол. */
export const COMPARE_LIMIT = 4;

type CompareState = {
  ids: number[];
  toggle: (id: number) => void;
  remove: (id: number) => void;
  clear: () => void;
  /** false, якщо ліміт вичерпано і товару ще немає в списку. */
  canAdd: (id: number) => boolean;
};

export const useCompareStore = create<CompareState>()(
  persist(
    (set, get) => ({
      ids: [],

      toggle: (id) =>
        set((state) => {
          if (state.ids.includes(id)) {
            return { ids: state.ids.filter((x) => x !== id) };
          }
          if (state.ids.length >= COMPARE_LIMIT) return state;
          return { ids: [...state.ids, id] };
        }),

      remove: (id) => set((state) => ({ ids: state.ids.filter((x) => x !== id) })),

      clear: () => set({ ids: [] }),

      canAdd: (id) => {
        const { ids } = get();
        return ids.includes(id) || ids.length < COMPARE_LIMIT;
      },
    }),
    {
      name: "complex.compare",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ ids: state.ids }),
    },
  ),
);

export const selectCompareCount = (state: CompareState) => state.ids.length;
export const selectIsComparing = (id: number) => (state: CompareState) =>
  state.ids.includes(id);
