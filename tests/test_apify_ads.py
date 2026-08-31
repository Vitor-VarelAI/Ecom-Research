"""Mocked contract and normalization tests for the Apify ads adapter.

The adapter is the source limiter: it drops inactive ads, applies gate-1
broad relevance and deduplicates, then persists every remaining ad as a
canonical candidate with a small ``match_evidence`` shape.  Product-fit
classification (matched / needs_review / rejected) moved to
``pipeline.matching.product_ads`` — the old gate-2 tests now live in
``tests/test_product_ads.py`` under classified-candidate semantics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from pipeline.adapters import apify_ads
from pipeline.matching import product_ads
from pipeline.matching.product_ads import MATCHING_SCHEMA_VERSION


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def ad(ad_id, copy="Mochila personalizada com entrega rápida", title=None, **overrides):
    """A minimal active ad row.

    ``title`` defaults to the copy so the ad carries a strong commercial
    field matching the first query token; pass ``title`` explicitly to test
    that a body-only mention still becomes a candidate (the matcher then
    classifies it, it is never filtered here).
    """
    value = {
        "ad_archive_id": ad_id,
        "page_id": "page-1",
        "page_name": "Loja Ágil",
        "ad_creation_time": "2026-01-01T00:00:00+00:00",
        "ad_creative_bodies": [copy],
        "title": copy if title is None else title,
        "ad_snapshot_url": f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "",
        "snapshot_links": [{"url": f"https://loja.example/{ad_id or 'same'}"}],
        "publisher_platforms": ["facebook", "instagram"],
        "countries": ["PT", "ES"],
        "active_status": "ACTIVE",
    }
    value.update(overrides)
    return value


def captured_ad(
    ad_id,
    copy_text,
    *,
    title="",
    caption="",
    link_description="",
    link_url="",
    page_name="Loja Capturada",
    page_id="page-1",
    start_date=None,
    end_date=None,
    is_active=True,
    publisher_platform="facebook",
    countries=("PT", "ES"),
    snapshot_page_name=None,
    snapshot_page_id=None,
    **overrides,
):
    """A representative row from actor build 2.7.19.

    Identity/lifecycle fields are top-level (``start_date``/``end_date`` as
    Unix epoch integers, singular ``publisher_platform``,
    ``targeted_or_reached_countries``), creative fields are nested under
    ``snapshot``.  Snapshot page duplicates intentionally differ so the
    tests can prove that top-level values win.
    """
    value = {
        "ad_archive_id": ad_id,
        "is_active": is_active,
        "page_id": page_id,
        "page_name": page_name,
        "start_date": start_date,
        "end_date": end_date,
        "publisher_platform": publisher_platform,
        "targeted_or_reached_countries": list(countries),
        "ad_library_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "snapshot": {
            "body": {"text": copy_text},
            "title": title,
            "caption": caption,
            "link_description": link_description,
            "link_url": link_url,
            "page_id": snapshot_page_id if snapshot_page_id is not None else f"{page_id}-snapshot",
            "page_name": snapshot_page_name if snapshot_page_name is not None else f"{page_name} (snapshot)",
        },
    }
    value.update(overrides)
    return value


def _fetch(monkeypatch, keyword, rows):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))
    return apify_ads.fetch(keyword)


def test_captured_shape_2_7_19_snapshot_merge(monkeypatch):
    """Actor build 2.7.19 nests creative fields under ``snapshot``.

    The internal merged view must let nested copy/title/caption/link fields
    feed relevance, normalized copy, ``landing_url`` and the canonical
    ``match_evidence`` while top-level identity, lifecycle, platform and
    country fields keep precedence; epoch ``start_date`` yields the correct
    ``days_active`` and inactive rows stay filtered.

    Inactivity precedence: an explicit ``is_active=True`` survives a past
    integer ``end_date`` (the actor reports it as an observation boundary,
    not proof of inactivity); an explicit ``is_active=False`` still filters;
    a past ``end_date`` still filters when no affirmative active flag
    exists.
    """
    now = datetime.now(timezone.utc)
    start_epoch = int(now.timestamp()) - 30 * 86400
    rows = [
        captured_ad(
            "1001",
            copy_text="Mochila personalizada com nome e entrega rápida",
            title="Mochilas Personalizadas",
            caption="Envio para todo o país",
            link_description="Feita à medida, várias cores",
            link_url="https://loja.example/produto",
            start_date=start_epoch,
            end_date=int(now.timestamp()) - 2 * 86400,
        ),
        captured_ad("1002", copy_text="Caneca azul de cerâmica", start_date=start_epoch),
        captured_ad(
            "1003",
            copy_text="Mochila personalizada premium",
            start_date=start_epoch,
            is_active=False,
            end_date=int(now.timestamp()) - 5 * 86400,
        ),
    ]
    no_active_flag = captured_ad(
        "1004",
        copy_text="Mochila personalizada sem flag",
        start_date=start_epoch,
        end_date=int(now.timestamp()) - 5 * 86400,
    )
    no_active_flag.pop("is_active")
    rows.append(no_active_flag)

    result = _fetch(monkeypatch, "mochila personalizada", rows)

    assert result["candidate_count"] == 1
    assert result["filtered_out_count"] == 3
    assert result["filtered_inactive_count"] == 2
    assert result["filtered_irrelevant_count"] == 1
    assert result["relevance_policy"] == apify_ads.RELEVANCE_POLICY
    assert result["duplicates_removed"] == 0
    assert result["matching_schema_version"] == MATCHING_SCHEMA_VERSION
    assert [ad["ad_archive_id"] for ad in result["candidates"]] == ["1001"]

    top = result["candidates"][0]
    # Nested creative text participates in relevance and normalized copy.
    assert "Mochila personalizada com nome" in top["copy"]
    # Nested link_url becomes landing_url.
    assert top["landing_url"] == "https://loja.example/produto"
    # Top-level values take precedence over nested duplicates.
    assert top["advertiser"] == "Loja Capturada"
    assert top["page_id"] == "page-1"
    # Integer epoch start_date produces the correct days_active.
    assert top["days_active"] == 30
    # Singular publisher_platform and targeted_or_reached_countries preserved.
    assert top["publisher_platforms"] == ["facebook"]
    assert top["countries"] == ["PT", "ES"]
    # Canonical match_evidence carries folded strong/geo/weak text.
    assert set(top["match_evidence"]) == {"commercial", "geo", "weak"}
    assert "mochilas personalizadas" in top["match_evidence"]["commercial"]
    assert "envio para todo o pais" in top["match_evidence"]["weak"]
    assert "feita a medida" in top["match_evidence"]["commercial"]  # link_description é forte
    assert "feita a medida" not in top["match_evidence"]["geo"]  # mas não prova geografia


def test_parse_datetime_epoch_seconds_millis_and_iso():
    seconds = apify_ads._parse_datetime(1_700_000_000)
    millis = apify_ads._parse_datetime(1_700_000_000_000)
    assert seconds == millis == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    assert apify_ads._parse_datetime("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert apify_ads._parse_datetime("2026-06-01T00:00:00+0000") == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert apify_ads._parse_datetime("2026-06-01T00:00:00+00:00") == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert apify_ads._parse_datetime("não é uma data") is None
    assert apify_ads._parse_datetime(None) is None
    assert apify_ads._parse_datetime("") is None


def test_missing_token_does_not_call_or_log_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: called.append(True))

    result = apify_ads.fetch("mochila")

    assert result == {"error": "APIFY_API_TOKEN não definido em .env"}
    assert called == []


def test_request_contract_and_normalized_fields(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    captured = {}
    payload = [ad("1")]

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response(201, payload)

    monkeypatch.setattr(apify_ads.requests, "post", post)
    result = apify_ads.fetch("Mochila personalizada")

    assert captured["url"] == apify_ads.APIFY_RUN_URL
    assert captured["headers"] == {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    assert captured["params"] == {"clean": "true", "format": "json"}
    assert captured["timeout"] == (10, 135)
    assert captured["json"]["count"] == 100
    library_url = captured["json"]["urls"][0]["url"]
    parsed = parse_qs(urlparse(library_url).query)
    assert parsed["active_status"] == ["active"]
    assert parsed["ad_type"] == ["all"]
    assert parsed["country"] == ["ALL"]
    assert parsed["is_targeted_country"] == ["false"]
    assert parsed["media_type"] == ["all"]
    assert parsed["q"] == ["Mochila personalizada"]
    assert parsed["search_type"] == ["keyword_unordered"]

    assert result["source"] == "Facebook Ad Library via Apify"
    assert result["query"] == "Mochila personalizada"
    assert result["sample_limit"] == 100
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert set(("ad_archive_id", "advertiser", "page_id", "days_active", "reported_active_time",
                "link", "landing_url", "copy", "publisher_platforms", "countries",
                "match_evidence")) <= set(candidate)
    assert candidate["link"].startswith("https://www.facebook.com/ads/library/")
    assert candidate["landing_url"] == "https://loja.example/1"


def test_request_json_uses_object_url_shape(monkeypatch):
    """Regression: actor 2.7.19 input schema rejects bare-string ``urls``.

    A live call failed with "Items in input.urls at positions [0] do not
    contain valid URLs" because the payload sent ``urls: [library_url]``.
    The request JSON must be ``urls: [{"url": library_url}]`` with the same
    count and the same encoded Ad Library URL.
    """
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response(201, [ad("1")])

    monkeypatch.setattr(apify_ads.requests, "post", post)
    result = apify_ads.fetch("mochila")

    expected_url = (
        f"{apify_ads.FACEBOOK_AD_LIBRARY_URL}?active_status=active&ad_type=all&country=ALL&"
        "is_targeted_country=false&media_type=all&q=mochila&search_type=keyword_unordered"
    )
    assert captured["json"] == {"urls": [{"url": expected_url}], "count": apify_ads.SAMPLE_LIMIT}
    assert "error" not in result


def test_market_fetch_scopes_ads_to_the_requested_country(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    captured = {}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response(201, [ad("1", copy="Scented candles for your home")])

    monkeypatch.setattr(apify_ads.requests, "post", post)
    market = type("Market", (), {"ads_country": "GB"})()

    result = apify_ads.fetch("scented candles", market=market)

    request_url = captured["json"]["urls"][0]["url"]
    assert "country=GB" in request_url
    assert "is_targeted_country=true" in request_url
    assert result["evidence_scope"] == "market"
    assert result["query_country"] == "GB"


def test_filters_relevance_inactive_and_deduplicates(monkeypatch):
    rows = [
        ad("1", copy="Mochila personalizada com nome"),
        ad("1", copy="Mochila personalizada duplicada"),
        ad("2", copy="Mochila personalizada outra", ad_snapshot_url="https://www.facebook.com/ads/library/?id=2"),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad("inactive", copy="Mochila personalizada", active_status="INACTIVE"),
        ad("irrelevant", copy="Caneca azul", title="Caneca azul", page_name="Outra loja"),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)

    assert result["candidate_count"] == 3
    assert result["filtered_out_count"] == 2
    assert result["filtered_inactive_count"] == 1
    assert result["filtered_irrelevant_count"] == 1
    assert result["duplicates_removed"] == 2
    assert [ad["ad_archive_id"] for ad in result["candidates"]] == ["1", "2", ""]


def test_gate1_head_only_reaches_matcher_geo_only_noise_drops(monkeypatch):
    """Gate 1 is broad head-token source relevance only: a generic school
    backpack and an oxygen ad that mention ``mochila`` only in weak body
    copy both become canonical candidates; the matcher then decides
    (needs_review / rejected).  Geo-only noise with no head token is
    filtered by gate 1 and never becomes a candidate.  This replaces the
    old 60%-coverage gate: two-token queries never require every token
    (``personalizada`` absent is fine at the source — the matcher owns
    strong vs ambiguous vs rejected).
    """
    rows = [
        ad("generic", copy="Mochila escolar com divisões e bolso para portátil",
           title="Mochila escolar", link_url="https://loja.example/escolar"),
        ad("oxygen", copy="Concentrador de oxigénio portátil com mochila incluída",
           title="Concentrador de oxigénio portátil", link_url="https://loja.example/oxigenio"),
        ad("geo-only", copy="Descobre Portugal em sete dias", title="Férias em Portugal",
           page_name="Agência de Viagens"),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)

    assert result["candidate_count"] == 2
    assert result["filtered_irrelevant_count"] == 1
    assert [ad["ad_archive_id"] for ad in result["candidates"]] == ["generic", "oxygen"]

    classified = product_ads.classify_ads_data("mochila personalizada", result)
    labels = {c["ad_archive_id"]: c["match"]["label"] for c in classified["candidates"]}
    assert labels == {"generic": "needs_review", "oxygen": "rejected"}


def test_gate2_is_no_longer_a_filter_body_only_mention_stays_a_candidate(monkeypatch):
    """A body-only mention of the product head used to be dropped by gate 2.
    Now it is a canonical candidate and the matcher classifies it (rejected);
    the adapter never decides product fit."""
    rows = [
        ad("body-only", copy="Caneca personalizada no corpo do anúncio",
           title="Oferta da semana", link_url="https://loja.example/oferta"),
    ]
    result = _fetch(monkeypatch, "caneca personalizada", rows)
    assert result["candidate_count"] == 1
    assert result["filtered_irrelevant_count"] == 0
    candidate = result["candidates"][0]
    assert candidate["ad_archive_id"] == "body-only"
    # O head está só no corpo/copy → campos fortes não o provam.
    assert "caneca" not in candidate["match_evidence"]["commercial"]
    assert "caneca personalizada" in candidate["match_evidence"]["weak"]


def test_empty_list_is_valid_success(monkeypatch):
    result = _fetch(monkeypatch, "inexistente", {"data": []})
    assert "error" not in result
    assert result["candidate_count"] == 0
    assert result["candidates"] == []
    assert result["filtered_out_count"] == 0
    assert result["matching_schema_version"] == MATCHING_SCHEMA_VERSION


def test_sample_cap_flag_and_candidate_cap(monkeypatch):
    """O invariante do contrato: ``candidates`` está limitado a <=100 mesmo
    com payloads arbitrários para além do sample do actor."""
    rows = [ad(str(index), copy=f"Mochila premium {index}") for index in range(150)]
    result = _fetch(monkeypatch, "mochila", rows)
    assert result["candidate_count"] == 100
    assert len(result["candidates"]) == 100
    assert result["sample_capped"] is True
    assert result["raw_ads_count"] == 150


def test_source_failures_are_errors_and_only_one_post(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        raise apify_ads.requests.Timeout("not logged")

    monkeypatch.setattr(apify_ads.requests, "post", post)
    result = apify_ads.fetch("mochila")
    assert "error" in result
    assert calls == [True]

    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, ValueError("bad json")))
    assert "error" in apify_ads.fetch("mochila")


def test_successful_result_marks_global_evidence_scope(monkeypatch):
    """Resultados normalizados com sucesso declaram explicitamente que a query
    é global (country=ALL) — descoberta, não prova de fit PT/ES/FR."""
    result = _fetch(monkeypatch, "mochila", [ad("1")])
    assert "error" not in result
    assert result["evidence_scope"] == "global"
    assert result["query_country"] == "ALL"
    assert result["raw_ads_count"] == 1


def test_empty_success_result_marks_global_evidence_scope(monkeypatch):
    """Um resultado vazio bem-sucedido carrega o mesmo contrato de scope —
    células em branco significam desconhecido, não zero."""
    result = _fetch(monkeypatch, "inexistente", {"data": []})
    assert "error" not in result
    assert result["candidate_count"] == 0
    assert result["evidence_scope"] == "global"
    assert result["query_country"] == "ALL"


def test_empty_success_helper_carries_scope_fields_and_schema():
    """O contrato do helper de sucesso vazio também expõe scope/país e o
    schema de matching — sem projeções (são do matcher)."""
    empty = apify_ads._empty_success(0, 0, 0, False)
    assert empty["evidence_scope"] == "global"
    assert empty["query_country"] == "ALL"
    assert empty["filtered_inactive_count"] == 0
    assert empty["filtered_irrelevant_count"] == 0
    assert empty["candidate_count"] == 0
    assert empty["candidates"] == []
    assert empty["matching_schema_version"] == MATCHING_SCHEMA_VERSION
    assert "total_ads" not in empty  # projeções matched-only são do matcher


# ── Malformed numerics never raise (adapter contract: never-raise) ─────────

def test_parse_datetime_rejects_huge_nan_inf_and_huge_int_epochs():
    """Epoch timestamps absurdos (overflow, NaN/Inf, ints gigantes que nem
    convertem a float) devolvem None em vez de levantar — o adapter nunca
    crasha por datas malformadas."""
    assert apify_ads._parse_datetime(10**400) is None      # int demasiado grande para float
    assert apify_ads._parse_datetime(10**30) is None        # overflow no fromtimestamp
    assert apify_ads._parse_datetime(float("nan")) is None
    assert apify_ads._parse_datetime(float("inf")) is None
    assert apify_ads._parse_datetime(float("-inf")) is None
    assert apify_ads._parse_datetime(1e15) is None          # millis → ainda fora do range
    assert apify_ads._parse_datetime(True) is None
    assert apify_ads._parse_datetime({}) is None
    assert apify_ads._parse_datetime("") is None
    # Valores legítimos continuam a funcionar (segundos e millis).
    assert apify_ads._parse_datetime(1_700_000_000) is not None
    assert apify_ads._parse_datetime(1_700_000_000_000) is not None


def test_days_active_never_raises_on_nan_inf_bool_or_garbage():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)

    for bad in (float("nan"), float("inf"), float("-inf"), True, False):
        # days_active malformado: cai para a data de criação, nunca crasha.
        days = apify_ads._days_active({"days_active": bad, "ad_creation_time": "2026-05-01T00:00:00+00:00"}, now)
        assert isinstance(days, int)
        assert days == 31

    # reported_active_time com número gigante é seguro (regex só apanha dígitos).
    days = apify_ads._days_active(
        {"reported_active_time": "active for 99999999999999 days"}, now
    )
    assert isinstance(days, int)
    assert days == 99999999999999

    # Sem qualquer fonte → 0 dias (contrato never-raise).
    assert apify_ads._days_active({}, now) == 0

    # Valor válido mantém-se.
    assert apify_ads._days_active({"days_active": 42}, now) == 42


def test_malformed_numerics_in_full_fetch_never_raise(monkeypatch):
    """O contrato do adapter é never-raise: um payload com dias/épocas
    malformados produz um resultado normalizado (0 dias ou data válida),
    nunca uma exceção, e os ads continuam a entrar na coorte de candidatos."""
    now = datetime.now(timezone.utc)
    rows = [
        ad("1", copy="Mochila personalizada premium", title="Mochilas personalizadas",
           days_active=float("nan"),
           ad_delivery_start_time=10**400,
           reported_active_time="Infinity days"),
        ad("2", copy="Mochila personalizada escolar", title="Mochila personalizada",
           days_active=float("inf"),
           start_date=float("nan"),
           end_date=float("inf")),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert "error" not in result
    assert result["candidate_count"] == 2
    for item in result["candidates"]:
        assert isinstance(item["days_active"], int)
        assert item["days_active"] >= 0
        assert isinstance(item["reported_active_time"], str)


# ── match_evidence tokeniza só URLs sanitizadas (scheme/hostname/path) ─────

def test_match_evidence_never_includes_raw_url_query_fragment_or_userinfo(monkeypatch):
    """A prova construída pelo adapter (match_evidence) tokeniza apenas a URL
    sanitizada — query/fragment/userinfo crus nunca entram nos campos fortes;
    a classificação do matcher nunca vê ``?country=portugal`` nem segredos."""
    rows = [
        ad("1", copy="Camisola de Portugal em algodão premium",
           title="Camisola de algodão premium",
           link_url="https://user:pass@loja.example/produto?country=portugal&query=camisola&token=SECRET#frag"),
    ]
    result = _fetch(monkeypatch, "camisola portugal", rows)
    candidate = result["candidates"][0]
    blob = str(candidate["match_evidence"])
    assert "country=portugal" not in blob
    assert "SECRET" not in blob
    assert "user:pass" not in blob
    assert "#frag" not in blob
    assert "https://loja.example/produto" in candidate["match_evidence"]["commercial"]


# ── URLs embutidas em texto livre nunca se tornam prova nem persistem ──────


def test_embedded_url_secrets_never_persist_in_evidence_or_copy(monkeypatch):
    """Tokens/segredos dentro de URLs http(s) embutidas em title,
    link_description ou copy nunca chegam ao JSON canónico do candidato nem
    ao match_evidence: userinfo/query/fragment são removidos, o
    scheme/host/path seguro fica e o texto envolvente sobrevive."""
    rows = [
        ad(
            "1",
            copy="Compre a mochila em https://user:pass@loja.example/produto?token=SECRET1&utm_source=fb#top",
            title="Mochilas premium em https://loja.example/produto?campanha=SECRET2",
            link_description="Envio rápido — https://loja.example/outro?api_key=SECRET3",
        ),
    ]
    result = _fetch(monkeypatch, "mochila", rows)
    candidate = result["candidates"][0]
    blob = json.dumps(candidate)
    for secret in ("SECRET1", "SECRET2", "SECRET3"):
        assert secret not in blob
    assert "user:pass" not in blob
    assert "utm_source=fb" not in blob
    assert "campanha=" not in blob
    assert "#top" not in blob
    # Scheme/host/path seguros permanecem na prova forte.
    assert "https://loja.example/produto" in candidate["match_evidence"]["commercial"]
    assert "https://loja.example/outro" in candidate["match_evidence"]["commercial"]
    # O copy canónico persistido também é sanitizado (texto envolvente intacto).
    assert "SECRET1" not in candidate["copy"]
    assert "https://loja.example/produto" in candidate["copy"]
    assert "Compre a mochila em" in candidate["copy"]


def test_embedded_url_query_tokens_cannot_fabricate_matched_proof(monkeypatch):
    """Tokens em query de URLs embutidas (country=portugal, camisola) não
    podem fabricar prova forte nem geo: após sanitização a evidência só tem o
    host/path seguro, por isso um anúncio cujos únicos tokens 'camisola'/
    'portugal' vivem numa query é rejeitado pelo matcher."""
    rows = [
        ad(
            "1",
            copy="Sem mochila nem camisolas à venda",
            title="Oferta especial https://loja.example/produto?country=portugal&camisola=desconto&token=SECRET",
            link_description="",
        ),
    ]
    result = _fetch(monkeypatch, "camisola portugal", rows)
    candidate = result["candidates"][0]
    commercial = candidate["match_evidence"]["commercial"]
    assert "camisola" not in commercial
    assert "portugal" not in commercial
    assert "SECRET" not in json.dumps(candidate)
    classified = product_ads.classify_ads_data("camisola portugal", result)
    assert classified["candidates"][0]["match"]["label"] == "rejected"


# ── Query liderada por cidade: o head é o produto, nunca a cidade ──────────


def test_city_led_query_head_agreement(monkeypatch):
    """Regression: ``lisboa mochila`` deve escolher ``mochila`` como head no
    adapter (gate 1) e no matcher — Lisboa (qualificador geográfico de
    suggestions) nunca é o head nem bloqueia a classificação."""
    rows = [
        ad("1", copy="Mochila personalizada com entrega em Lisboa",
           title="Mochilas personalizadas",
           link_url="https://loja.example/mochila-personalizada"),
        ad("2", copy="Visita Lisboa com guia turístico",
           title="Férias em Lisboa", page_name="Agência de Viagens"),
    ]
    result = _fetch(monkeypatch, "lisboa mochila", rows)
    # Gate 1 mantém apenas o anúncio cujo texto tem o head (mochila).
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["ad_archive_id"] == "1"
    assert result["filtered_irrelevant_count"] == 1
    classified = product_ads.classify_ads_data("lisboa mochila", result)
    verdict = classified["candidates"][0]["match"]
    assert verdict["head_token"] == "mochila"
    assert verdict["label"] == "matched"


# ── Dedup de fallback usa a landing sanitizada (sem query de tracking) ─────


def test_no_id_ads_differing_only_by_tracking_query_deduplicate(monkeypatch):
    """Dois anúncios sem ID e sem URL da Ad Library cujas landings diferem
    só na query de tracking colapsam num candidato via a landing sanitizada
    (scheme/host/path); o sobrevivente mantém a landing URL crua intacta para
    o fetch posterior."""
    rows = [
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="",
           snapshot_links=[{"url": "https://loja.example/produto?utm_source=fb-a&gclid=111"}]),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="",
           snapshot_links=[{"url": "https://loja.example/produto?utm_source=fb-b&gclid=222"}]),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 1
    assert result["duplicates_removed"] == 1
    survivor = result["candidates"][0]
    # A URL crua do sobrevivente nunca é mutada pela deduplicação.
    assert survivor["landing_url"] == "https://loja.example/produto?utm_source=fb-a&gclid=111"


# ── Landing URL com scheme maiúsculo: aceite e mantida crua ───────────────


def test_landing_url_accepts_uppercase_scheme_and_keeps_raw_url(monkeypatch):
    """Regression review-3: ``HTTPS://``/``HTTP://`` são esquemas de landing
    válidos (case-insensitive) e a URL crua original é retida sem mutação
    para o fetch posterior — a sanitização só acontece na fronteira final de
    persistência (``sanitize_ads_data``)."""
    from pipeline.adapters.url_sanitize import sanitize_ads_data

    raw = "HTTPS://loja.example/produto?utm_source=fb&token=RAW_SECRET#frag"
    rows = [
        ad("1", copy="Mochila personalizada premium",
           snapshot_links=[{"url": raw}]),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)

    assert result["candidate_count"] == 1
    # O candidato retém a URL crua exata (scheme maiúsculo, query, fragment)
    # antes da sanitização final — o fetch consome-a como veio.
    assert result["candidates"][0]["landing_url"] == raw

    # Na fronteira final de persistência a URL é reduzida a scheme/host/path.
    clean = sanitize_ads_data(result)
    assert clean["candidates"][0]["landing_url"] == "https://loja.example/produto"
    assert "RAW_SECRET" not in json.dumps(clean)
    assert "utm_source=fb" not in json.dumps(clean)
    assert "#frag" not in json.dumps(clean)


# ── Gate 1 agrega todos os campos de texto relevantes ───────────────────────


def test_gate1_title_head_not_masked_by_unrelated_body_or_page_name(monkeypatch):
    """Regression: o gate 1 agrega *todos* os campos de texto relevantes em
    vez de ``_first_value`` — um body e um page_name não relacionados não
    podem mascarar um head válido no title/headline/link block."""
    rows = [
        ad("title-head", copy="Oferta da semana em eletrónica",
           title="Mochilas personalizadas premium", page_name="Tech Store"),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 1
    assert result["filtered_irrelevant_count"] == 0
    assert result["candidates"][0]["ad_archive_id"] == "title-head"


def test_gate1_head_only_in_embedded_url_query_is_not_relevant(monkeypatch):
    """Regression: URLs http(s) embutidas em texto livre são sanitizadas
    antes da tokenização do gate 1 — um head que só existe na query de uma
    URL embutida (``?mochila=desconto``) nunca fabrica relevância de fonte."""
    rows = [
        ad("url-query", copy="Oferta especial https://loja.example/produto?mochila=desconto",
           title=""),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 0
    assert result["filtered_irrelevant_count"] == 1
    assert result["candidates"] == []


# ── _stable_key: URLs distintas da Ad Library nunca colapsam ────────────────


def test_no_id_ads_with_distinct_ad_library_urls_stay_distinct(monkeypatch):
    """Regression: URLs distintas da Ad Library identificam creatives
    distintas mesmo sem ``ad_id`` — dois anúncios sem ID, com URLs de
    snapshot diferentes e a mesma landing sanitizada continuam dois
    candidatos (colapsá-los subcontaria creatives separadas).  O fallback de
    dedup por landing sanitizada só se aplica quando faltam ambos o ID e a
    identidade Ad Library."""
    rows = [
        ad(None, copy="Mochila personalizada criativo A",
           ad_snapshot_url="https://www.facebook.com/ads/library/?id=AAA",
           snapshot_links=[{"url": "https://loja.example/produto?utm_source=fb-a"}]),
        ad(None, copy="Mochila personalizada criativo B",
           ad_snapshot_url="https://www.facebook.com/ads/library/?id=BBB",
           snapshot_links=[{"url": "https://loja.example/produto?utm_source=fb-b"}]),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 2
    assert result["duplicates_removed"] == 0
    assert [item["ad_archive_id"] for item in result["candidates"]] == ["", ""]
    # As landings cruas coincidem na forma sanitizada, mas a identidade Ad
    # Library (URL de snapshot distinta) tem precedência na chave estável.
    assert {item["link"] for item in result["candidates"]} == {
        "https://www.facebook.com/ads/library/?id=AAA",
        "https://www.facebook.com/ads/library/?id=BBB",
    }


# ── Hosts da Ad Library: allowlist exata, nunca sufixo ──────────────────────


def test_is_ad_library_url_uses_exact_facebook_host_allowlist():
    """Regression: ``_is_ad_library_url`` usa a mesma allowlist exata de hosts
    que ``_is_facebook_library_link`` — ``evilfacebook.com`` (sufixo
    ``facebook.com``) nunca é confundido com a Ad Library pública."""
    for value in (
        "https://www.facebook.com/ads/library/?id=1",
        "https://facebook.com/ads/library/?id=1",
        "https://m.facebook.com/ads/library/?id=1",
    ):
        assert apify_ads._is_ad_library_url(value) is True, value
        assert apify_ads._is_facebook_library_link(value) is True, value
    for value in (
        "https://evilfacebook.com/ads/library/?id=1",
        "https://notfacebook.com/ads/library/?id=1",
        "https://www.facebook.com.evil.test/ads/library/?id=1",
        "https://facebook.com/landing",
        "not a url",
        "",
        42,
    ):
        assert apify_ads._is_ad_library_url(value) is False, value
        assert apify_ads._is_facebook_library_link(value) is False, value


def test_evilfacebook_library_link_is_a_landing_not_a_reference(monkeypatch):
    """Regression: um ``ad_snapshot_url``/link ``evilfacebook.com/ads/library``
    não é uma referência da Ad Library — é tratado como landing (nunca
    excluído como link de biblioteca), e o ``link`` canónico do candidato só é
    emitido para os hosts exatos da Ad Library."""
    rows = [
        ad(None, copy="Mochila personalizada premium",
           ad_snapshot_url="https://evilfacebook.com/ads/library/?id=EVIL",
           snapshot_links=[{"url": "https://evilfacebook.com/ads/library/?id=EVIL"}]),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 1
    # Nem ``_is_ad_library_url`` nem ``_is_facebook_library_link`` reconhecem
    # o host malicioso como referência pública da Ad Library.
    assert apify_ads._is_ad_library_url("https://evilfacebook.com/ads/library/?id=EVIL") is False
    assert apify_ads._is_facebook_library_link("https://evilfacebook.com/ads/library/?id=EVIL") is False
    # O link da Ad Library não é fabricado a partir do host malicioso: sem
    # ad_id e com snapshot não-Facebook, o link canónico fica vazio.
    assert result["candidates"][0]["link"] == ""
    # A landing ``evilfacebook.com/ads/library`` sobrevive crua para o fetch.
    assert result["candidates"][0]["landing_url"] == "https://evilfacebook.com/ads/library/?id=EVIL"


# ── Gate 1: o path seguro de uma landing contribui o head ───────────────────


def test_gate1_head_in_sanitized_landing_url_path_is_relevant(monkeypatch):
    """Regression: o gate 1 inclui os valores de URL de alta intenção
    sanitizados (scheme/host/path) — um head que só existe no path de uma
    landing segura (``/mochilas-personalizadas``) chega ao matcher; o mesmo
    head numa query (``?mochila=desconto``) continua a não fabricar
    relevância de fonte."""
    rows = [
        ad("url-path", copy="Oferta especial da semana", title="",
           link_url="https://loja.example/mochilas-personalizadas"),
        ad("url-query", copy="Oferta especial da semana", title="",
           link_url="https://loja.example/produto?mochila=desconto"),
    ]
    result = _fetch(monkeypatch, "mochila personalizada", rows)
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["ad_archive_id"] == "url-path"
    assert result["filtered_irrelevant_count"] == 1
    # O candidato sobrevivente mantém a landing crua para o fetch; só a prova
    # de relevância usa o path seguro.
    assert result["candidates"][0]["landing_url"] == "https://loja.example/mochilas-personalizadas"
    assert "mochila" in result["candidates"][0]["match_evidence"]["commercial"]
