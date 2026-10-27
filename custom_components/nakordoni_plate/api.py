"""aiohttp client for the Nakordoni DevAPI plate recognition product."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import re
from typing import Any, Awaitable, Callable

import aiohttp

from .const import MAX_IMAGE_BYTES


class NakordoniError(Exception):
    """Generic API error."""


class NakordoniAuthError(NakordoniError):
    """Invalid or revoked credentials."""


def normalize_plate(raw: str | None) -> str:
    """Upper-case, keep A-Z0-9 only."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


@dataclass
class RecognitionResult:
    """Parsed API response (empty results when no plate was found)."""

    success: bool
    message: str | None
    processing_time: float | None
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.results)

    def in_regions(self, regions: list[str]) -> "RecognitionResult":
        """Local region filter (the API ignores regions). Null region is kept."""
        if not regions:
            return self
        wanted = {r.lower() for r in regions}

        def keep(r: dict[str, Any]) -> bool:
            reg = r.get("region")
            if not isinstance(reg, dict) or not reg.get("code"):
                return True
            return str(reg["code"]).lower() in wanted

        return RecognitionResult(
            self.success, self.message, self.processing_time,
            [r for r in self.results if keep(r)],
        )

    def filtered(self, min_score: float) -> "RecognitionResult":
        return RecognitionResult(
            self.success,
            self.message,
            self.processing_time,
            [r for r in self.results if (r.get("score") or 0) >= min_score],
        )


class NakordoniPlateClient:
    """Calls POST <api_url>. The token getter returns the DevAPI key.

    The OAuth access token IS the user's DevAPI key (F1449), so both auth
    modes send the same X-API-Key header.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        token_getter: Callable[[], Awaitable[str]],
    ) -> None:
        self._session = session
        self._url = api_url
        self._token = token_getter

    async def _headers(self) -> dict[str, str]:
        token = await self._token()
        return {"X-API-Key": token, "Accept": "application/json"}

    async def recognize_bytes(
        self, image: bytes, regions: list[str] | None = None
    ) -> RecognitionResult:
        if len(image) > MAX_IMAGE_BYTES:
            raise NakordoniError("image larger than 8 MB")
        form = aiohttp.FormData()
        form.add_field("image", image, filename="image.jpg", content_type="image/jpeg")
        for reg in regions or []:
            form.add_field("regions", reg)
        return await self._post(data=form)

    async def recognize_url(
        self, image_url: str, regions: list[str] | None = None
    ) -> RecognitionResult:
        body: dict[str, Any] = {"image_url": image_url}
        if regions:
            body["regions"] = regions
        return await self._post(json=body)

    async def recognize_base64(
        self, image: bytes, regions: list[str] | None = None
    ) -> RecognitionResult:
        body: dict[str, Any] = {"base64": base64.b64encode(image).decode()}
        if regions:
            body["regions"] = regions
        return await self._post(json=body)

    async def validate(self) -> None:
        """Check the key: an empty JSON body gives 400 for a valid key."""
        try:
            await self._post(json={})
        except NakordoniAuthError:
            raise
        except NakordoniError:
            return

    async def _post(self, **kwargs: Any) -> RecognitionResult:
        headers = await self._headers()
        try:
            async with self._session.post(
                self._url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), **kwargs
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                code = data.get("code") or data.get("error")
                if resp.status in (401, 403):
                    raise NakordoniAuthError(data.get("message") or f"HTTP {resp.status}")
                if resp.status == 404:
                    # no_plate_detected (or any 404) = zero plates, not an error
                    return RecognitionResult(
                        True, data.get("message") or code or "no_plate_detected",
                        data.get("processing_time"), [],
                    )
                if resp.status >= 400:
                    raise NakordoniError(
                        f"HTTP {resp.status}: {code or ''} {data.get('message') or ''}".strip()
                    )
                return RecognitionResult(
                    bool(data.get("success", True)),
                    data.get("message"),
                    data.get("processing_time"),
                    [r for r in (data.get("results") or []) if isinstance(r, dict)],
                )
        except aiohttp.ClientError as err:
            raise NakordoniError(str(err)) from err
