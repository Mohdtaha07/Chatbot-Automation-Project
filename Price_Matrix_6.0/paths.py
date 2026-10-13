"""
paths.py — single source of truth for every file the pipeline reads or writes.

Everything is anchored to the folder that contains THIS file, so the scripts can
be launched from any working directory (double-click, IDE, `python app.py`,
`python scrapper.py`, or an import from a test) and they still all agree on
where `amazon_<product>_data/`, `*_data_cleaned.csv` and
`compared_<product>_results.csv` live.

All the existing call sites already do a bare `from paths import PROJECT_ROOT`
(or use these helpers), so if you ever want the outputs somewhere else — a
`data/` subfolder, another drive — this is the only file you have to touch.
"""

from __future__ import annotations

import os

# The directory this file lives in == the project root that holds all modules.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def root_path(*parts: str) -> str:
    """Build an absolute path inside the project root: root_path('foo.csv')."""
    return os.path.join(PROJECT_ROOT, *parts)


def output_path(filename: str) -> str:
    """
    Build the absolute path for an output file.

    Absolute inputs are returned untouched, so callers that already know where
    they want a file (tests, exports to a temp dir) are unaffected.
    """
    if os.path.isabs(filename):
        return filename
    return os.path.join(PROJECT_ROOT, filename)


def data_folder(site_name: str, product_input: str) -> str:
    """Absolute path of the raw-HTML folder, matching parser.py / scrapper.py."""
    return root_path(f"{site_name.strip().lower()}_{product_input.strip().lower()}_data")


__all__ = ["PROJECT_ROOT", "root_path", "output_path", "data_folder"]
