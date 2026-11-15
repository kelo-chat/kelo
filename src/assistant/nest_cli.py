"""`kelo nest`: the command line for Kelo Nest publishers and for the Nest operator. Nothing here touches the network or runs git: it
reads and writes files, and prints what to do next.

Publishers (docs/nest.md):
    kelo nest init <dir> --kind skill|connector|extension --id <id>    a new package folder to edit
    kelo nest validate <dir>                                           check it as a Kelo server will, and show its permissions
    kelo nest pack <dir> [--out <dir>]                                 a reproducible .tar.gz, its sha256 and the content hash
    kelo nest publish <dir> --registry <checkout> --publisher <id> (--git-url <url> --commit <sha> [--path <sub>] |
                      --archive <file> --archive-url <url>)            the version file for a pull request to the registry

Operator (the registry repository, kelo-chat/nest):
    kelo nest check-registry <checkout>                                check every file of a registry checkout (for CI on pull requests)
    kelo nest keygen --out <dir>                                       a new signing key pair (minisign format)
    kelo nest index build --registry <checkout> --out index.json [--sequence N] [--expires-days 30]
    kelo nest index sign --key <secret key> index.json                 index.json.minisig
    kelo nest index verify --pub <public key> index.json

Registry layout:
    publishers.json            {"<publisher id>": {"name", "verified", "homepage"?}}
    verified.json              ["<package id>", ...]  (packages the operator reviewed)
    withdrawn.json             [{"id", "versions": "*" | [...], "hashes"?: [...], "reason"}]  (the kill-switch list)
    packages/<id>/<version>.json   one file per published version (written by `kelo nest publish`)
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
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import nest_manifest as nm
from .nest_index import FORMAT, PUBLISHER_ID, parse, version_key
from .nest_sign import SignatureError, generate, read_public_key, read_secret_key, sign, verify
from .package_index import _source, unpack
from .packages import MANIFEST, SKILL_FILE, Package, PackageError, _scan, content_hash, file_hashes, load_package

VERSION_FILE_FIELDS = {"id", "publisher", "name", "description", "kinds", "homepage", "version", "published_at", "content_hash", "permissions",
                       "connect", "source"}


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _load(folder: Path) -> tuple[Package, list[str]]:
    warnings: list[str] = []
    pkg = load_package(folder, source="installed", warnings=warnings, check_name=False)
    if pkg.id != folder.name:
        warnings.append(f"the folder is named {folder.name!r} but the id is {pkg.id!r}; a Kelo server stores it as {pkg.id}/")
    return pkg, warnings


def _texts(pkg: Package, key: str) -> dict[str, str]:
    out = {"en": str(getattr(pkg, key))}
    for lang, vals in pkg.strings.items():
        if vals.get(key):
            out[lang] = str(vals[key])
    return out


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


def version_record(folder: Path, publisher: str, source: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    pkg, _ = _load(folder)
    return {"id": pkg.id, "publisher": publisher, "name": _texts(pkg, "name"), "description": _texts(pkg, "description"),
            "kinds": list(pkg.kinds), **({"homepage": pkg.homepage} if pkg.homepage else {}), "version": pkg.version,
            "published_at": int(now if now is not None else time.time()), "content_hash": content_hash(file_hashes(folder)),
            "permissions": nm.plain(pkg.permissions), "connect": pkg.connect.get("type", "") if pkg.kind == "connector" else "",
            "source": dict(_source(source))}


def cmd_publish(args: argparse.Namespace) -> int:
    folder, reg = Path(args.dir), Path(args.registry)
    if not PUBLISHER_ID.fullmatch(args.publisher or ""):
        return _err("--publisher must be your publisher id (lower-case letters, digits and '-')")
    if args.git_url:
        if not args.commit:
            return _err("--commit is needed with --git-url (a full 40-character commit, never a tag or branch)")
        source: dict[str, Any] = {"type": "git", "url": args.git_url, "commit": args.commit, **({"path": args.path} if args.path else {})}
    elif args.archive and args.archive_url:
        data = Path(args.archive).read_bytes()
        source = {"type": "archive", "url": args.archive_url, "sha256": sha256(data).hexdigest(), **({"path": args.path} if args.path else {})}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                got, _ = unpack(data, Path(tmp), strip_top=None, subpath=args.path or "")
            except PackageError as e:
                return _err(f"the archive: {e}")
            if content_hash(file_hashes(got)) != content_hash(file_hashes(folder)):
                return _err("the archive does not hold the same files as the folder; pack it again (kelo nest pack)")
    else:
        return _err("say where Kelo servers fetch it: --git-url and --commit, or --archive and --archive-url")
    try:
        rec = version_record(folder, args.publisher, source)
    except (PackageError, OSError) as e:
        return _err(str(e))
    target = reg / "packages" / rec["id"] / f"{rec['version']}.json"
    if target.exists():
        return _err(f"{target} exists: a published version never changes; raise the version instead")
    others = sorted((reg / "packages" / rec["id"]).glob("*.json")) if (reg / "packages" / rec["id"]).is_dir() else []
    for o in others:
        try:
            prev = json.loads(o.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if prev.get("publisher") != args.publisher:
            return _err(f"{rec['id']} belongs to publisher {prev.get('publisher')!r}")
        if version_key(prev.get("version", "0.0.0")) >= version_key(rec["version"]):
            print(f"warning: {o.name} is not older than {rec['version']}")
        d = nm.diff(prev.get("permissions"), rec["permissions"])
        if d["widened"] and o == others[-1]:
            print("note: this version asks for more permissions than the last one; every server will ask its owner again:")
            for w in d["widened"]:
                print(f"  + {w['what']}: {', '.join(map(str, w['added']))}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rel = target.relative_to(reg)
    print(f"Wrote {target}.\nOpen a pull request to the Nest registry with it:\n"
          f"  cd {reg}\n  git checkout -b {rec['id']}-{rec['version']}\n  git add {rel}\n"
          f"  git commit -m \"{rec['id']} {rec['version']}\"\n  git push -u origin {rec['id']}-{rec['version']}\n"
          "Then open the pull request on the registry's page. The check runs `kelo nest check-registry`; once merged, the next index has it.")
    return 0


# ---------------------------------------------------------------------------------------------------------------- operator
def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_registry(reg: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """(publishers, version records, problems) of a registry checkout."""
    problems: list[str] = []
    publishers = _read_json(reg / "publishers.json", {})
    if not isinstance(publishers, dict):
        return {}, [], ["publishers.json must be an object"]
    records: list[dict[str, Any]] = []
    root = reg / "packages"
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir():
            problems.append(f"packages/{d.name}: not a folder")
            continue
        for f in sorted(d.glob("*.json")):
            where = f"packages/{d.name}/{f.name}"
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except ValueError as e:
                problems.append(f"{where}: not JSON ({e})")
                continue
            if not isinstance(rec, dict) or set(rec) - VERSION_FILE_FIELDS:
                problems.append(f"{where}: unknown fields {', '.join(sorted(set(rec) - VERSION_FILE_FIELDS)) if isinstance(rec, dict) else ''}")
                continue
            if rec.get("id") != d.name or f"{rec.get('version')}.json" != f.name:
                problems.append(f"{where}: the id and version must match the folder and file name")
                continue
            if rec.get("publisher") not in publishers:
                problems.append(f"{where}: publisher {rec.get('publisher')!r} is not in publishers.json")
                continue
            records.append(rec)
    by_id: dict[str, set[str]] = {}
    for r in records:
        by_id.setdefault(r["id"], set()).add(r["publisher"])
    for pid, pubs in by_id.items():
        if len(pubs) > 1:
            problems.append(f"packages/{pid}: versions from more than one publisher ({', '.join(sorted(pubs))})")
    return publishers, records, problems


def build_index(reg: Path, *, sequence: int, now: float, expires_days: float = 30) -> dict[str, Any]:
    publishers, records, problems = read_registry(reg)
    if problems:
        raise PackageError("; ".join(problems))
    verified = set(_read_json(reg / "verified.json", []))
    withdrawn = _read_json(reg / "withdrawn.json", [])
    entries: dict[str, dict[str, Any]] = {}
    for r in sorted(records, key=lambda r: (r["id"], version_key(r["version"])), reverse=True):
        e = entries.get(r["id"])
        if e is None:   # the newest version names the package
            e = entries[r["id"]] = {"id": r["id"], "publisher": r["publisher"], "name": r["name"], "description": r.get("description") or {"en": ""},
                                    "kinds": r["kinds"], "verified": r["id"] in verified, **({"homepage": r["homepage"]} if r.get("homepage") else {}),
                                    "versions": []}
            if not e["description"].get("en"):
                e.pop("description")
        e["versions"].append({k: r[k] for k in ("version", "published_at", "content_hash", "permissions", "source")}
                             | ({"connect": r["connect"]} if r.get("connect") else {}))
    index = {"format": FORMAT, "name": "Kelo Nest", "sequence": int(sequence), "generated_at": int(now), "expires_at": int(now + expires_days * 86400),
             "publishers": {k: {kk: vv for kk, vv in v.items() if kk in ("name", "verified", "homepage")} for k, v in sorted(publishers.items())},
             "packages": [entries[k] for k in sorted(entries)], "withdrawn": withdrawn}
    parse(json.dumps(index).encode(), now=now)   # the index a server will read: refused here first
    return index


def cmd_check_registry(args: argparse.Namespace) -> int:
    reg = Path(args.registry)
    _, records, problems = read_registry(reg)
    if not problems:
        try:
            build_index(reg, sequence=1, now=time.time())
        except PackageError as e:
            problems.append(str(e))
    for p in problems:
        print(f"problem: {p}")
    print(f"{len(records)} versions checked, {len(problems)} problems")
    return 1 if problems else 0


def cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sk_path, pk_path = out / "nest.key", out / "nest.pub"
    if sk_path.exists() and not args.force:
        return _err(f"{sk_path} exists (use --force to replace it)")
    key = generate()
    fd = os.open(sk_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.file())
    pk_path.write_text(key.public_key().file(), encoding="utf-8")
    print(f"secret key: {sk_path} (keep it offline; anyone with it can publish to every Kelo server)\npublic key: {pk_path}\n"
          f"Put this line in nest_index.TRUSTED_KEYS (or ASSISTANT_NEST_KEYS):\n{key.public_key().line()}")
    return 0


def cmd_index_build(args: argparse.Namespace) -> int:
    now = time.time()
    try:
        index = build_index(Path(args.registry), sequence=args.sequence or int(now), now=now, expires_days=args.expires_days)
    except (PackageError, OSError, ValueError) as e:
        return _err(str(e))
    Path(args.out).write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(index['packages'])} packages, sequence {index['sequence']}, expires in {args.expires_days:g} days")
    return 0


def cmd_index_sign(args: argparse.Namespace) -> int:
    path = Path(args.index)
    try:
        key = read_secret_key(Path(args.key).read_text(encoding="utf-8"))
        data = path.read_bytes()
        index = parse(data)
        sig = sign(data, key, trusted_comment=f"kelo-nest sequence={index.sequence} sha256={index.digest}")
    except (SignatureError, PackageError, OSError) as e:
        return _err(str(e))
    out = path.with_name(path.name + ".minisig")
    out.write_text(sig, encoding="utf-8")
    print(f"{out}")
    return 0


def cmd_index_verify(args: argparse.Namespace) -> int:
    path = Path(args.index)
    try:
        key = read_public_key(Path(args.pub).read_text(encoding="utf-8"))
        data = path.read_bytes()
        got = verify(data, path.with_name(path.name + ".minisig").read_text(encoding="utf-8"), [key])
        index = parse(data)
    except (SignatureError, PackageError, OSError) as e:
        return _err(str(e))
    print(f"OK: sequence {index.sequence}, {len(index.entries)} packages, trusted comment: {got.trusted_comment}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["nest"]:
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog="kelo nest", description="Kelo Nest: publish packages for Kelo, and run the Nest index")
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
    p = sub.add_parser("publish", help="write the version file for a pull request to the Nest registry (no network)")
    p.add_argument("dir")
    p.add_argument("--registry", required=True, help="your checkout of the registry repository")
    p.add_argument("--publisher", required=True)
    p.add_argument("--git-url")
    p.add_argument("--commit")
    p.add_argument("--path")
    p.add_argument("--archive")
    p.add_argument("--archive-url")
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("check-registry", help="operator: check a registry checkout")
    p.add_argument("registry")
    p.set_defaults(fn=cmd_check_registry)
    p = sub.add_parser("keygen", help="operator: a new index signing key")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_keygen)
    idx = sub.add_parser("index", help="operator: build, sign and verify the index")
    isub = idx.add_subparsers(dest="icmd", required=True)
    p = isub.add_parser("build")
    p.add_argument("--registry", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sequence", type=int, default=0, help="default: the current unix time (always higher than the last)")
    p.add_argument("--expires-days", type=float, default=30)
    p.set_defaults(fn=cmd_index_build)
    p = isub.add_parser("sign")
    p.add_argument("--key", required=True)
    p.add_argument("index")
    p.set_defaults(fn=cmd_index_sign)
    p = isub.add_parser("verify")
    p.add_argument("--pub", required=True)
    p.add_argument("index")
    p.set_defaults(fn=cmd_index_verify)
    args = parser.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_index", "main", "pack_bytes", "read_registry", "version_record"]
