#!/usr/bin/env python3
"""Compare Kelo APKs / App Bundles by what is inside, ignoring only signatures and the stamps Google Play adds.

Same idea as Signal's apkdiff (https://github.com/signalapp/Signal-Android/tree/main/reproducible-builds/apkdiff): an APK is a zip
file, and two honest builds of the same source have the same entries with the same content; only the signature differs (Google
Play signs with its own key). Standard library only.

  apkdiff.py A.apk B.apk                     compare two APKs (or two .aab files)
  apkdiff.py --splits BUILT DEVICE           BUILT = the .apks file (or folder of .apk) bundletool made from the bundle, DEVICE = the
                                             folder with the APKs pulled from the phone; every APK is paired by the split it is
                                             (base, config.arm64_v8a, config.xxhdpi, config.en ...) and compared

What is ignored (and nothing else):
  - META-INF/MANIFEST.MF, META-INF/*.SF, *.RSA, *.DSA, *.EC  (jar signatures; the APK signature block is not a zip entry at all)
  - stamp-cert-sha256                                          (Google Play's stamp)
  - in AndroidManifest.xml: the <meta-data> elements Google Play adds (com.android.vending.derived.apk.id, com.android.stamp.*)
Entries are compared by their uncompressed bytes, so a different zip compression level or entry order does not matter.

Exit code 0: they match. 1: they differ (the differences are listed). 2: bad usage or a file that cannot be read.
"""
import hashlib
import os
import struct
import sys
import zipfile

IGNORED_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")
IGNORED_NAMES = {"META-INF/MANIFEST.MF", "stamp-cert-sha256"}
PLAY_META_DATA = {"com.android.vending.derived.apk.id", "com.android.stamp.source", "com.android.stamp.type"}


def ignored(name):
    if name in IGNORED_NAMES:
        return True
    return name.startswith("META-INF/") and name.count("/") == 1 and name.endswith(IGNORED_SUFFIXES)


# ---- a small reader of Android's binary XML, enough to compare manifests and to know which split an APK is -------------------------
def _strings(data, off):
    # off = start of a RES_STRING_POOL chunk
    _t, hsize, _size, count, _styles, flags, strings_start, _sstart = struct.unpack_from("<HHIIIIII", data, off)
    utf8 = bool(flags & (1 << 8))
    offs = struct.unpack_from("<%dI" % count, data, off + hsize)
    base = off + strings_start
    out = []
    for o in offs:
        p = base + o
        if utf8:
            c = data[p]; p += 2 if c & 0x80 else 1          # length in characters (1 or 2 bytes), then in bytes (1 or 2 bytes)
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]; p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]; p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]; p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_axml(data):
    """Returns a list of elements: (depth, tag, ((attribute, value), ...)) in document order."""
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != 0x0003:
        raise ValueError("not a binary AndroidManifest.xml")
    pos = struct.unpack_from("<H", data, 2)[0]
    strings = []
    elements = []
    depth = 0
    total = len(data)
    while pos + 8 <= total:
        ctype, hsize, csize = struct.unpack_from("<HHI", data, pos)
        if csize < 8:
            break
        if ctype == 0x0001:
            strings = _strings(data, pos)
        elif ctype == 0x0102:
            ns, name, _astart, asize, acount = struct.unpack_from("<iiHHH", data, pos + 16)
            attrs = []
            ap = pos + 16 + 20
            for i in range(acount):
                ans, aname, raw, _vsize, _r0, vtype, vdata = struct.unpack_from("<iiiHBBI", data, ap + i * asize)
                value = strings[raw] if raw >= 0 else "@%d:%d" % (vtype, vdata)
                attrs.append((strings[aname] if aname >= 0 else "", value))
            elements.append((depth, strings[name], tuple(attrs)))
            depth += 1
        elif ctype == 0x0103:
            depth -= 1
        pos += csize
    return elements


def manifest_view(data):
    """The manifest as comparable lines, without what Google Play adds."""
    lines = []
    for depth, tag, attrs in parse_axml(data):
        if tag == "meta-data" and dict(attrs).get("name") in PLAY_META_DATA:
            continue
        lines.append("%s<%s %s>" % ("  " * depth, tag, " ".join("%s=%s" % a for a in attrs)))
    return lines


def split_name(path):
    """'' for a base APK, else the split name from the manifest's `split` attribute."""
    with zipfile.ZipFile(path) as z:
        for depth, tag, attrs in parse_axml(z.read("AndroidManifest.xml")):
            if tag == "manifest":
                return dict(attrs).get("split", "")
    return ""


# ---- comparison -----------------------------------------------------------------------------------------------------------------
def entries(path):
    out = {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir() or ignored(info.filename):
                continue
            out[info.filename] = z.read(info.filename)
    return out


def compare(a_path, b_path, label_a="A", label_b="B", out=print):
    """Returns the list of differences (empty when the two match)."""
    a, b = entries(a_path), entries(b_path)
    diffs = []
    for name in sorted(set(a) - set(b)):
        diffs.append("only in %s: %s" % (label_a, name))
    for name in sorted(set(b) - set(a)):
        diffs.append("only in %s: %s" % (label_b, name))
    for name in sorted(set(a) & set(b)):
        if a[name] == b[name]:
            continue
        if name == "AndroidManifest.xml":
            try:
                if manifest_view(a[name]) == manifest_view(b[name]):
                    continue
                va, vb = manifest_view(a[name]), manifest_view(b[name])
                first = next((i for i, (x, y) in enumerate(zip(va, vb)) if x != y), min(len(va), len(vb)))
                diffs.append("AndroidManifest.xml differs (first difference, line %d): %s=%r %s=%r" % (
                    first + 1, label_a, va[first] if first < len(va) else None, label_b, vb[first] if first < len(vb) else None))
                continue
            except Exception as e:  # unreadable manifest: fall through to the plain hash difference
                diffs.append("AndroidManifest.xml differs and could not be decoded (%s)" % e)
                continue
        diffs.append("differs: %s (%s %d bytes sha256 %s..., %s %d bytes sha256 %s...)" % (
            name, label_a, len(a[name]), hashlib.sha256(a[name]).hexdigest()[:12],
            label_b, len(b[name]), hashlib.sha256(b[name]).hexdigest()[:12]))
    return diffs


def _apks_in(path, tmp):
    """APK files named by a .apks archive (bundletool's output), a folder, or a single .apk."""
    if os.path.isdir(path):
        found = sorted(os.path.join(dp, f) for dp, _d, fs in os.walk(path) for f in fs if f.endswith(".apk"))
        return found
    if path.endswith(".apks"):
        found = []
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".apk") and not n.startswith("standalones/") and not n.startswith("instant/") and not n.startswith("archive/"):
                    target = os.path.join(tmp, n.replace("/", "_"))
                    with open(target, "wb") as f:
                        f.write(z.read(n))
                    found.append(target)
        return sorted(found)
    return [path]


def compare_splits(built, device):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        b_apks = _apks_in(built, tmp)
        d_apks = _apks_in(device, tmp)
        if not b_apks or not d_apks:
            print("no APK files found (built: %d, device: %d)" % (len(b_apks), len(d_apks)))
            return 2
        b_by = {split_name(p): p for p in b_apks}
        d_by = {split_name(p): p for p in d_apks}
        bad = 0
        for name in sorted(set(b_by) | set(d_by), key=lambda s: (s != "", s)):
            shown = name or "base"
            if name not in d_by:
                print("MISSING on the phone: split %s (your build has it, the phone does not)" % shown)
                bad += 1
            elif name not in b_by:
                print("EXTRA on the phone: split %s (the phone has it, your build does not)" % shown)
                bad += 1
            else:
                diffs = compare(b_by[name], d_by[name], "built", "phone")
                if diffs:
                    bad += 1
                    print("DIFFERENT  split %-22s (%s vs %s)" % (shown, os.path.basename(b_by[name]), os.path.basename(d_by[name])))
                    for d in diffs[:40]:
                        print("    " + d)
                    if len(diffs) > 40:
                        print("    ... and %d more" % (len(diffs) - 40))
                else:
                    print("match      split %-22s (%s vs %s)" % (shown, os.path.basename(b_by[name]), os.path.basename(d_by[name])))
        print("APKs match!" if not bad else "APKs don't match!")
        return 1 if bad else 0


def main(argv):
    if len(argv) == 4 and argv[1] == "--splits":
        return compare_splits(argv[2], argv[3])
    if len(argv) == 3 and not argv[1].startswith("-"):
        for p in argv[1:]:
            if not os.path.isfile(p):
                print("not a file: " + p)
                return 2
        diffs = compare(argv[1], argv[2], os.path.basename(argv[1]), os.path.basename(argv[2]))
        for d in diffs:
            print(d)
        print("APKs match!" if not diffs else "APKs don't match!")
        return 1 if diffs else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
