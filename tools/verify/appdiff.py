#!/usr/bin/env python3
"""Compare two copies of the Kelo desktop app, file by file (docs/verify-builds.md, "Windows" and "macOS").

  python3 -I appdiff.py <appA> <appB> [--normalize-signatures | --windows] [--json out.json]
  python3 -I appdiff.py --release-info <app>
  (macdiff.py is the same program under its old name)

--release-info: prints what the app says about itself, from its jars (chat.kelo.desktop.core.ReleaseInfo: PACKAGE, VERSION, BUILD,
SOURCE_COMMIT, SOURCE_DIRTY), one NAME=value per line: which source commit the app was built from, and the package number to build it with.

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
code itself is compared. Needs macOS (codesign).

--windows: for the Windows app (the folder with Kelo.exe, unpacked from the MSI or the MSIX, or built by createDistributable). In every
PE file (.exe, .dll, also inside jars) the Authenticode signature is removed on both sides (the certificate table at the end of the
file, its entry in the PE header, and the header's checksum, which signing recomputes), so a signed file and an unsigned one compare equal
when they hold the same code. A jar whose own bytes differ while all entries are equal is reported as zip-metadata (a reproducibility bug),
unless a signature was removed inside it. File permission bits are not compared. Files that only the MSIX adds to side A
(AppxManifest.xml, AppxSignature.p7x, AppxBlockMap.xml, [Content_Types].xml, AppxMetadata/, and Assets/, the Store's images) are installer
files: they are listed on stderr as "installer-file" and are not differences.

Standard library only.
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
MSIX_FILES = {"AppxManifest.xml", "AppxSignature.p7x", "AppxBlockMap.xml", "[Content_Types].xml"}
MSIX_PREFIXES = ("AppxMetadata/", "Assets/")


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


class NoNormalizer:
    """Compare the bytes as they are."""
    enabled = False

    def __init__(self):
        self.touched = 0     # how many files were changed (a signature removed) before comparing

    def applies(self, data):
        return False

    def normalize(self, data, name):
        return data

    def zip_metadata_ok(self, touched_before):
        """May a jar whose entries are equal but whose bytes differ be reported as zip-metadata?"""
        return True

    def close(self):
        pass


class MachoNormalizer(NoNormalizer):
    enabled = True

    def __init__(self):
        super().__init__()
        self.tmp = tempfile.mkdtemp(prefix="macdiff-")
        self.n = 0
        self.cache = {}

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def applies(self, data):
        return is_macho(data)

    def zip_metadata_ok(self, touched_before):
        return False     # the jars hold Mach-O files whose signatures differ in bytes

    def normalize(self, data, name):
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


def _pe_security_entry(data):
    """(offset of the checksum field, offset of the certificate table's entry in the header) of a PE file; None when it is not one."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe = int.from_bytes(data[0x3C:0x40], "little")
    if pe < 0x40 or pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        return None
    opt = pe + 24
    magic = int.from_bytes(data[opt:opt + 2], "little")
    if magic == 0x10B:      # PE32
        dirs = opt + 96
    elif magic == 0x20B:    # PE32+
        dirs = opt + 112
    else:
        return None
    entry = dirs + 4 * 8    # data directory 4: the certificate table (its "address" is a file offset, not an RVA)
    if entry + 8 > len(data):
        return None
    return opt + 64, entry


def is_pe(data):
    return _pe_security_entry(data) is not None


def strip_authenticode(data):
    """The PE file without its Authenticode signature: the certificate table removed, its header entry and the checksum zeroed."""
    where = _pe_security_entry(data)
    if where is None:
        return data
    checksum, entry = where
    off = int.from_bytes(data[entry:entry + 4], "little")
    size = int.from_bytes(data[entry + 4:entry + 8], "little")
    out = bytearray(data)
    out[checksum:checksum + 4] = b"\0\0\0\0"
    if off and size and off + size <= len(data):
        out[entry:entry + 8] = b"\0" * 8
        del out[off:off + size]
    return bytes(out)


class PeNormalizer(NoNormalizer):
    enabled = True

    def applies(self, data):
        return is_pe(data)

    def normalize(self, data, name):
        out = strip_authenticode(data)
        if len(out) != len(data):      # a certificate table was cut off (the checksum is zeroed on every file, signed or not)
            self.touched += 1
        return out

    def zip_metadata_ok(self, touched_before):
        return self.touched == touched_before


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
                before, touched = len(self.found), self.norm.touched
                self.compare_entries(path, ea, eb)
                if da != db and len(self.found) == before and self.norm.zip_metadata_ok(touched):
                    self.add("zip-metadata", path)
                return
        self.compare_plain(path, da, db)

    def compare_plain(self, path, da, db):
        if self.norm.enabled and (self.norm.applies(da) or self.norm.applies(db)):
            xa = self.norm.normalize(da, path) if self.norm.applies(da) else da
            xb = self.norm.normalize(db, path) if self.norm.applies(db) else db
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
            elif ea[name] != eb[name] or (self.norm.enabled and self.norm.applies(ea[name])):
                self.compare_blobs(p, ea[name], eb[name])


def class_constants(data):
    """{field name: value} of the `static final` fields with a constant value (Kotlin `const val`) of a Java class file; {} when it is not one."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        return {}
    pos, count = 10, int.from_bytes(data[8:10], "big")
    pool = [None] * count
    i = 1
    while i < count:
        tag = data[pos]
        if tag == 1:                                   # Utf8
            n = int.from_bytes(data[pos + 1:pos + 3], "big")
            pool[i] = data[pos + 3:pos + 3 + n].decode("utf-8", "replace")
            pos += 3 + n
        elif tag in (3, 4):                            # Integer, Float
            pool[i] = int.from_bytes(data[pos + 1:pos + 5], "big", signed=True) if tag == 3 else None
            pos += 5
        elif tag in (5, 6):                            # Long, Double: two pool slots
            pos += 9
            i += 1
        elif tag in (7, 8, 16, 19, 20):                # Class, String, MethodType, Module, Package: one index
            pool[i] = ("ref", int.from_bytes(data[pos + 1:pos + 3], "big"))
            pos += 3
        elif tag in (9, 10, 11, 12, 17, 18):           # Fieldref, Methodref, InterfaceMethodref, NameAndType, Dynamic, InvokeDynamic
            pos += 5
        elif tag == 15:                                # MethodHandle
            pos += 4
        else:
            return {}
        i += 1
    pos += 6                                           # access flags, this class, super class
    pos += 2 + 2 * int.from_bytes(data[pos:pos + 2], "big")   # interfaces
    out = {}
    fields = int.from_bytes(data[pos:pos + 2], "big")
    pos += 2
    for _ in range(fields):
        name = pool[int.from_bytes(data[pos + 2:pos + 4], "big")]
        pos += 6
        attrs = int.from_bytes(data[pos:pos + 2], "big")
        pos += 2
        for _ in range(attrs):
            attr = pool[int.from_bytes(data[pos:pos + 2], "big")]
            n = int.from_bytes(data[pos + 2:pos + 6], "big")
            if attr == "ConstantValue":
                v = pool[int.from_bytes(data[pos + 6:pos + 8], "big")]
                if isinstance(v, tuple):
                    v = pool[v[1]]
                out[name] = v
            pos += 6 + n
    return out


def release_info(app):
    """The constants of chat.kelo.desktop.core.ReleaseInfo from the app's jars; {} when the app has none."""
    for rel, full in sorted(list_tree(app).items()):
        if not rel.lower().endswith(".jar") or os.path.islink(full):
            continue
        entries = read_zip(open(full, "rb").read())
        if entries and "chat/kelo/desktop/core/ReleaseInfo.class" in entries:
            return class_constants(entries["chat/kelo/desktop/core/ReleaseInfo.class"])
    return {}


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


def installer_file(rel):
    """A file the MSIX packaging adds around the app (rel uses /)."""
    return rel in MSIX_FILES or rel.startswith(MSIX_PREFIXES)


def main(argv):
    if argv[:1] == ["--release-info"]:
        if len(argv) != 2 or not os.path.isdir(argv[1]):
            print("usage: appdiff.py --release-info <app folder>", file=sys.stderr)
            return 2
        info = release_info(argv[1])
        for k in ("PACKAGE", "VERSION", "BUILD", "SOURCE_COMMIT", "SOURCE_DIRTY"):
            if k in info:
                print(f"{k}={('true' if info[k] else 'false') if k == 'SOURCE_DIRTY' else info[k]}")
        return 0 if info else 2
    ap = argparse.ArgumentParser(description="Compare two copies of the Kelo desktop app file by file.")
    ap.add_argument("app_a")
    ap.add_argument("app_b")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--normalize-signatures", action="store_true", help="macOS: give both sides the same ad-hoc signature (needs codesign)")
    mode.add_argument("--windows", action="store_true", help="Windows: remove the Authenticode signatures, ignore the MSIX's own files")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args(argv)
    for p in (args.app_a, args.app_b):
        if not os.path.isdir(p):
            print(f"error: {p} is not a folder", file=sys.stderr)
            return 2
    if args.normalize_signatures and shutil.which("codesign") is None:
        print("error: --normalize-signatures needs codesign (macOS with the Xcode command line tools)", file=sys.stderr)
        return 2

    norm = MachoNormalizer() if args.normalize_signatures else PeNormalizer() if args.windows else NoNormalizer()
    d = Differ(norm)
    skipped = []
    try:
        ta, tb = list_tree(args.app_a), list_tree(args.app_b)
        ta, tb = ({k.replace(os.sep, "/"): v for k, v in t.items()} for t in (ta, tb))
        for rel in sorted(set(ta) | set(tb)):
            if args.normalize_signatures and signature_file(rel):
                continue
            if args.windows and rel not in tb and installer_file(rel):
                skipped.append(rel)
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
            if not args.windows:
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

    for rel in skipped:
        print(f"installer-file {rel} (only in the first app; not a difference)", file=sys.stderr)
    for f in d.found:
        if f["kind"] in ("differs", "mode"):
            print(f"{f['kind']} {f['path']} {f['a']} {f['b']}")
        else:
            print(f"{f['kind']} {f['path']}")
    if args.json:
        with open(args.json, "w") as out:
            json.dump(d.found, out, indent=1)
    if args.windows and norm.touched:
        print(f"{norm.touched} signed PE file(s) had their Authenticode signature removed before comparing", file=sys.stderr)
    if d.found:
        print(f"{len(d.found)} difference(s)", file=sys.stderr)
        return 1
    print("identical" + (" (apart from signatures)" if (args.normalize_signatures or args.windows) else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
