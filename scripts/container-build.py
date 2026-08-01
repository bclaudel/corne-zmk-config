#!/usr/bin/env python3
"""Build entries from build.yaml inside the official ZMK build container."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any, NoReturn

import yaml


WORKSPACE = Path("/work")
CONFIG = WORKSPACE / "config"
MATRIX_FILE = WORKSPACE / "build.yaml"
OUTPUT_DIR = Path("/firmware")
BUILD_ROOT = WORKSPACE / "build"

TARGETS = {
    "left": ("corne_left", "corne_left.uf2"),
    "right": ("corne_right", "corne_right.uf2"),
    "reset": ("settings_reset", "settings_reset.uf2"),
}


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str]) -> None:
    print("+", shlex.join(command), flush=True)
    subprocess.run(command, check=True)


def as_values(value: Any, field: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    fail(f"'build.yaml' field '{field}' must be a string or a list of strings")


def load_matrix() -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(MATRIX_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        fail(f"cannot read build.yaml: {error}")

    if not isinstance(document, dict):
        fail("build.yaml must contain a mapping")

    entries: list[dict[str, Any]] = []
    boards = as_values(document["board"], "board") if "board" in document else []
    shields = as_values(document["shield"], "shield") if "shield" in document else []
    if boards or shields:
        if not boards or not shields:
            fail("top-level 'board' and 'shield' must be specified together")
        entries.extend(
            {"board": board, "shield": shield}
            for board, shield in itertools.product(boards, shields)
        )

    included = document.get("include", [])
    if not isinstance(included, list) or not all(
        isinstance(entry, dict) for entry in included
    ):
        fail("'include' in build.yaml must be a list of mappings")
    entries.extend(included)

    if not entries:
        fail("build.yaml does not define any build entries")
    return entries


def logical_target(entry: dict[str, Any]) -> str | None:
    shield = entry.get("shield")
    if not isinstance(shield, str):
        fail("every build.yaml entry must have a string 'shield'")
    shield_names = shield.split()
    matches = [
        name for name, (required_shield, _) in TARGETS.items()
        if required_shield in shield_names
    ]
    if len(matches) > 1:
        fail(f"ambiguous shield list in build.yaml: {shield!r}")
    return matches[0] if matches else None


def selected_entries(requested: str) -> list[tuple[str, dict[str, Any]]]:
    by_target: dict[str, dict[str, Any]] = {}
    for entry in load_matrix():
        name = logical_target(entry)
        if name is None:
            continue
        if name in by_target:
            fail(f"build.yaml defines more than one '{name}' firmware entry")
        by_target[name] = entry

    wanted = list(TARGETS) if requested == "all" else [requested]
    missing = [name for name in wanted if name not in by_target]
    if missing:
        fail(
            "build.yaml has no entry for: "
            + ", ".join(missing)
            + ". Expected shields: "
            + ", ".join(TARGETS[name][0] for name in missing)
        )
    return [(name, by_target[name]) for name in wanted]


def argument_list(entry: dict[str, Any], *keys: str) -> list[str]:
    value = next((entry[key] for key in keys if key in entry), None)
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    fail(f"'{keys[0]}' must be a string or a list of strings")


def build(name: str, entry: dict[str, Any]) -> None:
    board = entry.get("board")
    shield = entry.get("shield")
    if not isinstance(board, str) or not isinstance(shield, str):
        fail(f"the '{name}' entry needs string 'board' and 'shield' fields")

    snippets = argument_list(entry, "snippet", "snippets")
    cmake_args = argument_list(entry, "cmake-args", "cmake_args")
    configuration = {
        "board": board,
        "shield": shield,
        "snippets": snippets,
        "cmake_args": cmake_args,
    }
    signature = hashlib.sha256(
        json.dumps(configuration, sort_keys=True).encode("utf-8")
    ).hexdigest()

    build_dir = BUILD_ROOT / name
    signature_file = build_dir / ".local-build-signature"
    previous_signature = (
        signature_file.read_text(encoding="ascii").strip()
        if signature_file.is_file()
        else None
    )
    pristine = "always" if build_dir.exists() and previous_signature != signature else "auto"

    command = [
        "west",
        "build",
        "-s",
        "zmk/app",
        "-d",
        os.fspath(build_dir),
        "-b",
        board,
        "-p",
        pristine,
    ]
    for snippet in snippets:
        command.extend(["-S", snippet])
    command.extend(["--", f"-DSHIELD={shield}", f"-DZMK_CONFIG={CONFIG}"])
    command.extend(cmake_args)

    print(f"\nBuilding {name} ({board}, {shield})", flush=True)
    run(command)

    source = build_dir / "zephyr" / "zmk.uf2"
    if not source.is_file() or source.stat().st_size == 0:
        fail(f"build succeeded but did not produce a nonempty {source}")
    destination = OUTPUT_DIR / TARGETS[name][1]
    shutil.copyfile(source, destination)
    signature_file.write_text(signature + "\n", encoding="ascii")
    print(f"Wrote {destination}", flush=True)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"all", *TARGETS}:
        fail("expected one target: all, left, right, or reset")

    entries = selected_entries(sys.argv[1])
    if not (WORKSPACE / ".west").is_dir():
        run(["west", "init", "-l", os.fspath(CONFIG)])
    run(["west", "update"])
    run(["west", "zephyr-export"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    for name, entry in entries:
        build(name, entry)


if __name__ == "__main__":
    main()
