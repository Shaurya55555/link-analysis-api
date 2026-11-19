"""Competitor checks. Competitors are LLM suggestions, so each one gets a real live fetch
to see whether the suggested site exists; the result is attached, never hidden."""
from typing import List

import httpx

from app.schemas import Competitor
from app.services.website_extractor import FetchError, parse_page, safe_get
from app.utils.validators import UnsafeURLError


def verify_competitors(competitors: List[Competitor], client: httpx.Client) -> List[Competitor]:
    for c in competitors:
        c.source = "ai_suggested"
        if not c.website:
            c.live_check = {"reachable": False, "note": "No website provided by the model"}
            continue
        try:
            resp = safe_get(c.website, client)
            if resp.status_code >= 400:
                c.live_check = {"reachable": False, "note": f"HTTP {resp.status_code}"}
                continue
            page = parse_page(str(resp.request.url), resp.content)
            c.live_check = {"reachable": True, "page_title": page.title[:120],
                            "meta_description": page.meta_description[:200]}
        except (UnsafeURLError, FetchError, httpx.HTTPError) as exc:
            c.live_check = {"reachable": False, "note": str(exc)[:120]}
    return competitors
