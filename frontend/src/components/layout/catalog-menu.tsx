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
 * ⚠️ ХТО ВІДКРИВ — ВАЖЛИВІШЕ, НІЖ «ВІДКРИТО ЧИ НІ». Тому стан не boolean, а `openBy`:
 * "click" | "hover" | null. Дві вимоги замовниці виглядають як взаємовиключні, і без
 * цього поля вони такими й були:
 *
 *   1. КНОПКА В ШАПЦІ — САМЕ КНОПКА. «Зараз воно по наведенню активується і коли
 *      прибираю звідти мишку — зникає». Кнопка ховер НЕ слухає взагалі.
 *   2. СПИСОК У САЙДБАРІ НА ГОЛОВНІЙ — НАВПАКИ, ЖИВИЙ. «Боковому меню повернути
 *      вспливаючу інтерактивність, щоб розгортало підкатегорії при наведенні».
 *
 * Конфлікт був би тут: відкрили КНОПКОЮ, повели мишу до сайдбара й вийшли з нього —
 * і меню згорталось би, тобто пункт 1 знову зламався б. `openBy` це розрізняє:
 * ховер закриває ЛИШЕ те, що ховер і відкрив (`openBy === "hover"`). Відкрите кліком
 * тримається до явної дії: клік по кнопці (toggle), Esc, клік повз меню.
 *
 * Таймери 150/200 мс — не косметика: без затримки на відкритті каталог спалахує від
 * випадкового проходу курсора, без затримки на закритті — гасне на щілині між
 * колонками, поки миша йде зі списку в підгрупи.
 */
type CatalogMenuValue = {
  open: boolean;
  /** Індекс кореневої категорії, підгрупи якої показані. */
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  /** Явні дії — клік по кнопці, Esc, клік повз. Скидають «відкрито ховером». */
  openNow: () => void;
  closeNow: () => void;
  toggle: () => void;
  /**
   * Ховер по списку сайдбара на головній. Відкриває із затримкою; якщо каталог уже
   * відкритий кліком — не чіпає нічого, щоб не «розжалувати» його до ховерного.
   */
  openByHover: () => void;
  /** Курсор пішов із сайдбара: згортає ЛИШЕ те, що сам ховер і розгорнув. */
  closeByHover: () => void;
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

/** Затримка перед розгортанням по ховеру — щоб випадковий прохід курсора не відкривав. */
const HOVER_OPEN_MS = 150;
/** Затримка перед згортанням — щоб меню не гасло на щілині між колонками. */
const HOVER_CLOSE_MS = 200;

export function CatalogMenuProvider({ children }: { children: React.ReactNode }) {
  // null = закрито. Рядок = ЧИМ відкрито; від цього залежить, чи має право закрити ховер.
  const [openBy, setOpenBy] = useState<"click" | "hover" | null>(null);
  const open = openBy !== null;
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
    setOpenBy("click");
  }, [clearTimers]);

  const closeNow = useCallback(() => {
    clearTimers();
    setOpenBy(null);
  }, [clearTimers]);

  const toggle = useCallback(() => {
    clearTimers();
    setOpenBy((v) => (v ? null : "click"));
  }, [clearTimers]);

  // ⚠️ `v ?? "hover"` — а не просто "hover": якщо каталог уже відкритий КЛІКОМ, ховер
  //    лишає джерело як є. Інакше проведення мишею по сайдбару перетворювало б
  //    закріплене кліком меню на ховерне, і воно згорталось би від виходу курсора.
  const openByHover = useCallback(() => {
    clearTimers();
    openTimer.current = setTimeout(() => setOpenBy((v) => v ?? "hover"), HOVER_OPEN_MS);
  }, [clearTimers]);

  const closeByHover = useCallback(() => {
    clearTimers();
    closeTimer.current = setTimeout(
      () => setOpenBy((v) => (v === "hover" ? null : v)),
      HOVER_CLOSE_MS,
    );
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
      setOpenBy(null);
      triggerRef.current?.focus();
    };

    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (target?.closest?.("[data-catalog-menu]")) return;
      clearTimers();
      setOpenBy(null);
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
      openNow,
      closeNow,
      toggle,
      openByHover,
      closeByHover,
      triggerRef,
      hasInline,
      setInlineEl,
    }),
    [
      open,
      activeIndex,
      openNow,
      closeNow,
      toggle,
      openByHover,
      closeByHover,
      hasInline,
      setInlineEl,
    ],
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
       * ⚠️ ШАРИ: підкладка z-20 < каталог z-30 < ХЕДЕР z-40 < dropdown z-50.
       * Каталог мусить бути НИЖЧЕ за хедер: спершу він мав z-40, як і хедер, і при
       * рівних z виграє той, хто пізніше в DOM — тобто сайдбар налазив на шапку при
       * прокрутці. Тепер він гарантовано під нею й гарантовано над підкладкою.
       */}
      {open && (
        <div
          aria-hidden
          className="pointer-events-none fixed inset-0 z-20 bg-foreground/10 backdrop-blur-[2px]"
        />
      )}
    </CatalogMenuContext.Provider>
  );
}
