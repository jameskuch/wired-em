// Which Viking "Open in Viking" hands a link to: the signed client (`viking://`) or
// the unsigned test build that installs beside it as "Viking Test" (`viking-test://`;
// James Anderson's Legacy branch, `VikingChannelIdentity.cs`).  Plain functions over a
// small record kept in the browser, so node can run them (tests/js/test_vikingclient.mjs);
// app.js asks for a plan and the launch tab (viking-launch.html) writes what happened.
//
// A web page cannot read the Windows registry or ask which programs are installed.
// The one signal a browser gives is that a link handed to a scheme with a handler
// takes the focus away from the page (the browser's "Open Viking?" question, or the
// program's own window), and one with no handler leaves the page where it was.  So
// "installed" here is what the last launches saw: `seen` is when a scheme took the
// focus, `absent` when it did not within the launch tab's wait.  Each launch updates
// them, which is also how a client installed or removed later is found.

/** The two clients, in the order they are tried when nothing is known. */
export const CLIENTS = Object.freeze({
  viking: Object.freeze({ id: 'viking', name: 'Viking', scheme: 'viking' }),
  test: Object.freeze({ id: 'test', name: 'Viking Test', scheme: 'viking-test' }),
});
export const CLIENT_IDS = Object.freeze(['viking', 'test']);

/** localStorage: what the launches have seen (kept across sessions). */
export const MEMORY_KEY = 'sbfsem.vikingClients';
/** sessionStorage: the client picked in this tab's session. */
export const CHOICE_KEY = 'sbfsem.vikingChoice';
/** A "no handler" answer is believed this long, then the client is tried again. */
export const ABSENT_MS = 7 * 24 * 3600 * 1000;

export const isClient = (id) => id === 'viking' || id === 'test';
const other = (id) => (id === 'viking' ? 'test' : 'viking');

export function emptyMemory() {
  return { seen: { viking: 0, test: 0 }, absent: { viking: 0, test: 0 }, last: null };
}

const stamp = (value) => (Number.isFinite(value) && value > 0 ? value : 0);

/** The memory in `text` (JSON), or an empty one for anything that is not. */
export function parseMemory(text) {
  const memory = emptyMemory();
  let raw = null;
  try { raw = JSON.parse(text); } catch { return memory; }
  if (!raw || typeof raw !== 'object') return memory;
  for (const id of CLIENT_IDS) {
    memory.seen[id] = stamp(raw.seen?.[id]);
    memory.absent[id] = stamp(raw.absent?.[id]);
  }
  memory.last = isClient(raw.last) ? raw.last : null;
  return memory;
}

/** What a launch of `id` found: a handler took the focus (`handled`), or none did. */
export function recordOutcome(memory, id, handled, now = Date.now()) {
  if (!isClient(id)) return memory;
  const next = parseMemory(JSON.stringify(memory));
  if (handled) {
    next.seen[id] = now;
    next.absent[id] = 0;
    next.last = id;
  } else {
    next.absent[id] = now;
    next.seen[id] = 0;
    if (next.last === id) next.last = null;
  }
  return next;
}

/** Did the launches say, recently, that `id` has no handler? */
export function isAbsent(memory, id, now = Date.now()) {
  const at = memory.absent[id];
  return at > 0 && now - at < ABSENT_MS;
}

/**
 * What a click on "Open in Viking" should do.
 *
 *   { ask: true }                       -- put the question to the person
 *   { ask: false, first, fallback }     -- launch `first`; if nothing answers it,
 *                                          the launch tab says so and offers `fallback`
 *                                          on a button (null: nothing to offer)
 *
 * The other client is only ever offered, never started by itself: a launch a
 * page starts seconds after it loaded is refused by Chrome ("a user gesture is
 * required"), and a client that answered before and is silent now is far more
 * likely slow than gone, so starting the other too would run two programs.
 *
 * In order: the person's choice this session; both clients seen to answer (the
 * question, once per session: a choice is kept for it); one seen (that one);
 * otherwise the last that answered, else the signed Viking.  A machine with only
 * the test build is found by the signed link finding no handler: the tab says
 * Viking is not installed and offers Viking Test, and one click on it is
 * remembered.  A client the launches recently found absent is skipped, unless
 * both are, and then both are tried again (a week is a long time to wait for an
 * install to be noticed).  `forceAsk` is the person asking to choose.
 */
export function planLaunch(memory, { choice = null, forceAsk = false, now = Date.now() } = {}) {
  if (forceAsk) return { ask: true, first: null, fallback: null, why: 'asked' };
  const gone = (id) => isAbsent(memory, id, now);
  const seen = (id) => memory.seen[id] > 0 && !gone(id);
  if (isClient(choice) && !gone(choice)) return planForPick(memory, choice, now);
  if (seen('viking') && seen('test')) return { ask: true, first: null, fallback: null, why: 'both' };
  if (seen('viking') || seen('test')) {
    const only = seen('test') ? 'test' : 'viking';
    return { ask: false, first: only, fallback: gone(other(only)) ? null : other(only), why: 'only' };
  }
  const live = CLIENT_IDS.filter((id) => !gone(id));
  const pool = live.length ? live : CLIENT_IDS;
  const first = isClient(memory.last) && pool.includes(memory.last) ? memory.last : pool[0];
  return { ask: false, first, fallback: pool.find((id) => id !== first) ?? null, why: 'unknown' };
}

/**
 * The plan for a client the person picked: that one first, whatever the launches
 * saw (they may have installed it since), with the other behind it unless it was
 * recently found absent.
 */
export function planForPick(memory, id, now = Date.now()) {
  return {
    ask: false, first: id, fallback: isAbsent(memory, other(id), now) ? null : other(id), why: 'chosen',
  };
}

// -- the browser's two stores, tolerant of both being unavailable --------------

/** The record the launches keep (empty when storage is unavailable or holds junk). */
export function readMemory(storage) {
  try { return parseMemory(storage?.getItem(MEMORY_KEY)); } catch { return emptyMemory(); }
}

export function writeMemory(storage, memory) {
  try { storage?.setItem(MEMORY_KEY, JSON.stringify(memory)); } catch { /* private window: nothing kept */ }
}

/** The client picked in this session, or null. */
export function readChoice(storage) {
  try {
    const id = storage?.getItem(CHOICE_KEY);
    return isClient(id) ? id : null;
  } catch { return null; }
}

export function writeChoice(storage, id) {
  try {
    if (isClient(id)) storage?.setItem(CHOICE_KEY, id);
    else storage?.removeItem(CHOICE_KEY);
  } catch { /* the question is asked again next time */ }
}

/**
 * The plan for a click now, from the browser's stores.  `forceAsk` is a person
 * choosing on purpose; otherwise the question is asked only when both clients are
 * known and nothing was chosen yet in this session.
 */
export function planFromStores({ local, session, forceAsk = false, now = Date.now() }) {
  return planLaunch(readMemory(local), { choice: readChoice(session), forceAsk, now });
}

/** A launch's result, written where the next plan reads it. */
export function recordFromLaunch(local, id, handled, now = Date.now()) {
  writeMemory(local, recordOutcome(readMemory(local), id, handled, now));
}

/** "Viking", "Viking Test", or "Viking or Viking Test" for the words around a launch. */
export function clientName(id) {
  return CLIENTS[id]?.name ?? 'Viking';
}
