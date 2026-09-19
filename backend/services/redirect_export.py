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
from collections.abc import Mapping
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


class ExportSelectionError(ValueError):
    """A mapping cannot be safely represented as a redirect rule."""


def _safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if (any(ord(char) < 0x20 or ord(char) == 0x7F or char == "\\" for char in value)
            or any(char.isspace() for char in value)):
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None):
            return None
        try:
            if parsed.hostname is None or parsed.port is not None and parsed.port < 0:
                return None
        except ValueError:
            return None
    elif not value.startswith("/"):
        return None
    return value


def _url_key(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme and not parsed.netloc:
        return urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.params,
        parsed.query,
        "",
    ))


def _decision_exclusion(row: Mapping[str, Any]) -> Optional[str]:
    states = {
        str(row.get(field, "")).strip().lower()
        for field in ("status", "decision", "state", "mapping_status")
        if row.get(field) is not None
    }
    if states & {"held", "rejected", "pending", "needs_review", "unresolved"}:
        return "held_or_rejected"
    if row.get("approved") is False or row.get("validated") is False:
        return "held_or_rejected"
    if row.get("needs_review") is True:
        return "held_or_rejected"
    return None


def _render_pair(old_url: str, new_url: str, url_format: str,
                 old_domain: Optional[str], new_domain: Optional[str]) -> tuple[str, str]:
    old = _transform(old_url, url_format, old_domain)
    new = _transform(new_url, url_format, new_domain)
    if url_format == "paths" and _origin(old_url) != _origin(new_url):
        # A path-only source can still redirect to another host.  Dropping the
        # destination origin would turn a domain move into an internal link.
        new = _transform(new_url, "full", new_domain)
    return old, new


def _origin(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    return parsed.scheme.lower(), parsed.netloc.lower()


def select_export_mappings(
    mappings: Iterable[dict[str, Any]],
    *,
    url_format: str = "paths",
    old_domain: Optional[str] = None,
    new_domain: Optional[str] = None,
) -> dict[str, Any]:
    """Select safe redirect rules and report every excluded row.

    Rows with no decision fields remain accepted for compatibility with the
    legacy export endpoint.  When decision fields exist, explicit holds,
    rejections, review flags, and validation failures are never exported.
    """
    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for index, row in enumerate(mappings):
        if not isinstance(row, Mapping):
            exclusions.append({"index": index, "reason": "invalid_mapping"})
            continue
        old_raw = row.get("old_url") or row.get("oldUrl")
        new_raw = row.get("new_url") or row.get("newUrl")
        old_url = _safe_url(old_raw)
        new_url = _safe_url(new_raw)
        if old_url is None or new_url is None:
            exclusions.append({"index": index, "reason": "invalid_url"})
            continue
        decision_reason = _decision_exclusion(row)
        if decision_reason:
            exclusions.append({"index": index, "reason": decision_reason})
            continue
        if _url_key(old_url) == _url_key(new_url):
            exclusions.append({"index": index, "reason": "no_op"})
            continue
        old_rendered, new_rendered = _render_pair(old_url, new_url, url_format, old_domain, new_domain)
        candidates.append({
            "index": index,
            "row": dict(row),
            "old_url": old_url,
            "new_url": new_url,
            "old_rendered": old_rendered,
            "new_rendered": new_rendered,
        })

    by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_source.setdefault(candidate["old_rendered"], []).append(candidate)
    kept: list[dict[str, Any]] = []
    for source, same_source in by_source.items():
        destinations = {item["new_rendered"] for item in same_source}
        if len(destinations) > 1:
            for item in same_source:
                exclusions.append({"index": item["index"], "reason": "conflicting_source"})
        else:
            kept.extend(same_source)

    # Exclude only actual cycles.  A chain is not silently rewritten; its
    # explicit rules remain inspectable and can be verified independently.
    edges = {_url_key(item["old_url"]): _url_key(item["new_url"]) for item in kept}
    cycle_nodes: set[str] = set()
    for start in edges:
        path: list[str] = []
        current = start
        while current in edges and current not in path:
            path.append(current)
            current = edges[current]
        if current in path:
            cycle_nodes.update(path[path.index(current):])
    selected: list[dict[str, Any]] = []
    for item in kept:
        if _url_key(item["old_url"]) in cycle_nodes:
            exclusions.append({"index": item["index"], "reason": "redirect_loop"})
        else:
            selected.append(item)
    selected.sort(key=lambda item: item["index"])
    exclusions.sort(key=lambda item: (item["index"], item["reason"]))
    return {
        "mappings": selected,
        "excluded": exclusions,
        "included_count": len(selected),
        "excluded_count": len(exclusions),
    }


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
    selection = select_export_mappings(
        mappings,
        url_format=url_format,
        old_domain=old_domain,
        new_domain=new_domain,
    )
    return [
        (item["old_rendered"], item["new_rendered"])
        for item in selection["mappings"]
    ]


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
    if fmt == NGINX:
        return (
            "Nginx output is an http-context map fragment; include it at http "
            "scope and add a server/location return using $new_uri."
        )
    if fmt == CLOUDFLARE:
        return (
            "This is a Cloudflare Pages _redirects file; Cloudflare Workers or "
            "other products require their own route configuration."
        )
    if url_format != "paths" and fmt in PATH_ONLY_FORMATS:
        return (
            f"{fmt} matches against the request path — absolute URLs in the "
            "source will never match. Use url_format='paths'."
        )
    return None
