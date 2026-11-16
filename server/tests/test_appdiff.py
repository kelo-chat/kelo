"""tools/verify/appdiff.py (and its old name macdiff.py): the file-by-file check of the desktop app against the public source
(docs/verify-builds.md, "Windows" and "macOS"). Small fake app folders; the PE files are built by hand (a header, one section,
and for the signed one a certificate table at the end)."""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "verify"
SPEC = importlib.util.spec_from_file_location("appdiff", TOOLS / "appdiff.py")
appdiff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(appdiff)


def pe(code=b"\x90" * 64, signed=False, plus=True, checksum=0x1234):
    """A tiny PE file: DOS header, PE header, optional header with 16 data directories, 'code'; signed adds a certificate table."""
    pe_off = 0x80
    opt_size = 240 if plus else 224
    head = bytearray(pe_off + 24 + opt_size)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = pe_off.to_bytes(4, "little")
    head[pe_off:pe_off + 4] = b"PE\0\0"
    opt = pe_off + 24
    head[opt:opt + 2] = (0x20B if plus else 0x10B).to_bytes(2, "little")
    head[opt + 64:opt + 68] = checksum.to_bytes(4, "little")
    body = bytes(head) + code
    if signed:
        cert = b"WIN_CERTIFICATE" + b"\xAA" * 33        # 48 bytes
        dirs = opt + (112 if plus else 96) + 4 * 8
        body = bytearray(body)
        body[dirs:dirs + 4] = len(body).to_bytes(4, "little")
        body[dirs + 4:dirs + 8] = len(cert).to_bytes(4, "little")
        body[opt + 64:opt + 68] = (checksum + 7).to_bytes(4, "little")   # signing recomputes the checksum
        body = bytes(body) + cert
    return bytes(body)


def jar(entries, date=(2026, 1, 1, 0, 0, 0), compress=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in entries:
            z.writestr(zipfile.ZipInfo(name, date), data, compress_type=compress)
    return buf.getvalue()


def make_app(root, files):
    for rel, data in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return str(root)


def run(a, b, *flags):
    return appdiff.main([a, b, *flags])


def base(**over):
    files = {
        "Kelo.exe": pe(b"launcher"),
        "app/Kelo.cfg": b"[Application]\napp.mainjar=kelo.jar\n",
        "app/kelo.jar": jar([("a/B.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x3d"), ("res/x.txt", b"hello")]),
        "runtime/bin/java.exe": pe(b"java"),
        "runtime/lib/modules": b"\x01\x02\x03" * 50,
    }
    files.update(over)
    return files


def test_identical(tmp_path, capsys):
    a, b = make_app(tmp_path / "a", base()), make_app(tmp_path / "b", base())
    assert run(a, b, "--windows") == 0
    assert "identical (apart from signatures)" in capsys.readouterr().err
    assert run(a, b) == 0


def test_differing_jar_entry_is_named(tmp_path, capsys):
    other = jar([("a/B.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x3d"), ("res/x.txt", b"HELLO")])
    a, b = make_app(tmp_path / "a", base()), make_app(tmp_path / "b", base(**{"app/kelo.jar": other}))
    assert run(a, b, "--windows") == 1
    out = capsys.readouterr().out
    assert out.startswith("differs app/kelo.jar!res/x.txt ")
    assert out.count("\n") == 1


def test_entry_only_on_one_side(tmp_path, capsys):
    other = jar([("a/B.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x3d"), ("res/x.txt", b"hello"), ("res/new.txt", b"n")])
    a, b = make_app(tmp_path / "a", base()), make_app(tmp_path / "b", base(**{"app/kelo.jar": other}))
    assert run(a, b, "--windows") == 1
    assert "only-in-b app/kelo.jar!res/new.txt" in capsys.readouterr().out


def test_missing_and_extra_files(tmp_path, capsys):
    a = make_app(tmp_path / "a", base(**{"extra.dll": pe()}))
    b_files = base()
    del b_files["runtime/lib/modules"]
    b = make_app(tmp_path / "b", b_files)
    assert run(a, b, "--windows") == 1
    out = capsys.readouterr().out
    assert "only-in-a extra.dll" in out and "only-in-a runtime/lib/modules" in out


def test_zip_metadata_only_is_reported(tmp_path, capsys):
    entries = [("a/B.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x3d"), ("res/x.txt", b"hello")]
    a = make_app(tmp_path / "a", base())
    b = make_app(tmp_path / "b", base(**{"app/kelo.jar": jar(entries, date=(2026, 11, 15, 8, 30, 2))}))
    assert run(a, b, "--windows") == 1
    assert capsys.readouterr().out.strip() == "zip-metadata app/kelo.jar"
    # stored instead of deflated: also only metadata
    c = make_app(tmp_path / "c", base(**{"app/kelo.jar": jar(entries, compress=zipfile.ZIP_STORED)}))
    assert run(a, c, "--windows") == 1
    assert capsys.readouterr().out.strip() == "zip-metadata app/kelo.jar"


def test_signed_vs_unsigned_pe(tmp_path, capsys):
    a = make_app(tmp_path / "a", base(**{"Kelo.exe": pe(b"launcher", signed=True), "runtime/bin/java.exe": pe(b"java", signed=True, plus=False)}))
    b = make_app(tmp_path / "b", base(**{"runtime/bin/java.exe": pe(b"java", plus=False)}))
    assert run(a, b) == 1          # plain comparison sees the signature
    capsys.readouterr()
    assert run(a, b, "--windows") == 0
    assert "2 signed PE file(s)" in capsys.readouterr().err
    assert run(b, a, "--windows") == 0


def test_signed_pe_with_different_code_still_differs(tmp_path, capsys):
    a = make_app(tmp_path / "a", base(**{"Kelo.exe": pe(b"launcher", signed=True)}))
    b = make_app(tmp_path / "b", base(**{"Kelo.exe": pe(b"launchex")}))
    assert run(a, b, "--windows") == 1
    assert capsys.readouterr().out.startswith("differs Kelo.exe ")


def test_signed_pe_inside_a_jar(tmp_path, capsys):
    classes = ("a/B.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x3d")
    a = make_app(tmp_path / "a", base(**{"app/kelo.jar": jar([classes, ("skiko.dll", pe(b"skiko", signed=True))])}))
    b = make_app(tmp_path / "b", base(**{"app/kelo.jar": jar([classes, ("skiko.dll", pe(b"skiko"))])}))
    assert run(a, b, "--windows") == 0          # the jar's bytes differ only by the signature inside: not zip-metadata
    capsys.readouterr()
    c = make_app(tmp_path / "c", base(**{"app/kelo.jar": jar([classes, ("skiko.dll", pe(b"skikX"))])}))
    assert run(a, c, "--windows") == 1
    assert capsys.readouterr().out.startswith("differs app/kelo.jar!skiko.dll ")


def test_msix_installer_files_are_left_out(tmp_path, capsys):
    msix = base(**{
        "AppxManifest.xml": b"<Package/>", "AppxSignature.p7x": b"PKCX", "AppxBlockMap.xml": b"<BlockMap/>", "[Content_Types].xml": b"<Types/>",
        "Assets/StoreLogo.png": b"png",
    })
    a, b = make_app(tmp_path / "a", msix), make_app(tmp_path / "b", base())
    assert run(a, b, "--windows") == 0
    err = capsys.readouterr().err
    assert err.count("installer-file ") == 5 and "installer-file AppxManifest.xml" in err
    # ...but only those names, and only when they are not in the build too: any other extra file is a difference
    a2 = make_app(tmp_path / "a2", dict(msix, **{"app/extra.jar": jar([("x", b"y")])}))
    assert run(a2, b, "--windows") == 1
    assert "only-in-a app/extra.jar" in capsys.readouterr().out
    # without --windows they are differences
    assert run(a, b) == 1


def test_modes_are_not_compared_for_windows(tmp_path):
    a, b = make_app(tmp_path / "a", base()), make_app(tmp_path / "b", base())
    os.chmod(Path(a) / "runtime/lib/modules", 0o755)
    assert run(a, b) == 1
    assert run(a, b, "--windows") == 0


def test_strip_authenticode_pieces():
    unsigned, signed = pe(b"code"), pe(b"code", signed=True)
    assert appdiff.is_pe(signed) and not appdiff.is_pe(b"\xca\xfe\xba\xbe" + b"\0" * 100) and not appdiff.is_pe(b"MZ" + b"\0" * 100)
    assert appdiff.strip_authenticode(signed) == appdiff.strip_authenticode(unsigned)
    assert len(appdiff.strip_authenticode(signed)) == len(unsigned)
    junk = b"not a pe file"
    assert appdiff.strip_authenticode(junk) == junk


def test_bad_usage(tmp_path, capsys):
    assert run(str(tmp_path / "nope"), str(tmp_path)) == 2
    with pytest.raises(SystemExit):
        run(str(tmp_path), str(tmp_path), "--windows", "--normalize-signatures")


def test_macdiff_is_the_same_program(tmp_path):
    a, b = make_app(tmp_path / "a", base()), make_app(tmp_path / "b", base(**{"runtime/lib/modules": b"x"}))
    r = subprocess.run([sys.executable, "-I", str(TOOLS / "macdiff.py"), a, b, "--windows"], capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout.startswith("differs runtime/lib/modules ")
    r = subprocess.run([sys.executable, "-I", str(TOOLS / "macdiff.py"), a, a], capture_output=True, text=True)
    assert r.returncode == 0 and "identical" in r.stderr


JAVAC = shutil.which("javac")


@pytest.mark.skipif(JAVAC is None or subprocess.run([JAVAC, "-version"], capture_output=True).returncode != 0, reason="no javac")
def test_release_info_is_read_from_the_apps_jar(tmp_path, capsys):
    src = tmp_path / "src" / "chat" / "kelo" / "desktop" / "core"
    src.mkdir(parents=True)
    (src / "ReleaseInfo.java").write_text(
        'package chat.kelo.desktop.core;\npublic final class ReleaseInfo {\n'
        '  public static final String VERSION = "0.9.0";\n  public static final int BUILD = 5;\n  public static final String PACKAGE = "1.0.9";\n'
        '  public static final String SOURCE_COMMIT = "84d65f21c430a21a80d0e7945fff017585d422fb";\n  public static final boolean SOURCE_DIRTY = false;\n'
        '  public static final long BIG = 5000000000L;\n}\n')
    out = tmp_path / "classes"
    assert subprocess.run([JAVAC, "-d", str(out), str(src / "ReleaseInfo.java")], capture_output=True).returncode == 0
    data = (out / "chat/kelo/desktop/core/ReleaseInfo.class").read_bytes()
    app = make_app(tmp_path / "app", {"Kelo.exe": pe(), "app/kelo.jar": jar([("chat/kelo/desktop/core/ReleaseInfo.class", data)])})
    assert appdiff.main(["--release-info", app]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "PACKAGE=1.0.9", "VERSION=0.9.0", "BUILD=5", "SOURCE_COMMIT=84d65f21c430a21a80d0e7945fff017585d422fb", "SOURCE_DIRTY=false"]
    empty = make_app(tmp_path / "empty", {"Kelo.exe": pe()})
    assert appdiff.main(["--release-info", empty]) == 2
