"""The public website pages (website.py) and the app downloads (downloads.py): no auth, plaintext on purpose."""

from __future__ import annotations

import hashlib
import json
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from .. import website
from ..downloads import PLATFORMS as DOWNLOAD_PLATFORMS
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    downloads = ctx.downloads

    # ---- the public website (website.py): home, install guide, privacy policy. Pairing links (/#pair=CODE) land on "/", whose
    # page carries the "open the Kelo app" panel that site.js shows when the address has #pair=.
    def site_page(page: str, request: Request) -> Response:
        lang = website.pick_lang(request.query_params.get("lang"), request.headers.get("accept-language"))
        apps = downloads.latest() if page == "index" else None
        html = website.render(page, lang, apps)
        tag = '"' + hashlib.sha256(html.encode()).hexdigest()[:16] + '"'
        headers = {"Cache-Control": "no-cache", "ETag": tag, "Vary": "Accept-Language", "Content-Language": lang,
                   "Referrer-Policy": "no-referrer", "Content-Security-Policy": website.CSP, "X-Content-Type-Options": "nosniff"}
        if tag in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers=headers)
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)

    @router.get("/", include_in_schema=False)
    async def landing_home(request: Request) -> Response:
        return site_page("index", request)

    @router.get("/install", include_in_schema=False)
    async def site_install(request: Request) -> Response:
        return site_page("install", request)

    @router.get("/privacy", include_in_schema=False)
    async def site_privacy(request: Request) -> Response:
        return site_page("privacy", request)

    # ---- app downloads (downloads.py): only files the publisher's manifest names; no listing; read only
    dl_headers = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}

    @router.api_route("/download/latest.json", methods=["GET", "HEAD"], include_in_schema=False)
    async def download_latest(request: Request) -> Response:
        body = json.dumps(downloads.latest(), sort_keys=True).encode()
        tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = {**dl_headers, "Cache-Control": "public, max-age=60", "ETag": tag, "Access-Control-Allow-Origin": "*"}
        if tag in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers=headers)
        return Response(b"" if request.method == "HEAD" else body, media_type="application/json", headers=headers)

    @router.api_route("/download/{platform}", methods=["GET", "HEAD"], include_in_schema=False)
    async def download_platform(platform: str) -> Response:
        got = downloads.file(platform) if platform in DOWNLOAD_PLATFORMS else None
        if got is None:
            raise HTTPException(404, "not published yet")
        # a stable link that always means the newest build; the file's own address carries its version
        return Response(status_code=302, headers={**dl_headers, "Location": f"/download/{platform}/{got[1]['file']}",
                                                  "Cache-Control": "no-cache"})

    @router.api_route("/downloads/{platform}", methods=["GET", "HEAD"], include_in_schema=False)
    async def downloads_platform(platform: str) -> Response:
        """The same stable link with an "s" (people type it both ways): /downloads/android -> /download/android's file."""
        return await download_platform(platform)

    @router.api_route("/download/{platform}/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def download_file(platform: str, name: str, request: Request) -> Response:
        got = downloads.file(platform, name) if platform in DOWNLOAD_PLATFORMS else None
        if got is None:
            raise HTTPException(404, "not found")
        path, row = got
        tag = f'"{row["sha256"][:32]}"'
        headers = {**dl_headers, "ETag": tag, "Cache-Control": "public, max-age=3600"}
        if tag in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type=DOWNLOAD_PLATFORMS[platform][1], filename=row["file"], headers=headers)

    @router.get("/assets/{name:path}", include_in_schema=False)   # nested only for the site's moments/ scripts (website.asset)
    async def site_asset(name: str, v: str = "") -> Response:
        got = website.asset(name)
        if got is None:
            raise HTTPException(404, "not found")
        # a link with the current ?v= may be kept for a year; anything else is checked again
        cache = "public, max-age=31536000, immutable" if v == website.version(name) else "public, max-age=300"
        return Response(got[0], media_type=got[1], headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})

    return router
