"""Reject local editor tools and private drafts before a public commit."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATHS = (
    ".local", "editor", "Edit Website.cmd", "scripts/edit-site.ps1",
    "scripts/editor_server.py", "scripts/new-post.ps1", "scripts/new_post.py",
    "tests/test_editor_server.py",
)


def local_only(path):
    value = path.casefold()
    return any(value == prefix.casefold() or value.startswith(prefix.casefold() + "/")
               for prefix in LOCAL_PATHS)


def validate_post(raw, path):
    """Require an explicit publishing decision; never echo private body text."""
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        if not text.startswith("+++\n") or "\n+++\n" not in text[4:]:
            raise ValueError("missing frontmatter")
        metadata = tomllib.loads(text[4:].split("\n+++\n", 1)[0])
    except (UnicodeError, ValueError):
        raise ValueError(f"{path}: invalid post metadata; publishing stopped.") from None
    if metadata.get("draft") is not False:
        raise ValueError(f"{path}: private draft in public content. Keep it in .local/drafts/; "
                         "only explicitly ready posts (draft = false) belong in content/posts/.")


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=True)
    return result.stdout


def check_worktree(root):
    folder = root / "content" / "posts"
    for parent in (root / "content", folder):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("Public content folders must not be symlinks or junctions.")
    for path in folder.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"{relative}: links are not allowed in public posts.")
        if path.is_file() and path.suffix.lower() == ".md":
            validate_post(path.read_bytes(), relative)


def check_staged(root):
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        details, encoded_path = entry.split(b"\t", 1)
        mode, object_id, stage = details.split()
        path = encoded_path.decode("utf-8")
        if stage != b"0":
            raise ValueError("Resolve Git conflicts before publishing.")
        if local_only(path):
            raise ValueError(f"{path}: local-only file is still tracked. Remove it from the Git "
                             "index while keeping the local file before publishing.")
        if path.casefold().startswith("content/posts/"):
            if mode not in {b"100644", b"100755"}:
                raise ValueError(f"{path}: only regular files are allowed in public posts.")
            if PurePosixPath(path).suffix.lower() == ".md":
                validate_post(git(root, "cat-file", "blob", object_id.decode("ascii")), path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--worktree", action="store_true", help="Check posts before staging.")
    mode.add_argument("--staged", action="store_true", help="Check exact Git index contents.")
    args = parser.parse_args()
    try:
        (check_worktree if args.worktree else check_staged)(ROOT)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Public source check failed: {error}\n")
    print("Public source check passed.")


if __name__ == "__main__":
    main()
