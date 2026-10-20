// Which Viking "Open in Viking" goes to (static/js/vikingclient.js): the signed client
// or the unsigned "Viking Test" that installs beside it.  There is no JS runner in this
// repo: run it with
//
//     node tests/js/test_vikingclient.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  CLIENTS, ABSENT_MS, MEMORY_KEY, CHOICE_KEY, emptyMemory, parseMemory, recordOutcome, isAbsent,
  planLaunch, planForPick, planFromStores, recordFromLaunch, readMemory, writeMemory, readChoice, writeChoice,
  clientName,
} = await import(pathToFileURL(path.join(jsDir, 'vikingclient.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const NOW = 1_800_000_000_000;
const DAY = 24 * 3600 * 1000;

/** A Storage that remembers what it is given, or throws like a private window. */
function storage(initial = {}, { throws = false } = {}) {
  const data = new Map(Object.entries(initial));
  const guard = () => { if (throws) throw new Error('SecurityError'); };
  return {
    getItem: (k) => { guard(); return data.has(k) ? data.get(k) : null; },
    setItem: (k, v) => { guard(); data.set(k, String(v)); },
    removeItem: (k) => { guard(); data.delete(k); },
    data,
  };
}

const seenBy = (...ids) => ids.reduce((m, id) => recordOutcome(m, id, true, NOW - DAY), emptyMemory());

test('the two clients are the signed Viking and the unsigned Viking Test, each on its own scheme', () => {
  assert.equal(CLIENTS.viking.scheme, 'viking');
  assert.equal(CLIENTS.test.scheme, 'viking-test');
  assert.equal(CLIENTS.test.name, 'Viking Test');
  assert.equal(clientName('test'), 'Viking Test');
  assert.equal(clientName('anything else'), 'Viking');
});

test('nothing known: the signed Viking first, and Viking Test behind it, so a machine with only the test build is found', () => {
  const plan = planLaunch(emptyMemory(), { now: NOW });
  assert.deepEqual([plan.ask, plan.first, plan.fallback], [false, 'viking', 'test']);
});

test('only Viking Test ever answered: it is used at once, with no question; the other is only offered', () => {
  const plan = planLaunch(seenBy('test'), { now: NOW });
  assert.deepEqual([plan.ask, plan.first, plan.fallback, plan.why], [false, 'test', 'viking', 'only']);
});

test('only the signed Viking ever answered: it is used at once', () => {
  const plan = planLaunch(seenBy('viking'), { now: NOW });
  assert.deepEqual([plan.ask, plan.first, plan.fallback], [false, 'viking', 'test']);
});

test('the other client is not offered once the launches found it absent', () => {
  const memory = recordOutcome(seenBy('test'), 'viking', false, NOW - DAY);
  const plan = planLaunch(memory, { now: NOW });
  assert.deepEqual([plan.first, plan.fallback], ['test', null]);
});

test('both answered: the question is asked, once per session', () => {
  const memory = seenBy('viking', 'test');
  assert.equal(planLaunch(memory, { now: NOW }).ask, true);
  const answered = planLaunch(memory, { choice: 'test', now: NOW });
  assert.deepEqual([answered.ask, answered.first, answered.why], [false, 'test', 'chosen']);
  assert.equal(planLaunch(memory, { choice: 'viking', now: NOW }).first, 'viking');
});

test('a choice made in this session is used while that client is not known absent', () => {
  let memory = seenBy('viking', 'test');
  memory = recordOutcome(memory, 'test', false, NOW - 1000);        // Viking Test did not answer
  const plan = planLaunch(memory, { choice: 'test', now: NOW });
  assert.equal(plan.ask, false);
  assert.equal(plan.first, 'viking', 'the choice is dropped: it has no handler');
  assert.equal(plan.why, 'only');
});

test('the person asking to choose always gets the question', () => {
  assert.equal(planLaunch(seenBy('viking'), { forceAsk: true, now: NOW }).ask, true);
  assert.equal(planLaunch(emptyMemory(), { forceAsk: true, choice: 'test', now: NOW }).ask, true);
});

test('a pick is honored whatever the launches saw, with the other behind it unless absent', () => {
  const memory = recordOutcome(seenBy('viking'), 'test', false, NOW - DAY);
  assert.deepEqual(
    [planForPick(memory, 'test', NOW).first, planForPick(memory, 'test', NOW).fallback], ['test', 'viking']);
  assert.deepEqual(
    [planForPick(memory, 'viking', NOW).first, planForPick(memory, 'viking', NOW).fallback], ['viking', null]);
});

test('a client that found no handler is skipped for a week, then tried again', () => {
  const memory = recordOutcome(emptyMemory(), 'viking', false, NOW - 2 * DAY);
  const plan = planLaunch(memory, { now: NOW });
  assert.deepEqual([plan.first, plan.fallback], ['test', null], 'the signed one is not tried first');
  assert.equal(isAbsent(memory, 'viking', NOW), true);
  assert.equal(isAbsent(memory, 'viking', NOW + ABSENT_MS), false, 'a week on, the answer is stale');
  const later = planLaunch(memory, { now: NOW + ABSENT_MS });
  assert.deepEqual([later.first, later.fallback], ['viking', 'test']);
});

test('both found absent recently: both are tried again, not locked out', () => {
  let memory = recordOutcome(emptyMemory(), 'viking', false, NOW - DAY);
  memory = recordOutcome(memory, 'test', false, NOW - DAY);
  const plan = planLaunch(memory, { now: NOW });
  assert.deepEqual([plan.first, plan.fallback], ['viking', 'test']);
});

test('the last client that answered goes first when nothing else is known', () => {
  // Viking Test answered once, then the signed one was seen absent and Test too (stale): last = null.
  let memory = recordOutcome(emptyMemory(), 'test', true, NOW - 20 * DAY);
  memory = { ...memory, seen: { viking: 0, test: 0 } };             // forgotten: only `last` is left
  const plan = planLaunch(memory, { now: NOW });
  assert.deepEqual([plan.first, plan.fallback], ['test', 'viking']);
});

test('an outcome turns seen and absent over, and a client that is gone is no longer the last', () => {
  let memory = recordOutcome(emptyMemory(), 'test', true, NOW);
  assert.deepEqual([memory.seen.test, memory.absent.test, memory.last], [NOW, 0, 'test']);
  memory = recordOutcome(memory, 'test', false, NOW + 5);
  assert.deepEqual([memory.seen.test, memory.absent.test, memory.last], [0, NOW + 5, null]);
  memory = recordOutcome(memory, 'test', true, NOW + 9);
  assert.deepEqual([memory.seen.test, memory.absent.test, memory.last], [NOW + 9, 0, 'test']);
  assert.equal(recordOutcome(memory, 'nonsense', true, NOW), memory, 'an unknown client changes nothing');
});

test('recording does not change the memory it was given', () => {
  const before = seenBy('viking');
  const copy = JSON.stringify(before);
  recordOutcome(before, 'test', true, NOW);
  assert.equal(JSON.stringify(before), copy);
});

test('junk in storage is an empty memory, not an error', () => {
  for (const text of [null, '', 'nope', '[]', '{"seen":5}', '{"seen":{"viking":"x","test":-4},"last":"zoom"}']) {
    const memory = parseMemory(text);
    assert.deepEqual(memory, emptyMemory(), String(text));
  }
  const good = parseMemory(JSON.stringify({ seen: { viking: NOW, test: 0 }, absent: { test: NOW }, last: 'viking' }));
  assert.deepEqual([good.seen.viking, good.absent.test, good.last], [NOW, NOW, 'viking']);
});

test('the stores round-trip, and a browser that refuses storage still gets a plan', () => {
  const local = storage();
  const session = storage();
  assert.equal(planFromStores({ local, session, now: NOW }).first, 'viking');
  recordFromLaunch(local, 'test', true, NOW);
  assert.ok(local.data.has(MEMORY_KEY));
  assert.equal(readMemory(local).seen.test, NOW);
  assert.equal(planFromStores({ local, session, now: NOW }).first, 'test');
  recordFromLaunch(local, 'viking', true, NOW);
  assert.equal(planFromStores({ local, session, now: NOW }).ask, true);
  writeChoice(session, 'viking');
  assert.equal(session.data.get(CHOICE_KEY), 'viking');
  assert.equal(readChoice(session), 'viking');
  assert.equal(planFromStores({ local, session, now: NOW }).first, 'viking');
  writeChoice(session, null);
  assert.equal(readChoice(session), null);
  const blocked = storage({}, { throws: true });
  assert.doesNotThrow(() => writeMemory(blocked, emptyMemory()));
  assert.doesNotThrow(() => writeChoice(blocked, 'test'));
  const plan = planFromStores({ local: blocked, session: blocked, now: NOW });
  assert.deepEqual([plan.ask, plan.first, plan.fallback], [false, 'viking', 'test']);
  assert.equal(readChoice(storage({ [CHOICE_KEY]: 'other' })), null);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
