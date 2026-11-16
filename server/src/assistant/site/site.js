// Kelo website: the pairing panel (an address with #pair=CODE, opened from a pairing QR or link), the language link, the install
// block (OS tabs, copy buttons), the phone menu and the hero's entrance. Everything works without this file; it only adds to the page.
(function () {
  "use strict";
  var MARKER = "#pair=";

  function hasPairing() {
    var h = location.hash || "";
    return h.indexOf(MARKER) === 0 && h.length > MARKER.length;
  }

  // the language link keeps a pairing fragment, so switching language does not lose the link
  function keepHash() {
    var links = document.querySelectorAll("a[data-keep-hash]");
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var base = a.getAttribute("href").split("#")[0];
      a.setAttribute("href", hasPairing() ? base + location.hash : base);
    }
  }

  function showPairing() {
    var panel = document.getElementById("pair");
    if (!panel) return;
    if (!hasPairing()) { panel.hidden = true; return; }
    panel.hidden = false;
    var isAndroid = /Android/i.test(navigator.userAgent || "");
    var open = document.getElementById("pair-open");
    var other = document.getElementById("pair-other");
    if (open) open.hidden = !isAndroid;
    if (other) other.hidden = isAndroid;
    var title = document.getElementById("pair-title");
    if (title) { title.setAttribute("tabindex", "-1"); title.focus({ preventScroll: true }); }
  }

  function copyLink() {
    var out = document.getElementById("pair-copied");
    var text = location.href;
    function done(ok) { if (out) out.textContent = ok ? out.getAttribute("data-ok") : out.getAttribute("data-fail"); }
    writeText(text, done);
  }

  function fallback(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  var KO = (document.documentElement.lang || "") === "ko";
  var WORDS = KO ? { copy: "복사", done: "복사했어요", fail: "복사 안 됨" } : { copy: "Copy", done: "Copied", fail: "Can't copy" };

  function writeText(text, then) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { then(true); }, function () { then(fallback(text)); });
    } else {
      then(fallback(text));
    }
  }

  // copy buttons: next to the one-line installs, and one added to every command block on the text pages
  function setupCopy() {
    var blocks = document.querySelectorAll(".prose pre");
    for (var i = 0; i < blocks.length; i++) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "copy"; b.setAttribute("data-copy", ""); b.textContent = WORDS.copy;
      blocks[i].classList.add("has-copy");
      blocks[i].appendChild(b);
    }
    document.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-copy]") : null;
      if (!btn) return;
      var code = btn.parentNode.querySelector("code");
      if (!code) return;
      writeText(code.textContent.trim(), function (ok) {
        btn.textContent = ok ? WORDS.done : WORDS.fail;
        btn.classList.toggle("done", ok);
        clearTimeout(btn._t);
        btn._t = setTimeout(function () { btn.textContent = WORDS.copy; btn.classList.remove("done"); }, 1800);
      });
    });
  }

  // install block: macOS/Linux and Windows tabs (without this script both lines simply show)
  function setupTabs() {
    var boxes = document.querySelectorAll("[data-tabs]");
    for (var i = 0; i < boxes.length; i++) {
      (function (box) {
        var tabs = box.querySelectorAll("[data-tab]");
        var panels = box.querySelectorAll("[data-panel]");
        function pick(name) {
          for (var j = 0; j < tabs.length; j++) tabs[j].setAttribute("aria-selected", tabs[j].getAttribute("data-tab") === name ? "true" : "false");
          for (var k = 0; k < panels.length; k++) panels[k].hidden = panels[k].getAttribute("data-panel") !== name;
        }
        for (var j = 0; j < tabs.length; j++) tabs[j].addEventListener("click", function () { pick(this.getAttribute("data-tab")); });
        box.classList.add("tabs-on");
        pick(/Windows/i.test(navigator.userAgent || "") ? "ps" : "sh");
      })(boxes[i]);
    }
  }

  // the hero's entrance, once on load (enter.js set html.m-enter): the h1's words rise out of masks line by line, then the
  // pill, lead and buttons fade up, and the screens settle. Under reduced motion, or if this runs after the 700 ms mark, the
  // hero simply stands. The h1 gets its own text back when it is done.
  function enterHero() {
    var hero = document.querySelector(".hero[data-enter]");
    var root = document.documentElement;
    if (!hero) return;
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || root.classList.contains("m-enter-done")) { hero.classList.add("is-entered"); return; }
    var h1 = hero.querySelector("h1");
    var saved = h1.innerHTML;
    var words = h1.textContent.trim().split(/\s+/);
    h1.setAttribute("aria-label", h1.textContent.trim());
    h1.innerHTML = words.map(function (w) { return '<span class="w" aria-hidden="true"><span></span></span>'; }).join(" ");
    var spans = h1.querySelectorAll(".w > span");
    for (var i = 0; i < spans.length; i++) spans[i].textContent = words[i];
    hero.classList.add("is-entering");
    // one delay per line (80 ms apart, at most 240 ms from the first line to the last)
    var tops = [], ws = h1.querySelectorAll(".w");
    for (var j = 0; j < ws.length; j++) {
      var top = ws[j].offsetTop, line = tops.indexOf(top);
      if (line < 0) { tops.push(top); line = tops.length - 1; }
      spans[j].style.setProperty("--d", Math.min(line * 80, 240) + "ms");
    }
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { hero.classList.add("is-entered"); });
    });
    setTimeout(function () {
      hero.classList.remove("is-entering");
      h1.innerHTML = saved;
      h1.removeAttribute("aria-label");
    }, 1300);
  }

  // the phone menu: the toggle opens the links as a full-screen sheet; a tap on a link, Esc or the toggle closes it
  function setupMenu() {
    var btn = document.querySelector(".nav__toggle");
    var menu = document.getElementById("site-menu");
    if (!btn || !menu) return;
    var root = document.documentElement;
    function set(open) { root.classList.toggle("nav-open", open); btn.setAttribute("aria-expanded", open ? "true" : "false"); }
    btn.addEventListener("click", function () { set(!root.classList.contains("nav-open")); });
    menu.addEventListener("click", function (e) { if (e.target.closest && e.target.closest("a")) set(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
  }


  // v2 pages: the scroll thread. One pass per frame reads where the sections are and sets
  //  - html[data-tone]: the section in the middle of the screen picks the page's ground (the stylesheet fades between them);
  //  - the top bar's mascot and status: the innermost [data-mood] element in the middle of the screen;
  //  - the request walk-through: the step in the middle of the screen, and the matching screen on the pinned phone;
  //  - html.scrolled once the page has moved, for the bar's bottom line.
  function setupThread() {
    var root = document.documentElement;
    if (!document.body.className.match(/design-/)) return;
    var tones = [].slice.call(document.querySelectorAll("[data-tone]"));
    var moods = [].slice.call(document.querySelectorAll("main [data-mood], .closing[data-mood]"));
    var steps = [].slice.call(document.querySelectorAll(".flow__step"));
    var frames = [].slice.call(document.querySelectorAll(".flow__frame, .flow__dot"));
    var face = document.querySelector(".nav__moods");
    var status = document.querySelector(".nav__status");
    var labels = {};
    if (status) status.getAttribute("data-labels").split("|").forEach(function (p) { var i = p.indexOf(":"); labels[p.slice(0, i)] = p.slice(i + 1); });
    var last = {}, queued = false;
    function inMiddle(el, line) { var r = el.getBoundingClientRect(); return r.top <= line && r.bottom > line && r.height > 0; }
    function pick(list, line) { var got = null; for (var i = 0; i < list.length; i++) if (inMiddle(list[i], line)) got = list[i]; return got; }
    function above(list, line) {
      var got = null;
      for (var i = 0; i < list.length; i++) { var r = list[i].getBoundingClientRect(); if (r.height > 0 && r.bottom <= line) got = list[i]; }
      return got;
    }
    function update() {
      queued = false;
      var h = window.innerHeight, line = h * 0.5;
      root.classList.toggle("scrolled", window.scrollY > 8);
      // nothing under the line (the footer, a gap): keep the ground of the last section above it, so the page never jumps back
      var t = pick(tones, line) || above(tones, line), tone = t ? t.getAttribute("data-tone") : "0";
      if (tone !== last.tone) { root.setAttribute("data-tone", tone); last.tone = tone; }
      var m = pick(moods, h * 0.45), mood = m ? m.getAttribute("data-mood") : "idle";
      if (mood !== last.mood && face) {
        last.mood = mood;
        face.setAttribute("data-mood", mood);
        if (status && labels[mood]) {
          status.classList.add("is-swap");
          clearTimeout(status._t);
          status._t = setTimeout(function () { status.textContent = labels[mood]; status.classList.remove("is-swap"); }, 180);
        }
      }
      var s = pick(steps, line);
      if (s) {
        var n = s.getAttribute("data-step");
        if (n !== last.step) {
          last.step = n;
          steps.forEach(function (el) { el.classList.toggle("is-on", el === s); });
          frames.forEach(function (el) { el.classList.toggle("is-on", el.getAttribute("data-step") === n); });
        }
      }
    }
    function queue() { if (!queued) { queued = true; requestAnimationFrame(update); } }
    window.addEventListener("scroll", queue, { passive: true });
    window.addEventListener("resize", queue);
    update();
  }

  function closePanel() {
    var panel = document.getElementById("pair");
    if (panel) panel.hidden = true;
  }

  document.addEventListener("DOMContentLoaded", function () {
    keepHash();
    showPairing();
    var copy = document.getElementById("pair-copy");
    if (copy) copy.addEventListener("click", copyLink);
    var close = document.getElementById("pair-close");
    if (close) close.addEventListener("click", closePanel);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePanel(); });
    setupCopy();
    setupTabs();
    setupMenu();
    enterHero();
    setupThread();
  });
  window.addEventListener("hashchange", function () { keepHash(); showPairing(); });
})();
