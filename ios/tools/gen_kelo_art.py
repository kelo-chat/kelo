"""Turns the Kelo character SVGs (android/design/character/kelo_<mood>.svg) into Swift data (KeloArt.swift) for KeloCore's
character player (ios/KeloCore/Sources/KeloCore/Character/KeloMotion.swift). Modelled on android/design/character/gen_art.py,
which writes the Android app's KeloArt.kt from the same SVGs, and kept in step with it:
  - every shape in SVG order as path data, its fill/stroke resolved from the groups around it (circles, ellipses, rects,
    polygons and lines become path data; a rect's rotate() attribute is applied to its corners),
  - every CSS-animated element as a group (its CSS class, its parent group, the range of shapes it holds) carrying a KeloAnim:
    duration, delay, timing function, alternate, transform-origin and its @keyframes as tracks (transform, opacity, fill), with
    transform lists normalised to translate/scale/rotate and padded with identity functions the way browsers interpolate them.
Only the Python standard library is used.
Run (from the repository root):
  python3 ios/tools/gen_kelo_art.py android/design/character ios/KeloCore/Sources/KeloCore/Character/KeloArt.swift"""
import math
import re
import sys
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"
INHERIT = ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin")
# the moods in the order of KeloMood (and of the Android app's KeloMood)
MOODS = ("idle", "working", "browser", "approval", "thinking", "done", "error", "offline", "sleeping", "listening", "reading")
EASING = {"linear": ".linear", "ease": ".ease", "ease-in": ".easeIn", "ease-out": ".easeOut", "ease-in-out": ".easeInOut"}
CAPS = {"butt": ".butt", "round": ".round", "square": ".square"}
JOINS = {"miter": ".miter", "round": ".round", "bevel": ".bevel"}
OPS = {"T": ".translate", "S": ".scale", "R": ".rotate"}


def num(v):
    f = float(v)
    s = ("%.4f" % f).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def dbl(v):
    """A Swift Double literal (always with a decimal point, so nothing has to be inferred)."""
    s = num(v)
    return s if "." in s else s + ".0"


# ---- shapes -----------------------------------------------------------------------------------------------------------------------
def ellipse_d(cx, cy, rx, ry):
    return f"M{num(cx - rx)} {num(cy)}A{num(rx)} {num(ry)} 0 1 0 {num(cx + rx)} {num(cy)}A{num(rx)} {num(ry)} 0 1 0 {num(cx - rx)} {num(cy)}Z"


def rect_d(x, y, w, h, rx):
    if rx <= 0:
        return f"M{num(x)} {num(y)}H{num(x + w)}V{num(y + h)}H{num(x)}Z"
    rx = min(rx, w / 2, h / 2)
    return (f"M{num(x + rx)} {num(y)}H{num(x + w - rx)}A{num(rx)} {num(rx)} 0 0 1 {num(x + w)} {num(y + rx)}"
            f"V{num(y + h - rx)}A{num(rx)} {num(rx)} 0 0 1 {num(x + w - rx)} {num(y + h)}"
            f"H{num(x + rx)}A{num(rx)} {num(rx)} 0 0 1 {num(x)} {num(y + h - rx)}"
            f"V{num(y + rx)}A{num(rx)} {num(rx)} 0 0 1 {num(x + rx)} {num(y)}Z")


def rotate_pts(pts, deg, cx, cy):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def poly_d(pts, close):
    return "M" + " L".join(f"{num(x)} {num(y)}" for x, y in pts) + ("Z" if close else "")


def zero_area(tag, d):
    if tag == "line":
        return True
    if re.search(r"[^MLml0-9.,\s-]", d):
        return False
    subs = [s for s in re.split(r"(?=[Mm])", d) if s.strip()]
    return all(len(re.findall(r"-?[\d.]+", s)) <= 4 for s in subs)


def argb(v):
    v = v.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    assert re.fullmatch(r"[0-9A-Fa-f]{6}", v), v
    return "0xFF" + v.upper()


def color(v):
    return "nil" if v is None or v == "none" else argb(v)


def shape_d(el):
    tag = el.tag.replace(NS, "")
    a = el.attrib
    f = lambda k, dflt=0.0: float(a.get(k, dflt))
    if tag == "path":
        return tag, re.sub(r"\s+", " ", a["d"].strip())
    if tag == "circle":
        return tag, ellipse_d(f("cx"), f("cy"), f("r"), f("r"))
    if tag == "ellipse":
        return tag, ellipse_d(f("cx"), f("cy"), f("rx"), f("ry"))
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        rx = f("rx", a.get("ry", 0))
        tr = a.get("transform")
        if tr:
            m = re.fullmatch(r"rotate\(\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\)", tr.strip())
            assert m and rx == 0, tr
            return tag, poly_d(rotate_pts([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], *map(float, m.groups())), True)
        return tag, rect_d(x, y, w, h, rx)
    if tag == "polygon":
        n = [float(v) for v in re.findall(r"-?[\d.]+", a["points"])]
        return tag, poly_d(list(zip(n[::2], n[1::2])), True)
    if tag == "line":
        return tag, poly_d([(f("x1"), f("y1")), (f("x2"), f("y2"))], False)
    raise ValueError(tag)


# ---- CSS ------------------------------------------------------------------------------------------------------------------------
def parse_css(css):
    keyframes, rules = {}, {}
    for name, body in re.findall(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", css):
        frames = []
        for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
            props = dict((k.strip(), v.strip()) for k, v in (p.split(":", 1) for p in decl.split(";") if p.strip()))
            for s in sel.split(","):
                s = s.strip()
                off = 0.0 if s == "from" else 1.0 if s == "to" else float(s.rstrip("%")) / 100
                frames.append((off, props))
        keyframes[name] = sorted(frames, key=lambda x: x[0])
    css_rules = re.sub(r"@keyframes\s+[\w-]+\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}", "", css)
    for sel, decl in re.findall(r"\.([\w-]+)\s*\{([^{}]*)\}", css_rules):
        rules[sel] = dict((k.strip(), v.strip()) for k, v in (p.split(":", 1) for p in decl.split(";") if p.strip()))
    return keyframes, rules


def secs(tok):
    return float(tok[:-2]) / 1000 if tok.endswith("ms") else float(tok[:-1])


def parse_transform(v):
    """-> [(op, a, b)] with op in T (translate), S (scale), R (rotate, b unused)."""
    out = []
    if v.strip() == "none":
        return out
    for fn, args in re.findall(r"([\w]+)\(([^)]*)\)", v):
        n = [float(re.sub(r"(px|deg)$", "", x.strip())) for x in re.split(r"[,\s]+", args.strip()) if x.strip()]
        if fn == "translate":
            out.append(("T", n[0], n[1] if len(n) > 1 else 0.0))
        elif fn == "translateX":
            out.append(("T", n[0], 0.0))
        elif fn == "translateY":
            out.append(("T", 0.0, n[0]))
        elif fn == "scale":
            out.append(("S", n[0], n[1] if len(n) > 1 else n[0]))
        elif fn == "scaleX":
            out.append(("S", n[0], 1.0))
        elif fn == "scaleY":
            out.append(("S", 1.0, n[0]))
        elif fn == "rotate":
            out.append(("R", n[0], 0.0))
        else:
            raise ValueError(fn)
    return out


IDENTITY = {"T": (0.0, 0.0), "S": (1.0, 1.0), "R": (0.0, 0.0)}


def dbls(vals):
    return "[" + ", ".join(dbl(v) for v in vals) + "]"


def anim_for(cls, rules, keyframes, attr_opacity):
    """-> (Swift KeloAnim(...) expression, period in seconds)"""
    rule = rules[cls]
    toks = rule["animation"].split()
    name = toks[0]
    times = [secs(t) for t in toks[1:] if re.fullmatch(r"[\d.]+m?s", t)]
    dur, delay = times[0], (times[1] if len(times) > 1 else 0.0)
    assert not any(t.startswith(("cubic-bezier", "steps")) for t in toks), rule   # not supported by the player (unused)
    easing = next((EASING[t] for t in toks if t in EASING), ".ease")
    alternate = "alternate" in toks
    assert "infinite" in toks, rule
    assert "transform-box" not in rule, rule
    origin = rule.get("transform-origin", "0 0").strip()
    if origin == "center":
        ox, oy = 60.0, 60.0          # transform-box view-box: the middle of the 120 × 120 viewBox
    else:
        ox, oy = (float(x.replace("px", "")) for x in origin.split())
    base_opacity = float(rule.get("opacity", attr_opacity if attr_opacity is not None else 1))
    frames = keyframes[name]
    for _, p in frames:
        assert "animation-timing-function" not in p, (cls, p)   # per-keyframe timing functions are not supported (unused)

    def track(prop):
        return [(o, p[prop]) for o, p in frames if prop in p]

    # transform: normalise, pad to the longest list (browsers pad the shorter list with identity functions)
    tf = [(o, parse_transform(v)) for o, v in track("transform")]
    ops = []
    if tf:
        if tf[0][0] > 0:
            tf.insert(0, (0.0, []))
        if tf[-1][0] < 1:
            tf.append((1.0, []))
        longest = max((t for _, t in tf), key=len)
        ops = [op for op, _, _ in longest]
        for _, t in tf:
            assert [op for op, _, _ in t] == ops[:len(t)], (cls, tf)
        tf = [(o, [(op, a, b) for op, a, b in t] + [(op, *IDENTITY[op]) for op in ops[len(t):]]) for o, t in tf]
    op_ = [(o, float(v)) for o, v in track("opacity")]
    if op_:
        if op_[0][0] > 0:
            op_.insert(0, (0.0, base_opacity))
        if op_[-1][0] < 1:
            op_.append((1.0, base_opacity))
    fi = [(o, v) for o, v in track("fill")]
    assert not fi or (fi[0][0] == 0 and fi[-1][0] == 1), (cls, fi)
    pad = " " * 16
    lines = [f"durationMs: {round(dur * 1000)}, delayMs: {round(delay * 1000)}, easing: {easing}, "
             f"alternate: {'true' if alternate else 'false'}, origin: KeloPoint(x: {dbl(ox)}, y: {dbl(oy)}),"]
    lines.append("ops: [" + ", ".join(OPS[o] for o in ops) + "],")
    lines.append("tKeys: " + dbls(o for o, _ in tf) + ",")
    lines.append("tVals: " + dbls(v for _, t in tf for _, a, b in t for v in (a, b)) + ",")
    lines.append("oKeys: " + dbls(o for o, _ in op_) + ",")
    lines.append("oVals: " + dbls(v for _, v in op_) + ",")
    lines.append(f"baseOpacity: {dbl(base_opacity)},")
    lines.append("fKeys: " + dbls(o for o, _ in fi) + ",")
    lines.append("fVals: [" + ", ".join(argb(v) for _, v in fi) + "]")
    return "KeloAnim(\n" + "\n".join(pad + x for x in lines) + "\n            )", dur * (2 if alternate else 1)


# ---- the tree -------------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, keyframes, rules):
        self.rules, self.keyframes = rules, keyframes
        self.shapes = []   # Swift KeloShape(...) expressions
        self.groups = []   # dicts: name, parent, start, end, anim
        self.stack = []    # indices of the open groups
        self.periods = []


def walk(ctx, el, st):
    for ch in el:
        tag = ch.tag.replace(NS, "")
        if tag in ("style", "title", "desc", "defs"):
            assert tag != "defs", "defs are not supported"
            continue
        s = dict(st)
        for k in INHERIT:
            if k in ch.attrib:
                s[k] = ch.attrib[k]
        cls = ch.attrib.get("class", "").strip()
        gi = None
        if cls and "animation" in ctx.rules.get(cls, {}):
            code, period = anim_for(cls, ctx.rules, ctx.keyframes, ch.attrib.get("opacity"))
            ctx.periods.append(period)
            gi = len(ctx.groups)
            ctx.groups.append({"name": cls, "parent": ctx.stack[-1] if ctx.stack else None, "start": len(ctx.shapes), "anim": code})
            ctx.stack.append(gi)
        else:
            assert tag != "g" or "transform" not in ch.attrib, ch.attrib
            assert "opacity" not in ch.attrib, ch.attrib   # a static opacity is only supported on animated elements
        if tag == "g":
            walk(ctx, ch, s)
        else:
            shape(ctx, ch, s)
        if gi is not None:
            ctx.stack.pop()
            ctx.groups[gi]["end"] = len(ctx.shapes)


def shape(ctx, el, st):
    tag, d = shape_d(el)
    assert '"' not in d and "\\" not in d, d
    fill = st.get("fill", "#000000")
    if zero_area(tag, d):
        fill = "none"
    stroke = st.get("stroke", "none")
    group = ctx.stack[-1] if ctx.stack else None
    ctx.shapes.append(f'KeloShape("{d}", fill: {color(fill)}, stroke: {color(stroke)}, width: {dbl(st.get("stroke-width", 1))}, '
                      f'cap: {CAPS[st.get("stroke-linecap", "butt")]}, join: {JOINS[st.get("stroke-linejoin", "miter")]}, '
                      f'group: {"nil" if group is None else group})')


def mood_code(name, ctx):
    shapes = ",\n".join("        " + s for s in ctx.shapes)
    groups = ",\n".join(
        f'        KeloGroup(\n            name: "{g["name"]}", parent: {"nil" if g["parent"] is None else g["parent"]}, '
        f'shapes: {g["start"]}..<{g["end"]},\n            anim: {g["anim"]}\n        )' for g in ctx.groups)
    return f"""    // MARK: kelo_{name}.svg

    private static let {name}Shapes: [KeloShape] = [
{shapes},
    ]

    private static let {name}Groups: [KeloGroup] = [
{groups},
    ]

    /// kelo_{name}.svg
    public static let {name}: KeloMoodArt = KeloMoodArt(mood: .{name}, shapes: {name}Shapes, groups: {name}Groups)"""


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 gen_kelo_art.py <svg dir> <out .swift>")
    src, dst = sys.argv[1], sys.argv[2]
    parts = []
    for name in MOODS:
        root = ET.parse(f"{src}/kelo_{name}.svg").getroot()
        assert root.attrib["viewBox"] == "0 0 120 120"
        css = "".join(e.text or "" for e in root.iter(NS + "style"))
        ctx = Ctx(*parse_css(css))
        walk(ctx, root, {})
        parts.append(mood_code(name, ctx))
    cases = "\n".join(f"        case .{m}: return {m}" for m in MOODS)
    body = "\n\n".join(parts)
    with open(dst, "w") as out:
        out.write(f"""// Generated from android/design/character/kelo_*.svg by ios/tools/gen_kelo_art.py. Do not edit by hand: change the SVGs and
// generate again (from the repository root):
//   python3 ios/tools/gen_kelo_art.py android/design/character ios/KeloCore/Sources/KeloCore/Character/KeloArt.swift
// Every shape in SVG order with its fill/stroke resolved from the groups around it, and every CSS-animated element as a KeloGroup
// carrying its KeloAnim (duration, delay, timing function, direction, transform-origin and @keyframes). The same data as the
// Android app's KeloArt.kt (android/design/character/gen_art.py).

// swiftlint:disable all
public enum KeloArt {{
    /// The SVGs' viewBox is 0 0 {120} {120}.
    public static let viewBox: Double = 120.0

    /// The drawing of a mood.
    public static func of(_ mood: KeloMood) -> KeloMoodArt {{
        switch mood {{
{cases}
        }}
    }}

{body}
}}
""")


main()
