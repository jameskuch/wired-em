// Parsing the Structure ID box: the Viking Data Export portal's grammar.
//
// Mirrors Servers/ExportPortal/js/portal.js (jamesra/Viking, Jotunn branch)
// and src/sbfsem/io/spec.py, which is the authoritative copy the server uses
// to resolve label terms. This client copy exists so a pure-numeric entry --
// the overwhelmingly common case -- parses without a round trip, and so the
// box can say "looking up labels" only when there are labels to look up.
//
// The grammar: entries are separated by newline, comma, or semicolon --
// never by whitespace alone, because labels contain spaces ("GC ON",
// "yAC ON+OFF"). An entry with a letter in it is a label term, matched
// server-side as a case-insensitive substring of parent-structure labels,
// and never split further. An entry without letters is whitespace-split
// into IDs and inclusive dash ranges ("180-476", "180 - 476", and the en
// dashes word processors substitute).

/** A wider range is refused rather than expanded, exactly as in the portal. */
export const MAX_RANGE_SPAN = 10000;

const ENTRY_SEPARATORS = /[\n\r,;]+/;
// Hyphen plus the en/em dashes a word processor produces.
const DASH_RUN = /\s*[-‐‑‒–—―]\s*/g;

/**
 * Split raw text into entries and classify each one.
 *
 * Returns { numericIds, labels, invalid }: sorted de-duplicated IDs, label
 * terms in order of first appearance, and the tokens that could not be read,
 * each with a reason. Unreadable input is reported, never silently dropped.
 */
export function parseStructureSpec(text) {
  const ids = new Set();
  const labels = [];
  const invalid = [];

  for (const rawEntry of String(text ?? '').split(ENTRY_SEPARATORS)) {
    const entry = rawEntry.trim();
    if (!entry) continue;

    // Anything with a letter is a label, and is never split further.
    if (/[a-z]/i.test(entry)) {
      if (!labels.includes(entry)) labels.push(entry);
      continue;
    }

    // Tighten "180 - 476" to "180-476" first, so the whitespace split below
    // cannot break a spaced range into three meaningless tokens.
    for (const token of entry.replace(DASH_RUN, '-').split(/\s+/)) {
      if (!token) continue;

      if (/^\d+$/.test(token)) {
        const n = Number(token);
        if (!Number.isSafeInteger(n)) invalid.push({ text: token, reason: 'number is too large' });
        else if (n < 1) invalid.push({ text: token, reason: 'ids start at 1' });
        else ids.add(n);
        continue;
      }

      const range = token.match(/^(\d+)-(\d+)$/);
      if (range) {
        let lo = Number(range[1]);
        let hi = Number(range[2]);
        if (!Number.isSafeInteger(lo) || !Number.isSafeInteger(hi)) {
          invalid.push({ text: token, reason: 'number is too large' });
          continue;
        }
        if (lo > hi) [lo, hi] = [hi, lo];
        const span = hi - lo + 1;
        if (span > MAX_RANGE_SPAN) {
          invalid.push({
            text: token,
            reason: `range covers ${span.toLocaleString()} IDs, over the ${MAX_RANGE_SPAN.toLocaleString()} limit`,
          });
          continue;
        }
        if (lo < 1) {
          invalid.push({ text: token, reason: 'ids start at 1' });
          continue;
        }
        for (let i = lo; i <= hi; i += 1) ids.add(i);
        continue;
      }

      invalid.push({ text: token, reason: 'not an ID or a range' });
    }
  }

  return {
    numericIds: [...ids].sort((a, b) => a - b),
    labels,
    invalid,
  };
}
