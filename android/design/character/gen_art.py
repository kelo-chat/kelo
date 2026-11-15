"""Turns the Kelo character SVGs into Kotlin data (KeloArt.kt) for the app's native renderer
(app/src/main/java/chat/kelo/app/ui/character/KeloCharacter.kt):
  - every shape in SVG order as path data, its fill/stroke resolved from the groups around it (circles, ellipses, rects,
    polygons and lines become path data; a rect's rotate() attribute is applied to its corners),
  - every CSS animation of the <style> block as an [Anim]: duration, delay, timing function, alternate, transform-origin and its
    @keyframes as tracks (transform, opacity, fill), with transform lists normalised to translate/scale/rotate and padded with
    identity functions the way browsers interpolate them.
With --icon, also writes the launcher icon's layers from the idle SVG's first frame.
Run: python3 gen_art.py <svg dir> <out .kt> [--icon <res dir>]"""
import math
import re
import sys
import xml.etree.ElementTree as ET
from functools import reduce

NS = "{http://www.w3.org/2000/svg}"
INHERIT = ("fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin")
# the moods in the order of the app's KeloMood
MOODS = ("idle", "working", "browser", "approval", "thinking", "done", "error", "offline", "sleeping", "listening", "reading")
EASING = {"linear": "LINEAR", "ease": "EASE", "ease-in": "EASE_IN", "ease-out": "EASE_OUT", "ease-in-out": "EASE_IN_OUT"}


def num(v):
    f = float(v)
    s = ("%.4f" % f).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def fl(v):
    return num(v) + "f"


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
    return "0xFF" + v.upper()


def color(v):
    return "null" if v is None or v == "none" else argb(v)


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


def anim_for(cls, rules, keyframes, attr_opacity):
    rule = rules[cls]
    toks = rule["animation"].split()
    name = toks[0]
    times = [secs(t) for t in toks[1:] if re.fullmatch(r"[\d.]+m?s", t)]
    dur, delay = times[0], (times[1] if len(times) > 1 else 0.0)
    easing = next((EASING[t] for t in toks if t in EASING), "EASE")
    alternate = "alternate" in toks
    assert "infinite" in toks, rule
    origin = rule.get("transform-origin", "0 0").strip()
    if origin == "center":
        ox, oy = 60.0, 60.0          # transform-box view-box: the middle of the 120 × 120 viewBox
    else:
        ox, oy = (float(x.replace("px", "")) for x in origin.split())
    base_opacity = float(rule.get("opacity", attr_opacity if attr_opacity is not None else 1))
    frames = keyframes[name]

    def track(prop):
        fs = [(o, p[prop]) for o, p in frames if prop in p]
        if not fs:
            return []
        return fs

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
    code = {"T": 0, "S": 1, "R": 2}
    parts = [f"durationMs = {round(dur * 1000)}", f"delayMs = {round(delay * 1000)}", f"easing = {easing}"]
    if alternate:
        parts.append("alternate = true")
    if ops:
        parts.append(f"ox = {fl(ox)}, oy = {fl(oy)}")
        parts.append("ops = intArrayOf(" + ", ".join(str(code[o]) for o in ops) + ")")
        parts.append("tKeys = floatArrayOf(" + ", ".join(fl(o) for o, _ in tf) + ")")
        parts.append("tVals = floatArrayOf(" + ", ".join(f"{fl(a)}, {fl(b)}" for _, t in tf for _, a, b in t) + ")")
    if op_:
        parts.append("oKeys = floatArrayOf(" + ", ".join(fl(o) for o, _ in op_) + ")")
        parts.append("oVals = floatArrayOf(" + ", ".join(fl(v) for _, v in op_) + ")")
    if base_opacity != 1:
        parts.append(f"baseOpacity = {fl(base_opacity)}")
    if fi:
        parts.append("fKeys = floatArrayOf(" + ", ".join(fl(o) for o, _ in fi) + ")")
        parts.append("fVals = longArrayOf(" + ", ".join(argb(v) for _, v in fi) + ")")
    return "Anim(" + ", ".join(parts) + ")", dur * (2 if alternate else 1)


# ---- the tree -------------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, rules, keyframes):
        self.rules, self.keyframes = rules, keyframes
        self.shapes, self.groups, self.periods = [], [], []


def animated(ctx, el):
    cls = el.attrib.get("class", "").strip()
    if not cls or "animation" not in ctx.rules.get(cls, {}):
        return None
    code, period = anim_for(cls, ctx.rules, ctx.keyframes, el.attrib.get("opacity"))
    ctx.periods.append(period)
    return code


def shape(ctx, el, st):
    tag, d = shape_d(el)
    fill = st.get("fill", "#000000")
    if zero_area(tag, d):
        fill = "none"
    stroke = st.get("stroke", "none")
    cap = {"butt": 0, "round": 1, "square": 2}[st.get("stroke-linecap", "butt")]
    join = {"miter": 0, "round": 1, "bevel": 2}[st.get("stroke-linejoin", "miter")]
    ctx.shapes.append({"d": d, "fill": fill, "stroke": stroke, "width": st.get("stroke-width", 1), "cap": st.get("stroke-linecap", "butt"),
                       "join": st.get("stroke-linejoin", "miter"), "group": ctx.groups[-1] if ctx.groups else None, "tag": tag})
    return f'S("{d}", {color(fill)}, {color(stroke)}, {fl(st.get("stroke-width", 1))}, {cap}, {join})'


def walk(ctx, el, st, depth):
    out = []
    pad = "    " * (depth + 1)
    for ch in el:
        tag = ch.tag.replace(NS, "")
        if tag == "style":
            continue
        s = dict(st)
        for k in INHERIT:
            if k in ch.attrib:
                s[k] = ch.attrib[k]
        anim = animated(ctx, ch)
        cls = ch.attrib.get("class", "")
        if anim:
            ctx.groups.append(cls)
        if tag == "g":
            inner = walk(ctx, ch, s, depth + 1)
        else:
            inner = [shape(ctx, ch, s)]
        if anim:
            ctx.groups.pop()
            out.append(f"G({anim}, listOf(\n" + ",\n".join(pad + "    " + x for x in inner) + f",\n{pad}))")
        else:
            assert tag != "g" or "transform" not in ch.attrib, ch.attrib
            out.extend(inner)
    return out


def lcm(a, b):
    return a * b // math.gcd(a, b)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    parts, idle_shapes, periods = [], [], []
    for name in MOODS:
        root = ET.parse(f"{src}/kelo_{name}.svg").getroot()
        assert root.attrib["viewBox"] == "0 0 120 120"
        css = "".join(e.text or "" for e in root.iter(NS + "style"))
        ctx = Ctx(*reversed(parse_css(css)))
        items = walk(ctx, root, {}, 1)
        if name == "idle":
            idle_shapes = list(ctx.shapes)
        periods += [round(p * 1000) for p in ctx.periods]
        parts.append(f"    /** kelo_{name}.svg */\n    val {name}: G = G(null, listOf(\n" + ",\n".join("        " + x for x in items) + ",\n    ))")
    body = "\n\n".join(parts)
    loop = reduce(lcm, sorted(set(periods)))
    open(dst, "w").write(f"""package chat.kelo.app.ui.character

import chat.kelo.app.ui.character.Anim.Companion.EASE
import chat.kelo.app.ui.character.Anim.Companion.EASE_IN
import chat.kelo.app.ui.character.Anim.Companion.EASE_IN_OUT
import chat.kelo.app.ui.character.Anim.Companion.EASE_OUT
import chat.kelo.app.ui.character.Anim.Companion.LINEAR

// Generated from android/design/character/kelo_*.svg by android/design/character/gen_art.py: every shape in SVG order with its
// fill/stroke resolved from the groups around it, and every CSS-animated element as a [G] carrying its [Anim] (duration, delay,
// timing function, direction, transform-origin and @keyframes). Do not edit by hand: change the SVG and generate again.

@Suppress("LargeClass", "unused")
internal object KeloArt {{
    /** The time after which every animation of every mood repeats (ms), for reference */
    const val LOOP_MS = {loop}L

{body}

    fun of(mood: KeloMood): G = when (mood) {{
{chr(10).join(f"        KeloMood.{m.capitalize()} -> {m}" for m in MOODS)}
    }}
}}
""")
    if "--icon" in sys.argv:
        icon(idle_shapes, sys.argv[sys.argv.index("--icon") + 1])


# ---- the launcher icon -----------------------------------------------------------------------------------------------------------
# The idle character's first frame (the leaf at rotate(-4deg) about 60,28; everything else at rest), centred on the adaptive icon's
# 108 × 108 canvas at 0.56 scale, so the whole sticker stays inside the 66 dp safe zone.
ICON_SCALE = 0.56
ICON_CENTER = (60, 56)   # the middle of the character's outline (x 14..106, y 3..109)


def vd_path(sh, indent):
    out = [f'{indent}<path android:pathData="{sh["d"]}"']
    if sh["fill"] not in (None, "none"):
        out.append(f'{indent}    android:fillColor="{sh["fill"].upper()}"')
    if sh["stroke"] not in (None, "none"):
        out.append(f'{indent}    android:strokeColor="{sh["stroke"].upper()}" android:strokeWidth="{num(sh["width"])}"'
                   f' android:strokeLineCap="{sh["cap"]}" android:strokeLineJoin="{sh["join"]}"')
    out[-1] += " />"
    return "\n".join(out)


def icon_group_open():
    tx = 54 - ICON_CENTER[0] * ICON_SCALE
    ty = 54 - ICON_CENTER[1] * ICON_SCALE
    return (f'    <group android:scaleX="{ICON_SCALE}" android:scaleY="{ICON_SCALE}" android:translateX="{num(tx)}" '
            f'android:translateY="{num(ty)}">')


def icon(shapes, res):
    head = ('<?xml version="1.0" encoding="utf-8"?>\n<!-- {what} Generated by android/design/character/gen_art.py with the icon option; do not '
            'edit by hand. -->\n<vector xmlns:android="http://schemas.android.com/apk/res/android"\n    android:width="108dp" android:height="108dp" '
            'android:viewportWidth="108" android:viewportHeight="108">\n')
    body = [icon_group_open()]
    for sh in shapes:
        if sh["group"] == "kelo-leaf":
            body.append('        <group android:rotation="-4" android:pivotX="60" android:pivotY="28">')
            body.append(vd_path(sh, "            "))
            body.append("        </group>")
        else:
            body.append(vd_path(sh, "        "))
    body.append("    </group>")
    open(f"{res}/drawable/ic_launcher_foreground.xml", "w").write(
        head.format(what="the launcher icon: Kelo's character at rest (the first frame of kelo_idle.svg).") + "\n".join(body) + "\n</vector>\n")
    # themed icon: one colour; the white outline layer is the silhouette, the eyes and the beak are holes in the body
    sticker = [sh for sh in shapes if sh["stroke"] == "#FFFFFF" and sh["group"] == "kelo-breathe"]
    body_d = next(sh["d"] for sh in shapes if sh["fill"] == "#29B6F6")
    holes = [sh["d"] for sh in shapes if sh["group"] == "kelo-eyes" and sh["fill"] == "#1A1A1A"]
    holes += [sh["d"] for sh in shapes if sh["fill"] == "#FFB300"]
    mono = [icon_group_open()]
    for sh in sticker:
        if sh["d"] == body_d:
            mono.append(f'        <path android:pathData="{sh["d"]}" android:strokeColor="#FFFFFFFF" android:strokeWidth="10"'
                        ' android:strokeLineJoin="round" />')
        else:
            mono.append(vd_path({**sh, "fill": None if sh["fill"] == "none" else "#FFFFFF"}, "        "))
    mono.append(f'        <path android:fillType="evenOdd" android:fillColor="#FFFFFFFF" android:pathData="{body_d} {" ".join(holes)}" />')
    mono.append("    </group>")
    open(f"{res}/drawable/ic_launcher_monochrome.xml", "w").write(
        head.format(what="themed (monochrome) icon: Kelo's silhouette, the eyes and the beak left open.") + "\n".join(mono) + "\n</vector>\n")


main()
