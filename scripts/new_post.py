"""Create a dated Markdown draft without overwriting existing content."""
import argparse
from datetime import date
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title")
    parser.add_argument("--slug", help="Optional URL-safe filename (ASCII letters, digits, hyphens)")
    args = parser.parse_args()
    slug = args.slug or re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        parser.error("Provide a slug with lowercase ASCII letters, digits, and hyphens.")
    today = date.today().isoformat()
    target = ROOT / "content" / "posts" / f"{today}-{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = f'+++\ntitle = {json.dumps(args.title, ensure_ascii=False)}\ndate = "{today}"\ndescription = ""\ntags = ["Research notes"]\ndraft = true\n+++\n\nWrite your note here.\n'
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    print(f"Draft created: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
