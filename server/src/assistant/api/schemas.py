"""Request bodies more than one router reads (a router keeps its own models otherwise)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SeenIn(BaseModel):
    up_to: int


class GitHubAppIn(BaseModel):
    client_id: str | None = Field(default=None, max_length=100)
    client_secret: str | None = Field(default=None, max_length=200)


class ExchangeIn(BaseModel):
    code: str = Field(min_length=4, max_length=200)
    redirect_uri: str = Field(max_length=300)
    code_verifier: str = Field(default="", max_length=200)
    install_id: str = Field(default="", max_length=128)   # Google: an approved beta copy's install secret (beta.py)
