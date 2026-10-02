import { useSyncExternalStore } from 'react'
import {
  t,
  getLocale,
  subscribe,
  DEFAULT_LOCALE,
  LOCALES,
  type Locale,
  type Vars,
} from './core'

export { t, getLocale, subscribe, DEFAULT_LOCALE, LOCALES }
export type { Locale, Vars }
export { setLocaleForTest, registerDictionary, syncLocaleFromHass } from './core'

/**
 * Subscribe a component to language changes so it repaints.
 *
 * `t` is imported at module scope rather than taken from a hook on purpose: a
 * value destructured out of a hook looks unstable to the React Compiler, which
 * then abandons the component's hand-written useCallback/useMemo and eslint
 * fails the build with `react-hooks/preserve-manual-memoization`. A module
 * import is known-stable, so the memoization survives and the dependency arrays
 * stay free of `t`.
 */
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale, getLocale)
}
