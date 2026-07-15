"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * ⚠️ У localStorage — ТІЛЬКИ id (див. пояснення у store/cart.ts).
 * Ціни й наявність тягне GET /catalog/products/bulk на сторінці бажань:
 * список бажань живе місяцями, ціна в ньому протухає гарантовано.
 *
 * Особистого кабінету в проєкті НЕМАЄ — бажання існують лише в браузері.
 */

type WishlistState = {
  ids: number[];
  toggle: (id: number) => void;
  remove: (id: number) => void;
  clear: () => void;
};

export const useWishlistStore = create<WishlistState>()(
  persist(
    (set) => ({
      ids: [],

      toggle: (id) =>
        set((state) => ({
          ids: state.ids.includes(id)
            ? state.ids.filter((x) => x !== id)
            : [...state.ids, id],
        })),

      remove: (id) => set((state) => ({ ids: state.ids.filter((x) => x !== id) })),

      clear: () => set({ ids: [] }),
    }),
    {
      name: "complex.wishlist",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ ids: state.ids }),
    },
  ),
);

export const selectWishlistCount = (state: WishlistState) => state.ids.length;
export const selectIsWished = (id: number) => (state: WishlistState) =>
  state.ids.includes(id);
