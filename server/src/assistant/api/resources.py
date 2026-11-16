"""What this server may use (memory, CPU, disk: quota.py, storage_watch.py) and the product pictures of try-on (tools/image_tools.py)."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from .. import profiles as size_profiles
from ..quota import cgroup_limits, host_capacity
from ..tools.image_tools import MEDIA
from fastapi import APIRouter
from .context import AppContext


def router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    cleanup = ctx.cleanup
    images = ctx.images
    quota = ctx.quota
    require_auth = ctx.require_auth
    storage_watch = ctx.storage_watch

    @router.get("/api/resources", dependencies=[Depends(require_auth)])
    async def get_resources(request: Request) -> dict[str, Any]:
        """What this container was given (memory, CPUs, storage cap) and how much is in use, so the app can show it, and the
        storage warning (storage_watch.py) the page and the My row show."""
        cg = cgroup_limits()
        disk: dict[str, Any] = {"used": 0, "limit": None, "parts": {}, "free_on_host": None}
        host = {**host_capacity(), "disk_free": None, "disk_total": None}
        alert: dict[str, Any] = {"level": "ok", "percent": None, "host_low": False, "top": None}
        if quota:
            nums = storage_watch.numbers(fresh=True)
            disk.update(used=nums["used"], limit=nums["limit"], parts=nums["parts"])
            disk["free_on_host"] = host["disk_free"] = nums["free"]
            host["disk_total"] = nums["total"]
            alert = storage_watch.public(nums)
        low, rec = size_profiles.caps("minimum"), size_profiles.caps("recommended")     # the server's share of each size

        limited = {"memory": cg["memory_limit"] is not None, "cpus": cg["cpus"] is not None, "disk": disk["limit"] is not None}

        def share(profile: str, caps: dict[str, Any]) -> dict[str, Any]:
            """Per item: with a limit set, the server container's share of that size (what the limit is compared with); without
            one, the whole computer's requirement (profiles.TOTALS, the site's and README's numbers), because the row then shows the
            whole computer, and a container's share (a 12.6 GB data share on an 11.4 GB disk) read as a need of the computer.
            Bytes as whole numbers (the apps read them as integers; 536870912.0 failed to parse there), CPUs as they are."""
            machine = dict(zip(("memory", "cpus", "disk"), size_profiles.TOTALS[profile]))
            out: dict[str, Any] = {}
            for o in size_profiles.GROUPS["server"]:
                v = caps[o] if limited[o] else machine[o]
                out[o] = v if o == "cpus" or v is None else int(v)
            return out

        return {"memory": {"used": cg["memory_used"], "limit": cg["memory_limit"]}, "cpus": cg["cpus"], "disk": disk, "host": host,
                "profile": size_profiles.guess(cg["memory_limit"], cg["cpus"], disk["limit"]),
                "minimum": share("minimum", low), "recommended": share("recommended", rec), "alert": alert,
                "can_clean": cleanup is not None and (request.state.device_id is None or bool(request.state.device_owner))}

    @router.get("/api/tryon/{name}", dependencies=[Depends(require_auth)])
    async def get_product_image(name: str) -> FileResponse:
        path = images.path(name) if images else None
        if not path:
            raise HTTPException(404, "no such image")
        return FileResponse(path, media_type=MEDIA[path.suffix[1:]],
                            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @router.delete("/api/tryon/{name}", dependencies=[Depends(require_auth)])
    async def remove_product_image(name: str) -> dict[str, bool]:
        if not images or not images.delete(name):
            raise HTTPException(404, "no such image")
        return {"ok": True}

    return router
