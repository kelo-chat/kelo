"""What the guided setup (wizard.py, setup_web.py) needs from the way Kelo runs on this computer.

Docker runs Kelo's parts everywhere (Linux; Windows and macOS inside Kelo's own Linux VM). The setup asks `CopyHost` for exactly
four things and never calls Docker itself:

  check()      is the runtime ready, and how much CPU and RAM does it hand out
  manager()    the copy list (instances.Instances) bound to this runtime: files are shared, start/stop/running/capacity are the runtime's own
  bring_up()   start the copy with the settings now in its .env and wait for the live output (the setup's step 3)
  commands     the words a person can type to start it and to read its log (shown in messages)

`DockerCopyHost` (wizard.py) is the one implementation; tests pass their own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class HostCheck:
    """The runtime's answer to "can Kelo run here". `failure` is (headline, lines) as English templates when it cannot."""

    ok: bool
    facts: list[str] = field(default_factory=list)      # one tick each, already readable (versions)
    cpus: int | None = None
    ram: int | None = None
    failure: tuple[str, list[str]] | None = None


class CopyHost(Protocol):
    """The runtime-specific half of the guided setup. One implementation per way of running Kelo."""

    start_command: str       # what a person types to start Kelo by hand (shown when the setup does not start it)
    logs_command: str        # what a person types to read the server's log
    health_seconds: float | None   # how long Kelo may take to answer after a start; None = the wizard's own default
    health_request_seconds: float  # how long one "are you there" request may take

    def check(self) -> HostCheck: ...

    def manager(self, out: Callable[[str], None]) -> Any:
        """The copy list (instances.Instances or a subclass) whose start/stop/running/host_capacity are this runtime's."""
        ...

    def bring_up(self, ui: Any, mgr: Any, inst: Any, *, rerun: bool, release: bool) -> bool:
        """Starts the copy with the settings in its .env; shows progress through `ui.live`; tells the person through
        `ui.fail` when it cannot. True when the parts were started (the wizard then waits for the server to answer)."""
        ...
