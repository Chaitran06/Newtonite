/**
 * Rebase a user's in-progress edit onto a newer server version.
 *
 * Rule: only fields the user actually changed (relative to the version they started from)
 * keep their draft value. Every field they did NOT touch takes the server's current value, so
 * we never silently revert someone else's change to a field the user never edited.
 */
export function rebaseDraft<T extends Record<string, unknown>>(started: T, draft: T, server: T): T {
  const next = { ...server };
  for (const key of Object.keys(started) as (keyof T)[]) {
    if (draft[key] !== started[key]) next[key] = draft[key];
  }
  return next;
}
