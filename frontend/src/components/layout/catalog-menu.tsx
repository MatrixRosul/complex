"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * СПІЛЬНИЙ СТАН КАТАЛОГУ — один на кнопку «Каталог» у хедері й на розгорнутий
 * список категорій на головній.
 *
 * ⚠️ НАВІЩО КОНТЕКСТ, А НЕ ДВА ЛОКАЛЬНІ СТАНИ. На головній ці два елементи були
 * двома НЕЗАЛЕЖНИМИ меню: кнопка відкривала свою панель зі своєю лівою колонкою,
 * і та лягала поверх сайдбара зі зсувом — візуально «дві менюшки одна на одній».
 * Замовник сформулював вимогу як «має бути НІБИ ОДИН І ТОЙ САМИЙ каталог».
 * Один стан на двох — єдиний спосіб це гарантувати: розсинхрону не може бути
 * в принципі, бо синхронізувати нічого.
 *
 * Хедер лежить у layout, сайдбар — у page, спільного React-батька нижче
 * <Providers> у них немає. Тому стан живе тут.
 *
 * ⚠️ ТАЙМЕРИ ХОВЕРА ТЕЖ ТУТ, а не в компонентах. Якби кожен тримав свій, перехід
 * мишею з кнопки в сайдбар виглядав би так: кнопка завела таймер закриття (200 мс),
 * сайдбар відкрив меню — і таймер кнопки все одно закрив би його вже після цього.
 * Спільний таймер скасовується будь-ким із двох, тож переходи не блимають.
 *   150 мс на відкриття — щоб меню не спалахувало, коли миша пролітає повз;
 *   200 мс на закриття  — щоб воно не зривалось при діагональному русі до підгруп.
 */
type CatalogMenuValue = {
  open: boolean;
  /** Індекс кореневої категорії, підгрупи якої показані. */
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  /** Ховер: відкрити з затримкою / закрити з затримкою. */
  openSoon: () => void;
  closeSoon: () => void;
  /** Без затримки — для кліку, фокуса й Esc. */
  openNow: () => void;
  closeNow: () => void;
  toggle: () => void;
  /** Кнопка-тригер у хедері: провайдер повертає на неї фокус після Esc. */
  triggerRef: React.RefObject<HTMLButtonElement | null>;
  /**
   * Чи є на цій сторінці inline-каталог (сайдбар головної) — незалежно від скролу.
   * Якщо є, dropdown НЕ малюється НІКОЛИ: відкриття прокручує сторінку вгору й
   * розгортає той самий сайдбар. Саме цього просив замовник — «щоб не було
   * 2 різних форм каталогів».
   */
  hasInline: boolean;
  /** Ref-callback: сайдбар реєструє свій <section>, провайдер вішає на нього observer. */
  setInlineEl: (el: HTMLElement | null) => void;
};

const CatalogMenuContext = createContext<CatalogMenuValue | null>(null);

export function useCatalogMenu(): CatalogMenuValue {
  const ctx = useContext(CatalogMenuContext);
  if (!ctx) throw new Error("useCatalogMenu має викликатись усередині <CatalogMenuProvider>");
  return ctx;
}

export function CatalogMenuProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [hasInline, setHasInline] = useState(false);
  const [inlineVisible, setInlineVisible] = useState(false);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const observer = useRef<IntersectionObserver | null>(null);
  const inlineEl = useRef<HTMLElement | null>(null);

  const clearTimers = useCallback(() => {
    if (openTimer.current) clearTimeout(openTimer.current);
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  const openNow = useCallback(() => {
    clearTimers();
    setOpen(true);
  }, [clearTimers]);

  const closeNow = useCallback(() => {
    clearTimers();
    setOpen(false);
  }, [clearTimers]);

  const openSoon = useCallback(() => {
    clearTimers();
    openTimer.current = setTimeout(() => setOpen(true), 150);
  }, [clearTimers]);

  const closeSoon = useCallback(() => {
    clearTimers();
    closeTimer.current = setTimeout(() => setOpen(false), 200);
  }, [clearTimers]);

  const toggle = useCallback(() => {
    clearTimers();
    setOpen((v) => !v);
  }, [clearTimers]);

  /**
   * Реєстрація inline-сайдбара + IntersectionObserver на нього.
   *
   * `hasInline` каже, чи є сайдбар на сторінці ВЗАГАЛІ (тобто чи це головна), а
   * `inlineVisible` — чи видно його ЗАРАЗ. Перше вирішує, який каталог показувати
   * (сайдбар vs dropdown), друге — чи треба спершу прокрутити сторінку вгору.
   */
  const setInlineEl = useCallback((el: HTMLElement | null) => {
    observer.current?.disconnect();
    inlineEl.current = el;
    setHasInline(Boolean(el));
    if (!el) {
      setInlineVisible(false); // сайдбара немає (не головна) → inline недоступний
      return;
    }
    const obs = new IntersectionObserver(([entry]) => setInlineVisible(entry.isIntersecting), {
      threshold: 0,
    });
    obs.observe(el);
    observer.current = obs;
  }, []);

  /**
   * ВІДКРИЛИ КАТАЛОГ, А САЙДБАР ЗА ЕКРАНОМ → ВЕЗЕМО СТОРІНКУ ДО НЬОГО.
   *
   * ⚠️ Раніше в цьому випадку малювався окремий dropdown зі стікі-хедера — і саме він
   * був другою «формою каталогу», на яку поскаржився замовник: та сама навігація, але
   * в іншому місці й іншого вигляду. Тепер форма одна на всю головну, а «дотягнутись»
   * до неї з будь-якої точки сторінки — задача скролу, а не другого компонента.
   */
  useEffect(() => {
    if (!open || !hasInline || inlineVisible) return;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [open, hasInline, inlineVisible]);

  useEffect(
    () => () => {
      clearTimers();
      observer.current?.disconnect();
    },
    [clearTimers],
  );

  /**
   * Esc і клік повз — теж на рівні провайдера, бо «повз» тепер означає різне:
   * на головній меню складається з кнопки В ХЕДЕРІ й панелі В СТОРІНЦІ, і жоден
   * з них не містить іншого в DOM.
   *
   * ⚠️ Перевірка йде по `[data-catalog-menu]`, а не по refs. З refs вийшов би баг:
   * на головній мегаменю своєї панелі не малює, тобто його panelRef порожній —
   * і клік по підгрупі в сайдбарі рахувався б як «повз». Меню закривалось би на
   * mousedown, посилання зникало б з-під курсора ДО того, як спрацює click,
   * і перехід у підкатегорію просто не відбувався б.
   */
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      clearTimers();
      setOpen(false);
      triggerRef.current?.focus();
    };

    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (target?.closest?.("[data-catalog-menu]")) return;
      clearTimers();
      setOpen(false);
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open, clearTimers]);

  const value = useMemo(
    () => ({
      open,
      activeIndex,
      setActiveIndex,
      openSoon,
      closeSoon,
      openNow,
      closeNow,
      toggle,
      triggerRef,
      hasInline,
      setInlineEl,
    }),
    [open, activeIndex, openSoon, closeSoon, openNow, closeNow, toggle, hasInline, setInlineEl],
  );

  return (
    <CatalogMenuContext.Provider value={value}>
      {children}
      {/*
       * ПІДКЛАДКА ПІД ВІДКРИТИМ КАТАЛОГОМ: тло трохи темніє й розмивається, щоб каталог
       * читався як окремий шар (прохання замовника).
       *
       * ⚠️ ТУТ КОЛИСЬ БУВ ОВЕРЛЕЙ І ЙОГО ЗНЕСЛИ — двічі не наступаємо на ті самі граблі:
       *   1. ЗАЛИПАННЯ. Той оверлей був ДИТИНОЮ контейнера з `onMouseLeave`: курсор над
       *      ним з погляду DOM лишався «всередині», mouseleave не спрацьовував і меню
       *      не закривалось. Цей — сусід усього дерева (рендериться з провайдера) і має
       *      `pointer-events-none`, тож жодних подій не перехоплює взагалі.
       *   2. ЛАГИ. Причина була в товстому blur на весь в'юпорт. Тут 2px — композиція
       *      одноразова, під час руху миші нічого не перераховується.
       * z-30: нижче за хедер (z-40) і за сам каталог, але вище за контент сторінки.
       */}
      {open && (
        <div
          aria-hidden
          className="pointer-events-none fixed inset-0 z-30 bg-foreground/10 backdrop-blur-[2px]"
        />
      )}
    </CatalogMenuContext.Provider>
  );
}
