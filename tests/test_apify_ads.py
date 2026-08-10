"""Mocked contract and normalization tests for the Apify ads adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from pipeline.adapters import apify_ads


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

    ``title`` defaults to the copy so the ad carries a commercial-intent
    field matching the first query token; pass ``title`` explicitly to test
    gate-2 rejection (body/copy alone must not satisfy relevance).
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


def test_captured_shape_2_7_19_snapshot_merge(monkeypatch):
    """Actor build 2.7.19 nests creative fields under ``snapshot``.

    The internal merged view must let nested copy/title/caption/link fields
    feed relevance, normalized copy and ``landing_url`` while top-level
    identity, lifecycle, platform and country fields keep precedence;
    epoch ``start_date`` yields the correct ``days_active`` and inactive
    rows stay filtered.

    Inactivity precedence: an explicit ``is_active=True`` survives a past
    integer ``end_date`` (the actor reports it as an observation boundary,
    not proof of inactivity); an explicit ``is_active=False`` still filters;
    a past ``end_date`` still filters when no affirmative active flag
    exists.
    """
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
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
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("mochila personalizada")

    assert result["total_ads"] == 1
    assert result["filtered_out_count"] == 3
    assert result["filtered_inactive_count"] == 2
    assert result["filtered_irrelevant_count"] == 1
    assert result["relevance_policy"] == apify_ads.RELEVANCE_POLICY
    assert result["duplicates_removed"] == 0
    assert result["ad_ids"] == ["1001"]

    top = result["top_ads"][0]
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
    assert result["total_ads"] == 1
    assert result["ad_ids"] == ["1"]
    top = result["top_ads"][0]
    assert set(("ad_archive_id", "advertiser", "page_id", "days_active", "reported_active_time", "link", "landing_url", "copy", "publisher_platforms", "countries")) <= set(top)
    assert top["link"].startswith("https://www.facebook.com/ads/library/")
    assert top["landing_url"] == "https://loja.example/1"


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


def test_filters_relevance_inactive_and_deduplicates(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    rows = [
        ad("1", copy="Mochila personalizada com nome"),
        ad("1", copy="Mochila personalizada duplicada"),
        ad("2", copy="Mochila personalizada outra", ad_snapshot_url="https://www.facebook.com/ads/library/?id=2"),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad("inactive", copy="Mochila personalizada", active_status="INACTIVE"),
        ad("irrelevant", copy="Caneca azul", title="Caneca azul", page_name="Outra loja"),
    ]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("mochila personalizada")

    assert result["total_ads"] == 3
    assert result["filtered_out_count"] == 2
    assert result["filtered_inactive_count"] == 1
    assert result["filtered_irrelevant_count"] == 1
    assert result["duplicates_removed"] == 2
    assert result["ad_ids"] == ["1", "2"]
    assert result["advertisers"][0]["active_ads"] == 3


def test_long_query_requires_sixty_percent_and_single_token_requires_token(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    rows = [
        ad("exact", copy="Saco vermelho premium"),
        ad("two-of-three", copy="Saco vermelho em promoção", page_name="Loja"),
        ad("one-of-three", copy="Saco azul", page_name="Loja"),
        ad("single-no", copy="Produto azul", page_name="Loja"),
    ]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("saco vermelho premium")

    assert result["ad_ids"] == ["exact", "two-of-three"]
    assert result["filtered_out_count"] == 2


def test_empty_list_is_valid_success_and_sample_cap_reaches_alta(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, {"data": []}))
    empty = apify_ads.fetch("inexistente")
    assert "error" not in empty
    assert empty["total_ads"] == 0
    assert empty["filtered_out_count"] == 0

    rows = [ad(str(index), copy=f"Mochila premium {index}") for index in range(51)]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))
    saturated = apify_ads.fetch("mochila")
    assert saturated["total_ads"] == 51
    assert saturated["saturation"] == "alta"
    assert saturated["sample_capped"] is False


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
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, [ad("1")]))
    result = apify_ads.fetch("mochila")
    assert "error" not in result
    assert result["evidence_scope"] == "global"
    assert result["query_country"] == "ALL"
    assert "global_market_proven" in result


def test_empty_success_result_marks_global_evidence_scope(monkeypatch):
    """Um resultado vazio bem-sucedido carrega o mesmo contrato de scope —
    células em branco significam desconhecido, não zero."""
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, {"data": []}))
    empty = apify_ads.fetch("inexistente")
    assert "error" not in empty
    assert empty["total_ads"] == 0
    assert empty["evidence_scope"] == "global"
    assert empty["query_country"] == "ALL"
    assert empty["global_market_proven"] is False


def test_empty_success_helper_carries_scope_fields():
    """O contrato do helper de sucesso vazio também expõe scope/país."""
    empty = apify_ads._empty_success(0, 0, 0, False)
    assert empty["evidence_scope"] == "global"
    assert empty["query_country"] == "ALL"
    assert empty["filtered_inactive_count"] == 0
    assert empty["filtered_irrelevant_count"] == 0
    assert empty["relevance_policy"] == apify_ads.RELEVANCE_POLICY


def test_global_market_proof_is_counted_over_global_retained_ads(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    rows = [ad(str(index), copy="Mochila premium", title="Mochilas premium") for index in range(5)]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("mochila")

    assert result["total_ads"] == 5
    assert result["global_market_proven"] is True
    assert result["evidence_scope"] == "global"
    assert result["query_country"] == "ALL"


class TestHighIntentGate:
    """Segunda gate fail-closed: product-head token da query num campo de
    intenção comercial (title/headline/link block/URL de destino).

    O corpo/copy sozinho nunca satisfaz a gate — o defeito provado para
    ``tapete yoga`` era ficção, cursos, mentoring e um macacão cujo corpo
    apenas mencionava um tapete de yoga a passar a gate de cobertura 60%%.
    """

    def _fetch(self, monkeypatch, keyword, rows):
        monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
        monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))
        return apify_ads.fetch(keyword)

    def test_tapete_fiction_course_and_jumpsuit_rejected_real_mat_retained(self, monkeypatch):
        rows = [
            # Ficção espanhola: o corpo menciona tapete, title/URL não.
            ad("fiction", copy="Los papeles del divorcio estaban sobre la encimera. Me temblaban los dedos mientras recorría el borde del tapete de yoga del salón.",
               title="Capítulo 3: El secreto", link_url="https://novela.example/capitulo-3"),
            # Curso de professores: o corpo menciona tapete, o title só yoga.
            ad("course", copy="Ser professora de yoga não é sobre ter uma vida perfeita. É sobre usar as ferramentas no seu tapete e saber guiar outras pessoas.",
               title="Formação de professores de yoga", link_url="https://curso.example/formacao"),
            # Mentoria 1:1: corpo menciona tapete.
            ad("mentoring", copy="Guio-te no teu tapete de yoga com método e dedicação.",
               title="Mentoria 1:1 de yoga", link_url="https://mentor.example/sessao"),
            # Macacão (jumpsuit): o corpo fala de yoga e tapete, o produto é roupa.
            ad("jumpsuit", copy="O Macacão Way acompanha os movimentos de yoga e te deixa livre para respirar no teu tapete.",
               title="Macacão Way Lycra", link_url="https://moda.example/macacao-way"),
            # Tapete real: o title tem o token (plural por prefixo).
            ad("mat-title", copy="Tapete de yoga antiderrapante 6mm com alça",
               title="Tapetes de yoga aveludado — oferta", link_url="https://loja.example/tapetes-de-yoga"),
            # Tapete real: só a URL de destino tem o token (``/tapetes-de-yoga-aveludado/``).
            ad("mat-url", copy="Tapete de yoga aveludado premium antiderrapante",
               title="Oferta especial da semana", link_url="https://loja.example/tapetes-de-yoga-aveludado/"),
            # App de aulas: title tem yoga mas nunca tapete.
            ad("app", copy="Aulas de yoga online para todos os níveis",
               title="App Yoga Life", link_url="https://app.example/yoga"),
        ]
        result = self._fetch(monkeypatch, "tapete yoga", rows)

        assert result["ad_ids"] == ["mat-title", "mat-url"]
        assert result["total_ads"] == 2
        assert result["filtered_out_count"] == 5
        assert result["filtered_inactive_count"] == 0
        assert result["filtered_irrelevant_count"] == 5
        assert result["relevance_policy"] == apify_ads.RELEVANCE_POLICY
        landing_urls = {item["landing_url"] for item in result["top_ads"]}
        assert "https://loja.example/tapetes-de-yoga-aveludado/" in landing_urls

    def test_commercial_examples_retained(self, monkeypatch):
        cases = [
            ("caneca personalizada", "Caneca personalizada com nome", "Canecas personalizadas com nome", "https://loja.example/canecas-personalizadas"),
            ("velas aromáticas", "Velas aromáticas naturais de soja", "Velas aromáticas premium", "https://loja.example/velas-aromaticas"),
            ("mochila personalizada", "Mochila personalizada escolar", "Mochilas personalizadas escolares", "https://loja.example/mochilas-personalizadas"),
        ]
        for keyword, copy, title, url in cases:
            result = self._fetch(
                monkeypatch,
                keyword,
                [ad("1", copy=copy, title=title, snapshot_links=[{"url": url}])],
            )
            assert result["ad_ids"] == ["1"], keyword
            assert result["total_ads"] == 1, keyword
            assert result["filtered_irrelevant_count"] == 0, keyword

    def test_accents_case_and_singular_plural(self, monkeypatch):
        # Accent/case folding: a query com acentos casa com title sem acentos.
        result = self._fetch(
            monkeypatch,
            "velas aromáticas",
            [ad("1", copy="Velas aromáticas de soja", title="VELAS AROMATICAS DE SOJA")],
        )
        assert result["total_ads"] == 1
        # Singular na query casa com plural no title (prefixo ``caneca``~``canecas``).
        result = self._fetch(
            monkeypatch,
            "caneca personalizada",
            [ad("1", copy="Caneca personalizada premium", title="Canecas personalizadas premium")],
        )
        assert result["total_ads"] == 1
        # Plural na query casa com singular no title (prefixo ``velas``~``vela``).
        result = self._fetch(
            monkeypatch,
            "velas aromáticas",
            [ad("1", copy="Velas aromáticas artesanais", title="Vela aromática artesanal")],
        )
        assert result["total_ads"] == 1

    def test_body_mention_alone_is_rejected(self, monkeypatch):
        # Gate 1 passa (cobertura 100%% no corpo) mas nenhum campo de intenção
        # comercial tem o product head → gate 2 rejeita.
        result = self._fetch(
            monkeypatch,
            "caneca personalizada",
            [ad("1", copy="Caneca personalizada no corpo do anúncio", title="Oferta da semana", link_url="https://loja.example/oferta")],
        )
        assert result["total_ads"] == 0
        assert result["filtered_irrelevant_count"] == 1
        assert result["filtered_inactive_count"] == 0

    def test_pasta_vs_pastelaria_shared_prefix_rejected(self, monkeypatch):
        """Regressão do defeito de relevância ``pasta``~``pastelaria``: o corpo
        contém literalmente ``pasta`` (gate 1 passa), mas os campos de intenção
        comercial (title/URL) só têm ``pastelaria`` — o prefixo partilhado não
        satisfaz a gate 2; massas reais (``pastas`` no title) continuam a entrar
        via variante de um só ``s``."""
        rows = [
            # Pastelaria: o corpo menciona literalmente ``pasta``, o title/URL só ``pastelaria``.
            ad("pastelaria",
               copy="Receita de pasta com legumes frescos e entrega rápida",
               title="Pastelaria Artesanal — Bolos e Doces",
               link_url="https://pastelaria.example/bolos"),
            # Massa real: o corpo tem ``pasta`` e o title a variante plural ``pastas``.
            ad("pastas",
               copy="Pasta fresca artesanal cozida em 3 minutos",
               title="Pastas frescas artesanais — oferta",
               link_url="https://massa.example/pastas-frescas"),
        ]
        result = self._fetch(monkeypatch, "pasta", rows)
        assert result["ad_ids"] == ["pastas"]
        assert result["total_ads"] == 1
        assert result["filtered_irrelevant_count"] == 1
        assert result["filtered_inactive_count"] == 0

    def test_geographic_qualifier_blocks_generic_and_keeps_real_portugal_shirt(self, monkeypatch):
        """Defeito provado para ``camisola portugal``: camisas de compressão e
        de clubes (Benfica/Porto) cujo title/URL não é Portugal passavam a
        gate só porque o qualificador geográfico aparecia no corpo ou na
        link_description ('entrega em Portugal').  O qualificador ``portugal``
        agora só pode ser provado nos campos geo-proof: product
        title/headline, ad creative link titles e path/domínio da URL de
        destino — body, copy, advertiser/page name, caption e
        link_description/ad creative link descriptions nunca o satisfazem.
        """
        rows = [
            # Camisa de compressão genérica: title tem camisola, Portugal só no corpo.
            ad("compression",
               copy="Camisola de compressão com envio para Portugal e devoluções fáceis",
               title="Camisola de compressão para treino",
               link_url="https://moda.example/camisola-compressao"),
            # Camisola do Benfica: Portugal só na link_description (entrega).
            ad("benfica",
               copy="Camisola oficial do Benfica com entrega em Portugal",
               title="Camisola oficial do Benfica",
               link_description="Entrega em Portugal em 48h",
               link_url="https://slb.example/camisola-benfica"),
            # Camisola do Porto: Portugal só no corpo (envio).
            ad("porto",
               copy="Camisola do FC Porto com envio para Portugal",
               title="Camisola FC Porto 24/25",
               link_url="https://porto.example/camisola-fc-porto"),
            # Camisola real de Portugal: qualificador no title.
            ad("title-pt",
               copy="Camisola de Portugal premium em algodão",
               title="Camisolas De Portugal",
               link_url="https://loja.example/camisas"),
            # Camisola real: qualificador só na link_description → rejeitada.
            ad("desc-pt",
               copy="Camisola de Portugal oficial",
               title="Camisola oficial da seleção",
               link_description="Camisola da seleção de Portugal",
               link_url="https://loja.example/selecao"),
            # Camisola real: qualificador e produto na URL de destino.
            ad("url-pt",
               copy="Camisola de Portugal oficial",
               title="Oferta da semana",
               link_url="https://sporta.example/collections/portugal/products/camisola-portugal"),
        ]
        result = self._fetch(monkeypatch, "camisola portugal", rows)

        assert result["ad_ids"] == ["title-pt", "url-pt"]
        assert result["total_ads"] == 2
        assert result["filtered_out_count"] == 4
        assert result["filtered_irrelevant_count"] == 4
        assert result["filtered_inactive_count"] == 0
        assert result["relevance_policy"] == apify_ads.RELEVANCE_POLICY
        landing_urls = {item["landing_url"] for item in result["top_ads"]}
        assert "https://sporta.example/collections/portugal/products/camisola-portugal" in landing_urls

    def test_geographic_requirement_is_driven_by_the_query_not_the_ad(self, monkeypatch):
        """Sem qualificador geográfico na query, o comportamento head-token
        mantém-se: uma camisola de compressão genérica (mesmo a mencionar
        Portugal no corpo) continua a passar como antes."""
        result = self._fetch(
            monkeypatch,
            "camisola",
            [ad("1",
               copy="Camisola de compressão com envio para Portugal",
               title="Camisolas de compressão",
               link_url="https://moda.example/camisolas")],
        )
        assert result["ad_ids"] == ["1"]
        assert result["total_ads"] == 1
        assert result["filtered_irrelevant_count"] == 0

    def test_geography_first_keyword_requires_product_head_in_commercial_fields(self, monkeypatch):
        """A geography-first query must use ``camisolas`` as its head.

        Both rows satisfy the broad relevance gate, but a Portugal travel ad
        whose commercial title only proves Portugal must not pass; the actual
        Portugal shirt proves both product head and geographic qualifier.
        """
        result = self._fetch(
            monkeypatch,
            "portugal camisolas",
            [
                ad(
                    "unrelated-portugal",
                    copy="Viaja por Portugal; camisolas não incluídas no passeio",
                    title="Descobre Portugal em sete dias",
                    link_url="https://travel.example/portugal",
                ),
                ad(
                    "actual-shirt",
                    copy="Camisolas de Portugal em algodão premium",
                    title="Camisolas de Portugal",
                    link_url="https://shop.example/portugal/camisolas",
                ),
            ],
        )

        assert result["ad_ids"] == ["actual-shirt"]
        assert result["total_ads"] == 1
        assert result["filtered_irrelevant_count"] == 1

    def test_geographic_qualifier_accent_and_case_folding(self, monkeypatch):
        # Case: PORTUGAL na query casa com portugal no title.
        result = self._fetch(
            monkeypatch,
            "camisola PORTUGAL",
            [ad("1", copy="Camisola de Portugal", title="Camisolas de portugal")],
        )
        assert result["total_ads"] == 1
        # Acento: França na query casa com FRANCA (folded) no title.
        result = self._fetch(
            monkeypatch,
            "camisola França",
            [ad("1", copy="Camisola de França", title="Camisola oficial da seleção de França")],
        )
        assert result["total_ads"] == 1
        # Qualificador no path da URL de destino → aceite.
        result = self._fetch(
            monkeypatch,
            "camisola França",
            [ad("1", copy="Camisola de França", title="Oferta da semana",
               link_url="https://sporta.example/franca/camisola-selecao")],
        )
        assert result["total_ads"] == 1
        # Qualificador só na link_description (contexto/entrega) → rejeitado.
        result = self._fetch(
            monkeypatch,
            "camisola França",
            [ad("1", copy="Camisola de compressão com entrega em França", title="Camisola de compressão",
               link_description="Entrega em França")],
        )
        assert result["total_ads"] == 0
        assert result["filtered_irrelevant_count"] == 1
        # Acento no nome do país no ad sem qualificador no title/URL → rejeitado.
        result = self._fetch(
            monkeypatch,
            "camisola brasil",
            [ad("1", copy="Camisola com envio para o Brasil", title="Camisola de compressão")],
        )
        assert result["total_ads"] == 0
        assert result["filtered_irrelevant_count"] == 1

    def test_non_geographic_modifier_not_required_in_high_intent(self, monkeypatch):
        """Modificadores não geográficos (personalizada, aromáticas) nunca são
        exigidos nos campos de intenção comercial — mochila/caneca/velas
        mantêm o comportamento fail-closed mas utilizável."""
        result = self._fetch(
            monkeypatch,
            "caneca personalizada",
            [ad("1", copy="Caneca personalizada com nome e entrega rápida",
               title="Canecas da loja", link_url="https://loja.example/canecas")],
        )
        assert result["ad_ids"] == ["1"]
        assert result["filtered_irrelevant_count"] == 0
        result = self._fetch(
            monkeypatch,
            "velas aromáticas",
            [ad("1", copy="Velas aromáticas naturais de soja", title="Velas artesanais",
               link_url="https://loja.example/velas")],
        )
        assert result["ad_ids"] == ["1"]
        assert result["filtered_irrelevant_count"] == 0
        result = self._fetch(
            monkeypatch,
            "mochila personalizada",
            [ad("1", copy="Mochila personalizada escolar", title="Mochilas escolares",
               link_url="https://loja.example/mochilas")],
        )
        assert result["ad_ids"] == ["1"]
        assert result["filtered_irrelevant_count"] == 0

    def test_geographic_qualifiers_set_is_explicit(self):
        assert apify_ads.GEOGRAPHIC_QUALIFIERS == frozenset({"portugal", "espanha", "franca", "brasil"})

    def test_high_intent_field_list_is_explicit(self):
        assert apify_ads.HIGH_INTENT_TEXT_FIELDS == (
            "title",
            "headline",
            "link_description",
            "ad_creative_link_titles",
            "ad_creative_link_descriptions",
        )
        assert "landing_url" in apify_ads.HIGH_INTENT_URL_FIELDS
        assert "body" not in apify_ads.HIGH_INTENT_TEXT_FIELDS
        assert "copy" not in apify_ads.HIGH_INTENT_TEXT_FIELDS
        # Geo-proof fields são mais estritos: link_description/ad creative link
        # descriptions/caption nunca provam geografia.
        assert apify_ads.GEO_PROOF_TEXT_FIELDS == ("title", "headline", "ad_creative_link_titles")
        assert "link_description" not in apify_ads.GEO_PROOF_TEXT_FIELDS
        assert "ad_creative_link_descriptions" not in apify_ads.GEO_PROOF_TEXT_FIELDS
        assert "caption" not in apify_ads.GEO_PROOF_TEXT_FIELDS
        assert "body" not in apify_ads.GEO_PROOF_TEXT_FIELDS


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
    nunca uma exceção, e os ads continuam a entrar na coorte."""
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
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
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(200, rows))
    result = apify_ads.fetch("mochila personalizada")
    assert "error" not in result
    assert result["total_ads"] == 2
    for item in result["top_ads"]:
        assert isinstance(item["days_active"], int)
        assert item["days_active"] >= 0
        assert isinstance(item["reported_active_time"], str)


# ── Relevance tokenizes only sanitized URL scheme/hostname/path ────────────

class TestSanitizedUrlRelevanceProof:
    """A prova de relevância (product head + geografia) tokeniza só a URL
    sanitizada (scheme/hostname/path) — nunca a query/fragment crus: uma
    landing genérica com ``?country=portugal&query=camisola`` não satisfaz
    prova de produto nem de geografia."""

    def _fetch(self, monkeypatch, keyword, rows):
        monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
        monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(200, rows))
        return apify_ads.fetch(keyword)

    def test_query_parameters_never_satisfy_product_or_geo_proof(self, monkeypatch):
        """O corpo prova ambos os tokens (gate 1) e o title prova o product
        head; só o URL query contém ``portugal``/``camisola`` — a landing
        genérica deve ser rejeitada pela gate 2 (geo não provado)."""
        rows = [
            ad("1", copy="Camisola de Portugal em algodão premium",
               title="Camisola de algodão premium",
               link_url="https://loja.example/produto?country=portugal&query=camisola"),
        ]
        result = self._fetch(monkeypatch, "camisola portugal", rows)
        assert result["total_ads"] == 0
        assert result["filtered_irrelevant_count"] == 1

    def test_fragment_and_userinfo_never_satisfy_proof(self, monkeypatch):
        rows = [
            ad("1", copy="Camisola de Portugal premium",
               title="Oferta da semana",
               link_url="https://loja.example/item?q=camisola#portugal"),
        ]
        result = self._fetch(monkeypatch, "camisola portugal", rows)
        assert result["total_ads"] == 0
        assert result["filtered_irrelevant_count"] == 1

    def test_sanitized_url_path_still_proves_product_and_geography(self, monkeypatch):
        """O path sanitizado continua a contar: ``/portugal/camisola`` prova
        os dois tokens mesmo com query a mais na URL crua."""
        rows = [
            ad("1", copy="Camisola de Portugal premium",
               title="Oferta da semana",
               link_url="https://loja.example/portugal/camisola?utm_source=fb&country=ES"),
        ]
        result = self._fetch(monkeypatch, "camisola portugal", rows)
        assert result["ad_ids"] == ["1"]
        assert result["total_ads"] == 1

    def test_sanitized_hostname_proves_geography(self, monkeypatch):
        rows = [
            ad("1", copy="Camisola de Portugal premium",
               title="Oferta da semana",
               link_url="https://portugal.example/camisola?x=1"),
        ]
        result = self._fetch(monkeypatch, "camisola portugal", rows)
        assert result["total_ads"] == 1
