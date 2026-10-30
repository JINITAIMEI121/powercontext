"""Offline acceptance checks for NoteSeek's hybrid retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import notesdb


def main() -> int:
    notesdb.init()
    notesdb.seed_defaults()
    cases = json.loads((Path(__file__).parent / "samples" / "queries.json").read_text(encoding="utf-8"))
    failures = []
    for case in cases["queries"]:
        results = notesdb.search_notes(case["query"], limit=case.get("limit", 5))
        titles = [item["title"] for item in results]
        ok = all(expected in titles for expected in case["expect_titles"])
        print(("PASS" if ok else "FAIL"), case["query"], "->", titles[:5])
        if not ok:
            failures.append(case["query"])
    notesdb.shutdown()
    if failures:
        print("failed:", failures, file=sys.stderr)
        return 1
    print(f"all {len(cases['queries'])} queries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
