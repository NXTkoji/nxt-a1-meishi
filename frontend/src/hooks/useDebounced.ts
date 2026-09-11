import { useEffect, useState } from 'react'

/**
 * Returns `value` once it has stopped changing for `delay` ms.
 *
 * Keeps a fast typist from firing a request per keystroke: every change restarts the
 * timer, so only the value that survives a full `delay` of quiet is ever returned.
 *
 * The first render returns `value` as-is, with no delay, so a component that mounts with
 * a non-empty value does not flash through an empty state first.
 */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    // Cleanup runs before the next effect, i.e. on every change of `value`. Cancelling
    // the pending timer here is what makes this a debounce rather than a delay.
    return () => clearTimeout(id)
  }, [value, delay])

  return debounced
}
