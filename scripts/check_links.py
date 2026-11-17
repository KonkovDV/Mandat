"""Проверяет относительные ссылки и якоря во всех .md репозитория.

Внешние ссылки (http, https, mailto) не проверяются. Якорь сверяется со
слагом заголовка по правилам GitHub: нижний регистр, без пунктуации, пробел -> дефис.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def slug(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    result = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else HEADING.match(line)
        if m:
            base = slug(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            result.add(base if n == 0 else f"{base}-{n}")
    return result


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    errors = []
    for rel in files.stdout.split():
        path = ROOT / rel
        in_fence = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in LINK.finditer(line):
                target = m.group(1) or m.group(2)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = (path.parent / unquote(file_part)).resolve() if file_part else path
                if not dest.exists():
                    errors.append(f"{rel}:{number}: нет файла {target}")
                    continue
                if anchor and dest.suffix == ".md" and unquote(anchor) not in anchors(dest):
                    errors.append(f"{rel}:{number}: нет якоря {target}")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    print(f"ссылки и якоря в {len(files.stdout.split())} файлах .md в порядке")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
