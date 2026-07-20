"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * ⚠️⚠️ КРИТИЧНО. У localStorage лежить ТІЛЬКИ {id, qty}.
 *
 * Ні ціни, ні назви, ні наявності, ні фото. Причина проста: кошик живе тижнями,
 * а прайс синкається щодня. Якби ми зберегли ціну в localStorage, покупець побачив би
 * стару ціну, додав би товар у замовлення — і на чекауті сума не зійшлася б з БД.
 * Гірший варіант: він би цю стару ціну ще й вимагав.
 *
 * Тому все, що показується на екрані (ціна, наявність, фото, чи можна частинами),
 * ЗАВЖДИ приходить з POST /api/cart/preview. Див. hooks/use-cart-preview.ts.
 *
 * Той самий принцип — у compare.ts і wishlist.ts.
 */

export type CartLine = {
  id: number;
  qty: number;
};

const MAX_QTY = 99;

type CartState = {
  lines: CartLine[];
  /**
   * Чи відкрита панель кошика (CartDrawer).
   *
   * ⚠️ Живе в цьому ж сторі, але НЕ потрапляє в localStorage (див. partialize нижче):
   * інакше кошик відкривався б сам при кожному заході на сайт.
   */
  isOpen: boolean;
  setOpen: (open: boolean) => void;
  /** Додати й одразу показати кошик — сценарій кнопки «Купити». */
  addAndOpen: (id: number, qty?: number) => void;
  add: (id: number, qty?: number) => void;
  setQty: (id: number, qty: number) => void;
  remove: (id: number) => void;
  clear: () => void;
  /** Прибирає позиції, які бекенд позначив як недоступні (preview.unavailable_items). */
  pruneUnavailable: (ids: number[]) => void;
};

const clampQty = (qty: number) => Math.max(1, Math.min(Math.trunc(qty) || 1, MAX_QTY));

export const useCartStore = create<CartState>()(
  persist(
    (set, get) => ({
      lines: [],
      isOpen: false,

      setOpen: (open) => set({ isOpen: open }),

      addAndOpen: (id, qty = 1) => {
        get().add(id, qty);
        set({ isOpen: true });
      },

      add: (id, qty = 1) =>
        set((state) => {
          const existing = state.lines.find((l) => l.id === id);
          if (existing) {
            return {
              lines: state.lines.map((l) =>
                l.id === id ? { ...l, qty: clampQty(l.qty + qty) } : l,
              ),
            };
          }
          return { lines: [...state.lines, { id, qty: clampQty(qty) }] };
        }),

      setQty: (id, qty) =>
        set((state) => ({
          lines: state.lines.map((l) => (l.id === id ? { ...l, qty: clampQty(qty) } : l)),
        })),

      remove: (id) => set((state) => ({ lines: state.lines.filter((l) => l.id !== id) })),

      clear: () => set({ lines: [] }),

      pruneUnavailable: (ids) =>
        set((state) => ({ lines: state.lines.filter((l) => !ids.includes(l.id)) })),
    }),
    {
      name: "complex.cart",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      // Явний whitelist: навіть якщо хтось додасть у стор поле з ціною,
      // воно не потрапить у localStorage.
      partialize: (state) => ({ lines: state.lines }),
    },
  ),
);

/** Кількість позицій (не одиниць) — для лічильника в хедері. */
export const selectCartCount = (state: CartState) => state.lines.length;

/** Загальна кількість одиниць. */
export const selectCartUnits = (state: CartState) =>
  state.lines.reduce((sum, l) => sum + l.qty, 0);

export const selectIsInCart = (id: number) => (state: CartState) =>
  state.lines.some((l) => l.id === id);
