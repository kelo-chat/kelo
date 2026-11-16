"""`assistant eval ...`: behaviour checks on this copy.

    assistant eval export [--days 7] --out DIR [--instance main]   the replies of the last days as redacted golden threads (read-only)
    assistant eval live [--scenario NAME] [--instance main] --yes  the live scenarios against the instance's own model (never in CI)

The export is for server/tests/evals/golden (review by hand, then commit only the reviewed files). The live run is in eval_live.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .eval_export import ExportError, db_of, env_for, export
from .instances import Instances


def add_parser(sub: Any) -> None:
    ev = sub.add_parser("eval", help="behaviour checks: export real replies as golden tests, or run the live scenarios on your model")
    evsub = ev.add_subparsers(dest="evcmd", required=True)
    ex = evsub.add_parser("export", help="write the replies of the last days as redacted golden threads (the database is only read)")
    ex.add_argument("--days", type=float, default=7, help="how many days back (replies are kept 24 hours; default 7)")
    ex.add_argument("--out", type=Path, required=True, help="folder for the JSON files (created if missing)")
    ex.add_argument("--instance", default="main", help="which copy (default main)")
    ex.add_argument("--env-file", type=Path, help="env file of the copy (default: the instance's)")
    live = evsub.add_parser("live", help="run the live scenarios against the instance's model (spends model quota; refused in CI)")
    live.add_argument("--scenario", help="one live scenario by name")
    live.add_argument("--instance", default="main", help="which copy (default main)")
    live.add_argument("--env-file", type=Path, help="env file of the copy (default: the instance's)")
    live.add_argument("--yes", action="store_true", help="confirm: this spends the model quota")


def _env_file(args: argparse.Namespace) -> Path:
    return args.env_file or Instances(Path.cwd()).env_path(args.instance)


def run_command(args: argparse.Namespace, out=print) -> int:
    env_file = _env_file(args)
    if args.evcmd == "export":
        env = env_for(env_file)
        db, key = db_of(env)
        if not db.is_file():
            out(f"no database at {db}")
            return 2
        try:
            report = export(db, args.out, args.days, secret_key=key)
        except ExportError as e:
            out(str(e))
            return 2
        if not report.ok:
            out(f"export stopped: the scan found sensitive text ({', '.join(report.leaks)}); nothing was kept")
            return 1
        out(f"{len(report.written)} thread(s) written to {args.out}")
        if report.redactions:
            out("redacted: " + ", ".join(f"{k} {n}" for k, n in sorted(report.redactions.items())))
        return 0
    from .eval_live import run_live
    return run_live(env_file, name=args.scenario, yes=args.yes, out=out)
