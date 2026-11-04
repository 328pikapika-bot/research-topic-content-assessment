"""Bounded public discovery supplement. Abstracts remain abstracts, never full text."""
from __future__ import annotations
import asyncio
import hashlib
import re
import httpx
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from .schemas import EvidenceRecord


def abstract_text(index):
    if not isinstance(index, dict):
        return ""
    words = {}
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < 20000:
                words[pos] = str(word)
    return " ".join(words[pos] for pos in sorted(words))


class AnthologyAbstract(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth = 0; self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}:
                self.depth += 1
            else:
                self.parts.append(" ")
        elif dict(attrs).get("id") == "abstract":
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth and tag not in {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


async def source_abstract(client, url, fallback):
    # Only known public publisher abstract pages; no arbitrary fetched content
    # is interpreted as instructions or promoted to a fulltext evidence level.
    if re.match(r"https://aclanthology\.org/[^/?#]+/?$", url):
        try:
            response = await client.get(url); response.raise_for_status()
            parser = AnthologyAbstract(); parser.feed(response.text)
            text = " ".join(parser.parts).removeprefix("Abstract").strip()
            if len(text) > 120:
                return text
        except (httpx.HTTPError, ValueError):
            pass
    return fallback


async def arxiv_records(client, claim, query, year_range):
    """Use actual repository abstracts when public discovery is rate limited.

    These are explicitly preprints. The PDF link does not confer full-text
    evidence: no body has been parsed or anchored by this discovery request.
    """
    phrase = re.sub(r'[^A-Za-z0-9 -]', ' ', query).strip()
    response = await client.get('https://export.arxiv.org/api/query', params={
        'search_query': f'all:"{phrase}"', 'max_results': 12,
        'sortBy': 'relevance', 'sortOrder': 'descending'})
    response.raise_for_status()
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(response.text)
    # Long method descriptions are not necessarily literal phrases in a
    # repository abstract. Retry a named acronym, retaining the downstream
    # object/method relevance check before any source enters the report.
    acronym = re.match(r'([A-Z][A-Z0-9]{2,})\b', phrase)
    if not root.findall('a:entry', ns) and acronym and acronym.group(1) != phrase:
        await asyncio.sleep(3)
        response = await client.get('https://export.arxiv.org/api/query', params={
            'search_query': f'all:"{acronym.group(1)}"', 'max_results': 12,
            'sortBy': 'relevance', 'sortOrder': 'descending'})
        response.raise_for_status()
        root = ET.fromstring(response.text)
    records = []
    for entry in root.findall('a:entry', ns):
        url = entry.findtext('a:id', '', ns).replace('http://', 'https://')
        title = ' '.join(entry.findtext('a:title', '', ns).split())
        abstract = ' '.join(entry.findtext('a:summary', '', ns).split())
        published = entry.findtext('a:published', '', ns)
        if not re.fullmatch(r'https://arxiv\.org/abs/[A-Za-z0-9./-]+', url) or not title or not abstract or not published[:4].isdigit():
            continue
        year = int(published[:4])
        if len(year_range) == 2 and not year_range[0] <= year <= year_range[1]:
            continue
        records.append(EvidenceRecord(
            evidence_id='E-ARX-' + hashlib.sha256(url.encode()).hexdigest()[:12],
            paper_id=url, source_type='literature', title=title, abstract=abstract,
            excerpt=abstract, year=year, url=url, source_url=url,
            authors=[author.findtext('a:name', '', ns) for author in entry.findall('a:author', ns)],
            venue='arXiv（预印本）', access_status='abstract_only', evidence_level='B',
            retrieval_source='arXiv', retrieval_query=query, claim_ids=[claim.claim_id],
            locator={'metadata_url':url, 'section':'abstract'}))
    return records


async def supplement(state):
    from .scholarly_report import selected_sources
    pending = [c for c in state.claims[:12] if len(selected_sources(state, c)) < 4]
    year_range = state.output_json.get("year_range") or []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        fallback_lock = asyncio.Lock()
        async def search(claim):
            method = re.search(r"[A-Za-z][A-Za-z0-9 -]*", str(claim.claim_fields.get("method", "")))
            query = method.group().strip() if method else " ".join(claim.searchable_terms[:5])
            if not query:
                return [], {"claim_id": claim.claim_id, "status": "empty_query"}
            try:
                params = {"search": query, "per-page": 12}
                if len(year_range) == 2:
                    params["filter"] = f"from_publication_date:{year_range[0]}-01-01,to_publication_date:{year_range[1]}-12-31"
                response = await client.get("https://api.openalex.org/works", params=params)
                response.raise_for_status()
                records = []
                for work in response.json().get("results", []):
                    title = str(work.get("display_name") or "")
                    abstract = abstract_text(work.get("abstract_inverted_index"))
                    if not title or not abstract:
                        continue
                    year = work.get("publication_year")
                    if len(year_range) == 2 and (not isinstance(year, int) or not year_range[0] <= year <= year_range[1]):
                        continue
                    doi_url = work.get("doi") or ""
                    location = work.get("primary_location") or {}
                    source_url = location.get("landing_page_url") or doi_url or work.get("id", "")
                    abstract = await source_abstract(client, source_url, abstract)
                    records.append(EvidenceRecord(
                        evidence_id="E-OA-" + hashlib.sha256(str(work.get("id")).encode()).hexdigest()[:12],
                        paper_id=str(work.get("id") or ""), source_type="literature", title=title,
                        abstract=abstract, excerpt=abstract, year=work.get("publication_year"),
                        url=source_url, source_url=source_url, doi=doi_url.removeprefix("https://doi.org/"),
                        authors=[a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])],
                        venue=(location.get("source") or {}).get("display_name", ""),
                        access_status="abstract_only", evidence_level="B", retrieval_source="OpenAlex",
                        retrieval_query=query, claim_ids=[claim.claim_id],
                        locator={"metadata_url":work.get("id", ""), "section":"abstract"}))
                return records, {"claim_id": claim.claim_id, "query": query, "status": "completed", "count": len(records)}
            except (httpx.HTTPError, ValueError, TypeError) as error:
                trace = {"claim_id": claim.claim_id, "query": query, "status": "failed", "error_type": type(error).__name__}
                try:
                    async with fallback_lock:
                        records = await arxiv_records(client, claim, query, year_range)
                        # Respect the public repository's request interval.
                        await asyncio.sleep(3)
                    return records, {**trace, 'status':'completed', 'provider':'arXiv',
                        'fallback_from':'OpenAlex', 'count':len(records)}
                except (httpx.HTTPError, ValueError, TypeError, ET.ParseError) as fallback_error:
                    return [], {**trace, 'fallback_error_type':type(fallback_error).__name__}
        results = await asyncio.gather(*(search(c) for c in pending))
    seen = {e.doi.lower() or e.title.lower(): e for e in state.evidence_records}
    for records, trace in results:
        state.output_json.setdefault("public_search_trace", []).append(trace)
        for record in records:
            identity = record.doi.lower() or record.title.lower()
            if identity not in seen:
                seen[identity] = record
                state.evidence_records.append(record)
            else:
                existing = seen[identity]
                existing.claim_ids = list(dict.fromkeys([*existing.claim_ids, *record.claim_ids]))
