import { useEffect, useState } from 'react'
import { storage } from './storage'

/** Keep lightweight view choices across route unmounts; callers validate persisted values. */
export function usePageState<T>(
  key: keyof typeof storage.pageView,
  initial: T | (() => T),
  isValid: (value: unknown) => value is T,
) {
  const [value, setValue] = useState<T>(() => {
    const saved = storage.pageView[key].get(null)
    return isValid(saved) ? saved : typeof initial === 'function' ? (initial as () => T)() : initial
  })
  useEffect(() => { storage.pageView[key].set(value) }, [key, value])
  return [value, setValue] as const
}
