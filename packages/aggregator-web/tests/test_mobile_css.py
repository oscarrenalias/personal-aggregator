"""Automated assertions for the mobile CSS overrides in styles.css.

Verifies that the @media (max-width: 639px) block contains font-size
declarations in rem for the three in-scope reading-text selectors, and
that no root/html standalone selector has a font-size rule inside the block.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS_PATH = (
    Path(__file__).parent.parent
    / "src" / "aggregator_web" / "static" / "styles.css"
)


def _extract_mobile_block(css: str) -> str:
    """Return the concatenated contents of all @media (max-width: 639px) blocks.

    The stylesheet may contain more than one ``@media (max-width: 639px)`` block
    (e.g. layout overrides near the top and reading-text overrides appended at the
    end so they win the cascade). Concatenate them all so the assertions find the
    rules regardless of which block they live in.
    """
    marker = "@media (max-width: 639px)"
    blocks: list[str] = []
    search_from = 0
    while True:
        start = css.find(marker, search_from)
        if start == -1:
            break
        depth = 0
        i = css.index("{", start)
        block_start = i
        while i < len(css):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(css[block_start + 1 : i])
                    search_from = i + 1
                    break
            i += 1
        else:
            raise AssertionError("Unclosed @media block in styles.css")
    assert blocks, "@media (max-width: 639px) block not found in styles.css"
    return "\n".join(blocks)



def test_mobile_block_exists() -> None:
    css = _CSS_PATH.read_text()
    block = _extract_mobile_block(css)
    assert block, "Mobile breakpoint block is empty"


def test_summary_block_font_size_in_rem() -> None:
    css = _CSS_PATH.read_text()
    block = _extract_mobile_block(css)
    # The rule is grouped: .summary-block,\n  .summary-block p { ... }
    # Assert each token appears in the block and a rem font-size follows.
    assert "summary-block" in block, ".summary-block not found in mobile block"
    assert re.search(
        r"\.summary-block[\s,][^{]*\{[^}]*font-size\s*:\s*[\d.]+rem", block, re.DOTALL
    ), ".summary-block rule does not have font-size in rem"


def test_detail_body_font_size_in_rem() -> None:
    css = _CSS_PATH.read_text()
    block = _extract_mobile_block(css)
    assert "detail-body" in block, ".detail-body not found in mobile block"
    assert re.search(
        r"\.detail-body[\s,][^{]*\{[^}]*font-size\s*:\s*[\d.]+rem", block, re.DOTALL
    ), ".detail-body rule does not have font-size in rem"


def test_card_excerpt_font_size_in_rem() -> None:
    css = _CSS_PATH.read_text()
    block = _extract_mobile_block(css)
    assert "card-excerpt" in block, ".card-excerpt not found in mobile block"
    assert re.search(
        r"\.card-excerpt\s*\{[^}]*font-size\s*:\s*[\d.]+rem", block, re.DOTALL
    ), ".card-excerpt rule does not have font-size in rem"


def test_no_root_html_font_size_in_mobile_block() -> None:
    css = _CSS_PATH.read_text()
    block = _extract_mobile_block(css)
    # Neither :root nor an html { ... } block should set font-size here.
    assert not re.search(
        r":root\s*\{[^}]*font-size", block, re.DOTALL
    ), ":root font-size found inside the mobile breakpoint block"
    assert not re.search(
        r"\bhtml\b[^{]*\{[^}]*font-size", block, re.DOTALL
    ), "html selector font-size found inside the mobile breakpoint block"
