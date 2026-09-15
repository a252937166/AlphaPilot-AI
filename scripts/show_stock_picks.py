from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    os.chdir(PROJECT_DIR)
    parser = argparse.ArgumentParser(
        description="Show the weekly stock-pick lists, their moves since entry and matured tallies."
    )
    parser.add_argument("--json", action="store_true", help="Print the status document as JSON.")
    parser.add_argument(
        "--no-live", action="store_true", help="Skip the Futu snapshot and use daily bars only."
    )
    args = parser.parse_args()
    from alphapilot.core.config import get_settings
    from alphapilot.db.engine import get_session, init_db
    from alphapilot.services.stock_pick_status import build_status, futu_quotes, render

    init_db()
    root = Path(get_settings().stock_pick_forward_test_dir)
    with get_session() as session:
        status = build_status(session, root, quote_fn=None if args.no_live else futu_quotes)
    print(json.dumps(status, ensure_ascii=False, indent=1) if args.json else render(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
