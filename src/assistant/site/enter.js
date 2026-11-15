// Kelo website, loaded in <head> before the page paints (CSP allows no inline script). It marks the page for the stylesheet:
// html.js (the phone menu works), the page's kind for the layout rules, the motion layer, and html.m-enter so the hero's words
// wait for site.js to raise them instead of flashing; after 700 ms (m-enter-done) they show anyway. mo-js / mo-safe are the
// moments' own marks (site/moments/moments.js sets them too). Without this file nothing is hidden.
(function () {
  "use strict";
  var d = document.documentElement;
  d.className += (d.className ? " " : "") + "js m-enter mo-js";
  d.setAttribute("data-type", "software");
  d.setAttribute("data-radius", "soft");
  d.setAttribute("data-buttons", "arrow");
  d.setAttribute("data-motion", "");
  setTimeout(function () { d.classList.add("m-enter-done"); }, 700);
  setTimeout(function () { d.classList.add("mo-safe"); }, 2500);
})();
