"""Seed NoteSeek with a handful of useful sample notes."""

from __future__ import annotations

import notesdb


def main() -> None:
    notesdb.init()
    created = notesdb.seed_defaults()
    print(f"seeded {created} notes; total={notesdb.count_notes()}")
    notesdb.shutdown()


if __name__ == "__main__":
    main()
