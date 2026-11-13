#!/usr/bin/env python3
"""Copy a YAML Jigx solution to a test solution for timing, leaving the original untouched.

Standard library only. Copies the solution folder (the one with index.jigx) to --out, sets
the copy's top-level `name:` and `title:` in index.jigx to the test solution, and copies the
nearest .vscode/settings.json so Jigx Builder publishes to the same organisation. Refuses a
test name equal to the original's: publishing the copy would then overwrite the real
solution. Then open the copy in VS Code and publish it with Jigx Builder. Read
references/run-and-track.md.

Example:
  python3 copy-to-test.py AcumaticaFieldServices --name field-services-test-me --out /tmp/fs-test
"""
import argparse
import os
import re
import shutil
import sys

SKIP = {".git", "node_modules", "build", "dist", "perf-review", "perf-runs"}


def top_level(text, key):
    m = re.search(rf"^{key}:[ \t]*(.*)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else None


def set_top_level(text, key, value):
    line = f"{key}: {value}"
    if re.search(rf"^{key}:", text, re.M):
        return re.sub(rf"^{key}:[^\n]*", lambda _: line, text, count=1, flags=re.M)
    return f"{line}\n{text}"


def find_settings(folder, _main=True):
    """The nearest .vscode/settings.json in the folder or above it, up to the repository root.
    In a git worktree (.git is a file) the settings are usually not committed, so the same
    place in the main checkout is searched too."""
    d = os.path.abspath(folder)
    while True:
        path = os.path.join(d, ".vscode", "settings.json")
        if os.path.exists(path):
            return path
        git = os.path.join(d, ".git")
        if os.path.isfile(git) and _main:
            main = main_checkout(git)
            if main:
                return find_settings(os.path.join(main, os.path.relpath(os.path.abspath(folder), d)), _main=False)
            return None
        if os.path.exists(git) or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def main_checkout(git_file):
    """The main checkout of a worktree: its .git file says gitdir: <main>/.git/worktrees/<name>."""
    try:
        with open(git_file, encoding="utf-8") as f:
            m = re.match(r"gitdir:\s*(.+)", f.read().strip())
    except OSError:
        return None
    if not m:
        return None
    gitdir = os.path.normpath(os.path.join(os.path.dirname(git_file), m.group(1)))
    parts = gitdir.split(os.sep)
    if "worktrees" in parts:
        common = os.sep.join(parts[:len(parts) - 1 - parts[::-1].index("worktrees")])
        return os.path.dirname(common)
    return None


def copy_to_test(src, out, name, title=None):
    index = os.path.join(src, "index.jigx")
    if not os.path.exists(index):
        raise SystemExit(f"{src} has no index.jigx; give a YAML solution folder")
    with open(index, encoding="utf-8") as f:
        text = f.read()
    original = top_level(text, "name")
    if original and original.lower() == name.lower():
        raise SystemExit(f"--name {name} is the original solution's name: publishing would overwrite it. Use a test name.")
    if os.path.exists(out) and os.listdir(out):
        raise SystemExit(f"{out} exists and is not empty; give a new folder")
    shutil.copytree(src, out, ignore=lambda d, names: [n for n in names if n in SKIP], dirs_exist_ok=True)
    text = set_top_level(set_top_level(text, "name", name), "title", title or name)
    with open(os.path.join(out, "index.jigx"), "w", encoding="utf-8") as f:
        f.write(text)
    settings = find_settings(src)
    if settings:
        os.makedirs(os.path.join(out, ".vscode"), exist_ok=True)
        shutil.copy(settings, os.path.join(out, ".vscode", "settings.json"))
    return {"original": original, "settings": settings}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("solution", help="YAML solution folder (with index.jigx)")
    p.add_argument("--name", required=True, help="the test solution's name")
    p.add_argument("--title", help="the test solution's title (default: the name)")
    p.add_argument("--out", required=True, help="new folder for the copy")
    args = p.parse_args(argv)
    r = copy_to_test(args.solution, args.out, args.name, args.title)
    print(f"Copied {r['original'] or args.solution} to {args.out} as {args.name}.")
    print("Organisation settings: " + (f"copied from {r['settings']}" if r["settings"]
                                       else "none found; set jigx.workspace.organizationId in the copy's .vscode/settings.json"))
    print(f"Next: open {args.out} in VS Code and publish it with Jigx Builder, then time the test solution {args.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
