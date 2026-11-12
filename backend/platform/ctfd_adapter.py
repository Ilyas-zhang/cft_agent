"""CTFd platform adapter — reference implementation of PlatformAdapter.

Wraps the existing CTFdClient to conform to the abstract interface.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.ctfd import CTFdClient
from backend.platform.base import (
    ChallengeInfo,
    PlatformAdapter,
    PlatformConfig,
    SubmitResult,
)

logger = logging.getLogger(__name__)


class CTFdAdapter(PlatformAdapter):
    """CTFd platform adapter."""

    def __init__(self, config: PlatformConfig) -> None:
        self.config = config
        self._client = CTFdClient(
            base_url=config.base_url,
            token=config.token,
            username=config.username,
            password=config.password,
        )
        self._challenge_cache: dict[str, ChallengeInfo] = {}
        self._solved_cache: set[str] = set()

    async def connect(self) -> None:
        """Initialize connection — just ensure the client is ready."""
        # CTFdClient lazily connects on first request
        logger.info(f"CTFd adapter ready: {self.config.base_url}")

    async def get_challenges(self) -> list[ChallengeInfo]:
        """Fetch all challenges from CTFd."""
        raw_challenges = await self._client.fetch_all_challenges()
        results = []
        for ch in raw_challenges:
            info = self._ctfd_to_info(ch)
            self._challenge_cache[info.id] = info
            results.append(info)
        return results

    async def get_challenge(self, challenge_id: str) -> ChallengeInfo:
        """Fetch a single challenge by name (CTFd uses names as IDs in our mapping)."""
        if challenge_id in self._challenge_cache:
            return self._challenge_cache[challenge_id]
        # Refresh cache
        await self.get_challenges()
        if challenge_id in self._challenge_cache:
            return self._challenge_cache[challenge_id]
        raise KeyError(f"Challenge '{challenge_id}' not found on CTFd")

    async def get_unsolved_challenges(self) -> list[ChallengeInfo]:
        """Get challenges not yet solved."""
        all_challenges = await self.get_challenges()
        solved = await self.get_solved_challenges()
        return [ch for ch in all_challenges if ch.id not in solved]

    async def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        """Submit a flag via CTFd."""
        try:
            result = await self._client.submit_flag(challenge_id, flag)
            return SubmitResult(
                status=result.status,
                message=result.message,
                display=result.display,
            )
        except Exception as e:
            return SubmitResult(
                status="unknown",
                message=str(e),
                display=f"Submission error: {e}",
            )

    async def get_solved_challenges(self) -> list[str]:
        """Get names of solved challenges."""
        self._solved_cache = await self._client.fetch_solved_names()
        return sorted(self._solved_cache)

    async def get_score(self) -> int:
        """Get current score from CTFd."""
        try:
            me = await self._client._get("/users/me")
            return me.get("data", {}).get("score", 0)
        except Exception:
            return 0

    async def get_time_remaining(self) -> float | None:
        """CTFd doesn't provide a standard API for competition end time."""
        return None

    async def download_file(self, url: str, output_path: str) -> str:
        """Download a file from CTFd."""
        client = await self._client._ensure_client()
        headers = {}
        if self.config.token:
            headers["Authorization"] = f"Token {self.config.token}"

        try:
            resp = await client.get(
                url, headers=headers,
                follow_redirects=True, timeout=60.0,
            )
            resp.raise_for_status()
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(resp.content)
            return output_path
        except Exception as e:
            logger.error(f"Download failed: {url} — {e}")
            raise

    async def get_hint(self, challenge_id: str, hint_id: int) -> str | None:
        """Try to fetch a hint from CTFd."""
        # Not implemented in current CTFdClient — return None
        return None

    async def close(self) -> None:
        await self._client.close()

    def _ctfd_to_info(self, ch: dict) -> ChallengeInfo:
        """Convert a CTFd challenge dict to ChallengeInfo."""
        name = ch.get("name", "Unknown")
        tags = [t["value"] if isinstance(t, dict) else str(t) for t in (ch.get("tags") or [])]
        hints_raw = ch.get("hints") or []
        hints = [{"id": h.get("id"), "cost": h.get("cost", 0), "content": h.get("content")} for h in hints_raw]
        return ChallengeInfo(
            id=name,  # Use name as ID (matches existing convention)
            name=name,
            category=ch.get("category", ""),
            value=ch.get("value", 0),
            description=ch.get("description", ""),
            connection_info=ch.get("connection_info", ""),
            tags=tags,
            hints=hints,
            solves=ch.get("solves", 0),
            files=ch.get("files", []),
            metadata=ch,
        )
