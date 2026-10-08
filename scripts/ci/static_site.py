from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


def validate(root: Path) -> None:
    root = root.resolve()
    pending: list[Path] = []
    inspected: set[Path] = set()

    def asset(reference: str, parent: Path) -> None:
        url = urlsplit(reference.strip())
        if url.scheme or url.netloc:
            raise ValueError(f"Rendering asset must be local: {reference}")
        if not url.path:
            return
        path = (
            root / unquote(url.path).lstrip("/")
            if url.path.startswith("/")
            else parent / unquote(url.path)
        )
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Rendering asset escapes site: {reference}")
        relative = path.relative_to(root)
        if any(
            (root / Path(*relative.parts[:i])).is_symlink()
            for i in range(1, len(relative.parts) + 1)
        ):
            raise ValueError(f"Rendering asset is a symlink: {reference}")
        if not path.is_file():
            raise ValueError(f"Missing rendering asset: {reference}")
        if path.suffix.lower() in {".css", ".html"}:
            pending.append(path)

    def css(text: str, parent: Path) -> None:
        if "\\" in text:
            raise ValueError("CSS escapes require browser-level validation")
        for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", text, re.I | re.S):
            asset(match[2], parent)
        for match in re.finditer(r"@import\s+(['\"])(.*?)\1", text, re.I):
            asset(match[2], parent)

    class Page(HTMLParser):
        def __init__(self, parent: Path) -> None:
            super().__init__(convert_charrefs=True)
            self.parent = parent
            self.in_style = False

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            values = dict(attrs)
            if tag == "base":
                raise ValueError("HTML base URLs are not supported")
            if values.get("srcset"):
                raise ValueError(
                    "Responsive asset sets require browser-level validation"
                )
            for key in ("src", "poster"):
                if values.get(key):
                    asset(str(values[key]), self.parent)
            if tag == "link" and values.get("href"):
                asset(str(values["href"]), self.parent)
            if tag == "object" and values.get("data"):
                asset(str(values["data"]), self.parent)
            if values.get("style"):
                css(str(values["style"]), self.parent)
            self.in_style = tag == "style" or self.in_style

        def handle_endtag(self, tag: str) -> None:
            if tag == "style":
                self.in_style = False

        def handle_data(self, data: str) -> None:
            if self.in_style:
                css(data, self.parent)

    for name in ("index.html", "style.css"):
        asset(name, root)
    while pending:
        path = pending.pop()
        if path in inspected:
            continue
        inspected.add(path)
        if len(inspected) > 256 or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Site validation input exceeds its limit")
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".css":
            css(text, path.parent)
        else:
            page = Page(path.parent)
            page.feed(text)
            page.close()


if __name__ == "__main__":
    validate(Path.cwd())
    print("Static site files and declared rendering assets validated")
