"""Replace Google goto links in OpenSEO SERP results before they are saved.

OpenSEO's live SERP reads can return result URLs as
``https://google.com/goto?url=...`` instead of the page's address. The shared
goto-resolver in seo-service sends them to DataForSEO's free converter; see
``~/projects/seo-service/tools/goto-resolver/README.md``.

Resolving is best effort: if the resolver is missing or fails, the content is
returned unchanged and the outcome says why, so validation never fails on it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_RESOLVER = (
    Path.home() / "projects/seo-service/tools/goto-resolver/goto_resolver.py"
)
RESOLVER_TIMEOUT_S = 120
_GOTO_LINK = re.compile(r"https?://(?:www\.)?google\.[a-z.]+/goto\?")


def count_goto_links(content: Any) -> int:
    return len(_GOTO_LINK.findall(json.dumps(content, ensure_ascii=False)))


def resolve_goto_links(
    content: Any,
    *,
    resolver: str | Path | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Any, dict[str, Any]]:
    """Return the content with goto links replaced, and what happened."""

    found = count_goto_links(content)
    if found == 0:
        return content, {"status": "none"}

    path = Path(resolver or os.getenv("GOTO_RESOLVER_PATH") or DEFAULT_RESOLVER)
    if not path.is_file():
        return content, {"status": "unavailable", "unresolved": found}

    try:
        completed = run(
            [sys.executable, "-I", str(path), "--json"],
            input=json.dumps(content, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=RESOLVER_TIMEOUT_S,
            check=True,
        )
        resolved = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return content, {"status": "failed", "unresolved": found}

    remaining = count_goto_links(resolved)
    return resolved, {
        "status": "resolved" if remaining == 0 else "partial",
        "resolved": found - remaining,
        "unresolved": remaining,
    }


__all__ = ["count_goto_links", "resolve_goto_links"]
