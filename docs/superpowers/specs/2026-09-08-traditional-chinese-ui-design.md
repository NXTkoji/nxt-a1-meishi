# Traditional Chinese UI language

**Date:** 2026-09-08
**Branch:** `feat/zh-tw-ui-language`

## Problem

The app has two independent notions of "language":

1. **Card data language** — `ja / zh / zh-TW / en / ko`, attached to each `PersonName`,
   `OrganizationName` and `PositionDetail`. Traditional Chinese was **already fully
   supported** here: the Claude Vision prompt tags it, the DB stores it, and
   `LANG_OPTIONS` in `ParsedCardEditor` offers it.
2. **App UI language** — `frontend/src/i18n.ts`, which only had `ja` and `en`, driven by a
   two-way toggle button in the nav bar.

Only the second was missing Traditional Chinese. Most people handling these cards are in
Taipei and read Chinese more comfortably than Japanese or English.

## Scope

Frontend only. No backend, DB, migration or parser changes.

## Design

### `frontend/src/i18n.ts`

- `LANG_CYCLE = ['ja', 'en', 'zh-TW'] as const` is exported as the single source of truth
  for both the switcher order and the `Lang` union (`type Lang = (typeof LANG_CYCLE)[number]`).
  Adding a fourth language means editing one line plus adding a block.
- A `'zh-TW'` block with all 165 keys, in Taiwan business Chinese (台灣用語).
- `langToggle` keeps its existing "name the *next* language" semantics:
  `ja → 'English'`, `en → '繁體中文'`, `zh-TW → '日本語'`.

**Key parity is enforced by the compiler, not by discipline.** `Translations` is derived
from the `ja` block, and `LangContext` assigns `translations[lang]` to it. A missing key in
any locale block fails `tsc -b`. Verified by deliberately deleting `mergeError` from the
zh-TW block and confirming the expected error:

```
src/LangContext.tsx(35,42): error TS2322: … Property 'mergeError' is missing in type
'{ … readonly navCollection: "名片庫" … }' but required in type '{ … }'
```

### `frontend/src/LangContext.tsx`

The stored-preference check was `stored === 'en' || stored === 'ja'`, which would silently
reset a Chinese preference to English. Now validates against `LANG_CYCLE`.

`document.documentElement.lang` needed no change — `zh-TW` is a valid BCP-47 tag, and
setting it correctly makes the browser pick Traditional Chinese glyph variants rather than
Japanese ones for shared Han characters.

### `frontend/src/App.tsx`

Toggle → cycle:

```ts
setLang(LANG_CYCLE[(LANG_CYCLE.indexOf(lang) + 1) % LANG_CYCLE.length])
```

Button markup and styling unchanged.

### `frontend/src/pages/CollectionPage.tsx`

`const _intlNames = new Intl.DisplayNames(['ja', 'en'], …)` was a module-level constant
pinned to Japanese, used to label country groups for codes not in the `countries` table.
That was already wrong in English; a third language made it worse. `countryLabel()` now
takes an `Intl.DisplayNames` and the component builds one with `useMemo` keyed on `lang`.

Note this only affects **unregistered** country codes. Registered ones (`JP → "Japan"`,
`TW → "Taiwan"`, `US → "USA"`) return the user-entered name from the DB and are unaffected
by UI language — by design.

### Incidental fix

`confirmDeletePerson` contained `\\n\\n` (an escaped backslash) in both `ja` and `en`, so
the confirm dialog rendered a literal `\n\n` instead of blank lines. Corrected to `\n\n` in
all three locales rather than reproducing the bug in the new one.

## Non-goals

- `honorificLabel()` in `PersonEditor.tsx` is keyed on the **card's** name language and
  already returns `敬稱` for `zh*`. Unchanged.
- No i18n library. The hand-rolled `translations` object is fine at this size.

## Verification

- `npm run build` — `tsc -b` clean, vite build clean.
- Negative test of the type guard (above).
- Browser, against the live backend on `:8000`:
  - Cycle 日本語 → English → 繁體中文 → 日本語 closes correctly; button always names the next language.
  - Preference persists across reload (`localStorage.lang === 'zh-TW'`).
  - `document.documentElement.lang` tracks the selection.
  - Collection, Scan, Settings, Export and Card Detail all render Chinese, including
    nested `contactLabels` (手機 / 傳真 / 地址（公司）/ 網站) and the org labels
    (公司名稱 / 職稱 / 部門).
  - No console errors.

## Terminology (confirmed by Koji, 2026-09-08)

| Key | Term | Rejected alternative |
|---|---|---|
| `navCollection`, `collectionTitle` | 名片庫 | 收藏 |
| `myCompanyLabel`, `exportFilterMetAs` | 見面身分 | 當時身分 |
| `occasionLabel` | 場合 | 場面 / 場景 |
| `stageAnalyze` | 解析 | 分析 |
| `addImageLabel` | 新增影像 | 新增圖片 |
| `dupNotDuplicate` | 並非重複 → | 不是重複，另存新檔 → |
