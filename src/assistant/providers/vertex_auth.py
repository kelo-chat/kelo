"""Google Vertex AI: Application Default Credentials + the OpenAI-compatible endpoint.

Needs the optional `gcp` extra (google-auth). Run `gcloud auth application-default login`
or point GOOGLE_APPLICATION_CREDENTIALS at a service-account key.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from .types import ProviderError

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def endpoint(project: str, location: str) -> str:
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project}/locations/{location}/endpoints/openapi"


def default_token_source() -> Callable[[], tuple[str, str | None]]:
    """Returns a function giving (access_token, project_id_from_credentials)."""
    try:
        import google.auth  # type: ignore[import-not-found]
        from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise ProviderError("vertex needs the 'gcp' extra: uv sync --extra gcp") from e

    def get() -> tuple[str, str | None]:
        creds, project = google.auth.default(scopes=[SCOPE])
        creds.refresh(Request())
        return creds.token, project

    return get


async def resolve(
    env: dict[str, str] | None = None,
    token_source: Callable[[], tuple[str, str | None]] | None = None,
) -> tuple[str, str]:
    """Return (base_url, bearer_token)."""
    env = dict(os.environ) if env is None else env
    source = token_source or default_token_source()
    token, adc_project = await asyncio.to_thread(source)
    project = env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCLOUD_PROJECT") or adc_project
    if not project:
        raise ProviderError("vertex: set GOOGLE_CLOUD_PROJECT")
    location = env.get("GOOGLE_CLOUD_LOCATION") or env.get("GOOGLE_CLOUD_REGION") or "global"
    return endpoint(project, location), token
