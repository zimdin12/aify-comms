// The live `PiSession` instances, keyed by agent id — and NOTHING else.
//
// This module exists because exactly two modules must share one Map and neither of them may own it.
// `pi-session-pool.mjs` registers and evicts entries; `PiSession` itself de-registers on teardown
// ("remove me if I am still the current entry for my agent"). The pool already imports the class, so
// if the Map lived in either file the other would have to import it back and the two would form a
// cycle. Giving the shared mutable name its own owner is the only arrangement with no cycle and no
// second copy.
//
// A SECOND COPY WOULD NOT FAIL LOUDLY, which is why this is worth a file. Two Maps would leave the
// pool handing out a session the class had already de-registered — a leaked child process per turn,
// visible only as pi agents that never die.
//
// Keep this module free of imports. It is the bottom of the pi dependency chain; anything added here
// is inherited by both readers and can re-introduce the cycle it was created to prevent.

export const piSessionPool = new Map();
