"""CSS paths to the elements a page script picked, computed on the page and never written into it.

The page scripts used to mark an element with a data-kelo-* attribute so that Playwright could find it again; a page could see those
marks (addendum 9 of the browser-identity card). A path is a plain selector the page's own DOM answers for (an id when unique, else the
tag and its position among its siblings, from the nearest such id or the root), so nothing is added to the page. A path changes when an
element is inserted before it in the same parent: the caller uses it at once, like the marks it replaces.
"""

from __future__ import annotations

# the page-side function; it is pasted into each script that needs it (the script itself is never shared with the page)
PATH_FN = """function keloPath(e) {
    const seg = [];
    for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
      if (n.id && document.querySelectorAll('#' + CSS.escape(n.id)).length === 1) { seg.unshift('#' + CSS.escape(n.id)); break; }
      let k = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.localName === n.localName) k++;
      seg.unshift(n.localName + ':nth-of-type(' + k + ')');
    }
    return seg.join(' > ');
  }"""

# a script for a page function `(args) => {...}`: the path function is put at the top of its body
def with_path(script: str) -> str:
    cut = script.index("=> {") + len("=> {")
    return script[:cut] + " " + PATH_FN + "\n" + script[cut:]


# the path of the element a locator gave (evaluated on that element)
PATH_OF_JS = "(e) => { " + PATH_FN + " return keloPath(e); }"

# a selector that matches nothing on any page (a custom element name nobody defines): what a field that is not there looks up
NO_ELEMENT = "kelo-no-such-element"
