/**
 * Keyboard helpers shared by inputs that save on Enter.
 */

/**
 * True for an Enter press that should submit the input — false for the Enter that
 * confirms an IME conversion (Japanese / Chinese input), which must not save the field
 * while the user is still typing.
 *
 * Browsers flag that confirming key press differently:
 * - Chrome / Firefox: `isComposing` is still true on the keydown.
 * - Safari: `compositionend` fires BEFORE the keydown, so `isComposing` is already false,
 *   but `keyCode` is 229 ("key processed by IME"). This is also why tracking
 *   compositionstart/compositionend in a ref is not enough on Safari.
 */
export function isSubmitEnter(e: React.KeyboardEvent): boolean {
  if (e.key !== 'Enter') return false
  return !e.nativeEvent.isComposing && e.keyCode !== 229
}
