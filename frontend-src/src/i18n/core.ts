/**
 * i18n runtime for the Home Assistant panel.
 *
 * Same design as the main Homelable frontend — English source text is the key,
 * `t()` falls back to the key when a locale has no entry, and the locale lives
 * in a module-level variable rather than React context. Context would have to
 * wrap the tree, and the suite renders components bare.
 *
 * One difference from the main app: there is no language switcher here. Home
 * Assistant owns the user's language setting and hands it to us on `hass`, so
 * the panel follows it rather than keeping a second, divergent one. An earlier
 * draft persisted an override in localStorage; nothing ever wrote it, which
 * left a second source of truth that could silently disagree with HA — so the
 * override path is gone rather than left looking like a feature.
 */

import zhCN from './locales/zh-CN'

export const LOCALES = ['en', 'zh-CN'] as const
export type Locale = (typeof LOCALES)[number]

export const DEFAULT_LOCALE: Locale = 'en'

export type Vars = Record<string, string | number>

function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value)
}

// Home Assistant reports e.g. `zh-Hans`, `zh-CN` or plain `zh`.
function fromLanguage(language: string | undefined | null): Locale | null {
  if (!language) return null
  return /^zh\b/i.test(language) ? 'zh-CN' : null
}

// `t()` *is* the English translation: the `en` table stays empty so a missing
// key falls through to the English source rather than rendering nothing.
const dictionaries: Record<Locale, Record<string, string>> = {
  en: {},
  'zh-CN': zhCN,
}

let current: Locale = DEFAULT_LOCALE
const listeners = new Set<() => void>()

export function t(key: string, vars?: Vars): string {
  const entry = dictionaries[current][key]
  // A blank entry would render an invisible label, which is worse than English.
  const out = typeof entry === 'string' && entry.trim() ? entry : key
  if (!vars) return out
  return out.replace(/\{(\w+)\}/g, (m, name: string) => {
    const v = vars[name]
    return v === undefined ? m : String(v)
  })
}

export function getLocale(): Locale {
  return current
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function apply(next: Locale): void {
  if (next === current) return
  current = next
  for (const fn of listeners) fn()
}

export function registerDictionary(locale: Locale, entries: Record<string, string>): void {
  Object.assign(dictionaries[locale], entries)
}

/** Set the locale without notifying — for tests that assert on `t()` alone. */
export function setLocaleForTest(next: Locale): void {
  if (isLocale(next)) apply(next)
}

// The panel bundle is also loaded as a Lovelace card, where `hass` may already
// be on the window by the time this module runs.
apply(
  fromLanguage(
    (globalThis as { hass?: { language?: string } }).hass?.language ??
      (globalThis.navigator as { language?: string } | undefined)?.language,
  ) ?? DEFAULT_LOCALE,
)

/**
 * Home Assistant hands the panel a `hass` object carrying the user's language.
 * Re-evaluate on every change to it, so the panel follows a language change made
 * in HA's own settings — in *both* directions. A language we have no translation
 * for resolves to English rather than to "leave it alone", otherwise switching
 * HA from 简体中文 back to English would strand the panel in Chinese.
 *
 * An undefined language is a different case and deliberately ignored: HA can
 * hand over a partial object, and that must not undo a locale already resolved
 * from a language it did report.
 */
export function syncLocaleFromHass(language: string | undefined | null): void {
  if (language === undefined || language === null) return
  apply(fromLanguage(language) ?? DEFAULT_LOCALE)
}
