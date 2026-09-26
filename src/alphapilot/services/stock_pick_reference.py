"""Reference scores on the part of each list the owner can trade: no Beijing exchange.

The forward-test verdict scores each list as registered, Beijing-exchange names included.
The owner's account does not buy them (decided 2026-09-26), so every official score gets a
reference twin: the same frozen list with those names removed (order kept, ranks
renumbered), scored by the same function with the same thresholds. References never enter
the verdict; they are written create-only under ``<root>/reference/no-beijing/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alphapilot.services.stock_pick_jev import j_tally
from alphapilot.services.stock_pick_paper import is_beijing
from alphapilot.services.stock_picks import CANDIDATES, forward_test_tally

REFERENCE = "no-beijing"


def without_beijing(doc: dict[str, Any]) -> dict[str, Any]:
    """The list without Beijing-exchange names; its top decile is the registered one minus them."""

    kept = [m for m in doc["members"] if not is_beijing(m["symbol"])]
    top_n = sum(1 for m in kept if m["rank"] <= doc["top_decile_n"])
    members = [{**m, "rank": i} for i, m in enumerate(kept, start=1)]
    return {
        **doc,
        "members": members,
        "top_decile_n": top_n,
        "top_decile_symbols": [m["symbol"] for m in members[:top_n]],
        "reference": REFERENCE,
    }


def reference_path(root: Path, candidate: str, stem: str, horizon: int) -> Path:
    return root / "reference" / REFERENCE / candidate / f"{stem}-h{horizon}.json"


def _h5(folder: Path, candidate: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_bytes()) for p in sorted(folder.glob(f"{candidate}-*-h5.json"))]


def tallies(root: Path) -> dict[str, dict[str, Any]]:
    """Official and reference tallies for every candidate that has a matured 5-session score."""

    out: dict[str, dict[str, Any]] = {}
    for name in (*CANDIDATES, "J"):
        official = _h5(root / "scores" / name, name)
        if not official:
            continue
        reference = _h5(root / "reference" / REFERENCE / name, name)
        tally = j_tally if name == "J" else forward_test_tally
        out[name] = {
            "official": tally(official),
            "reference": tally(reference) if reference else None,
        }
    return out
