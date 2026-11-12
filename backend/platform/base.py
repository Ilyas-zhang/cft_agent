"""Abstract base class for CTF competition platform adapters.

All platform adapters must implement this interface. The competition agent
interacts ONLY with this interface, never with platform-specific APIs directly.

When the official competition platform API is released, create a new subclass
and register it in the factory function.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChallengeInfo:
    """A challenge from the platform."""
    id: str  # Platform-specific unique identifier
    name: str
    category: str = ""
    value: int = 0  # Points
    description: str = ""
    connection_info: str = ""  # How to connect (URL, nc command, etc.)
    tags: list[str] = field(default_factory=list)
    hints: list[dict[str, Any]] = field(default_factory=list)
    solves: int = 0  # Number of teams that solved this
    files: list[str] = field(default_factory=list)  # Download URLs for attachments
    metadata: dict[str, Any] = field(default_factory=dict)  # Platform-specific extra data


@dataclass
class SubmitResult:
    """Result of submitting a flag."""
    status: str  # "correct" | "incorrect" | "already_solved" | "unknown"
    message: str = ""
    display: str = ""


@dataclass
class PlatformConfig:
    """Configuration for a platform adapter."""
    base_url: str = ""
    token: str = ""
    username: str = ""
    password: str = ""
    team_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # Platform-specific config


class PlatformAdapter(ABC):
    """Abstract interface for CTF competition platforms.

    All methods are async. Implementations must handle authentication,
    rate limiting, and error recovery internally.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Initialize the platform connection (authenticate, fetch initial state)."""
        ...

    @abstractmethod
    async def get_challenges(self) -> list[ChallengeInfo]:
        """Fetch all available challenges."""
        ...

    @abstractmethod
    async def get_challenge(self, challenge_id: str) -> ChallengeInfo:
        """Fetch a single challenge by ID."""
        ...

    @abstractmethod
    async def get_unsolved_challenges(self) -> list[ChallengeInfo]:
        """Fetch challenges that haven't been solved yet."""
        ...

    @abstractmethod
    async def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        """Submit a flag for a challenge.

        Returns the submission result — correct, incorrect, or already solved.
        """
        ...

    @abstractmethod
    async def get_solved_challenges(self) -> list[str]:
        """Get IDs of all solved challenges."""
        ...

    @abstractmethod
    async def get_score(self) -> int:
        """Get the current team score."""
        ...

    @abstractmethod
    async def get_time_remaining(self) -> float | None:
        """Get seconds remaining in the competition, or None if unknown."""
        ...

    @abstractmethod
    async def download_file(self, url: str, output_path: str) -> str:
        """Download a challenge attachment file.

        Returns the local file path.
        """
        ...

    # ── Optional methods for platforms that support them ────────────────

    async def get_hint(self, challenge_id: str, hint_id: int) -> str | None:
        """Unlock and retrieve a hint. Returns None if not supported."""
        return None

    async def update_challenge_state(self, challenge_id: str, state: dict[str, Any]) -> None:
        """Update local state for a challenge (e.g. notes, progress). Not sent to platform."""
        return None

    async def get_feedback(self, challenge_id: str) -> str | None:
        """Get judge feedback for a challenge submission. Returns None if not supported."""
        return None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def close(self) -> None:
        """Clean up resources (HTTP clients, connections, etc.)."""
        return None


# ── Challenge creation interface (reserved for future use) ──────────────────
# The competition requires online interactive challenge creation.
# This interface is reserved until the official format is specified.


class ChallengeCreator(ABC):
    """Abstract interface for creating CTF challenges (online interactive type).

    This is reserved for the competition's auto-challenge-creation requirement.
    Implement when the official format and delivery API are specified.
    """

    @abstractmethod
    async def create_challenge(self, spec: dict[str, Any]) -> ChallengeInfo:
        """Create a new challenge based on a specification.

        Args:
            spec: Challenge specification (format TBD based on official rules).

        Returns:
            The created challenge info.
        """
        ...

    @abstractmethod
    async def deploy_service(self, challenge_id: str, config: dict[str, Any]) -> str:
        """Deploy the interactive service for a challenge.

        Returns:
            Connection info (URL, port, etc.)
        """
        ...

    @abstractmethod
    async def verify_challenge(self, challenge_id: str) -> bool:
        """Verify that a challenge is solvable with the intended solution."""
        ...

    @abstractmethod
    async def validate_interaction(self, challenge_id: str, interaction_log: str) -> bool:
        """Validate that an interaction with the challenge service is correct."""
        ...
