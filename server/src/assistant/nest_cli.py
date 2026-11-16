"""`kelo nest`: the command line for people who make Kelo Nest packages. Nothing here touches the network or runs git: it reads and writes
files, and prints what to do next. A package is a folder in a GitHub repository (kelo-package.json at the top); share the repository
link and anyone can install it in the Kelo app (My -> Nest -> Install from link). docs/nest.md.

    kelo nest init <dir> --kind skill|connector|extension --id <id>    a new package folder to edit
    kelo nest validate <dir>                                           check it as a Kelo server will, and show its permissions
    kelo nest pack <dir> [--out <dir>]                                 a reproducible .tar.gz, its sha256 and the content hash
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import tarfile
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import nest_manifest as nm
from .package_index import unpack
from .packages import MANIFEST, SKILL_FILE, Package, PackageError, _scan, content_hash, file_hashes, load_package


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _load(folder: Path) -> tuple[Package, list[str]]:
    warnings: list[str] = []
    pkg = load_package(folder, source="installed", warnings=warnings, check_name=False)
    if pkg.id != folder.name:
        warnings.append(f"the folder is named {folder.name!r} but the id is {pkg.id!r}; a Kelo server stores it as {pkg.id}/")
    return pkg, warnings


# ---------------------------------------------------------------------------------------------------------------- publisher
def cmd_init(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    if folder.exists() and any(folder.iterdir()):
        return _err(f"{folder} is not empty")
    folder.mkdir(parents=True, exist_ok=True)
    pid = args.id or folder.name
    manifest: dict[str, Any] = {"format": 1, "id": pid, "kind": args.kind, "version": "0.1.0", "name": args.name or pid.replace("-", " ").replace("_", " ").title(),
                                "description": "One line: what the user gets from it", "author": "Your name"}
    if args.kind == "connector":
        manifest["permissions"] = {"hosts": ["api.example.com"]}
        manifest["connector"] = {"connect": {"type": "key", "hosts": ["api.example.com"], "placement": {"type": "bearer_header"},
                                             "base_url": "https://api.example.com/v1", "api": "Short notes for the agent: the main endpoints.",
                                             "help": ["Where the user finds or makes the key.", "Paste it here."], "blurb": "What Kelo can do with it"}}
    elif args.kind == "extension":
        manifest["permissions"] = {"ui": ["home_card"], "actions": ["store"], "store": True}
        manifest["store"] = {"collections": {"items": {"fields": {"title": {"type": "text", "max": 60, "required": True}}}}}
        manifest["ui"] = {"schema": nm.UI_SCHEMA,
                          "surfaces": [{"id": "main", "place": "home_card", "title": "My card", "icon": "star", "screen": "main"}],
                          "data": {"items": {"source": "store", "collection": "items", "limit": 20}},
                          "actions": {"add": {"type": "store_add", "collection": "items", "values": {"title": "{{form.title}}"}, "label": "Add"}},
                          "screens": {"main": {"components": [
                              {"type": "list", "data": "items", "item": {"title": "{{item.title}}"}, "empty": {"text": "Nothing yet."}},
                              {"type": "form", "fields": [{"id": "title", "type": "text", "label": "Title", "required": True}],
                               "submit": {"label": "Add", "action": "add"}}]}}}
    else:
        (folder / SKILL_FILE).write_text(f"---\nname: {manifest['name']}\ndescription: One line saying when Kelo should use this guide\n"
                                         f"requires: []\n---\n# {manifest['name']}\n\nThe steps, written for Kelo.\n", encoding="utf-8")
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Made {folder}/ ({args.kind} {pid}). Edit it, then: kelo nest validate {folder}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    try:
        pkg, warnings = _load(folder)
    except (PackageError, OSError) as e:
        return _err(str(e))
    for w in warnings:
        print(f"warning: {w}")
    ctype = pkg.connect.get("type", "") if pkg.kind == "connector" else ""
    print(f"{pkg.id} {pkg.version}: {', '.join(pkg.kinds)}{f' ({ctype})' if ctype else ''} — OK")
    print(f"content hash: {content_hash(file_hashes(folder))}")
    if not pkg.declared:
        print("note: no permissions section; Kelo gives it exactly what the manifest uses. Add one to show your permissions on purpose.")
    print("What the owner is shown before installing:")
    for line in nm.describe(pkg.permissions, kind=pkg.kind, connect_type=ctype, lang="en"):
        print(f"  - {line['text']}")
    return 0


def pack_bytes(folder: Path) -> bytes:
    """A reproducible .tar.gz of the package's files (sorted, no owners, fixed times and modes), under one top folder named by its id."""
    pkg = load_package(folder, source="installed", check_name=False)
    files, _ = _scan(folder)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for rel, size in sorted(files):
            info = tarfile.TarInfo(f"{pkg.id}/{rel.replace(os.sep, '/')}")
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = size, 0, 0o644, 0, 0, "", ""
            with open(folder / rel, "rb") as f:
                tf.addfile(info, f)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def cmd_pack(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    try:
        pkg, warnings = _load(folder)
        data = pack_bytes(folder)
        with tempfile.TemporaryDirectory() as tmp:   # unpacked the way a server will: refuses programs, links, path tricks
            got, _ = unpack(data, Path(tmp), strip_top=None)
            if content_hash(file_hashes(got)) != content_hash(file_hashes(folder)):
                return _err("the archive does not hold the same files")
    except (PackageError, OSError) as e:
        return _err(str(e))
    for w in warnings:
        print(f"warning: {w}")
    out_dir = Path(args.out or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{pkg.id}-{pkg.version}.tar.gz"
    target.write_bytes(data)
    print(f"{target}\nsha256: {sha256(data).hexdigest()}\ncontent hash: {content_hash(file_hashes(folder))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["nest"]:
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog="kelo nest", description="Kelo Nest: make and check packages for Kelo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="make a new package folder")
    p.add_argument("dir")
    p.add_argument("--kind", choices=("skill", "connector", "extension"), default="skill")
    p.add_argument("--id")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("validate", help="check a package as a Kelo server will")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("pack", help="a reproducible .tar.gz with its sha256 and content hash")
    p.add_argument("dir")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_pack)
    args = parser.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "pack_bytes"]
