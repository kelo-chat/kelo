# iOS: what to mirror for the Nest "exactly what this installs" view

Source of truth: the shared Kotlin code (apps/shared, my/NestContents.kt, my/NestContentsSheet.kt, my/NestLinks.kt, my/NestPage.kt)
and the strings in apps/shared/strings/values*/strings_nest_contents.xml (20 locales). Server contract: server/src/assistant/nest_routes.py docstring.

## Data (decode only what is there; every field optional, older servers send none)
- POST /api/nest/install-from-link card: `contents` = { files: [{path, bytes, sha256}], total_bytes, texts: {path: text},
  cut: [path], endpoints: [string], warnings: [string] }.
- GET /api/nest/updates and /api/nest/installed rows: `update.changed` = { added: [path], removed: [path], modified: [path] }.
  Installed rows carry no `contents`.
- 422 from install-from-link and confirm: `detail` text is shown under the link field as the reason (server's words, not replaced).

## Behaviour
1. Preview card: after the "What it may do" word lines, a link button "See everything it will install" when contents has any
   files, endpoints or warnings. It opens a sheet:
   - Title, then Warnings (red text) only when not empty.
   - "Where it connects": each endpoint on its own line, monospace; "Nothing" when empty.
   - "Files": each file as path (monospace) with its size under it. Size: under 1024 bytes "N B", else whole KB rounded UP.
     "Total" line at the bottom from total_bytes.
   - A file whose path is a key of `texts` is tappable: the sheet swaps to that file's text (monospace), with a back link
     "All files"; if the path is in `cut`, show "This file is too long, so only part of it is shown." under the text.
   - The sheet scrolls vertically only. Long paths and long lines WRAP (no horizontal scroll, no page overflow).
   - Must render at 320 pt and 412 pt wide with long spaceless paths.
2. Update row (installed packages): when changed count > 0, show "N changed files" (N = added + removed + modified) under the
   "version ready" line, and a link "See the changes". The sheet: title "What this update changes", the installed package name,
   then three lists, each only when non-empty: "Added files", "Removed files", "Changed files" (monospace paths, wrapping).
3. Refusal reason: under the problem text in the link field, show the server's `detail` in muted small text when not blank.
4. Stacking: the contents sheet opens on top of the card sheet; closing it returns to the card. A new preview clears it.

## Strings (keys in strings_nest_contents.xml; English is values/, ko and others in their folders)
| key | en | ko |
|---|---|---|
| nest_contents_open | See everything it will install | 설치될 내용 전체 보기 |
| nest_contents_title | What gets installed | 설치될 내용 |
| nest_contents_warnings | Notes from loading it | 불러오면서 나온 알림 |
| nest_contents_endpoints | Where it connects | 접속하는 곳 |
| nest_contents_none | Nothing | 없음 |
| nest_contents_files | Files | 파일 |
| nest_contents_total | %1$s in total | 전체 %1$s |
| nest_contents_cut | This file is too long, so only part of it is shown. | 이 파일은 너무 길어서 일부만 보여요. |
| nest_contents_back | All files | 전체 파일 |
| nest_contents_bytes | %1$d B | %1$d B |
| nest_contents_kb | %1$d KB | %1$d KB |
| nest_changed_open | See the changes | 바뀐 내용 보기 |
| nest_update_changed | %1$d changed files | 바뀐 파일 %1$d개 |
| nest_changed_title | What this update changes | 이번 업데이트에서 바뀌는 것 |
| nest_changed_added | Added files | 추가된 파일 |
| nest_changed_removed | Removed files | 삭제된 파일 |
| nest_changed_modified | Changed files | 바뀐 파일 |

Use the same key names in the iOS string catalog, with the translations from the Android files (copy by key, do not retranslate).

## Tests to mirror
- Parsing of the card's contents and the update's changed (see apps/shared/src/desktopTest/.../my/NestContentsTest.kt).
- Count rule: changed count = added + removed + modified; size rule as above (1023 -> B, 1024 -> 1 KB, 1025 -> 2 KB).
- Wrap check at 320 and 412 pt with a long spaceless path.
