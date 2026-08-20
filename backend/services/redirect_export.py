"""
Deploy-ready redirect files, server-side.

Every export format lived in the React export modal, which meant the rules
could only be produced by a human with a browser open. An agent asked to "run
the migration and install the redirects" had no way to obtain the artifact —
the last step of the job was unreachable over HTTP. These are ports of the
client formatters, kept byte-compatible so the API and the UI cannot drift
into producing different files from the same matches.

Path handling is the subtlety. Apache, Nginx, Cloudflare, Shopify and Vercel
all match against the request *path*; a full URL in the source silently never
matches. The UI warns about this and lets the user choose. An API has nobody
to warn, so `paths` is the default here.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse

APACHE = "apache"
NGINX = "nginx"
WORDPRESS = "wordpress"
VERCEL = "vercel"
CLOUDFLARE = "cloudflare"
SHOPIFY = "shopify"
CSV_FORMAT = "csv"
JSON_FORMAT = "json"

FORMATS = (
    APACHE,
    NGINX,
    WORDPRESS,
    VERCEL,
    CLOUDFLARE,
    SHOPIFY,
    CSV_FORMAT,
    JSON_FORMAT,
)

FILENAMES = {
    APACHE: "redirects.htaccess",
    NGINX: "redirects_nginx.conf",
    WORDPRESS: "redirects_wordpress.csv",
    VERCEL: "vercel.json",
    CLOUDFLARE: "_redirects",
    SHOPIFY: "shopify_redirects.csv",
    CSV_FORMAT: "redirects.csv",
    JSON_FORMAT: "redirects.json",
}

CONTENT_TYPES = {
    APACHE: "text/plain; charset=utf-8",
    NGINX: "text/plain; charset=utf-8",
    WORDPRESS: "text/csv; charset=utf-8",
    VERCEL: "application/json; charset=utf-8",
    CLOUDFLARE: "text/plain; charset=utf-8",
    SHOPIFY: "text/csv; charset=utf-8",
    CSV_FORMAT: "text/csv; charset=utf-8",
    JSON_FORMAT: "application/json; charset=utf-8",
}

# Formats whose matcher only ever sees the path. Emitting absolute URLs here
# produces a file that loads without error and silently redirects nothing.
PATH_ONLY_FORMATS = frozenset({APACHE, NGINX, CLOUDFLARE, SHOPIFY, VERCEL})


class UnknownExportFormat(ValueError):
    def __init__(self, fmt: str):
        super().__init__(
            f"Unknown export format '{fmt}'. Supported: {', '.join(FORMATS)}."
        )
        self.format = fmt


def to_path(url: str) -> str:
    """
    The path portion of a URL, or the input unchanged if it is not parseable.

    Returning the input rather than raising matches the UI: a row that is
    already a bare path ("/pricing") must survive untouched.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    return parsed.path or "/"


def rehost(url: str, domain: str) -> str:
    """Swap a URL onto another origin, preserving path and query."""
    try:
        parsed = urlparse(url)
        target = urlparse(domain if "://" in domain else f"https://{domain}")
    except Exception:
        return url
    if not target.netloc:
        return url
    return urlunparse((
        target.scheme or "https",
        target.netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))


def _transform(
    url: str,
    url_format: str,
    domain: Optional[str],
) -> str:
    if url_format == "full":
        return url
    if url_format == "custom" and domain:
        return rehost(url, domain)
    return to_path(url)


def _pairs(
    mappings: Iterable[dict[str, Any]],
    url_format: str,
    old_domain: Optional[str],
    new_domain: Optional[str],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in mappings:
        old_url = row.get("old_url") or row.get("oldUrl") or ""
        new_url = row.get("new_url") or row.get("newUrl") or ""
        if not old_url or not new_url:
            continue
        pairs.append((
            _transform(old_url, url_format, old_domain),
            _transform(new_url, url_format, new_domain),
        ))
    return pairs


def _csv_rows(header: list[str], rows: list[list[str]]) -> str:
    """
    Real CSV quoting rather than comma-joining.

    The client builds these by string concatenation, which corrupts any URL
    containing a comma. Writing them properly is a strict improvement and
    still byte-identical for the overwhelming majority of rows, which contain
    no quotable characters.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if header:
        writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().rstrip("\n")


def build_export(
    mappings: Iterable[dict[str, Any]],
    fmt: str,
    *,
    url_format: str = "paths",
    old_domain: Optional[str] = None,
    new_domain: Optional[str] = None,
) -> str:
    """
    Render matches as a deploy-ready redirect file.

    Args:
        mappings: rows with old_url/new_url (camelCase accepted too).
        fmt: one of FORMATS.
        url_format: 'paths' (default), 'full', or 'custom' with a domain.
    """
    if fmt not in FORMATS:
        raise UnknownExportFormat(fmt)

    pairs = _pairs(mappings, url_format, old_domain, new_domain)

    if fmt == APACHE:
        return "\n".join(f"Redirect 301 {old} {new}" for old, new in pairs)

    if fmt == NGINX:
        body = "\n".join(f"    {old} {new};" for old, new in pairs)
        return "map $uri $new_uri {\n" + body + ("\n" if body else "") + "}"

    if fmt == WORDPRESS:
        return _csv_rows([], [[old, new, "301"] for old, new in pairs])

    if fmt == VERCEL:
        return json.dumps(
            {
                "redirects": [
                    {"source": old, "destination": new, "permanent": True}
                    for old, new in pairs
                ]
            },
            indent=2,
        )

    if fmt == CLOUDFLARE:
        return "\n".join(f"{old} {new} 301" for old, new in pairs)

    if fmt == SHOPIFY:
        return _csv_rows(["Redirect from", "Redirect to"], [[o, n] for o, n in pairs])

    if fmt == CSV_FORMAT:
        return _csv_rows(["old_url", "new_url", "status"], [[o, n, "301"] for o, n in pairs])

    # JSON
    return json.dumps(
        [{"from": old, "to": new, "status": 301} for old, new in pairs],
        indent=2,
    )


def filename_for(fmt: str) -> str:
    if fmt not in FORMATS:
        raise UnknownExportFormat(fmt)
    return FILENAMES[fmt]


def content_type_for(fmt: str) -> str:
    if fmt not in FORMATS:
        raise UnknownExportFormat(fmt)
    return CONTENT_TYPES[fmt]


def warning_for(fmt: str, url_format: str) -> Optional[str]:
    """
    Why a chosen combination will not work, if it will not.

    Returned rather than raised: an agent that explicitly asked for absolute
    URLs should still get its file, along with the reason it will not route.
    """
    if url_format != "paths" and fmt in PATH_ONLY_FORMATS:
        return (
            f"{fmt} matches against the request path — absolute URLs in the "
            "source will never match. Use url_format='paths'."
        )
    return None
