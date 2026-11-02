from __future__ import annotations

from typing import Mapping

from osint_scraper.extractors import ExtractionContext, load_extractors, run_extractors
from osint_scraper.extractors.crypto import CryptoExtractor
from osint_scraper.extractors.emails import EmailExtractor
from osint_scraper.extractors.phones import PhoneExtractor
from osint_scraper.extractors.secrets import SecretExtractor
from osint_scraper.extractors.social import SocialExtractor
from osint_scraper.extractors.structured_data import StructuredDataExtractor
from osint_scraper.extractors.technologies import TechnologyExtractor
from osint_scraper.models import ScriptSource
from osint_scraper.parser import parse_page

URL = "https://example.com/page"


def make_context(
    html: str,
    *,
    headers: Mapping[str, str] | None = None,
    scripts: tuple[ScriptSource, ...] = (),
) -> ExtractionContext:
    return ExtractionContext(
        html=html,
        base_url=URL,
        headers=headers or {},
        page=parse_page(html, URL),
        scripts=scripts,
    )


def test_auto_discovers_all_extractors() -> None:
    names = {extractor.name for extractor in load_extractors()}
    assert names == {
        "emails",
        "phones",
        "social",
        "crypto",
        "secrets",
        "structured_data",
        "technologies",
    }


def test_emails_handle_obfuscation_scripts_and_deduplication() -> None:
    html = """
    <p>alice@example.com and bob [at] example [dot] org</p>
    <p>alice@example.com not-an-email@localhost</p>
    """
    scripts = (ScriptSource("inline-script[0]", "carol@example.net; alice@example.com"),)

    findings = EmailExtractor().extract(make_context(html, scripts=scripts))

    assert {finding.value for finding in findings} == {
        "alice@example.com",
        "bob@example.org",
        "carol@example.net",
    }
    assert all(finding.category == "emails" for finding in findings)
    assert all(finding.source.startswith(("html:", "inline-script[0]:")) for finding in findings)


def test_phones_validate_text_and_tel_links() -> None:
    html = """
    <p>Call +1 415 555 2671 or +1 234 567.</p>
    <a href="tel:+442071838750">London office</a>
    """

    findings = PhoneExtractor().extract(make_context(html))

    assert {finding.value for finding in findings} == {
        "+14155552671",
        "+442071838750",
    }


def test_social_profiles_are_classified_and_deduplicated() -> None:
    html = """
    <a href="https://github.com/octocat">GitHub</a>
    <a href="https://github.com/octocat?tab=repos">Again</a>
    <a href="https://x.com/jack">X</a>
    <a href="https://facebook.com/sharer/sharer.php?u=x">Share</a>
    <a href="https://infosec.exchange/@alice">Mastodon</a>
    """

    findings = SocialExtractor().extract(make_context(html))
    by_platform = {finding.data["platform"]: finding for finding in findings if finding.data}

    assert set(by_platform) == {"GitHub", "Twitter/X", "Mastodon"}
    assert by_platform["GitHub"].data["handle"] == "octocat"
    assert by_platform["Twitter/X"].data["handle"] == "@jack"
    assert by_platform["Mastodon"].data["handle"] == "@alice"
    assert len(findings) == 3


def test_crypto_addresses_require_valid_checksums() -> None:
    html = """
    <p>BTC 1BoatSLRHtKNngkdXEeobR76b53LETtpyT</p>
    <p>Bech32 bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4</p>
    <p>ETH 0x52908400098527886E0F7030069857D2E4169EE7</p>
    <p>Fake 1BoatSLRHtKNngkdXEeobR76b53LETtpyX</p>
    """

    findings = CryptoExtractor().extract(make_context(html))

    assert {finding.data["asset"] for finding in findings if finding.data} == {"BTC", "ETH"}
    values = {finding.value for finding in findings}
    assert "1BoatSLRHtKNngkdXEeobR76b53LETtpyT" in values
    assert "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" in values
    assert "0x52908400098527886E0F7030069857D2E4169EE7" in values
    assert not any(value.endswith("pyX") for value in values)


def test_secrets_are_known_patterns_and_masked() -> None:
    raw_github = "ghp_" + "a" * 36
    scripts = (
        ScriptSource(
            "inline-script[0]",
            f'const aws = "AKIA1234567890ABCDEF";\nconst gh = "{raw_github}";',
        ),
        ScriptSource("https://cdn.example.com/app.js", "const safe = 'hello';"),
    )

    findings = SecretExtractor().extract(make_context("<html></html>", scripts=scripts))

    github = next(finding for finding in findings if finding.data == {"pattern": "github_token"})
    assert raw_github not in github.value
    assert github.value.startswith("ghp_") and github.value.endswith("aaaa")
    assert github.source == "inline-script[0]:line 2"
    assert any(finding.data == {"pattern": "aws_access_key"} for finding in findings)


def test_structured_data_extracts_jsonld_and_microdata() -> None:
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Person", "name": "Jane Doe",
     "email": "jane@example.com"}
    </script>
    <script type="application/ld+json">{broken json</script>
    <div itemscope itemtype="https://schema.org/Organization">
      <span itemprop="name">Example Inc</span>
      <a itemprop="url" href="https://example.com">Site</a>
    </div>
    """

    findings = StructuredDataExtractor().extract(make_context(html))
    by_format = {finding.data["format"]: finding for finding in findings if finding.data}

    assert by_format["json-ld"].value == "Person"
    assert by_format["json-ld"].data["object"]["name"] == "Jane Doe"
    assert by_format["microdata"].data["object"]["name"] == "Example Inc"
    assert by_format["microdata"].data["object"]["url"] == "https://example.com"
    assert len(findings) == 2


def test_technologies_come_from_headers_meta_and_assets() -> None:
    html = """
    <meta name="generator" content="WordPress 6.5">
    <script src="/assets/jquery-3.7.1.min.js"></script>
    <div id="__NEXT_DATA__"></div>
    """
    headers = {"Server": "nginx/1.25.3", "X-Powered-By": "PHP/8.3"}

    findings = TechnologyExtractor().extract(make_context(html, headers=headers))
    values = {finding.value for finding in findings}

    assert "Nginx 1.25.3" in values
    assert "PHP 8.3" in values
    assert "WordPress 6.5" in values
    assert "jQuery 3.7.1" in values
    assert "Next.js" in values


def test_runner_combines_findings() -> None:
    html = '<p>alice@example.com</p><a href="https://github.com/octocat">GitHub</a>'
    findings = run_extractors(make_context(html))

    assert any(finding.category == "emails" for finding in findings)
    assert any(finding.category == "social" for finding in findings)
