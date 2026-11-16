# Repository layout: server/ and apps/

Status: design v1 (2026-11-14), owner: make the layout easy to read as server/, apps/shared, apps/android, apps/ios, apps/desktop. One private
monorepo stays the source (docs/native-server.md and the open-source plan push read-only public mirrors of subsets later).

## Target

```
/                       the server's deployment stays here (see "What must not move")
├── apps/
│   ├── shared/         Kotlin Multiplatform code + strings of all apps   (was shared/)
│   ├── android/        Android app                                       (was android/)
│   ├── ios/            iPhone app                                        (was ios/)
│   └── desktop/        Windows and macOS app                             (was desktop/)
├── server/
│   ├── src/assistant/  the server's Python package                       (was src/)
│   ├── tests/          the server's tests                                (was tests/)
│   ├── web/            the small web app                                 (was web/)
│   ├── content/        prompts and skills                                (was content/)
│   └── examples/       example packages                                  (was examples/)
├── docker/             the four Dockerfiles (server, cli, updater, workroom) and what they copy in
├── .github/            workflows, issue templates, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
├── compose.yml, .env, instances/, connect/                           ← unchanged
├── pyproject.toml, uv.lock, version.json, CHANGELOG.md, README*, LICENSE, NOTICE ← unchanged place
├── docs/, tools/, scripts/, releases/                                 ← unchanged place
```

## What must not move (and why)
- `compose.yml`, `.env`, `instances/`, `connect/`: the running main copy and the extra copies (u-test1, the
  review copies, the owners' copies) are this folder. Compose names its project and volumes after the folder that holds compose.yml;
  moving it would start new, empty volumes. `assistant update --from-source` builds from this root.
- `pyproject.toml`, `uv.lock`: `uv run assistant …` is run from the root by the owner and by the docs; the package path inside it
  changes (server/src), the file does not.
- `version.json`: one file all apps and the server read.

## Phases (each its own branch, full checks, merged alone)
1. **apps/** — `git mv` shared, android, ios, desktop into apps/ and fix every reference. No behaviour change.
2. **server/** — `git mv` src and tests into server/; pyproject (package and test paths), Dockerfiles' COPY lines, CI, tools, docs.
   Checked additionally by a Docker build and a deploy to a test copy before the main copy.
3. **root tidy** (2026-11-15) — the Dockerfiles into docker/ (`server.Dockerfile`, `cli.Dockerfile`, `updater.Dockerfile`,
   `workroom.Dockerfile`; the build context stays the root), web/, content/ and examples/ under server/, the community files into
   .github/. The CLA draft is gone (AGPL-3.0 only, no CLA). `connect/` stays: per-machine settings already on disk.

Old open branches: none at the time of writing (everything merged 2026-11-14). A branch started before a phase merges is rebased
with `git mv` renames detected (`git rebase -X find-renames`).

## Rules for the move
- Only `git mv` (history follows the files). No file content changes except path references.
- Relative paths between the apps keep working because they move together (apps/android/kelo → ../../shared is still apps/shared);
  paths to the root (version.json, keystore, tools) get one more `../`.
- Every reference is found by search, not by memory: each old prefix (`shared/`, `android/`, `ios/`, `desktop/`; later `src/`,
  `tests/`) in every tracked file, including hidden ones (.github, .gitignore, .dockerignore, .gitattributes), scripts, docs,
  Gradle, Xcode project specs, Python tools and tests. A leftover old path is a failing check.
