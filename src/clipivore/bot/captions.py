"""The delivery caption: the tweet's text plus a footer naming its author.

The single place the bot emits HTML (docs/ARCHITECTURE.md D10, D17). Everything
foreign — the tweet's text, the author's handle, both URLs — is escaped, and
escaping happens *after* fitting, so a trim can never cut an entity in half.
Telegram counts the caption limit on the rendered text in UTF-16 code units,
markup excluded, which is why the budget below is computed from the visible
parts only. The footer is appended after fitting and is never truncated.
"""

import html

from clipivore.bot import texts

# Bot API ceiling for a media caption, which aiogram does not enforce itself.
CAPTION_LIMIT = 1024
_ELLIPSIS = "…"  # 1 UTF-16 unit
_SEPARATOR = "\n\n"  # 2 units
_DOT = " · "  # 3 units


def utf16_length(text: str) -> int:
    """Length as Telegram counts it: UTF-16 code units, not code points.

    Anything outside the basic plane — an emoji, most notably — is two units to
    Telegram and one character to Python, so counting characters lets a caption
    Telegram considers too long slip through.
    """
    return len(text.encode("utf-16-le")) // 2


def build_caption(
    description: str,
    url: str,
    *,
    uploader: str = "",
    uploader_url: str = "",
    limit: int = CAPTION_LIMIT,
) -> str:
    """The tweet's text, a blank line, then ``@author · Open in X``.

    The handle links to the author's profile (plain text when no profile URL is
    known) and the label links to the tweet itself. Without a text the caption
    is just the footer.
    """
    open_link = f'<a href="{html.escape(url, quote=True)}">{texts.OPEN_IN_X}</a>'
    footer_html, footer_rendered = open_link, texts.OPEN_IN_X
    if uploader:
        handle = f"@{uploader}"
        handle_html = (
            f'<a href="{html.escape(uploader_url, quote=True)}">{html.escape(handle)}</a>'
            if uploader_url
            else html.escape(handle)
        )
        footer_html = f"{handle_html}{_DOT}{open_link}"
        footer_rendered = f"{handle}{_DOT}{texts.OPEN_IN_X}"
    budget = limit - utf16_length(footer_rendered) - len(_SEPARATOR)
    if not description or budget <= 0:
        return footer_html
    return f"{html.escape(_fit(description, budget))}{_SEPARATOR}{footer_html}"


def _fit(text: str, budget: int) -> str:
    if utf16_length(text) <= budget:
        return text
    # Trimmed one character at a time from the end rather than sliced by index:
    # a slice at a fixed offset can land between the halves of a surrogate pair
    # and produce a caption Telegram rejects outright.
    kept = text
    while kept and utf16_length(kept) > budget - 1:
        kept = kept[:-1]
    return kept + _ELLIPSIS
