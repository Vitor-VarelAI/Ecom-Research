"""Request exceptions never leak secrets: sanitized messages in adapters and
nothing reaching pack/run/digest/Telegram state."""

from __future__ import annotations

import json
import os

import requests

from pipeline import quota
from pipeline.adapters import serp_seo, trends


def _isolate_quota(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))
    monkeypatch.setattr(serp_seo.quota, "QUOTA_FILE", str(quota_file))


def test_trends_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)

    def boom(*args, **kwargs):
        raise requests.ConnectionError("url https://serpapi.com/search?api_key=SECRET broke")

    monkeypatch.setattr(trends.requests, "get", boom)
    result = trends.fetch("kw-secret")

    assert "SECRET" not in json.dumps(result)
    assert "serpapi.com" not in json.dumps(result)
    assert "ConnectionError" in result["error"]  # exception class name only
    assert "SECRET" not in caplog.text
    assert "serpapi.com" not in caplog.text
    assert quota.get_usage()["calls"] == 0  # failure before charge


def test_trends_related_call_inside_fetch_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    responses = [
        type("R", (), {"status_code": 200, "json": lambda self: {
            "interest_over_time": {"timeline_data": [
                {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
                {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
            ]},
            "interest_by_region": [],
        }})(),
    ]

    def get(*args, **kwargs):
        if responses:
            return responses.pop(0)
        raise requests.ConnectionError("api_key=SECRET on related queries")

    monkeypatch.setattr(trends.requests, "get", get)
    result = trends.fetch("kw-secret-2")

    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    # Timeseries succeeded; the failed related call degrades gracefully.
    assert result["direction"] == "estável"
    assert result["related_queries"] == []


def test_fetch_related_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.Timeout("api_key=SECRET timed out")))
    result = trends.fetch_related("kw-secret-rel")
    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    assert "Timeout" in result["error"]


def test_serp_seo_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    result = serp_seo.fetch("kw-secret-seo")
    assert "SECRET" not in json.dumps(result)
    assert "serpapi.com" not in json.dumps(result)
    assert "ConnectionError" in result["error"]
    assert "SECRET" not in caplog.text
    assert quota.get_usage()["calls"] == 0


def test_http_error_returns_sanitized_message_logs_status_only(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: type("R", (), {
        "status_code": 429,
        "text": '{"error": "api_key=SECRET echoed in body"}',
    })())
    result = serp_seo.fetch("kw-secret-429")
    # The provider message field is parsed, sanitized (credential pair
    # redacted) and bounded — never the raw body, token or URL.
    assert result["error"] == "SerpAPI SERP: HTTP 429 — [removido] echoed in body"
    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    assert "echoed in body" not in caplog.text


def test_technical_keyword_exception_is_sanitized(monkeypatch, caplog):
    import run as run_module
    from tests.fixtures.products_helpers import product

    monkeypatch.setattr(
        run_module,
        "run_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("https://provider.test?api_key=SECRET")
        ),
    )

    result = run_module._safe_run_keyword(product("mochila"))

    assert result["error"] == "Erro técnico inesperado (RuntimeError)"
    assert "SECRET" not in json.dumps(result)
    assert "provider.test" not in caplog.text


# ── End-to-end: a secret in a request exception cannot reach pack/run/digest ──

def test_secret_exception_never_reaches_pack_manifest_or_digest(monkeypatch, tmp_path, capsys):
    import run as run_module
    from tests.fixtures.products_helpers import write_registry

    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")  # other suites pop this at import
    registry = write_registry(tmp_path, ["mochila"])
    products = list(registry.products)
    ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
           "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
           "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
           "saturation": "baixa"}
    CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda kw: dict(ADS))
    monkeypatch.setattr(run_module.trends.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    monkeypatch.setattr(run_module.serp_seo.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: dict(CSV))
    generated = []
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run_module._run_all(products, send=False)

    # Run result, pack kwargs, stdout digest and persisted manifest: no secret.
    assert "SECRET" not in json.dumps(results)
    assert "SECRET" not in json.dumps(generated, default=str)
    assert "SECRET" not in capsys.readouterr().out
    store = run_module._run_store()
    run_id = run_module._compute_run_id("all", products, None)
    manifest = store.load(run_id)
    assert "SECRET" not in json.dumps(manifest)
    # The sanitized class-name message is what travels inside the result
    # instead of the raw exception text.
    assert results[0]["trends_data"]["error"] == "Erro de rede na SerpAPI (ConnectionError)"
    assert results[0]["seo_data"]["error"] == "Erro de rede na SerpAPI (SERP) (ConnectionError)"


# ── sanitize_ads_data: deep-copy + candidatos + top_ads, sem mutação ────────


def _ads_with_raw_urls():
    from tests.fixtures.ads_helpers import classified_ads_data
    raw = "https://user:pass@shop.example/produto?api_key=TOPSECRET&utm_source=fb#frag"
    ads = classified_ads_data()
    ads["top_ads"] = [{**ads["top_ads"][0], "landing_url": raw}]
    ads["candidates"] = [{**ads["candidates"][0], "landing_url": raw}]
    return ads


def test_sanitize_ads_data_deep_copies_and_sanitizes_candidates_and_top_ads():
    from pipeline.adapters.url_sanitize import sanitize_ads_data
    ads = _ads_with_raw_urls()
    snapshot = json.dumps(ads, ensure_ascii=False)

    clean = sanitize_ads_data(ads)

    # O input nunca é mutado (nem os anúncios nem os dicionários aninhados).
    assert json.dumps(ads, ensure_ascii=False) == snapshot
    assert clean is not ads
    assert clean["candidates"] is not ads["candidates"]
    assert clean["candidates"][0] is not ads["candidates"][0]
    assert clean["top_ads"][0] is not ads["top_ads"][0]
    # Ambos os sítios ficam sanitizados.
    assert clean["candidates"][0]["landing_url"] == "https://shop.example/produto"
    assert clean["top_ads"][0]["landing_url"] == "https://shop.example/produto"
    blob = json.dumps(clean, ensure_ascii=False)
    assert "TOPSECRET" not in blob and "user:pass" not in blob and "utm_source=fb" not in blob
    # A referência pública da Ad Library (link com ad-ID) sobrevive intacta —
    # só a landing_url é sanitizada.
    assert clean["candidates"][0]["link"] == "https://www.facebook.com/ads/library/?id=a1"
    assert "facebook.com/ads/library" in blob


def test_sanitize_ads_data_normalizes_invalid_landing_url_to_empty_string():
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    clean = sanitize_ads_data({"candidates": [{"landing_url": "not-a-url"}]})

    assert clean["candidates"][0]["landing_url"] == ""


def test_sanitize_ads_data_recursively_strips_secrets_from_copy_evidence_advertiser():
    """Defesa em profundidade na fronteira final de persistência: além da
    ``landing_url`` (reduzida como URL inteira), todas as outras strings dos
    objetos candidate/top_ad — ``copy``, ``advertiser`` e o
    ``match_evidence`` aninhado — têm as URLs http(s) embutidas reduzidas a
    scheme/host/path, para que payloads de adapters alternativos/malformados
    nunca persistam userinfo/query/fragment.  O input nunca é mutado e o
    ``link`` público da Ad Library é preservado."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data
    ads = _ads_with_raw_urls()
    candidate = ads["candidates"][0]
    candidate["copy"] = "Compre em https://user:pass@shop.example/x?token=COPY_SECRET#frag agora"
    candidate["advertiser"] = "Loja https://shop.example/loja?token=ADV_SECRET"
    candidate["match_evidence"] = {
        "commercial": "oferta https://shop.example/produto?token=EVID_SECRET",
        "geo": "https://shop.example/geo?token=GEO_SECRET",
        "weak": "texto https://shop.example/weak?token=WEAK_SECRET",
    }
    ads["top_ads"][0]["copy"] = "Top ad em https://user:pass@shop.example/t?token=TOP_SECRET#x"
    snapshot = json.dumps(ads, ensure_ascii=False)

    clean = sanitize_ads_data(ads)

    # O input nunca é mutado, mesmo com URLs embutidas em vários campos.
    assert json.dumps(ads, ensure_ascii=False) == snapshot
    cleaned = clean["candidates"][0]
    # copy: texto envolvente intacto, URL reduzida, segredo/userinfo/fragment
    # removidos.
    assert "Compre em" in cleaned["copy"] and "agora" in cleaned["copy"]
    assert "https://shop.example/x" in cleaned["copy"]
    assert "COPY_SECRET" not in cleaned["copy"]
    assert "user:pass" not in cleaned["copy"]
    # advertiser com URL embutida sanitizada.
    assert "ADV_SECRET" not in cleaned["advertiser"]
    assert "https://shop.example/loja" in cleaned["advertiser"]
    # match_evidence aninhado: query removida, host/path seguro preservado.
    assert "https://shop.example/produto" in cleaned["match_evidence"]["commercial"]
    assert "https://shop.example/geo" in cleaned["match_evidence"]["geo"]
    assert "https://shop.example/weak" in cleaned["match_evidence"]["weak"]
    blob = json.dumps(clean, ensure_ascii=False)
    for secret in ("EVID_SECRET", "GEO_SECRET", "WEAK_SECRET", "TOP_SECRET", "#frag"):
        assert secret not in blob
    # A referência pública da Ad Library (link com ad-ID) é preservada em
    # candidates e top_ads; a landing_url continua reduzida como URL inteira.
    assert clean["candidates"][0]["link"] == "https://www.facebook.com/ads/library/?id=a1"
    assert clean["top_ads"][0]["link"] == "https://www.facebook.com/ads/library/?id=a1"
    assert clean["candidates"][0]["landing_url"] == "https://shop.example/produto"
    assert clean["top_ads"][0]["landing_url"] == "https://shop.example/produto"


def test_sanitize_ads_data_sanitizes_advertisers_and_copy_examples_projections():
    """Regression review-3: as projeções top-level ``advertisers`` e
    ``copy_examples`` (derivadas pelo matcher a partir dos candidatos
    classificados) também passam por sanitização de URLs embutidas — um
    ``page_name``/copy com segredo classificado para a projeção nunca chega
    limpo ao pack/digest.  O input nunca é mutado e a cópia profunda
    mantém-se."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data
    from pipeline.matching.product_ads import classify_ads_data
    from tests.fixtures.ads_helpers import classified_ads_data

    secret_page = "Loja Ágil https://user:pass@shop.example/loja?token=PAGE_SECRET#frag"
    secret_copy = "Compre em https://shop.example/produto?token=COPY_EXAMPLE_SECRET#x"
    ads = classified_ads_data()
    candidate = ads["candidates"][0]
    candidate["advertiser"] = secret_page
    candidate["copy"] = secret_copy
    # O matcher deriva as projeções matched-only a partir dos candidatos.
    classified = classify_ads_data("mochila", ads)
    assert classified["advertisers"][0]["page_name"] == secret_page
    assert secret_copy in classified["copy_examples"]
    snapshot = json.dumps(classified, ensure_ascii=False)

    clean = sanitize_ads_data(classified)

    # O input nunca é mutado (nem as projeções derivadas).
    assert json.dumps(classified, ensure_ascii=False) == snapshot
    assert clean is not classified
    assert clean["advertisers"] is not classified["advertisers"]
    assert clean["copy_examples"] is not classified["copy_examples"]
    # O candidato fica limpo.
    assert "PAGE_SECRET" not in clean["candidates"][0]["advertiser"]
    assert "https://shop.example/loja" in clean["candidates"][0]["advertiser"]
    assert "COPY_EXAMPLE_SECRET" not in clean["candidates"][0]["copy"]
    # A projeção ``advertisers`` (page_name) fica limpa.
    assert clean["advertisers"][0]["page_name"] == "Loja Ágil https://shop.example/loja"
    # A projeção ``copy_examples`` (pack/digest) fica limpa.
    assert "Compre em https://shop.example/produto" in clean["copy_examples"]
    assert "COPY_EXAMPLE_SECRET" not in clean["copy_examples"][0]
    blob = json.dumps(clean, ensure_ascii=False)
    for secret in ("PAGE_SECRET", "COPY_EXAMPLE_SECRET", "user:pass", "#frag", "#x"):
        assert secret not in blob


def test_sanitize_url_returns_none_for_non_string_values():
    """Regression review-3: ``sanitize_url`` nunca levanta exceção — valores
    não-string (int/dict/list/bool/None) devolvem None."""
    from pipeline.adapters.url_sanitize import sanitize_url

    assert sanitize_url(None) is None
    assert sanitize_url(42) is None
    assert sanitize_url(True) is None
    assert sanitize_url({"url": "https://x.test/p?token=S"}) is None
    assert sanitize_url(["https://x.test/p"]) is None


def test_sanitize_ads_data_maps_non_string_and_missing_landing_url_to_empty_string():
    """Regression review-3: ``_sanitize_ad`` mapeia ``landing_url`` ausente,
    inválida e não-string para ``""`` — nunca None nem exceção."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    for bad in (None, 42, True, {"url": "https://x.test/p?token=S"}, ["https://x.test/p"], "not-a-url"):
        ads = {"candidates": [{"ad_archive_id": "a1", "landing_url": bad}]}
        assert sanitize_ads_data(ads)["candidates"][0]["landing_url"] == ""
    # Ausente: a chave passa a existir com "".
    missing = sanitize_ads_data({"candidates": [{"ad_archive_id": "a1"}]})
    assert missing["candidates"][0]["landing_url"] == ""


# ── Política final do campo ``link``: só a Ad Library pública canónica ─────


def test_sanitize_ads_data_preserves_canonical_ad_library_link():
    """Uma ligação pública da Ad Library válida (host permitido, sem
    userinfo, porta válida, path ``/ads/library``, id público) sobrevive na
    forma canónica — é a referência pública, não uma landing."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    link = "https://www.facebook.com/ads/library/?id=12345"
    clean = sanitize_ads_data({"candidates": [{"landing_url": "https://shop.example/p", "link": link}]})
    assert clean["candidates"][0]["link"] == link


def test_sanitize_ads_data_canonicalizes_ad_library_link_dropping_extra_params_and_fragment():
    """Tokens extra e fragmentos numa ligação da Ad Library são removidos —
    só o id público persiste."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    link = "https://www.facebook.com/ads/library/?id=12345&active_status=active&token=EXTRA#frag"
    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": link}]})
    assert clean["candidates"][0]["link"] == "https://www.facebook.com/ads/library/?id=12345"
    blob = json.dumps(clean, ensure_ascii=False)
    assert "EXTRA" not in blob and "active_status" not in blob and "#frag" not in blob


def test_sanitize_ads_data_removes_credential_bearing_ad_library_link():
    """Uma ligação da Ad Library com credenciais (userinfo) nunca é
    preservada — é removida por completo."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    link = "https://user:pass@www.facebook.com/ads/library/?id=12345"
    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": link}]})
    assert clean["candidates"][0]["link"] == ""
    assert "user:pass" not in json.dumps(clean)


def test_sanitize_ads_data_removes_ad_library_link_without_public_id():
    """Sem id público utilizável não há referência pública — a ligação é
    removida (``""``), nunca preservada crua."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": "https://www.facebook.com/ads/library/?active_status=active"}]})
    assert clean["candidates"][0]["link"] == ""
    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": "https://www.facebook.com/ads/library/?id="}]})
    assert clean["candidates"][0]["link"] == ""
    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": "https://www.facebook.com/ads/library"}]})
    assert clean["candidates"][0]["link"] == ""


def test_sanitize_ad_library_link_accepts_only_bounded_opaque_public_ids():
    """Regression review-4: o id público é um token opaco limitado (ASCII
    alfanumérico + ``_`` + ``-``, 1..128 chars) — fixtures existentes
    (``a1``, ids numéricos) continuam a sobreviver canónicas."""
    from pipeline.adapters.url_sanitize import sanitize_ad_library_link

    for ad_id in ("a1", "12345", "0", "ABC-123_xY"):
        link = f"https://www.facebook.com/ads/library/?id={ad_id}"
        assert sanitize_ad_library_link(link) == link, ad_id
    # Limite superior exato: 128 chars aceite, 129 rejeitado.
    ok = "a" * 128
    assert sanitize_ad_library_link(f"https://www.facebook.com/ads/library/?id={ok}") == f"https://www.facebook.com/ads/library/?id={ok}"
    assert sanitize_ad_library_link(f"https://www.facebook.com/ads/library/?id={'a' * 129}") == ""


def test_sanitize_ad_library_link_rejects_url_credential_and_encoded_ids():
    """Regression review-4: ids tipo URL/credenciais — crus ou
    percent-encoded — nunca sobrevivem como referência pública; a ligação é
    removida por completo."""
    from pipeline.adapters.url_sanitize import sanitize_ad_library_link

    evil_ids = (
        # URL-like com credenciais (cru e percent-encoded).
        "https://user:pass@evil.test/?token=SECRET",
        "https%3A%2F%2Fuser%3Apass%40evil.test%2F%3Ftoken%3DSECRET",
        # Credenciais/paths/queries disfarçados de id.
        "user:pass@evil.test",
        "evil.test/?token=SECRET",
        "a b",  # espaço (decodificado de %20 ou +)
        "a%2Fb",  # slash codificado
        "123;DROP TABLE",
    )
    for evil_id in evil_ids:
        link = f"https://www.facebook.com/ads/library/?id={evil_id}"
        assert sanitize_ad_library_link(link) == "", evil_id


def test_sanitize_ads_data_drops_malicious_ad_library_ids_without_trace():
    """Regression review-4: na fronteira final de persistência, um link da Ad
    Library com id URL-like/credencial (cru ou percent-encoded) é removido por
    completo — nenhum segredo nem link sobrevive no blob persistido."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    evil_links = (
        "https://www.facebook.com/ads/library/?id=https://user:pass@evil.test/?token=SECRET",
        "https://www.facebook.com/ads/library/?id=https%3A%2F%2Fuser%3Apass%40evil.test%2F%3Ftoken%3DSECRET",
    )
    for evil_link in evil_links:
        clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": evil_link}]})
        assert clean["candidates"][0]["link"] == "", evil_link
        blob = json.dumps(clean, ensure_ascii=False)
        assert "SECRET" not in blob, evil_link
        assert "evil.test" not in blob, evil_link
        assert "user:pass" not in blob, evil_link


def test_sanitize_ads_data_sanitizes_generic_external_link():
    """Um ``link`` externo genérico não é preservado cegamente — é reduzido
    pela sanitização ordinária (scheme/host/path), sem userinfo/query/
fragment."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    link = "https://user:pass@shop.example/produto?token=SECRET&utm=fb#frag"
    clean = sanitize_ads_data({"candidates": [{"landing_url": "", "link": link}]})
    assert clean["candidates"][0]["link"] == "https://shop.example/produto"
    blob = json.dumps(clean, ensure_ascii=False)
    assert "SECRET" not in blob and "user:pass" not in blob and "utm=fb" not in blob and "#frag" not in blob


def test_run_keyword_persists_only_sanitized_candidate_landing_urls(monkeypatch, tmp_path):
    """O result e o pack JSON nunca carregam a URL crua de um candidato
    (userinfo/query/fragment) — candidates e top_ads são sanitizados na
    mesma mudança antes de qualquer serialização."""
    import run as run_module
    from tests.fixtures.ads_helpers import classified_ads_data
    from tests.fixtures.products_helpers import product

    packs_dir, data_dir = tmp_path / "packs", tmp_path / "data"
    packs_dir.mkdir()
    data_dir.mkdir()
    raw_url = "https://shop.example/produto?api_key=TOPSECRET&utm_source=fb#frag"

    def fake_paths(keyword, run_date=None, mode=None, run_id=None, product_id=None):
        from datetime import date as _date
        run_date = run_date or _date.today()
        slug = run_module.pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        return str(packs_dir / f"{base}.md"), str(data_dir / f"{base}.json")

    ads = classified_ads_data()
    ads["top_ads"] = [{**ads["top_ads"][0], "landing_url": raw_url}]
    ads["candidates"] = [{**ads["candidates"][0], "landing_url": raw_url}]
    consumed = []

    def fetch_prices(data):
        consumed.append(data["top_ads"][0]["landing_url"])
        return {"price_range": {"min": 10.0, "max": 20.0, "currency": "EUR", "sources": 1}}

    monkeypatch.setattr(run_module.pack, "_pack_paths", fake_paths)
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda kw: ads)
    monkeypatch.setattr(run_module.trends, "fetch", lambda kw: {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []})
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda kw: {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"})
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", fetch_prices)
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"volume": 1000, "cpc": 1, "related_keywords": []})

    result = run_module.run_keyword(product("mochila"))

    # O fetch consumiu a URL crua; o que é persistido está sanitizado.
    assert consumed == [raw_url]
    persisted = result["ads_data"]["candidates"][0]["landing_url"]
    assert persisted == "https://shop.example/produto"
    json_path = result["md_path"].replace("/packs/", "/data/").replace(".md", ".json")
    raw_pack = open(json_path, encoding="utf-8").read()
    assert "https://shop.example/produto" in raw_pack
    assert "TOPSECRET" not in raw_pack
    assert "api_key=TOPSECRET" not in raw_pack
    assert "utm_source=fb" not in raw_pack


# ── sanitize_embedded_urls: URLs embutidas em texto livre ───────────────────


def test_sanitize_embedded_urls_strips_userinfo_query_fragment_keeps_text():
    from pipeline.adapters.url_sanitize import sanitize_embedded_urls

    text = (
        "Compre em https://user:pass@loja.example/produto?token=SECRET&utm=x#frag "
        "— envio grátis. Também HTTPS://loja.example/outro?api_key=K#top hoje"
    )
    clean = sanitize_embedded_urls(text)

    assert "SECRET" not in clean
    assert "user:pass" not in clean
    assert "utm=x" not in clean
    assert "api_key=K" not in clean
    assert "#frag" not in clean and "#top" not in clean
    # Scheme/host/path seguros preservados; texto envolvente intacto.
    assert "Compre em" in clean
    assert "https://loja.example/produto" in clean
    assert "https://loja.example/outro" in clean
    assert "envio grátis" in clean and "hoje" in clean


def test_sanitize_embedded_urls_drops_invalid_and_ad_library_urls():
    from pipeline.adapters.url_sanitize import sanitize_embedded_urls

    text = (
        "Veja https://www.facebook.com/ads/library/?id=12345 ou https:// "
        "compre em https://loja.example/produto."
    )
    clean = sanitize_embedded_urls(text)

    # Ad Library e URLs inválidas nunca deixam o conteúdo cru para trás.
    assert clean == "Veja  ou  compre em https://loja.example/produto."


def test_sanitize_embedded_urls_leaves_non_http_text_alone():
    from pipeline.adapters.url_sanitize import sanitize_embedded_urls

    text = "mailto:user@example.com e ftp://user:pass@host/x não são http(s)."
    assert sanitize_embedded_urls(text) == text
