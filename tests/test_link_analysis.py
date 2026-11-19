import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import analysis_service, llm_service
from app.services.llm_service import LLMError, LLMResult, _parse_json
from app.services.website_extractor import parse_page
from app.utils.validators import UnsafeURLError, validate_url

client = TestClient(app)

GOOD_LLM = {
    "brand": {"name": "Acme Dental", "category": "Dental clinic", "summary": "Family dentistry in Pune."},
    "analysis": {
        "competitor_analysis": {
            "brand_overview": "Acme Dental offers family dentistry.",
            "competitors": [
                {"name": "Clove Dental", "website": None, "main_service": "Dental care",
                 "target_market": "Urban families", "strengths": ["Chain"], "weaknesses": ["Pricing"]}
            ] * 3,
            "comparison_table": [{"comparison": "Brand Name", "your_brand": "Acme", "competitor": "Clove"}],
            "strengths": ["Local"], "opportunities": ["Reviews"],
        },
        "target_search_queries": [
            {"query": f"dentist in pune {i}", "platforms": ["Google"], "intent": "Service Search"} for i in range(5)
        ],
        "seo_improvements": {
            "primary_keyword": "dentist in pune", "recommended_title": "Dentist in Pune | Acme Dental",
            "meta_description": "Family dentistry in Pune.", "supporting_content": {"faqs": ["Cost of root canal?"]},
        },
        "top_services": [{"name": f"Service {i}", "description": "d"} for i in range(5)],
        "target_audience": {"primary_audience": ["Families"]},
    },
}


@pytest.fixture(autouse=True)
def _clear_cache():
    analysis_service._cache.clear()


# ---- validators / SSRF ----
@pytest.mark.parametrize("bad", [
    "http://localhost/admin", "http://127.0.0.1", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5", "http://192.168.1.1", "http://[::1]/", "ftp://example.com", "file:///etc/passwd",
    "http://user:pw@example.com", "http://0.0.0.0", "", "http://example.com:22",
])
def test_blocks_unsafe_urls(bad):
    with pytest.raises(UnsafeURLError):
        validate_url(bad)


def test_accepts_public_ip_and_adds_scheme():
    assert validate_url("93.184.216.34") == "https://93.184.216.34"


# ---- extraction ----
def test_parse_page_extracts_and_cleans():
    html = """<html><head><title>Acme Dental</title><meta name="description" content="Family dentistry">
    </head><body><nav><a href="/services">Services</a></nav><script>var x=1</script>
    <main><h1>Smile with Acme</h1><p>We provide root canal treatment and teeth whitening for families.</p>
    <p>We provide root canal treatment and teeth whitening for families.</p></main></body></html>"""
    p = parse_page("https://acme.test/", html)
    assert p.title == "Acme Dental" and p.meta_description == "Family dentistry"
    assert "var x" not in p.text
    assert p.text.count("root canal") == 1  # duplicate removed
    assert p.nav_links and p.nav_links[0][0] == "https://acme.test/services"


# ---- LLM json handling ----
def test_parse_json_handles_code_fences_and_noise():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('Here: {"a": 2} done') == {"a": 2}
    with pytest.raises(LLMError):
        _parse_json("not json")


def test_fallback_provider_used(monkeypatch):
    from app.config import Settings
    calls = []

    def fail(*a, **k):
        calls.append("gemini")
        raise LLMError("down")

    def ok(*a, **k):
        calls.append("openai")
        return LLMResult({"x": 1}, "openai", "m", 0.1)

    monkeypatch.setitem(llm_service.PROVIDERS, "gemini", fail)
    monkeypatch.setitem(llm_service.PROVIDERS, "openai", ok)
    res = llm_service.generate_json("s", "u", Settings(llm_provider="gemini", fallback_provider="openai"),
                                    client=httpx.Client())
    assert res.provider == "openai" and calls == ["gemini", "openai"]


# ---- API ----
def test_invalid_body_returns_422():
    r = client.post("/link-analysis", json={"nope": 1})
    assert r.status_code == 422 and r.json()["success"] is False


def test_private_url_blocked_via_api():
    r = client.post("/link-analysis", json={"url": "http://169.254.169.254/"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_or_blocked_url"


def _mock_site(monkeypatch):
    html = ("<html><head><title>Acme Dental</title></head><body><main><p>" + "Root canal and whitening. " * 30 +
            "</p></main></body></html>")

    def fake_get(url, client):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=html.encode(),
                              request=httpx.Request("GET", "https://acme.test/"))

    monkeypatch.setattr("app.services.website_extractor.safe_get", fake_get)
    monkeypatch.setattr("app.services.competitor_service.safe_get", fake_get)


def test_success_flow_and_cache(monkeypatch):
    _mock_site(monkeypatch)
    n = {"calls": 0}

    def fake_llm(system, user, *a, **k):
        n["calls"] += 1
        assert "Root canal" in user  # real site content reached the prompt
        return LLMResult(GOOD_LLM, "gemini", "test-model", 1.2, 900, 700)

    monkeypatch.setattr(analysis_service, "generate_json", fake_llm)
    r = client.post("/link-analysis", json={"url": "https://acme.test"})
    body = r.json()
    assert r.status_code == 200 and body["success"] is True
    assert body["brand"]["name"] == "Acme Dental"
    assert set(body["analysis"]) == {"competitor_analysis", "target_search_queries", "seo_improvements",
                                     "top_services", "target_audience"}
    assert all(c["source"] == "ai_suggested" for c in body["analysis"]["competitor_analysis"]["competitors"])
    assert all(q["search_volume"] is None for q in body["analysis"]["target_search_queries"])
    assert body["meta"]["cached"] is False
    r2 = client.post("/link-analysis", json={"url": "https://acme.test"})
    assert r2.json()["meta"]["cached"] is True and n["calls"] == 1


def test_llm_invalid_output_retried_then_502(monkeypatch):
    _mock_site(monkeypatch)
    calls = {"n": 0}

    def bad_llm(*a, **k):
        calls["n"] += 1
        return LLMResult({"brand": {}}, "gemini", "m", 0.1)

    monkeypatch.setattr(analysis_service, "generate_json", bad_llm)
    r = client.post("/link-analysis", json={"url": "https://acme.test"})
    assert r.status_code == 502 and r.json()["code"] == "llm_failure" and calls["n"] == 2


def test_llm_api_failure_returns_502(monkeypatch):
    _mock_site(monkeypatch)

    def boom(*a, **k):
        raise LLMError("quota")

    monkeypatch.setattr(analysis_service, "generate_json", boom)
    r = client.post("/link-analysis", json={"url": "https://acme.test"})
    assert r.status_code == 502 and "quota" not in r.text  # internals are not leaked


# ---- schema strictness ----
def test_schema_caps_extra_items_and_rejects_too_few():
    import copy
    from pydantic import ValidationError
    from app.schemas import LLMOutput
    many = copy.deepcopy(GOOD_LLM)
    many["analysis"]["target_search_queries"] *= 2  # 10 -> capped to 5
    many["analysis"]["top_services"] *= 2
    out = LLMOutput.model_validate(many)
    assert len(out.analysis.target_search_queries) == 5 and len(out.analysis.top_services) == 5
    few = copy.deepcopy(GOOD_LLM)
    few["analysis"]["top_services"] = few["analysis"]["top_services"][:3]
    with pytest.raises(ValidationError):
        LLMOutput.model_validate(few)
    nocomp = copy.deepcopy(GOOD_LLM)
    nocomp["analysis"]["competitor_analysis"]["competitors"] = nocomp["analysis"]["competitor_analysis"]["competitors"][:2]
    with pytest.raises(ValidationError):
        LLMOutput.model_validate(nocomp)


# ---- rate limiting + request id ----
def test_rate_limit_and_request_id(monkeypatch):
    import app.main as m
    _mock_site(monkeypatch)
    monkeypatch.setattr(analysis_service, "generate_json",
                        lambda *a, **k: LLMResult(GOOD_LLM, "gemini", "m", 0.1, 1, 1))
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "2")
    m._hits.clear()
    codes = [client.post("/link-analysis", json={"url": "https://acme.test"}) for _ in range(3)]
    assert [r.status_code for r in codes] == [200, 200, 429]
    assert codes[2].json()["code"] == "rate_limited"
    assert codes[0].headers["x-request-id"] and codes[0].json()["meta"]["request_id"] == codes[0].headers["x-request-id"]
    m._hits.clear()


# ---- anthropic provider (HTTP mocked, not live-tested) ----
def test_anthropic_provider_parses_response():
    from app.config import Settings
    def handler(request):
        assert request.headers["x-api-key"] == "k" and request.headers["anthropic-version"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"ok": true}'}],
                                         "usage": {"input_tokens": 11, "output_tokens": 7}})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    res = llm_service.generate_json("s", "u", Settings(llm_provider="anthropic", anthropic_api_key="k"), client=c)
    assert res.data == {"ok": True} and res.provider == "anthropic" and (res.input_tokens, res.output_tokens) == (11, 7)


def test_gemini_provider_parses_response_and_reports_tokens():
    from app.config import Settings
    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}],
                                         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    res = llm_service.generate_json("s", "u", Settings(llm_provider="gemini", gemini_api_key="k"), client=c)
    assert res.data == {"a": 1} and (res.input_tokens, res.output_tokens) == (5, 3)


def test_missing_key_gives_clean_error():
    from app.config import Settings
    with pytest.raises(LLMError):
        llm_service.generate_json("s", "u", Settings(llm_provider="gemini", gemini_api_key=""), client=httpx.Client())


# ---- SSRF via redirect ----
def test_redirect_into_private_address_is_blocked():
    from app.services.website_extractor import safe_get
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURLError):
        safe_get("http://93.184.216.34/", c)


def test_unresolvable_host_is_unreachable_not_blocked():
    r = client.post("/link-analysis", json={"url": "https://this-domain-does-not-exist-12345.com"})
    assert r.status_code == 502 and r.json()["code"] == "website_unreachable"


def test_gemini_falls_back_to_next_model_on_503(monkeypatch):
    from app.config import Settings
    monkeypatch.setattr(llm_service.time, "sleep", lambda *_: None)
    seen = []

    def handler(request):
        model = request.url.path.split("/models/")[1].split(":")[0]
        seen.append(model)
        if model == "primary":
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    s = Settings(llm_provider="gemini", gemini_api_key="k", gemini_model="primary", gemini_fallback_models="backup")
    res = llm_service.generate_json("s", "u", s, client=c)
    assert res.model == "backup" and seen == ["primary", "primary", "backup"]  # 1 retry, then fallback


def test_gemini_auth_error_is_not_retried(monkeypatch):
    from app.config import Settings
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, json={})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    s = Settings(llm_provider="gemini", gemini_api_key="bad", gemini_model="m", gemini_fallback_models="")
    with pytest.raises(LLMError):
        llm_service.generate_json("s", "u", s, client=c)
    assert len(calls) == 1


def test_dns_oserror_is_treated_as_unresolvable(monkeypatch):
    from app.utils.validators import UnresolvableHostError

    def boom(*a, **k):
        raise OSError(16, "Device or resource busy")  # what some serverless runtimes raise for NXDOMAIN

    monkeypatch.setattr("app.utils.validators.socket.getaddrinfo", boom)
    with pytest.raises(UnresolvableHostError):
        validate_url("https://nope.example")
