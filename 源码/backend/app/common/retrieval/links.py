"""Reader-facing evidence links, shared by reports and retrieval adapters."""
import re
from urllib.parse import quote, urlsplit


_LINK_ALIASES = {
    "nstl_url": ("nstl_url", "nstlUrl", "detailUrl", "detail_url", "detailURL", "paperDetailUrl", "paper_detail_url", "nstlDetailUrl", "nstl_detail_url", "landingPage", "landing_page"),
    "source_url": ("source_url", "sourceUrl", "sourceURL", "originalUrl", "original_url", "originalURL", "documentUrl", "document_url", "fulltextUrl", "fullTextUrl", "fulltextURL", "full_text_url", "pdfUrl", "pdf_url", "link", "href"),
    "url": ("url", "fileUrl", "file_url", "hasFile"),
    "paper_id": ("paper_id", "paperId", "paperID", "id", "documentId", "document_id", "articleId", "article_id", "recordId", "record_id"),
    "doi": ("doi", "DOI", "doiUrl", "doi_url"),
}


def _field(value, aliases: tuple[str, ...], depth: int = 0):
    """Read a link/id field from legacy flat or shallow nested records."""
    if not isinstance(value, dict) or depth > 2:
        return ""
    for key in aliases:
        candidate = value.get(key)
        if candidate not in (None, "", False, []):
            if isinstance(candidate, (str, int, float)):
                return candidate
            if isinstance(candidate, dict):
                nested = _field(candidate, aliases, depth + 1)
                if nested:
                    return nested
                for nested_key in ("url", "href", "value", "id"):
                    nested_value = candidate.get(nested_key)
                    if nested_value not in (None, "", False, []):
                        return nested_value
    for child in value.values():
        if isinstance(child, dict):
            nested = _field(child, aliases, depth + 1)
            if nested:
                return nested
    return ""


def http_url(value) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return ""
        # A bare NSTL landing page is not a citation destination.  Reject it
        # so callers can fall back to an original source, DOI, or paper id.
        host = parsed.hostname.lower().rstrip(".")
        if host == "nstl.gov.cn" or host.endswith(".nstl.gov.cn"):
            path = (parsed.path or "").rstrip("/").lower()
            if path in {"", "/index.html", "/index.htm", "/home", "/homepage"}:
                return ""
        return value
    except ValueError:
        return ""


def doi_url(value) -> str:
    doi = re.sub(r"^https?://(?:dx\.)?doi.org/|^doi:\s*", "", str(value or "").strip(), flags=re.I)
    return "https://doi.org/" + doi if re.fullmatch(r"10\.\d{4,9}/\S+", doi) else ""


def nstl_detail_url(paper_id) -> str:
    """Build the stable NSTL detail page for a retrieved paper identifier.

    NSTL responses are not consistent about returning the detail URL.  The
    identifier is still part of the response, so it is safe to use the
    documented detail route as a deterministic fallback.  Keep the value
    tightly encoded so an identifier can never inject a path or query.
    """
    identifier = str(paper_id or "").strip()
    if not identifier or len(identifier) > 256:
        return ""
    return "https://www.nstl.gov.cn/paper_detail.html?id=" + quote(identifier, safe="._-:")


def evidence_url(evidence) -> str:
    if isinstance(evidence, dict):
        def values(key):
            aliases = _LINK_ALIASES[key]
            yielded = []
            for alias in aliases:
                value = evidence.get(alias)
                if not any(value == previous for previous in yielded):
                    yielded.append(value)
                    yield value
            nested = _field(evidence, aliases)
            if not any(nested == previous for previous in yielded):
                yield nested
    else:
        def values(key):
            yield getattr(evidence, key, "")
    explicit = next((url for key in ("nstl_url", "source_url", "url")
                     for value in values(key) if (url := http_url(value))), "")
    if explicit:
        return explicit
    # Retrieved NSTL records historically kept only paper_id/id.  Use the
    # official detail page before falling back to a DOI.
    paper_id = next((value for value in values("paper_id") if value), "")
    if paper_id:
        return nstl_detail_url(paper_id)
    return next((url for value in values("doi") if (url := doi_url(value))), "")


def citation_urls(records, numbers: dict[str, int]) -> dict[str, str]:
    """Resolve one URL per number, including aliases of a deduplicated source."""
    groups = {}
    for record in records:
        get = record.get if isinstance(record, dict) else lambda key: getattr(record, key, "")
        number = numbers.get(get("evidence_id"))
        if number is not None:
            groups.setdefault(str(number), []).append(record)
    result = {}
    for number, aliases in groups.items():
        # A citation may have been deduplicated from records produced by
        # different gateway versions. Resolve the first usable destination
        # across all aliases while preserving the canonical priority.
        for key in ("nstl_url", "source_url", "url", "paper_id", "doi"):
            for record in aliases:
                if isinstance(record, dict):
                    raw_values = [record.get(alias) for alias in _LINK_ALIASES[key]]
                    raw_values.append(_field(record, _LINK_ALIASES[key]))
                else:
                    raw_values = [getattr(record, key, "")]
                for value in raw_values:
                    if key == "paper_id":
                        url = nstl_detail_url(value)
                    elif key == "doi":
                        url = doi_url(value)
                    else:
                        url = http_url(value)
                    if url:
                        result[number] = url
                        break
                if number in result:
                    break
            if number in result:
                break
    return result
