from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass
class ParsedFeed:
    url: str
    name: str


def parse_opml(text: str) -> list[ParsedFeed]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Malformed XML: {exc}") from exc

    if root.tag != "opml":
        raise ValueError(f"Expected root element <opml>, got <{root.tag}>")

    seen: set[str] = set()
    results: list[ParsedFeed] = []

    def walk(outlines) -> None:
        for outline in outlines:
            url = outline.get("xmlUrl", "").strip()
            if url:
                if url not in seen:
                    seen.add(url)
                    name = (
                        outline.get("title", "").strip()
                        or outline.get("text", "").strip()
                        or url
                    )
                    results.append(ParsedFeed(url=url, name=name))
            # Always recurse so folder outlines are descended
            walk(list(outline))

    body = root.find("body")
    top_level = list(body) if body is not None else list(root)
    walk(top_level)

    return results
