"""Test the Reuters module against the saved Article_MiddleEast.html fixture.

Run from the repo root::

    cd fetcher && uv run python test_reuters_module.py
"""

import sys
from pathlib import Path

from bs4 import BeautifulSoup  # type: ignore

# Allow running from the fetcher/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from modules.Reuters.module import ReutersModule  # noqa: E402
from modules.base import ArticleData  # noqa: E402

# ── paths ────────────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).resolve().parent / "test_sites" / "Reuters"
ARTICLE_HTML = FIXTURE_DIR / "Article_MiddleEast.html"
ARTICLE_URL = (
    "https://www.reuters.com/world/middle-east/"
    "trump-says-isis-second-command-abu-bilal-al-minuki-eliminated-2026-05-16/"
)


def load_soup(path: Path) -> BeautifulSoup:
    with open(path, encoding="utf-8") as fh:
        return BeautifulSoup(fh.read(), "html.parser")


# ── expected content (matching the saved fixture) ─────────────────────

EXPECTED_HEADLINE = (
    "Trump says ISIS second-in-command "
    "Abu-Bilal al-Minuki killed by US and Nigerian forces"
)

EXPECTED_CONTENT = (
    "<section>\n\n"
    "<title>\n\n"
    "Summary\n\n"
    "</title>\n\n"
    "Trump says sources tracked al-Minuki's movements\n\n"
    "Al-Minuki was labeled a 'specially designated global terrorist' "
    "by the Biden administration in 2023\n\n"
    "US increased drone and troop presence in Nigeria after December "
    "strikes on militants\n\n"
    "</section>\n\n"
    "<section>\n\n"
    "May 15 (Reuters) - U.S. President Donald Trump said on Friday "
    "that Abu-Bilal al-Minuki, second in command of ISIS globally, "
    "was killed in an operation conducted by U.S. and Nigerian forces.\n\n"
    '"Tonight, at my direction, brave American forces and the Armed Forces '
    "of Nigeria flawlessly executed a meticulously planned and very complex "
    "mission to eliminate the most active terrorist in the world from the "
    "battlefield. Abu-Bilal al-Minuki, second in command of ISIS globally, "
    "thought he could hide in Africa, but little did he know we had sources "
    'who kept us informed on what he was doing," Trump said on Truth Social.\n\n'
    "Trump did not disclose in his post the exact location of the operation.\n\n"
    'Al-Minuki, a Nigerian national, was designated as a "specially designated '
    'global terrorist" by the former Biden administration in 2023, according '
    "to the U.S. Federal Register.\n\n"
    "Trump, who has previously accused Nigeria of failing to protect "
    "Christians from Islamist militants in the northwest, thanked the "
    "Nigerian government for its partnership in the operation.\n\n"
    "Nigeria denies discriminating against any religion, saying its "
    "security forces target armed groups that attack both Christians "
    "and Muslims.\n\n"
    "The U.S. had earlier carried out strikes targeting Islamic State-linked "
    "militants in Nigeria in December. Since then, Washington has deployed "
    "drones and 200 troops to provide training and intelligence support to "
    "the Nigerian military against Islamic State and al Qaeda-linked "
    "insurgencies that are spreading across West Africa.\n\n"
    "The U.S. forces were operating in a strictly non-combat role, "
    "Nigerian military officials said earlier this year.\n\n"
    "Reporting by Shubham Kalia in Bengaluru; Editing by William Mallard, "
    "Muralikumar Anantharaman and Tom Hogue\n\n"
    "Our Standards: The Thomson Reuters Trust Principles.\n\n"
    "</section>"
)


# ── tests ─────────────────────────────────────────────────────────────

def test_extract_headline() -> None:
    """Headline strips the ' | Reuters' suffix correctly."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None, "extract() returned None for a known article"
    assert isinstance(result, ArticleData)
    assert result.headline == EXPECTED_HEADLINE, (
        f"Headline mismatch:\n  got: {result.headline!r}\n  exp: {EXPECTED_HEADLINE!r}"
    )
    print(result.headline)


def test_extract_content() -> None:
    """Full article content — summary bullets, paragraphs, sign-off, trust."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content is not None
    assert result.content == EXPECTED_CONTENT, (
        f"Content mismatch.\n\n=== GOT ===\n{result.content}\n\n=== EXPECTED ===\n{EXPECTED_CONTENT}"
    )
    print(result.content)


def test_content_hash_is_computed() -> None:
    """ArticleData auto-computes a SHA-256 content_hash."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content_hash
    assert len(result.content_hash) == 64  # SHA-256 hex digest
    # Hash must be deterministic.
    result2 = mod.extract(soup, ARTICLE_URL)
    assert result2 is not None
    assert result.content_hash == result2.content_hash


def test_content_has_no_invisible_chars() -> None:
    """Extracted content must not contain zero-width / invisible Unicode."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content is not None
    for ch in ReutersModule._INVISIBLE_CHARS:
        assert ch not in result.content, (
            f"Invisible char U+{ord(ch):04X} found in content"
        )


def test_skips_promo_box() -> None:
    """The 'Sign up here.' promo-box must not appear in content."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content is not None
    assert "Sign up" not in result.content, (
        "Promo-box 'Sign up' leaked into article content"
    )


def test_content_has_section_structure() -> None:
    """Content must be wrapped in <section> tags with <title> for named sections."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content is not None
    # Content must start with a <section> tag.
    assert result.content.startswith("<section>"), (
        "Content should start with <section> tag"
    )
    # Content must end with a </section> tag.
    assert result.content.rstrip().endswith("</section>"), (
        "Content should end with </section> tag"
    )
    # Must have exactly as many <section> as </section>.
    assert result.content.count("<section>") == result.content.count("</section>"), (
        "Mismatched <section> / </section> count"
    )
    # Every <title> must have a closing </title>.
    assert result.content.count("<title>") == result.content.count("</title>"), (
        "Mismatched <title> / </title> count"
    )


def test_content_length() -> None:
    """Content must exceed the MIN_CONTENT_LENGTH threshold."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.content is not None
    assert len(result.content) >= mod.MIN_CONTENT_LENGTH


def test_url_and_domain_set() -> None:
    """Result carries the correct URL and source_domain."""
    soup = load_soup(ARTICLE_HTML)
    mod = ReutersModule()
    mod.domain = "reuters.com"
    result = mod.extract(soup, ARTICLE_URL)
    assert result is not None
    assert result.url == ARTICLE_URL
    assert result.source_domain == "reuters.com"


def test_module_registered() -> None:
    """ReutersModule is discoverable via the registry."""
    from modules.registry import get_module  # noqa: E402
    cls = get_module("reuters.com")
    assert cls is not None, "reuters.com not registered"
    assert cls is ReutersModule


# ── runner ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("extract_headline", test_extract_headline),
        ("extract_content", test_extract_content),
        ("content_hash", test_content_hash_is_computed),
        ("no_invisible_chars", test_content_has_no_invisible_chars),
        ("skips_promo_box", test_skips_promo_box),
        ("section_structure", test_content_has_section_structure),
        ("content_length", test_content_length),
        ("url_and_domain", test_url_and_domain_set),
        ("module_registered", test_module_registered),
    ]

    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {name}  — {exc}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}  — UNEXPECTED ERROR")
            import traceback
            traceback.print_exc()

    print(f"\n{failed} failed, {len(tests) - failed} passed")
    sys.exit(1 if failed else 0)
