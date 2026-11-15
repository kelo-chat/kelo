"""tools/verify/apkdiff.py: the comparison people run to check an app against the public source (docs/verify-builds.md).

The manifests here are binary XML written by hand (a UTF-16 string pool and start/end element chunks), so the tests do not need
the Android build tools.
"""

import importlib.util
import struct
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("apkdiff", Path(__file__).resolve().parents[1] / "tools" / "verify" / "apkdiff.py")
apkdiff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apkdiff)


def axml(elements):
    """Binary XML for [(tag, {attr: value}), ...] written as a flat list of start+end elements (no nesting), value = a string."""
    strings = []

    def sid(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    body = b""
    for tag, attrs in elements:
        name = sid(tag)
        attr_chunks = b""
        for k, v in attrs.items():
            raw = sid(v)
            attr_chunks += struct.pack("<iiiHBBI", -1, sid(k), raw, 8, 0, 3, raw)
        start = struct.pack("<HHIIiiiHHHHHH", 0x0102, 16, 36 + len(attr_chunks), 1, -1, -1, name, 20, 20, len(attrs), 0, 0, 0)
        body += start + attr_chunks
        body += struct.pack("<HHIIiii", 0x0103, 16, 24, 1, -1, -1, name)
    pool_strings = b""
    offsets = []
    for s in strings:
        offsets.append(len(pool_strings))
        raw = s.encode("utf-16-le")
        pool_strings += struct.pack("<H", len(s)) + raw + b"\0\0"
    while len(pool_strings) % 4:
        pool_strings += b"\0"
    header = 28 + 4 * len(strings)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, header + len(pool_strings), len(strings), 0, 0, header, 0)
    pool += b"".join(struct.pack("<I", o) for o in offsets) + pool_strings
    total = 8 + len(pool) + len(body)
    return struct.pack("<HHI", 0x0003, 8, total) + pool + body


def manifest(split=None, extra_meta=()):
    attrs = {"package": "chat.kelo.app", "versionName": "0.1.1"}
    if split:
        attrs["split"] = split
    els = [("manifest", attrs), ("uses-permission", {"name": "android.permission.INTERNET"})]
    for name in extra_meta:
        els.append(("meta-data", {"name": name, "value": "1"}))
    return axml(els)


def make_apk(path, files, manifest_bytes=None, compress=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compress) as z:
        z.writestr("AndroidManifest.xml", manifest_bytes if manifest_bytes is not None else manifest())
        for name, data in files.items():
            z.writestr(name, data)
    return str(path)


FILES = {"classes.dex": b"dex" * 100, "resources.arsc": b"arsc", "assets/a.txt": b"hello"}


def test_identical_apks_match(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES)
    b = make_apk(tmp_path / "b.apk", FILES)
    assert apkdiff.compare(a, b) == []


def test_signature_files_and_play_stamp_are_ignored(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES)
    b = make_apk(tmp_path / "b.apk", {**FILES, "META-INF/MANIFEST.MF": b"x", "META-INF/KELO.SF": b"y", "META-INF/KELO.RSA": b"z",
                                      "META-INF/BNDLTOOL.RSA": b"z", "stamp-cert-sha256": b"s"})
    assert apkdiff.compare(a, b) == []


def test_compression_level_and_order_do_not_matter(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES, compress=zipfile.ZIP_STORED)
    b = make_apk(tmp_path / "b.apk", dict(reversed(list(FILES.items()))), compress=zipfile.ZIP_DEFLATED)
    assert apkdiff.compare(a, b) == []


def test_a_changed_file_is_reported(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES)
    b = make_apk(tmp_path / "b.apk", {**FILES, "classes.dex": b"evil" * 100})
    diffs = apkdiff.compare(a, b)
    assert len(diffs) == 1 and "classes.dex" in diffs[0]


def test_extra_and_missing_files_are_reported(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES)
    b = make_apk(tmp_path / "b.apk", {"classes.dex": FILES["classes.dex"], "resources.arsc": b"arsc", "lib/arm64/x.so": b"so"})
    diffs = apkdiff.compare(a, b, "built", "phone")
    assert any("only in built: assets/a.txt" in d for d in diffs)
    assert any("only in phone: lib/arm64/x.so" in d for d in diffs)


def test_meta_data_added_by_google_play_is_ignored_but_other_changes_are_not(tmp_path):
    plain = make_apk(tmp_path / "a.apk", FILES, manifest())
    stamped = make_apk(tmp_path / "b.apk", FILES, manifest(extra_meta=["com.android.vending.derived.apk.id", "com.android.stamp.type"]))
    assert apkdiff.compare(plain, stamped) == []
    other = make_apk(tmp_path / "c.apk", FILES, manifest(extra_meta=["com.example.tracker"]))
    diffs = apkdiff.compare(plain, other)
    assert len(diffs) == 1 and diffs[0].startswith("AndroidManifest.xml differs")


def test_split_name_reads_the_manifest(tmp_path):
    assert apkdiff.split_name(make_apk(tmp_path / "base.apk", FILES)) == ""
    assert apkdiff.split_name(make_apk(tmp_path / "s.apk", FILES, manifest(split="config.arm64_v8a"))) == "config.arm64_v8a"


def _bundle_output(tmp_path, tamper=False, drop=None):
    """bundletool's .apks (splits/base-master.apk ...) and the folder pulled from a phone (base.apk, split_config.*.apk)."""
    built = tmp_path / "mine.apks"
    phone = tmp_path / "phone"
    phone.mkdir()
    names = {"": ("base-master", "base"), "config.arm64_v8a": ("base-arm64_v8a", "split_config.arm64_v8a"),
             "config.xxhdpi": ("base-xxhdpi", "split_config.xxhdpi")}
    with zipfile.ZipFile(built, "w") as z:
        for split, (mine, theirs) in names.items():
            files = {"classes.dex": split.encode() + b"-dex"} if split == "" else {"res/x": split.encode()}
            one = make_apk(tmp_path / f"{mine}.apk", files, manifest(split=split or None))
            z.write(one, f"splits/{mine}.apk")
            if drop == split:
                continue
            if tamper and split == "":
                files = {"classes.dex": b"tampered"}
            make_apk(phone / f"{theirs}.apk", files, manifest(split=split or None, extra_meta=["com.android.vending.derived.apk.id"]))
    return str(built), str(phone)


def test_splits_are_paired_by_what_they_are(tmp_path, capsys):
    built, phone = _bundle_output(tmp_path)
    assert apkdiff.compare_splits(built, phone) == 0
    assert "APKs match!" in capsys.readouterr().out


def test_a_tampered_split_fails_and_names_the_split(tmp_path, capsys):
    built, phone = _bundle_output(tmp_path, tamper=True)
    assert apkdiff.compare_splits(built, phone) == 1
    out = capsys.readouterr().out
    assert "DIFFERENT  split base" in out and "classes.dex" in out and "APKs don't match!" in out


def test_a_split_missing_on_the_phone_fails(tmp_path, capsys):
    built, phone = _bundle_output(tmp_path, drop="config.xxhdpi")
    assert apkdiff.compare_splits(built, phone) == 1
    assert "MISSING on the phone: split config.xxhdpi" in capsys.readouterr().out


def test_main_exit_codes(tmp_path):
    a = make_apk(tmp_path / "a.apk", FILES)
    b = make_apk(tmp_path / "b.apk", {**FILES, "assets/a.txt": b"changed"})
    assert apkdiff.main(["apkdiff.py", a, a]) == 0
    assert apkdiff.main(["apkdiff.py", a, b]) == 1
    assert apkdiff.main(["apkdiff.py", a, str(tmp_path / "missing.apk")]) == 2
    assert apkdiff.main(["apkdiff.py"]) == 2
