// One console pane under measurement: a terminal, a renderer, its witnesses and its samples.
//
// SPLIT OUT OF `probe.js`, which had grown to 471 lines holding both this and the run around it.
// An Arm is a thing with identity and state; the run is a sequence over Arms. Neither reads better
// for being in the other's file.
//
// The header on `probe.js` states what the three spans are and why they all start at one origin.
// Read it before reading the timing loop below -- the ordering it warns about is the thing this
// file gets right and its first version got wrong.

(function () {
  "use strict";

  var ESC = String.fromCharCode(27);
  var LF = String.fromCharCode(10);

  //: The console's own PTY geometry, from `terminal_snapshot.py`'s 132x26 producer and the 40-row
  //: pane the dashboard fits to. 132x40 is the shape the other probes time, so they compare.
  var COLS = 132;
  var ROWS = 40;
  //: The same count for every arm, so SIZE is the only thing that differs between them -- the
  //: confound `measure-xterm-write.mjs` records finding in its own first version.
  var WRITES = 60;
  //: A write whose callback or whose render never arrives is a REJECTION, counted, never aged.
  var SETTLE_TIMEOUT_MS = 4000;
  //: How long the SUSTAINED phase offers output with no pacing. Long enough to cross many frames,
  //: short enough that six arms finish in a run somebody will actually wait for.
  var SUSTAIN_MS = 2000;
  //: HOW MANY RECOVERY REPAINTS TO TIME. Fewer than the paced phase's writes because each one
  //: tears down and rebuilds the row DOM, and twenty is enough for a median that does not move
  //: between runs at this geometry.
  var RECOVERIES = 20;
  //: A marker no arm writes. If this is ever found, the search is not searching.
  var CR = String.fromCharCode(13);
  var ABSENT_MARKER = "<never-painted-by-any-arm>";

  /**
   * Painted bytes of a requested size: cursor addressing, colour and text, which is what a TUI
   * sends. Plain text of the same length would measure the cheapest possible parse and call it a
   * frame. Byte-for-byte the recipe `measure-xterm-write.mjs` uses, so the two probes' arms are the
   * same workload rather than two workloads with the same name.
   */
  function paintedBytes(targetChars) {
    var parts = [];
    var size = 0;
    var row = 1;
    while (size < targetChars) {
      var line = ESC + "[" + row + ";1H" + ESC + "[38;5;" + ((row % 200) + 16) + "m"
        + "row " + row + " of a full-screen redraw with some content on it" + ESC + "[0m";
      parts.push(line);
      size += line.length;
      row = (row % 200) + 1;
    }
    // THE WITNESS GOES LAST AND ON ITS OWN ROW. Behind `ESC[H` it lands on row 1, which the first
    // painted line addresses, so the arm overwrites its own marker and the run refuses -- the
    // positive control doing its job on the probe, recorded in the sibling probe that hit it.
    parts.push("@@MARKER@@");
    return parts.join("");
  }

  /**
   * What screen a body leaves, derived from the body itself.
   *
   * A MARKER IS NOT A WORKLOAD. The per-recovery witness asked only whether that iteration's
   * marker arrived, so a write of `ESC[40;1H<marker>` -- twenty-odd bytes -- satisfied it while
   * the offered-byte column claimed sixty-five thousand. Filling the same length with spaces
   * satisfied it too, so counting bytes would not have repaired it.
   *
   * The payload addresses rows cyclically and the viewport is fixed, so the screen a correct
   * repaint leaves is DETERMINED: for each visible row, the text of the LAST segment addressing
   * it. Read off the bytes that will actually be written, never from the recipe -- the same rule
   * the PTY probe's row handshake follows, and for the same reason.
   */
  function expectedRows(body) {
    var expected = new Map();
    var pattern = /\u001b\[(\d+);1H(?:\u001b\[[0-9;]*m)?([^\u001b]*)/g;
    var match = pattern.exec(body);
    while (match !== null) {
      var row = Number(match[1]);
      if (row >= 1 && row <= ROWS) expected.set(row, match[2]);
      match = pattern.exec(body);
    }
    return expected;
  }

  /** How many of a body's visible rows the screen actually carries. */
  function rowsDelivered(term, expected) {
    var lines = screenOf(term).split(LF);
    var found = 0;
    expected.forEach(function (text, row) {
      var line = lines[row - 1];
      if (typeof line === "string" && text && line.indexOf(text) !== -1) found += 1;
    });
    return found;
  }

  function median(values) {
    if (!values.length) return NaN;
    var sorted = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function screenOf(term) {
    var buffer = term.buffer.active;
    var lines = [];
    for (var y = 0; y < buffer.length; y += 1) {
      var line = buffer.getLine(y);
      if (line) lines.push(line.translateToString(true));
    }
    return lines.join(LF);
  }

  /** What the renderer left in the DOM, which is how the two configurations are told apart. */
  function rendererShape(term) {
    var element = term.element;
    if (!element) return { canvases: -1, rowDivs: -1 };
    var rows = element.querySelector(".xterm-rows");
    return {
      canvases: element.querySelectorAll("canvas").length,
      rowDivs: rows ? rows.children.length : 0,
    };
  }

  /**
   * One renderer configuration at one payload size, with its own terminal and its own witnesses.
   *
   * ONE TERMINAL PER ARM, at the geometry the console uses. A terminal reused across arms carries
   * the previous arm's scrollback and charset state into this one's timings.
   */
  function Arm(options) {
    this.label = options.label;
    this.webgl = options.webgl;
    this.targetChars = options.targetChars;
    var stem = this.label.replace(/[^a-z0-9]/gi, "");
    // TWO WITNESSES, ONE PER PHASE, AND ON DIFFERENT ROWS. A single marker lets the second phase
    // satisfy the first phase's control -- the buffer says "something wrote this" while the paced
    // spans measured nothing. The sibling headless probe records finding exactly that.
    this.marker = "<paced-" + stem + ">";
    this.sustainMarker = "<sust-" + stem + ">";
    this.parseMs = [];
    this.paintedMs = [];
    this.paintOnlyMs = [];
    this.framedMs = [];
    this.timedOut = 0;
    this.badSpans = 0;
    this.noRender = 0;
    this.renderedBeforeParse = 0;
    this.contextLosses = 0;
    //: A render COUNTER, armed once at mount and never disposed between writes. Subscribing per
    //: write cannot see a render that fired before the subscription, and cannot tell the render
    //: this write caused from the previous write's.
    this.renderCount = 0;
    this.lastRenderAt = NaN;
    this.wroteSomething = false;
    this.sustainWrote = false;
    this.sustainTimeouts = 0;
    this.sustained = { writes: 0, bytes: 0, renders: 0, wallMs: 0 };
    //: THE RECOVERY PHASE: `reset()` then one whole screen, which is what the console does
    //: on every detected sequence gap. Timed from the reset, because the reset is part of
    //: the cost.
    this.recoveryMarker = "<recov-" + stem + ">";
    //: SCROLLED OFF THE VIEWPORT ON PURPOSE, because that is what tells an append from a
    //: repaint. A write pushes old rows into scrollback; `reset()` clears scrollback. A
    //: sentinel still on the VIEWPORT would be overwritten by the repaint either way -- which
    //: is exactly why the first version of this control could not fire.
    this.scrollbackSentinel = "<pre-recovery-" + stem + ">";
    this.recoveryMs = [];
    this.recoveryParseMs = [];
    this.recoveryTimeouts = 0;
    this.recoveryNoRender = 0;
    //: THIS PHASE'S OWN EXCLUSIONS. They were counted into the PACED phase's `badSpans`,
    //: which its accounting identity reads against WRITES -- so one bad recovery span broke
    //: the paced check and reported it with the paced phase's message. An exclusion
    //: attributed to the wrong phase makes a green run untrustworthy in both directions.
    this.recoveryBadSpans = 0;
    this.recoveryRenderedBeforeParse = 0;
    //: RECOVERIES WHOSE OWN SENTINEL SURVIVED, i.e. that did not reset. Planting one sentinel
    //: for the whole phase let the FIRST reset satisfy every later one -- review published
    //: six arms with twenty samples each against `if (i === 0) term.reset()`.
    this.recoveryNotReset = 0;
    this.recoveryNotPainted = 0;
    //: RECOVERIES WHOSE BODY DID NOT LAND, as distinct from ones whose MARKER did not: review
    //: published twenty samples an arm from writes of twenty-odd bytes, marker only.
    this.recoveryNotFullyPainted = 0;
    //: THE RECOVERY BODY'S OWN SIZE. `bytes` is the PACED body's, and the recovery body carries a
    //: per-iteration suffix that makes it three or four bytes longer -- so the recovery table was
    //: printing a number belonging to a different payload.
    this.recoveryBytes = [];
    //: PAIRED PER RECOVERY, like the paced phase's `paintOnlyMs`. A difference of two
    //: independently-taken medians is a different statistic from the median of the paired
    //: differences, and this file has published the wrong one of those before.
    this.recoveryPaintOnlyMs = [];
    this.recoveryWrote = false;
    this.resetLeftTheOldScreen = false;
    this.foundAbsent = false;
    this.shape = { canvases: -1, rowDivs: -1 };
    this.bytes = 0;
  }

  Arm.prototype.mount = function (stage) {
    this.container = document.createElement("div");
    this.container.style.width = "1080px";
    this.container.style.height = "680px";
    stage.appendChild(this.container);
    this.term = new window.Terminal({
      cols: COLS, rows: ROWS, allowProposedApi: true, scrollback: 1000,
    });
    if (window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon) {
      this.term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
      this.term.unicode.activeVersion = "11";
    }
    this.term.open(this.container);
    var counting = this;
    this.term.onRender(function () {
      counting.renderCount += 1;
      counting.lastRenderAt = performance.now();
    });
    if (this.webgl) {
      // LOADED THE WAY `xterm-mount.mjs` LOADS IT -- after open(), with the same context-loss
      // handler shape -- because a differently-ordered activation is a different measurement.
      var self = this;
      var addon = new window.WebglAddon.WebglAddon();
      addon.onContextLoss(function () {
        self.contextLosses += 1;
        try { addon.dispose(); } catch (ignored) { /* the loss already took it */ }
      });
      this.term.loadAddon(addon);
      this.addon = addon;
    }
  };

  /**
   * Resolves when the render COUNTER has moved past `seenCount`, with the time that render ran.
   *
   * Polled on animation frames rather than waited for on a fresh subscription, so a render that
   * fired between the write and this call is still counted -- it is the render this write caused,
   * and missing it is what made every sample time out.
   */
  Arm.prototype.renderPast = function (seenCount) {
    var self = this;
    var deadline = performance.now() + SETTLE_TIMEOUT_MS;
    return new Promise(function (resolve) {
      if (self.renderCount > seenCount) { resolve(self.lastRenderAt); return; }
      var poll = function () {
        if (self.renderCount > seenCount) { resolve(self.lastRenderAt); return; }
        if (performance.now() > deadline) { resolve(NaN); return; }
        requestAnimationFrame(poll);
      };
      requestAnimationFrame(poll);
    });
  };

  Arm.prototype.run = async function () {
    var body = paintedBytes(this.targetChars).replace("@@MARKER@@", ESC + "[40;1H" + this.marker);
    // UTF-8 BYTES, not UTF-16 units. `String.length` counts units, and a probe that divides by the
    // wrong number reports a throughput for a payload it did not send.
    this.bytes = new TextEncoder().encode(body).length;
    for (var i = 0; i < WRITES; i += 1) {
      var seenRenders = this.renderCount;
      var started = performance.now();
      var callbackAt = await this.writeOnce(body);
      if (!Number.isFinite(callbackAt)) { this.timedOut += 1; continue; }
      var renderAt = await this.renderPast(seenRenders);
      if (!Number.isFinite(renderAt)) { this.noRender += 1; continue; }
      var frameAt = await new Promise(function (resolve) {
        requestAnimationFrame(function () { resolve(performance.now()); });
      });
      var parse = callbackAt - started;
      var painted = renderAt - started;
      var framed = frameAt - started;
      // A SPAN THAT IS NOT A POSITIVE FINITE NUMBER IS NOT A SAMPLE, whatever the median does with
      // it. `performance.now()` is monotonic, so a non-increasing sequence means the model of what
      // happens in what order is wrong here -- which is a finding, not a row to average.
      if (!(parse >= 0) || !(painted >= 0) || !(framed >= painted)
          || !Number.isFinite(parse) || !Number.isFinite(painted) || !Number.isFinite(framed)) {
        this.badSpans += 1;
        continue;
      }
      // COUNTED, NOT AVERAGED IN. A render finishing before the write callback does happen -- both
      // are deferred and neither promises to come first -- and it means PAINTED minus PARSE is not
      // a renderer cost for this sample. It is reported as its own number rather than folded away.
      if (painted < parse) { this.renderedBeforeParse += 1; continue; }
      this.parseMs.push(parse);
      this.paintedMs.push(painted);
      this.framedMs.push(framed);
      // PAIRED, PER WRITE. A difference between two independently-taken medians is a different
      // statistic from the median of the per-write differences, and this repo has already published
      // the wrong one of those once.
      this.paintOnlyMs.push(painted - parse);
    }
    // ASKED WHILE THIS PHASE'S OUTPUT IS STILL THE MOST RECENT THING ON THE SCREEN. The sustained
    // phase below repaints every row, so a check deferred to the end of the arm would be asking
    // about the wrong phase.
    var pacedScreen = screenOf(this.term);
    this.wroteSomething = pacedScreen.indexOf(this.marker) !== -1;
    this.foundAbsent = pacedScreen.indexOf(ABSENT_MARKER) !== -1;

    await this.runSustained();

    var screen = screenOf(this.term);
    this.sustainWrote = screen.indexOf(this.sustainMarker) !== -1;
    if (screen.indexOf(ABSENT_MARKER) !== -1) this.foundAbsent = true;
    // THE RENDERER SHAPE IS READ BEFORE THE RECOVERY PHASE, deliberately: a `reset()` tears
    // the row DOM down and rebuilds it, so a shape read after one is a shape mid-rebuild.
    this.shape = rendererShape(this.term);

    await this.runRecovery();
  };

  /**
   * THE PHASE THAT TESTS THE CLAIM. `index.html` blames the DOM renderer for lag "under heavy TUI
   * output", and the paced phase above deliberately waits a frame between writes, so it never puts
   * either renderer under that. This one offers output as fast as the parser will take it for a
   * fixed wall-clock window and counts how many RENDERS came out of it.
   *
   * A renderer that cannot keep up does not get slower per render -- it renders FEWER times for the
   * same offered bytes, because xterm coalesces onto animation frames. So the number that separates
   * them is renders per second, not milliseconds per render.
   */
  Arm.prototype.runSustained = async function () {
    var body = paintedBytes(this.targetChars)
      .replace("@@MARKER@@", ESC + "[40;1H" + this.sustainMarker);
    var rendersBefore = this.renderCount;
    var started = performance.now();
    var deadline = started + SUSTAIN_MS;
    var writes = 0;
    while (performance.now() < deadline) {
      var at = await this.writeOnce(body);
      if (!Number.isFinite(at)) { this.sustainTimeouts += 1; break; }
      writes += 1;
    }
    var ended = performance.now();
    // ONE MORE FRAME, so a render the last write scheduled is counted rather than lost to the
    // window closing between the parse and the paint.
    await new Promise(function (resolve) { requestAnimationFrame(function () { resolve(); }); });
    this.sustained = {
      writes: writes,
      bytes: writes * new TextEncoder().encode(body).length,
      renders: this.renderCount - rendersBefore,
      wallMs: ended - started,
    };
  };

  /**
   * What one recovery repaint costs: `reset()`, then a whole server-rendered screen.
   *
   * THE SHAPE THE LAG MECHANISM PAID FOR. A detected sequence gap costs an HTTP refetch, a
   * `term.reset()` and a full repaint; the refetch is measured elsewhere and this is the
   * browser's half. Neither of the other phases has ever timed a reset -- both APPEND to a
   * live screen -- so this is the only figure here about the recovery path.
   */
  Arm.prototype.runRecovery = async function () {
    var self = this;
    // THE PAYLOAD'S MARKER IS PER RECOVERY TOO, and for the same reason the sentinel is. Checking
    // one marker after the phase let a single real repaint certify twenty: review painted a space
    // nineteen times and the real body once, and all six arms published twenty samples. Reset
    // COMPLETION and repaint EFFECT are two obligations, so they get two witnesses, each per
    // sample and each outside the timed bracket.
    var bodyFor = function (marker) {
      return paintedBytes(self.targetChars).replace("@@MARKER@@", ESC + "[40;1H" + marker);
    };
    // WHAT THIS ARM CLAIMS TO WRITE, computed once from the intended payload and BEFORE the loop.
    //
    // AN EXPECTATION READ OFF THE BODY THAT WAS ACTUALLY WRITTEN IS SATISFIED BY ANY BODY. My
    // first version did exactly that: a marker-only write addresses one row, the marker's own,
    // which is then excluded -- leaving an EMPTY expectation that everything satisfies. It
    // published review's carrier unchanged. The claim has to come from the payload the column
    // names, so a body that addresses fewer rows is a body that is not that payload.
    var claimed = expectedRows(bodyFor(this.recoveryMarker + "-claim>"));
    claimed.delete(ROWS);
    this.claimedRows = claimed.size;
    var filler = [];
    for (var f = 0; f < ROWS + 5; f += 1) filler.push("");
    for (var i = 0; i < RECOVERIES; i += 1) {
      // ONE SENTINEL PER RECOVERY, planted immediately before it and outside the timed bracket.
      // A single sentinel for the whole phase is cleared by the FIRST reset, and every later
      // recovery then inherits a witness it did not earn -- review published twenty samples an
      // arm against a single real reset.
      var sentinel = this.scrollbackSentinel + "-" + i + ">";
      var marker = this.recoveryMarker + "-" + i + ">";
      var body = bodyFor(marker);
      // THE CANONICAL MAP ITSELF, values and all -- not a fresh one read off the body that was
      // written. Rebuilding it from the current body let that body define its own oracle: review
      // replaced every row's text with x characters of the SAME LENGTH, keeping the cursor
      // addresses, the colours, the marker and the exact byte count, and all six arms published.
      // The count matched because the rows were still addressed; only the VALUES had changed,
      // and the values were the half nothing compared.
      var expected = new Map(claimed);
      this.recoveryBytes.push(new TextEncoder().encode(body).length);
      await this.writeOnce(CR + LF + sentinel + filler.join(CR + LF));
      var seenRenders = this.renderCount;
      var started = performance.now();
      // THE RESET IS INSIDE THE SPAN. A recovery that only wrote the snapshot would leave
      // stale rows, a stuck charset and the previous alt-screen state underneath -- which is
      // why `console-actions.mjs` resets first, and why timing the write alone would report a
      // cost the console never pays.
      this.term.reset();
      var callbackAt = await this.writeOnce(body);
      if (!Number.isFinite(callbackAt)) { this.recoveryTimeouts += 1; continue; }
      var renderAt = await this.renderPast(seenRenders);
      if (!Number.isFinite(renderAt)) { this.recoveryNoRender += 1; continue; }
      var parse = callbackAt - started;
      var painted = renderAt - started;
      if (!(parse >= 0) || !(painted >= 0) || !Number.isFinite(parse)
          || !Number.isFinite(painted)) { this.recoveryBadSpans += 1; continue; }
      // COUNTED, NOT AVERAGED IN, exactly as the paced phase does it. Both spans are
      // deferred and neither promises to come first, and this table publishes REPAINT MINUS
      // PARSE -- which is not a renderer cost for a sample whose render finished first.
      if (painted < parse) { this.recoveryRenderedBeforeParse += 1; continue; }
      // ASKED PER RECOVERY, AFTER ITS SPAN. The sentinel went into scrollback, which an append
      // leaves in place and a reset clears -- so its survival says THIS recovery did not reset.
      // TWO QUESTIONS, ASKED PER RECOVERY, AFTER ITS SPAN. Did this reset happen -- the sentinel
      // went to scrollback, which an append leaves and a reset clears -- and did THIS recovery's
      // payload reach the screen. Either answer alone certifies half of what the sample claims.
      var after = screenOf(this.term);
      // COUNTED APART, because a message that names the wrong obligation is the defect this
      // phase has already been corrected for twice. A surviving sentinel says the RESET did not
      // happen; a missing marker says the REPAINT did not reach the screen. One counter for both
      // reported review's paint-a-space carrier as a reset failure, which it was not.
      if (after.indexOf(sentinel) !== -1) { this.recoveryNotReset += 1; continue; }
      if (after.indexOf(marker) === -1) { this.recoveryNotPainted += 1; continue; }
      // AND THE BODY, NOT ONLY ITS MARKER. Every visible row this payload addresses must carry
      // the text that payload put there; the marker's own row is excluded because the marker
      // deliberately overwrites it.
      if (rowsDelivered(this.term, expected) !== expected.size) {
        this.recoveryNotFullyPainted += 1;
        continue;
      }
      this.recoveryParseMs.push(parse);
      this.recoveryMs.push(painted);
      this.recoveryPaintOnlyMs.push(painted - parse);
    }
    var screen = screenOf(this.term);
    this.recoveryWrote = screen.indexOf(this.recoveryMarker) !== -1;
    void body;
    // THE CONTROL THAT MAKES THE REST OF THIS PHASE MEAN ANYTHING, and the THIRD version of it.
    //
    // The first asked whether the SUSTAINED phase's marker survived, and could not fire: both
    // markers are written at `ESC[40;1H`, so the repaint overwrites it either way. Removing
    // `term.reset()` produced zero refusals.
    //
    // The second planted ONE scrollback sentinel for the whole phase, and the FIRST reset cleared
    // it -- so `if (i === 0) term.reset()` published twenty samples an arm, nineteen of them
    // unwitnessed. Review drove that too.
    //
    // A SENTINEL PER RECOVERY DISCRIMINATES PER RECOVERY. An append leaves it in scrollback and a
    // reset clears it, `screenOf` walks the whole of `buffer.active`, and the check happens inside
    // the loop against that recovery's own sentinel. What is left here is the ordinary
    // end-of-phase question: did the last recovery reach the screen at all.
    this.resetLeftTheOldScreen = this.recoveryNotReset > 0 || this.recoveryNotPainted > 0
      || this.recoveryNotFullyPainted > 0;
    if (screen.indexOf(ABSENT_MARKER) !== -1) this.foundAbsent = true;
  };

  Arm.prototype.writeOnce = function (body) {
    var term = this.term;
    return new Promise(function (resolve) {
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        resolve(NaN);
      }, SETTLE_TIMEOUT_MS);
      term.write(body, function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(performance.now());
      });
    });
  };

  Arm.prototype.dispose = function () {
    try { this.term.dispose(); } catch (ignored) { /* nothing left to dispose */ }
    if (this.container && this.container.parentNode) {
      this.container.parentNode.removeChild(this.container);
    }
  };

  // THE ONLY WAY OUT. These files load as classic scripts because the harness is opened over
  // `file://`, where ES modules are refused -- so the seam is a namespace rather than an import.
  window.PaintArm = {
    RECOVERIES: RECOVERIES,
    Arm: Arm,
    median: median,
    COLS: COLS,
    ROWS: ROWS,
    WRITES: WRITES,
    SUSTAIN_MS: SUSTAIN_MS,
    LF: LF,
  };
}());
