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
  //: A marker no arm writes. If this is ever found, the search is not searching.
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
    this.shape = rendererShape(this.term);
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
    Arm: Arm,
    median: median,
    COLS: COLS,
    ROWS: ROWS,
    WRITES: WRITES,
    SUSTAIN_MS: SUSTAIN_MS,
    LF: LF,
  };
}());
