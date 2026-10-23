# Receipt key `__proto__` (final review of e72dd7c3/aefca598, C5 note)

aify-env `1fde203` (on `a97ffc9`): `connectRecorded` stores each installed row with `Object.defineProperty`, so a
legal artifact id such as `__proto__` is the run's own key and never a prototype write.

Command, in the aify-env worktree: `npm test -- tests/the-install-chain-runs-its-phases-in-order.test.js`

- `red.txt`: the old assignment line restored in place (byte copy kept aside): exit 1, 29 pass, 1 fail,
  "an id of __proto__ is an own key".
- `green.txt`: the fix restored from the byte copy (`cmp` equal): exit 0, 30 pass, 0 fail.
