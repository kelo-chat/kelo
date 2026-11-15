#!/usr/bin/env python3
"""Upload a Kelo AAB to a Google Play track with the Play Developer API (androidpublisher v3), from this Mac.

Needs a service account key (JSON) that has been invited in Play Console -> Users and permissions with release rights for
chat.kelo.app. The key file is read from --key or $KELO_PLAY_KEY; it is never printed. See PLAY.md, "자동 업로드".

Run (uv fetches the two libraries into a throwaway environment):
    uv run --with google-auth --with requests python3 android/play/upload.py --track internal
    uv run --with google-auth --with requests python3 android/play/upload.py --track internal --aab path/to.aab --notes "버그 수정"

Defaults are the safe ones: the release is created as a draft (status "draft") so nothing reaches testers until you press
"Review release / Roll out" in Play Console. Use --status completed to roll it out to that track right away (internal testing
only goes to your tester list). A first-ever upload of the app must be done by hand in Play Console; the API only works on an
app that already has one release.
"""
import argparse
import glob
import os
import sys

PACKAGE = "chat.kelo.app"
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/" + PACKAGE
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/" + PACKAGE
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
HERE = os.path.dirname(os.path.abspath(__file__))
BUILT = os.path.join(HERE, "..", "kelo", "app", "build", "outputs", "bundle", "release", "app-release.aab")


def newest_aab():
    found = sorted(glob.glob(os.path.join(HERE, "*.aab")), key=os.path.getmtime)
    if os.path.exists(BUILT):
        found.append(BUILT)
        found.sort(key=os.path.getmtime)
    return found[-1] if found else None


def fail(resp, what):
    # Google's error body names the problem (wrong permission, version code used before, ...); it holds no secrets.
    sys.exit(f"{what} failed: HTTP {resp.status_code}\n{resp.text[:2000]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=os.environ.get("KELO_PLAY_KEY"), help="service account JSON key file (or $KELO_PLAY_KEY)")
    ap.add_argument("--aab", help="the bundle to upload (default: newest .aab in android/play or the last bundleRelease)")
    ap.add_argument("--track", default="internal", help="internal | alpha (closed testing) | beta (open testing) | production")
    ap.add_argument("--status", default="draft", choices=["draft", "completed", "inProgress"],
                    help="draft (default): you roll it out in Play Console; completed: roll out now; inProgress: staged rollout")
    ap.add_argument("--fraction", type=float, default=0.1, help="share of users for --status inProgress (0-1)")
    ap.add_argument("--notes", default="", help="release notes (ko-KR); empty for none")
    ap.add_argument("--dry-run", action="store_true", help="check the key and the app, then throw the edit away")
    args = ap.parse_args()

    if not args.key or not os.path.isfile(args.key):
        sys.exit("No service account key. Pass --key <file.json> or set KELO_PLAY_KEY (see PLAY.md).")
    aab = args.aab or newest_aab()
    if not aab or not os.path.isfile(aab):
        sys.exit("No .aab found. Build one with ./gradlew bundleRelease or pass --aab.")

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        sys.exit("Missing libraries. Run with: uv run --with google-auth --with requests python3 android/play/upload.py ...")

    creds = service_account.Credentials.from_service_account_file(args.key, scopes=[SCOPE])
    s = AuthorizedSession(creds)

    r = s.post(f"{API}/edits", json={})
    if r.status_code != 200:
        fail(r, "Opening an edit")
    edit = r.json()["id"]
    try:
        if args.dry_run:
            r = s.get(f"{API}/edits/{edit}/tracks")
            if r.status_code != 200:
                fail(r, "Reading tracks")
            for t in r.json().get("tracks", []):
                codes = [c for rel in t.get("releases", []) for c in rel.get("versionCodes", [])]
                print(f"track {t['track']}: version codes {codes}")
            print("Dry run: key and app are fine; nothing was uploaded.")
            return

        print(f"Uploading {os.path.basename(aab)} ({os.path.getsize(aab) // 1024} KB)...")
        with open(aab, "rb") as f:
            r = s.post(f"{UPLOAD}/edits/{edit}/bundles", params={"uploadType": "media"}, data=f,
                       headers={"Content-Type": "application/octet-stream"}, timeout=600)
        if r.status_code != 200:
            fail(r, "Uploading the bundle")
        code = r.json()["versionCode"]
        print(f"Uploaded version code {code}.")

        release = {"versionCodes": [str(code)], "status": args.status}
        if args.status == "inProgress":
            release["userFraction"] = args.fraction
        if args.notes:
            release["releaseNotes"] = [{"language": "ko-KR", "text": args.notes[:500]}]
        r = s.put(f"{API}/edits/{edit}/tracks/{args.track}", json={"track": args.track, "releases": [release]})
        if r.status_code != 200:
            fail(r, f"Putting it on the {args.track} track")

        r = s.post(f"{API}/edits/{edit}:commit")
        if r.status_code != 200:
            fail(r, "Committing the edit")
        edit = None
        print(f"Done: version code {code} is on the {args.track} track as {args.status}.")
        # Review and rollout can take days, so this script never marks a release live. The operator records it when it is
        # (a minimum app version may never be above a recorded release; internal testing does not count).
        print(f"When it is live for people (production or open testing, not internal testing), record it with\n"
              f"  assistant release-record android <version> {code} play")
        if args.status == "draft":
            print("Open Play Console -> Test and release -> the track -> Edit release -> Review release -> roll it out.")
    finally:
        if edit:
            s.delete(f"{API}/edits/{edit}")


if __name__ == "__main__":
    main()
