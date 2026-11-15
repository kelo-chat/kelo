#!/usr/bin/env python3
"""Compare two Kelo.app folders file by file (docs/verify-builds.md, "macOS").

  python3 -I macdiff.py <appA> <appB> [--normalize-signatures] [--json out.json]

One line per difference, exit code 0 when the two are identical, 1 when they differ, 2 on bad usage:

  only-in-a <path>                    the path exists only in the first app
  only-in-b <path>
  differs <path> <sha-a> <sha-b>      different bytes (sha-256, first 12 hex digits); a path inside a jar is <jar>!<entry>
  mode <path> <a> <b>                 different permission bits (only the executable bit is compared)
  zip-metadata <jar>                  every entry equal, but the jar's own bytes differ (times, order, compression): a reproducibility bug
  unsignable <path>                   --normalize-signatures: codesign failed on this Mach-O file

Jars and zips are compared by their ENTRIES (names, and each entry's bytes; nested jars again by their entries), not by their bytes.

--normalize-signatures: for comparing a build signed and notarized by Apple's certificate with an unsigned one. Skips the signature
files (Contents/_CodeSignature/**, Contents/CodeResources) and, in every Mach-O file (also those inside jars, e.g. skiko's .dylib),
replaces the signature by the same ad-hoc one on both sides (codesign --remove-signature, then --sign - with no metadata), so only the
code itself is compared. Needs macOS (codesign). Standard library only.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}   # 0xfeedfacf (LE), 0xcafebabe, 0xbebafeca
ZIP_SUFFIXES = (".jar", ".zip")
SIGNATURE_PREFIXES = ("Contents/_CodeSignature/", )
SIGNATURE_FILES = {"Contents/CodeResources", "Contents/_CodeSignature"}


def sha(data):
    return hashlib.sha256(data).hexdigest()[:12]


def is_macho(data):
    # a Java class file also starts with 0xcafebabe: a universal Mach-O binary has a small architecture count in the next 4 bytes,
    # a class file has minor/major version there (major >= 45)
    head = data[:8]
    if len(head) < 8 or head[:4] not in MACHO_MAGICS:
        return False
    if head[:4] == b"\xcf\xfa\xed\xfe":
        return True
    count = int.from_bytes(head[4:8], "big" if head[:4] == b"\xca\xfe\xba\xbe" else "little")
    return 0 < count < 20


class Normalizer:
    def __init__(self, enabled):
        self.enabled = enabled
        self.tmp = tempfile.mkdtemp(prefix="macdiff-")
        self.n = 0
        self.cache = {}

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def macho(self, data, name):
        """The bytes of a Mach-O file with the same ad-hoc signature (or None when codesign fails)."""
        key = (hashlib.sha256(data).hexdigest(), os.path.basename(name))
        if key in self.cache:
            return self.cache[key]
        self.n += 1
        d = os.path.join(self.tmp, str(self.n))
        os.makedirs(d)
        f = os.path.join(d, os.path.basename(name))   # the ad-hoc identifier comes from the file name: same on both sides
        with open(f, "wb") as out:
            out.write(data)
        os.chmod(f, 0o755)
        result = None
        # a file that was never signed may refuse to have a signature removed: then there is nothing to remove
        subprocess.run(["codesign", "--remove-signature", f], capture_output=True)
        r = subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", "--preserve-metadata=", f], capture_output=True)
        if r.returncode == 0:
            with open(f, "rb") as inp:
                result = inp.read()
        shutil.rmtree(d, ignore_errors=True)
        self.cache[key] = result
        return result


def read_zip(data):
    """{entry name: bytes} of a zip; None when it is not a readable zip."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return {i.filename: z.read(i) for i in z.infolist()}
    except (zipfile.BadZipFile, OSError, NotImplementedError):
        return None


class Differ:
    def __init__(self, norm):
        self.norm = norm
        self.found = []

    def add(self, kind, path, a=None, b=None):
        self.found.append({"kind": kind, "path": path, "a": a, "b": b})

    def compare_blobs(self, path, da, db):
        """Two files' bytes; zips by entries."""
        if path.lower().endswith(ZIP_SUFFIXES):
            ea, eb = read_zip(da), read_zip(db)
            if ea is not None and eb is not None:
                before = len(self.found)
                self.compare_entries(path, ea, eb)
                if not self.norm.enabled and da != db and len(self.found) == before:
                    self.add("zip-metadata", path)
                return
        self.compare_plain(path, da, db)

    def compare_plain(self, path, da, db):
        if self.norm.enabled and (is_macho(da) or is_macho(db)):
            xa = self.norm.macho(da, path) if is_macho(da) else da
            xb = self.norm.macho(db, path) if is_macho(db) else db
            if xa is None or xb is None:
                self.add("unsignable", path)
                return
            da, db = xa, xb
        if da != db:
            self.add("differs", path, sha(da), sha(db))

    def compare_entries(self, jar, ea, eb):
        for name in sorted(set(ea) | set(eb)):
            p = f"{jar}!{name}"
            if name not in eb:
                self.add("only-in-a", p)
            elif name not in ea:
                self.add("only-in-b", p)
            elif ea[name] != eb[name] or (self.norm.enabled and is_macho(ea[name])):
                self.compare_blobs(p, ea[name], eb[name])


def list_tree(root):
    """{relative path: absolute path} of every file and symlink under root (folders themselves are not listed)."""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for n in sorted(files + [d for d in dirs if os.path.islink(os.path.join(base, d))]):
            full = os.path.join(base, n)
            out[os.path.relpath(full, root)] = full
    return out


def signature_file(rel):
    return rel in SIGNATURE_FILES or rel.startswith(SIGNATURE_PREFIXES)


def main(argv):
    ap = argparse.ArgumentParser(description="Compare two Kelo.app folders file by file.")
    ap.add_argument("app_a")
    ap.add_argument("app_b")
    ap.add_argument("--normalize-signatures", action="store_true")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args(argv)
    for p in (args.app_a, args.app_b):
        if not os.path.isdir(p):
            print(f"error: {p} is not a folder", file=sys.stderr)
            return 2
    if args.normalize_signatures and shutil.which("codesign") is None:
        print("error: --normalize-signatures needs codesign (macOS with the Xcode command line tools)", file=sys.stderr)
        return 2

    norm = Normalizer(args.normalize_signatures)
    d = Differ(norm)
    try:
        ta, tb = list_tree(args.app_a), list_tree(args.app_b)
        for rel in sorted(set(ta) | set(tb)):
            if args.normalize_signatures and signature_file(rel):
                continue
            if rel not in tb:
                d.add("only-in-a", rel)
                continue
            if rel not in ta:
                d.add("only-in-b", rel)
                continue
            fa, fb = ta[rel], tb[rel]
            la, lb = os.path.islink(fa), os.path.islink(fb)
            if la or lb:
                if la != lb or os.readlink(fa) != os.readlink(fb):
                    d.add("differs", rel, "link:" + (os.readlink(fa) if la else "-"), "link:" + (os.readlink(fb) if lb else "-"))
                continue
            xa, xb = os.stat(fa).st_mode & stat.S_IXUSR, os.stat(fb).st_mode & stat.S_IXUSR
            if xa != xb:
                d.add("mode", rel, "x" if xa else "-", "x" if xb else "-")
            with open(fa, "rb") as f:
                da = f.read()
            with open(fb, "rb") as f:
                db = f.read()
            d.compare_blobs(rel, da, db)
    finally:
        norm.close()

    for f in d.found:
        if f["kind"] in ("differs", "mode"):
            print(f"{f['kind']} {f['path']} {f['a']} {f['b']}")
        else:
            print(f"{f['kind']} {f['path']}")
    if args.json:
        with open(args.json, "w") as out:
            json.dump(d.found, out, indent=1)
    if d.found:
        print(f"{len(d.found)} difference(s)", file=sys.stderr)
        return 1
    print("identical" + (" (apart from signatures)" if args.normalize_signatures else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
