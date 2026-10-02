#!/usr/bin/env python3
"""Build ``homelable.zip`` locally, byte-for-byte the way CI does.

Why this exists: the built panel bundle is gitignored and HACS installs the
release zip, so a fork's Chinese translation is *not* in any installable
artifact until a tag is pushed and `release.yml` runs on GitHub. That is fine
for releases and useless for testing a branch, and `dev-ha.sh` only helps if
you happen to have Docker.

This produces the same archive `release.yml` does, so what you install by hand
is the same thing HACS would have installed:

    cd frontend-src && npm run build:ha
    cd custom_components/homelable && zip -r ../../homelable.zip .

`zip` is not present everywhere (Windows has none, WSL often does not), so the
archive is written with `zipfile` from the standard library instead. The layout
matters: the zip holds the *contents* of `custom_components/homelable`, not the
directory itself, because that is what `hacs.json`'s `zip_release` expects.

Usage:
    python3 scripts/build-release-zip.py
    python3 scripts/build-release-zip.py --out-dir /path/to/ha/config
    python3 scripts/build-release-zip.py --skip-build     # reuse current bundle
    python3 scripts/build-release-zip.py --set-version 1.4.0-i18n.1
    python3 scripts/build-release-zip.py --force          # overwrite a stale zip

`--out-dir` expects the directory that *contains* `custom_components/`, i.e.
your HA `config/`. The integration is copied there, replacing any previous
copy. Stop Home Assistant first, or it will rewrite the folder on restart.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# A Chinese Windows console defaults to GBK, and printing a non-ASCII character
# then raises UnicodeEncodeError the moment stdout is redirected into a pipe or
# a CI log — the script dies on its own progress message. Files are always
# written as UTF-8 separately.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - Python < 3.7
        pass

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "homelable"
FRONTEND_SRC = ROOT / "frontend-src"
ZIP_NAME = "homelable.zip"

# Files that must never reach the archive. __pycache__ would shadow a rebuilt
# module inside the container and is pure noise in a zip anyway.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

# A build with no Chinese in it is the failure this script is most able to
# prevent: you rebuild, repackage, reinstall, and the panel is still English
# because a stale bundle got zipped. One probe string is enough to tell.
TRANSLATION_PROBE = "正在加载画布"  # "Loading canvas…"
PANEL_BUNDLE_GLOB = "homelable-panel-*.js"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def build_frontend() -> None:
    """Run the same build the release workflow runs."""
    log("building frontend (npm run build:ha) ...")
    result = subprocess.run(
        ["npm", "run", "build:ha"],
        cwd=FRONTEND_SRC,
        shell=False,
    )
    if result.returncode != 0:
        sys.exit(f"npm run build:ha failed with exit code {result.returncode}")


def check_bundle_contains_translation() -> None:
    """Warn if the freshly built bundle carries no Chinese at all.

    Not a hard failure: an English-only build is legitimate for upstream. But on
    a branch that is supposed to be translated it is almost always a stale
    bundle, and it is worth saying so before the user reinstalls and wonders
    why the panel is still English.

    The probe sweeps every JS file rather than the panel entry: Rollup hoists the
    dictionary into a shared chunk because both the card and the panel import
    it, so a correct build has an all-English `homelable-panel-*.js` and the
    Chinese lives in `homelable-chunk-*.js`. Probing the panel entry alone would
    warn on every single run and train the reader to ignore it.
    """
    frontend = INTEGRATION / "frontend"
    scripts = sorted(frontend.glob("*.js"))
    if not scripts:
        log(f"WARNING: no .js files in {frontend} — did the build run?")
        return

    for path in scripts:
        if TRANSLATION_PROBE in path.read_text(encoding="utf-8", errors="replace"):
            log(f"translation found in {path.name}")
            return
    log("WARNING: no Chinese in any built bundle — the zip would install an English panel.")
    log("         Expected on a translation branch. Re-run the build if this is stale.")


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path


def set_version(version: str) -> None:
    manifest_path = INTEGRATION / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old = manifest.get("version")
    manifest["version"] = version
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"manifest.json version {old} -> {version}")
    version_file = ROOT / "VERSION"
    if version_file.exists():
        version_file.write_text(version + "\n", encoding="utf-8")
        log(f"VERSION -> {version}")


def make_zip(dest: Path) -> Path:
    if not INTEGRATION.is_dir():
        sys.exit(f"missing {INTEGRATION} — run from a clone of the repository")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(INTEGRATION):
            # arcname is relative to the integration dir, so the archive root is
            # manifest.json / frontend/ / *.py — matching release.yml's `zip .`
            # and what HACS's zip_release expects.
            archive.write(path, arcname=str(path.relative_to(INTEGRATION)))
    return dest


def stage_into_ha(config_dir: Path) -> None:
    target = config_dir / "custom_components" / "homelable"
    if target.resolve() == INTEGRATION.resolve():
        log("staging skipped: --out-dir points at this clone")
        return
    if target.exists():
        log(f"replacing existing {target}")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        INTEGRATION,
        target,
        ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc"),
    )
    log(f"installed to {target}")
    log("remember to restart Home Assistant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="HA config directory (the one holding custom_components/) to install into",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse whatever is already in custom_components/homelable/frontend",
    )
    parser.add_argument(
        "--set-version",
        metavar="VERSION",
        help="rewrite manifest.json/VERSION before packaging (mirrors the CI step)",
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing zip without asking"
    )
    args = parser.parse_args()

    if not args.skip_build:
        build_frontend()

    check_bundle_contains_translation()

    if args.set_version:
        set_version(args.set_version)

    dest = ROOT / ZIP_NAME
    if dest.exists():
        if not args.force:
            # Never silently clobber: a zip left over from an earlier build is
            # exactly how you end up installing a stale bundle.
            log(f"{ZIP_NAME} already exists. Re-run with --force to replace it.")
            sys.exit(1)
        dest.unlink()

    make_zip(dest)
    size_kb = dest.stat().st_size / 1024
    log(f"wrote {dest.name} ({size_kb:.0f} KB)")

    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
    has_manifest = "manifest.json" in names
    has_panel = any(n.startswith("frontend/") and n.endswith(".js") for n in names)
    log(f"  entries: {len(names)}  manifest.json: {has_manifest}  frontend bundle: {has_panel}")
    if not (has_manifest and has_panel):
        sys.exit("archive is missing manifest.json or the frontend bundle")

    if args.out_dir:
        stage_into_ha(args.out_dir.resolve())


if __name__ == "__main__":
    main()
