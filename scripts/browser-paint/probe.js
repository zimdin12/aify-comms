// HOP 5b — what the browser pays to PAINT a console frame, and what the DOM fallback costs.
//
// THE HALF EVERY OTHER PROBE SAID IT COULD NOT MEASURE. `measure-xterm-write.mjs` times
// `@xterm/headless`, which is the same parser with the renderer REMOVED, and says so in its own
// header: "the renderer painting the buffer, and the browser scheduling that work against everything
// else on the page, are in no figure here." This is that figure.
//
// WHY IT IS WORTH A BROWSER. `index.html` says, as a claim with no measurement attached: "The DOM
// renderer was the main source of console lag under heavy TUI output; WebGL is activated after
// open() with an onContextLoss fallback to DOM." If that is true, then `xterm-mount.mjs`'s context-
// loss handler -- which disposes the addon and sets `webglAddon = null` for the life of that mount --
// silently returns the console to the renderer the comment blames, with no retry and no notice. That
// is a mechanism for "sometimes", which is the operator's word. This probe does not observe a
// context loss; it measures what one would COST, which is the half that can be measured on demand.
//
// THREE SPANS, ALL FROM ONE ORIGIN, because a span anchored on another span's END assumes an
// ordering, and the first version of this file assumed the wrong one:
//   PARSE     write() -> its callback                 the same span the headless probe times
//   PAINTED   write() -> the render it caused         parse AND the renderer drawing it
//   FRAMED    write() -> the next rAF after that      a PROXY for reaching the compositor
//
// PAINT-ONLY is reported as PAINTED minus PARSE, which is a difference between two spans measured
// from the same instant rather than a span whose start is another span's end. That matters here:
// xterm's write callback and its renderer are BOTH deferred, so "the render after the callback" is
// not guaranteed to be the render the write caused, and subscribing after the callback misses a
// render that already happened -- which is exactly what the first run did, waiting out a 4-second
// timeout on every one of 360 writes.
//
// FRAME IS A PROXY AND NOT A PIXEL. Nothing here observes the screen. `requestAnimationFrame`
// firing says the browser began a frame, not that a viewer saw one, and in headless Chrome its
// cadence is not a physical display's vsync. It is reported because a renderer that finishes inside
// one frame and one that does not are different experiences; it is not a photon.
//
// THE CONTROLS ARE IN THE SAME RUN. POSITIVE: after each arm the terminal's buffer must contain a
// marker that arm wrote. NEGATIVE: a marker no arm writes must not be found. RENDERER: the two
// configurations must produce DIFFERENT renderer DOM, or they are the same renderer twice and the
// comparison is void -- which is the control that matters most here, since "we loaded the addon" is
// exactly the kind of thing that reports success and changes nothing.
//
// NOTHING IS PUBLISHED UNLESS EVERY CONTROL HELD.

// THE ARM ITSELF LIVES IN `console-arm.js`, loaded before this file by the harness. What is here is
// the run: which arms exist, what disqualifies the whole run, and how the result reads.

(function () {
  "use strict";

  var Arm = window.PaintArm.Arm;
  var median = window.PaintArm.median;
  var COLS = window.PaintArm.COLS;
  var ROWS = window.PaintArm.ROWS;
  var WRITES = window.PaintArm.WRITES;
  var SUSTAIN_MS = window.PaintArm.SUSTAIN_MS;
  var RECOVERIES = window.PaintArm.RECOVERIES;
  var LF = window.PaintArm.LF;

  var SIZES = [
    { label: "1 KB", chars: 1024 },
    { label: "16 KB", chars: 16 * 1024 },
    { label: "64 KB", chars: 64 * 1024 },
  ];

  function refusalsFor(arms) {
    var refusals = [];
    arms.forEach(function (arm) {
      if (!arm.wroteSomething) {
        refusals.push(arm.label + ": the buffer does not contain the marker this arm wrote, so the "
          + "spans timed something that never reached the screen");
      }
      if (!arm.sustainWrote) {
        refusals.push(arm.label + ": the buffer does not contain the marker the SUSTAINED phase "
          + "wrote, so that phase's rates describe writes that never reached the screen");
      }
      if (arm.sustainTimeouts) {
        refusals.push(arm.label + ": " + arm.sustainTimeouts + " sustained write(s) never called "
          + "back, so the window was cut short and its rates are over an unknown span");
      }
      if (!arm.sustained.renders || !arm.sustained.writes) {
        refusals.push(arm.label + ": the sustained phase produced " + arm.sustained.writes
          + " write(s) and " + arm.sustained.renders + " render(s), so there is no rate to report");
      }
      if (arm.foundAbsent) {
        refusals.push(arm.label + ": a marker no arm writes was found, so the search is not "
          + "searching and its absences mean nothing");
      }
      if (arm.timedOut || arm.noRender || arm.badSpans) {
        refusals.push(arm.label + ": " + arm.timedOut + " write(s) never called back, "
          + arm.noRender + " never rendered and " + arm.badSpans + " produced a span that is not a "
          + "positive finite number");
      }
      if (arm.contextLosses) {
        refusals.push(arm.label + ": the WebGL context was lost " + arm.contextLosses + " time(s) "
          + "during the run, so this arm is partly the renderer it was measuring against");
      }
      // THE LEDGER HAS TO BALANCE. Every write is either a usable sample or one of the four named
      // exclusions; a total that does not add up means writes are going somewhere unaccounted for.
      var accounted = arm.parseMs.length + arm.timedOut + arm.noRender + arm.badSpans
        + arm.renderedBeforeParse;
      if (accounted !== WRITES) {
        refusals.push(arm.label + ": " + accounted + " writes are accounted for out of " + WRITES
          + ", so some went somewhere this probe does not name");
      }
      if (arm.parseMs.length * 2 < WRITES) {
        refusals.push(arm.label + ": only " + arm.parseMs.length + " of " + WRITES
          + " writes produced a usable sample, which is too few to publish a median from");
      }
      // THE RECOVERY PHASE'S OWN CONTROLS. The first is the ordinary positive one; the second is
      // the one that decides whether the phase measured a REPAINT at all. The sustained phase
      // leaves its marker on screen immediately before, so a `reset()` that reset nothing leaves
      // it there -- and every recovery figure would then be an append wearing a repaint's name.
      if (!arm.recoveryWrote) {
        refusals.push(arm.label + ": the buffer does not contain the marker the RECOVERY phase "
          + "wrote, so its spans timed something that never reached the screen");
      }
      // TWO OBLIGATIONS, NAMED APART. A recovery has to RESET (its scrollback sentinel is gone)
      // and to REPAINT (its own payload marker is on screen). One counter for both reported a
      // carrier that reset twenty times and painted once as a reset failure.
      if (arm.recoveryNotReset) {
        refusals.push(arm.label + ": " + arm.recoveryNotReset + " recovery(ies) left their own "
          + "scrollback sentinel in place, so they appended rather than resetting");
      }
      if (arm.recoveryNotPainted) {
        refusals.push(arm.label + ": " + arm.recoveryNotPainted + " recovery(ies) reset but left "
          + "no payload marker on screen, so the span timed a repaint that never arrived");
      }
      // A REFUSAL OF ITS OWN, NOT ONLY A LEDGER ENTRY. Moving these counters off the paced
      // phase fixed the misattribution and LOST THE REJECTION: `recoveryBadSpans` was read only
      // by the identity, which it balances, so one interval that is not a number published with
      // nineteen samples and no refusal at all. A render that finished before its own parse
      // callback is a legitimate ordering exclusion; a span that is not a positive finite number
      // is the instrument saying it does not know what it measured.
      if (arm.recoveryTimeouts || arm.recoveryNoRender || arm.recoveryBadSpans) {
        refusals.push(arm.label + ": " + arm.recoveryTimeouts + " recovery write(s) never called "
          + "back, " + arm.recoveryNoRender + " never rendered and " + arm.recoveryBadSpans
          + " produced a span that is not a positive finite number");
      }
      if (arm.recoveryMs.length * 2 < RECOVERIES) {
        refusals.push(arm.label + ": only " + arm.recoveryMs.length + " of " + RECOVERIES
          + " recoveries produced a usable sample, which is too few to publish a median from");
      }
      // THE RECOVERY PHASE'S OWN IDENTITY. Samples plus every named exclusion must be the
      // number of recoveries attempted; anything else means a repaint went somewhere this
      // probe does not name. The paced phase has had this check from the start and the
      // recovery phase shipped without one, which is how its exclusions ended up in the
      // paced phase's counter.
      var recoveryAccounted = arm.recoveryMs.length + arm.recoveryTimeouts
        + arm.recoveryNoRender + arm.recoveryBadSpans + arm.recoveryRenderedBeforeParse
        + arm.recoveryNotReset + arm.recoveryNotPainted;
      if (recoveryAccounted !== RECOVERIES) {
        refusals.push(arm.label + ": " + recoveryAccounted + " recoveries are accounted for "
          + "out of " + RECOVERIES + ", so some went somewhere this probe does not name");
      }
    });
    // THE CONTROL THAT MATTERS MOST. "We loaded the addon" is exactly the kind of claim that
    // reports success and changes nothing, and two arms running one renderer would publish a
    // difference of zero and read as a reassuring result.
    var webglShapes = arms.filter(function (a) { return a.webgl; }).map(function (a) { return a.shape; });
    var domShapes = arms.filter(function (a) { return !a.webgl; }).map(function (a) { return a.shape; });
    var sameShape = webglShapes.some(function (w) {
      return domShapes.some(function (d) { return w.canvases === d.canvases && w.rowDivs === d.rowDivs; });
    });
    if (sameShape) {
      refusals.push("the WebGL and DOM arms left the SAME renderer DOM (canvases and row divs both "
        + "equal), so they are one renderer measured twice and any difference between them is not "
        + "a difference between renderers");
    }
    if (!webglShapes.length || !domShapes.length) {
      refusals.push("one of the two renderer configurations produced no arm at all");
    }
    return refusals;
  }

  function report(arms) {
    var lines = ["HOP 5b: WHAT THE BROWSER PAYS TO PAINT A CONSOLE FRAME, at " + COLS + "x" + ROWS
      + ", " + WRITES + " writes per arm.",
      "ALL THREE SPANS START AT term.write(). PARSE ends at its callback, PAINTED at the render that",
      "write caused, FRAMED at the next requestAnimationFrame -- a PROXY for reaching the",
      "compositor, not a pixel. PAINT-ONLY is PAINTED minus PARSE, a difference of two spans from",
      "one origin rather than a span starting where another ended.",
      "",
      "  renderer     offered B   parse p50  painted p50   paint-only    framed p50   n   canvas rows"];
    arms.forEach(function (arm) {
      lines.push("  " + arm.label.padEnd(13)
        + String(arm.bytes).padStart(8)
        + median(arm.parseMs).toFixed(3).padStart(12)
        + median(arm.paintedMs).toFixed(3).padStart(13)
        + median(arm.paintOnlyMs).toFixed(3).padStart(13)
        + median(arm.framedMs).toFixed(3).padStart(14)
        + String(arm.parseMs.length).padStart(4)
        + String(arm.shape.canvases).padStart(9)
        + String(arm.shape.rowDivs).padStart(5));
    });
    lines.push("");
    lines.push("WHAT ONE RECOVERY REPAINT COSTS -- term.reset() then a whole screen, timed from the");
    lines.push("RESET. This is the browser's half of what a detected sequence gap pays; the HTTP");
    lines.push("refetch in front of it is measured elsewhere and is not in these numbers. No other");
    lines.push("phase here has ever timed a reset -- both of the others APPEND to a live screen.");
    lines.push("BOTH spans start at the reset, so neither isolates it and their paired difference is");
    lines.push("the paint AFTER the parse callback, not the reset's own cost. Nothing here measures");
    lines.push("what the reset alone costs.");
    lines.push("");
    lines.push("  renderer     offered B   parse p50  repaint p50   paint after parse    n");
    arms.forEach(function (arm) {
      lines.push("  " + arm.label.padEnd(13)
        + String(arm.bytes).padStart(8)
        + median(arm.recoveryParseMs).toFixed(3).padStart(12)
        + median(arm.recoveryMs).toFixed(3).padStart(13)
        // PAIRED, PER RECOVERY, and named for what it is. This was a difference of two
        // independently-taken medians labelled `reset+paint` -- wrong twice: both spans START at
        // the reset, so subtracting removes it rather than isolating it, and a difference of
        // medians is not the median of paired differences (review's control: 2 against 100).
        + median(arm.recoveryPaintOnlyMs).toFixed(3).padStart(21)
        + String(arm.recoveryMs.length).padStart(5));
    });
    var ordering = arms.reduce(function (sum, arm) { return sum + arm.renderedBeforeParse; }, 0);
    lines.push("");
    lines.push("EXCLUDED, NOT HIDDEN: " + ordering + " write(s) across all arms rendered before "
      + "their own parse callback fired, so PAINTED minus PARSE is not a renderer cost for them.");
    lines.push("PAINT-ONLY IS THE MEDIAN OF PAIRED PER-WRITE DIFFERENCES, not the difference "
      + "between two independently-taken medians.");
    lines.push("");
    lines.push("SUSTAINED: output offered as fast as the parser takes it for " + SUSTAIN_MS
      + "ms, which is the load `index.html` blames the DOM renderer for. A renderer that cannot");
    lines.push("keep up RENDERS FEWER TIMES for the same offered bytes -- xterm coalesces onto "
      + "animation frames -- so renders/s is the column that separates them, not ms per render.");
    lines.push("");
    lines.push("  renderer      writes   renders   renders/s   offered MB/s   writes per render");
    arms.forEach(function (arm) {
      var seconds = arm.sustained.wallMs / 1000;
      lines.push("  " + arm.label.padEnd(13)
        + String(arm.sustained.writes).padStart(8)
        + String(arm.sustained.renders).padStart(10)
        + (arm.sustained.renders / seconds).toFixed(1).padStart(12)
        + (arm.sustained.bytes / 1048576 / seconds).toFixed(1).padStart(15)
        + (arm.sustained.writes / arm.sustained.renders).toFixed(2).padStart(20));
    });
    return lines.join(LF);
  }

  window.runProbe = async function runProbe() {
    var stage = document.getElementById("stage");
    var missing = [];
    if (!window.Terminal) missing.push("xterm.js did not load");
    if (!window.WebglAddon || !window.WebglAddon.WebglAddon) missing.push("addon-webgl.js did not load");
    if (missing.length) return { refusals: missing, report: null };

    var arms = [];
    SIZES.forEach(function (size) {
      arms.push(new Arm({ label: "webgl " + size.label, webgl: true, targetChars: size.chars }));
      arms.push(new Arm({ label: "dom   " + size.label, webgl: false, targetChars: size.chars }));
    });

    // ONE LIVE TERMINAL AT A TIME. Mounting all six and disposing at the end left up to five other
    // terminals rendering alongside the arm being timed, and five live WebGL contexts competing for
    // the browser's limit -- so a later arm would have been measured under a load the first arm
    // never saw, and a context lost to that limit would have read as a renderer finding.
    for (var i = 0; i < arms.length; i += 1) {
      arms[i].mount(stage);
      await arms[i].run();
      arms[i].dispose();
    }

    var refusals = refusalsFor(arms);
    var out = refusals.length ? null : report(arms);
    var rows = arms.map(function (arm) {
      return {
        label: arm.label, bytes: arm.bytes, webgl: arm.webgl,
        parseP50: median(arm.parseMs), paintedP50: median(arm.paintedMs),
        paintOnlyP50: median(arm.paintOnlyMs), framedP50: median(arm.framedMs),
        samples: arm.parseMs.length, renderedBeforeParse: arm.renderedBeforeParse,
        shape: arm.shape, contextLosses: arm.contextLosses, sustained: arm.sustained,
      };
    });
    document.getElementById("out").textContent = out || ("REFUSED:" + LF + refusals.join(LF));
    return { refusals: refusals, report: out, rows: rows };
  };
}());
