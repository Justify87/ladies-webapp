from __future__ import annotations

from html.parser import HTMLParser


class _AdvertiserWebsiteParser(HTMLParser):
    """Extract only the advertiser website explicitly marked by Ladies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.website_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.website_url is not None or tag.casefold() != "a":
            return
        values = {str(key).casefold(): value for key, value in attrs}
        classes = {part for part in str(values.get("class") or "").split() if part}
        href = str(values.get("href") or "").strip()
        if "einzelansicht-url" in classes and href:
            self.website_url = href


def business_website_url(raw_html: str) -> str | None:
    """Return the website attached to the advert itself, if Ladies exposes one.

    Generic external links on the page deliberately do not count.  The
    classifier only accepts ``<a class="... einzelansicht-url ..." href="...">``.
    """
    parser = _AdvertiserWebsiteParser()
    parser.feed(str(raw_html or ""))
    parser.close()
    return parser.website_url
