"""Progress emitters — a tiny abstraction so the orchestrator can report
each step as it runs without knowing whether it's being driven from the CLI,
the MCP server, or a unit test.

Three implementations ship in the box:

  * NullProgress       — silent. Default. Used by the library API.
  * RichLiveProgress   — animated terminal spinner via rich.live.Live.
                         Used by the `forgent` CLI.
  * MCPContextProgress — emits MCP `notifications/progress` and structured
                         log messages back to the calling client (Claude
                         Code, Cursor, Claude Desktop, etc.).
                         Used by the `run_task` MCP tool.

The orchestrator calls these methods at fixed checkpoints:

    progress.start(task)             # session opened, recall about to run
    progress.recall(n_chars)         # context recalled from memory
    progress.route(decision)         # router picked the agents
    progress.dispatch(agent_name)    # adapter is about to run
    progress.dispatch_done(result)   # adapter finished
    progress.persist(session_id)     # outputs written back to memory
    progress.done(success)           # whole run is over
    progress.error(message)          # any step blew up

Every implementation must be safe to call concurrently with itself (the
orchestrator dispatches multiple agents in parallel mode). The `Null` and
`MCPContext` versions are inherently safe; `RichLive` serializes via the
Live render lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol — what every emitter must implement
# ---------------------------------------------------------------------------


@runtime_checkable
class Progress(Protocol):
    def start(self, task: str) -> None: ...
    def recall(self, n_chars: int) -> None: ...
    def route(self, primary: str, supporting: list[str], mode: str, confidence: float) -> None: ...
    def dispatch(self, agent: str, ecosystem: str) -> None: ...
    def dispatch_done(self, agent: str, success: bool, output_chars: int) -> None: ...
    def persist(self, session_id: str, n_outputs: int) -> None: ...
    def done(self, success: bool) -> None: ...
    def error(self, message: str) -> None: ...
    def to_markdown(self) -> str: ...


# ---------------------------------------------------------------------------
# 1. Null — does nothing. The library default.
# ---------------------------------------------------------------------------


class NullProgress:
    def start(self, task: str) -> None: pass
    def recall(self, n_chars: int) -> None: pass
    def route(self, primary: str, supporting: list[str], mode: str, confidence: float) -> None: pass
    def dispatch(self, agent: str, ecosystem: str) -> None: pass
    def dispatch_done(self, agent: str, success: bool, output_chars: int) -> None: pass
    def persist(self, session_id: str, n_outputs: int) -> None: pass
    def done(self, success: bool) -> None: pass
    def error(self, message: str) -> None: pass
    def to_markdown(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# 2. RichLive — animated terminal spinner for the CLI
# ---------------------------------------------------------------------------


class RichLiveProgress:
    """Animated CLI progress display using rich.live.

    Renders a multi-row panel:

        ▶ forgent
          [⠋] task: review my Python code for security issues
          ↳ recall ........ 1234 chars from memory
          ↳ route .......... typescript-pro (confidence 0.87, parallel)
          ↳ dispatch ....... typescript-pro (claude_code) ⠋ 1.4s
          ↳ persist ........ 3 outputs to session abc123
          ✓ done

    The spinner advances on a background thread while the API call runs.
    """

    def __init__(self, console: Any = None):
        from rich.live import Live
        from rich.console import Console
        from rich.spinner import Spinner

        self._console = console or Console()
        self._live: Live | None = None
        self._spinner = Spinner("dots", text="working")
        self._lines: list[tuple[str, str]] = []  # (icon, text)
        self._task = ""
        self._dispatching: dict[str, float] = {}
        self._started_at: float = 0.0

    # --- internal render -------------------------------------------------

    def _build_panel(self) -> Any:
        from rich.panel import Panel
        from rich.text import Text
        from rich.console import Group
        import time

        body_lines: list[Any] = [
            Text.from_markup(f"[bold #dfe0e2]task[/]  [#aaaaaa]{self._task}[/]")
        ]
        for icon, text in self._lines:
            body_lines.append(Text.from_markup(f"  {icon} {text}"))

        # Live spinner row for any in-flight dispatch
        for agent, started in self._dispatching.items():
            elapsed = time.time() - started
            spinner_text = (
                f"  [#eb5160]…[/] dispatch [bold #dfe0e2]{agent}[/] "
                f"[dim]({elapsed:.1f}s)[/dim]"
            )
            body_lines.append(Text.from_markup(spinner_text))

        return Panel(
            Group(*body_lines),
            title="[bold #eb5160]forgent[/bold #eb5160]",
            border_style="#b7999c",
            padding=(0, 1),
        )

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._build_panel())

    # --- context manager so the CLI can use `with progress:` -------------

    def __enter__(self) -> "RichLiveProgress":
        from rich.live import Live
        import time
        self._started_at = time.time()
        self._live = Live(self._build_panel(), console=self._console, refresh_per_second=10)
        self._live.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._live is not None:
            self._refresh()
            self._live.__exit__(*args)
            self._live = None

    # --- progress callbacks ----------------------------------------------

    def start(self, task: str) -> None:
        self._task = task[:80] + ("…" if len(task) > 80 else "")
        self._refresh()

    def recall(self, n_chars: int) -> None:
        if n_chars > 0:
            self._lines.append(("[#aaaaaa]↳[/]", f"[#aaaaaa]recall {n_chars} chars from memory[/]"))
        self._refresh()

    def route(self, primary: str, supporting: list[str], mode: str, confidence: float) -> None:
        sup = f" + {', '.join(supporting)}" if supporting else ""
        self._lines.append((
            "[#eb5160]↳[/]",
            f"route → [bold #eb5160]{primary}[/bold #eb5160]{sup}  [dim](mode={mode}, conf={confidence:.2f})[/dim]",
        ))
        self._refresh()

    def dispatch(self, agent: str, ecosystem: str) -> None:
        import time
        self._dispatching[agent] = time.time()
        self._refresh()

    def dispatch_done(self, agent: str, success: bool, output_chars: int) -> None:
        import time
        elapsed = time.time() - self._dispatching.pop(agent, time.time())
        status = "[#7ecf94]✓[/]" if success else "[#eb5160]✗[/]"
        self._lines.append((
            status,
            f"dispatch [bold]{agent}[/bold] [dim]→ {output_chars} chars in {elapsed:.1f}s[/dim]",
        ))
        self._refresh()

    def persist(self, session_id: str, n_outputs: int) -> None:
        self._lines.append((
            "[#aaaaaa]↳[/]",
            f"[#aaaaaa]persist {n_outputs} outputs → session {session_id[:8]}[/]",
        ))
        self._refresh()

    def done(self, success: bool) -> None:
        import time
        elapsed = time.time() - self._started_at
        icon = "[#7ecf94]✓[/]" if success else "[#eb5160]✗[/]"
        self._lines.append((icon, f"[dim]{'done' if success else 'failed'} in {elapsed:.1f}s[/dim]"))
        self._refresh()

    def error(self, message: str) -> None:
        self._lines.append(("[#eb5160]✗[/]", f"[#eb5160]{message}[/]"))
        self._refresh()

    def to_markdown(self) -> str:
        # Used to mirror the CLI panel into the final printed output if needed
        return ""


# ---------------------------------------------------------------------------
# 3. MCPContextProgress — sends MCP progress notifications + builds a
#                        structured markdown trace for the final response
# ---------------------------------------------------------------------------


class MCPContextProgress:
    """Reports progress to an MCP client (Claude Code, Cursor, etc.).

    Two channels:
      1. `ctx.report_progress(current, total)` — the standard MCP progress
         protocol. Clients that support it render a live progress bar.
      2. `ctx.info(message)` — log notifications. Clients that don't render
         progress bars usually still surface log messages inline.

    AND it accumulates a structured markdown trace via `to_markdown()` so
    the final response text shows the same step-by-step view even when the
    client renders nothing live.
    """

    # 7 fixed checkpoints — used as the denominator for the progress bar
    _TOTAL_STEPS = 7

    def __init__(self, ctx: Any | None = None):
        self._ctx = ctx
        self._step = 0
        self._lines: list[str] = []
        self._task = ""

    def _bump(self, label: str) -> None:
        """Advance the progress counter and send a notification."""
        self._step += 1
        if self._ctx is not None:
            try:
                # FastMCP Context.report_progress is async — fire-and-forget.
                # We're inside an async tool handler so this is safe to await.
                import asyncio
                coro = self._ctx.report_progress(self._step, self._TOTAL_STEPS)
                if asyncio.iscoroutine(coro):
                    asyncio.create_task(coro)
                log_coro = self._ctx.info(label)
                if asyncio.iscoroutine(log_coro):
                    asyncio.create_task(log_coro)
            except Exception:
                # Never let progress reporting break the actual task
                pass

    # ----- callbacks --------------------------------------------------

    def start(self, task: str) -> None:
        self._task = task
        self._lines.append(f"**task** — {task}")
        self._bump(f"forgent: starting task")

    def recall(self, n_chars: int) -> None:
        if n_chars > 0:
            self._lines.append(f"**recall** — {n_chars} chars of relevant context from memory")
            self._bump(f"forgent: recalled {n_chars} chars from memory")
        else:
            self._lines.append("**recall** — no prior context")
            self._bump("forgent: no prior context to recall")

    def route(self, primary: str, supporting: list[str], mode: str, confidence: float) -> None:
        sup = f" + supporting={', '.join(supporting)}" if supporting else ""
        self._lines.append(
            f"**route** — `{primary}`{sup} (mode={mode}, confidence={confidence:.2f})"
        )
        self._bump(f"forgent: routed to {primary}")

    def dispatch(self, agent: str, ecosystem: str) -> None:
        self._lines.append(f"**dispatch** — running `{agent}` via {ecosystem} adapter…")
        self._bump(f"forgent: dispatching to {agent}")

    def dispatch_done(self, agent: str, success: bool, output_chars: int) -> None:
        icon = "✓" if success else "✗"
        self._lines.append(
            f"**{icon} {agent}** — {'returned ' + str(output_chars) + ' chars' if success else 'failed'}"
        )
        self._bump(f"forgent: {agent} {'done' if success else 'failed'}")

    def persist(self, session_id: str, n_outputs: int) -> None:
        self._lines.append(f"**persist** — {n_outputs} outputs → session `{session_id[:8]}`")
        self._bump(f"forgent: persisted {n_outputs} outputs")

    def done(self, success: bool) -> None:
        icon = "✓ done" if success else "✗ failed"
        self._lines.append(f"**{icon}**")
        self._bump(f"forgent: {'done' if success else 'failed'}")

    def error(self, message: str) -> None:
        self._lines.append(f"**✗ error** — {message}")
        if self._ctx is not None:
            try:
                import asyncio
                coro = self._ctx.error(message)
                if asyncio.iscoroutine(coro):
                    asyncio.create_task(coro)
            except Exception:
                pass

    def to_markdown(self) -> str:
        if not self._lines:
            return ""
        body = "\n".join(f"- {line}" for line in self._lines)
        return f"## trace\n\n{body}"


# ---------------------------------------------------------------------------
# Convenience: composite emitter that fans out to multiple targets
# ---------------------------------------------------------------------------


class CompositeProgress:
    """Fan a single progress event out to multiple emitters.

    Useful when you want to send MCP notifications AND build the structured
    trace AND log to a file all at once.
    """

    def __init__(self, *children: Progress):
        self._children = children

    def __getattr__(self, name: str) -> Any:
        def _broadcast(*args: Any, **kwargs: Any) -> None:
            for child in self._children:
                method = getattr(child, name, None)
                if callable(method):
                    method(*args, **kwargs)
        return _broadcast

    def to_markdown(self) -> str:
        for child in self._children:
            md = child.to_markdown()
            if md:
                return md
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def cli_progress(console: Any = None) -> Iterator[RichLiveProgress]:
    """Context manager wrapping RichLiveProgress for CLI use:

        with cli_progress() as progress:
            orch.run("...", progress=progress)
    """
    p = RichLiveProgress(console=console)
    with p:
        yield p
