from pipeline.suggestions import (
    extract_keyword_suggestions,
    filter_relevant_related_queries,
    is_product_related,
    product_head_token,
)


def test_breakout_then_numeric_and_advisory_status():
    result = extract_keyword_suggestions(
        "mochila personalizada",
        {
            "related_queries": [
                {"query": "mochila barata", "value": 900},
                {"query": "mochila premium", "value": "Breakout"},
                {"query": "mochila escolar", "extracted_value": 1200},
            ]
        },
    )
    assert [item["keyword"] for item in result] == ["mochila premium", "mochila escolar", "mochila barata"]
    assert all(item["status"] == "proposed" for item in result)
    assert result[0]["score"] == "Breakout"


def test_removes_source_duplicates_urls_interrogatives_and_empty():
    result = extract_keyword_suggestions(
        "Caneca personalizada",
        {
            "related_queries": [
                {"keyword": "caneca personalizada", "score": 100},
                {"keyword": "caneca personalizada", "score": 90},
                {"keyword": "https://example.com", "score": 1000},
                {"keyword": "Como fazer caneca", "score": 800},
                {"keyword": "Caneca personalizada", "score": 700},
                {"keyword": "caneca premium", "score": 20},
            ]
        },
    )
    assert [item["keyword"] for item in result] == ["caneca premium"]


def test_error_or_limit_returns_empty():
    assert extract_keyword_suggestions("x", {"error": "offline"}) == []
    assert extract_keyword_suggestions("x", {"related_queries": [{"keyword": "one", "score": 1}]}, limit=0) == []


# ── Product-relevance filter ────────────────────────────────────────────────


def test_filter_keeps_only_queries_containing_the_product_head():
    """Defect regression: ``camisolas portugal`` must keep ``camisola portugal
    preta`` and drop the cycling event that shares only the geographic token."""
    filtered = filter_relevant_related_queries(
        "camisolas portugal",
        {
            "related_queries": [
                {"keyword": "camisola portugal preta", "score": 100},
                {"keyword": "volta a portugal 2026", "score": 90},
                {"keyword": "camisolas de algodão", "score": 80},
            ]
        },
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == [
        "camisola portugal preta",
        "camisolas de algodão",
    ]


def test_filter_head_selection_skips_geo_and_stopwords_and_falls_back():
    assert product_head_token("camisolas portugal") == "camisolas"
    assert product_head_token("portugal camisolas") == "camisolas"
    assert product_head_token("tapete de yoga") == "tapete"
    assert product_head_token("caneca personalizada") == "caneca"
    # All tokens excluded → fallback to the first token.
    assert product_head_token("de portugal") == "de"
    # No tokens at all → None.
    assert product_head_token("") is None
    assert product_head_token("!!!") is None


def test_filter_singular_plural_equivalence_in_both_directions():
    # Head plural, candidate singular: kept.
    filtered = filter_relevant_related_queries(
        "camisolas", {"related_queries": [{"keyword": "camisola preta", "score": 10}]}
    )
    assert len(filtered["related_queries"]) == 1
    # Head singular, candidate plural: kept.
    filtered = filter_relevant_related_queries(
        "caneca",
        {"related_queries": [{"keyword": "canecas", "score": 10}, {"keyword": "caneca", "score": 9}]},
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == ["canecas", "caneca"]
    # Short stop-like tokens never prefix-match unrelated words.
    filtered = filter_relevant_related_queries(
        "caneca de", {"related_queries": [{"keyword": "decoracao", "score": 10}]}
    )
    assert filtered["related_queries"] == []


def test_filter_preserves_order_and_never_mutates_input():
    source = {
        "related_queries": [
            {"keyword": "camisola preta", "score": 30},
            {"keyword": "volta a portugal 2026", "score": 100},
            {"keyword": "camisolas largas", "score": 20},
        ]
    }
    original = {key: list(value) for key, value in source.items()}
    filtered = filter_relevant_related_queries("camisolas", source)
    assert [item["keyword"] for item in filtered["related_queries"]] == [
        "camisola preta",
        "camisolas largas",
    ]
    # The input object and list are untouched (cache safety).
    assert source == original
    assert filtered is not source
    assert filtered["related_queries"] is not source["related_queries"]


def test_filter_graceful_on_error_and_non_list_data():
    assert filter_relevant_related_queries("camisolas", {"error": "offline"}) == {"error": "offline"}
    assert filter_relevant_related_queries("camisolas", None) is None
    payload = {"direction": "subida", "related_queries": "not-a-list"}
    assert filter_relevant_related_queries("camisolas", payload) == payload
    # Empty related list still produces a valid copy.
    empty = {"related_queries": []}
    result = filter_relevant_related_queries("camisolas", empty)
    assert result["related_queries"] == []
    assert result is not empty


def test_filter_audit_is_bounded_and_never_stores_excluded_text():
    filtered = filter_relevant_related_queries(
        "camisolas portugal",
        {
            "related_queries": [
                {"keyword": "camisola portugal preta", "score": 100},
                {"keyword": "volta a portugal 2026", "score": 90},
            ]
        },
    )
    audit = filtered["related_queries_filter"]
    assert audit["raw_count"] == 2
    assert audit["retained_count"] == 1
    assert audit["head_token"] == "camisolas"
    assert "policy" in audit and audit["policy"]
    serialized = str(audit)
    assert "volta a portugal 2026" not in serialized
    assert "camisola portugal preta" not in serialized


def test_filter_without_anchor_keeps_behaviour_and_no_audit():
    payload = {"related_queries": [{"keyword": "uma coisa", "score": 1}]}
    result = filter_relevant_related_queries("", payload)
    assert result["related_queries"] == payload["related_queries"]
    assert "related_queries_filter" not in result


def test_filter_keeps_urls_and_interrogatives_for_extractor_rejection():
    """The filter only decides product relevance; URL/interrogative rejection
    stays exactly where it was (extract_keyword_suggestions), so a URL or
    question containing the head token is dropped downstream as today."""
    filtered = filter_relevant_related_queries(
        "camisolas",
        {"related_queries": [{"keyword": "https://camisolas-shop.example", "score": 10}]},
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == ["https://camisolas-shop.example"]
    assert extract_keyword_suggestions("camisolas", filtered) == []


# ── Public product-head helper (reused by the Keyword Planner CSV) ───────────


def test_is_product_related_public_helper():
    """``is_product_related`` expõe a mesma política determinística de product
    head usada pelo filtro de related do Trends e pela selecção de related do
    CSV do Keyword Planner: o candidato tem de conter o token de produto da
    fonte sob equivalência singular/plural acento/case-folded."""
    # camisola de portugal mantém variantes de camisola.
    assert is_product_related("camisola de portugal", "camisola") is True
    assert is_product_related("camisola de portugal", "camisolas de algodão") is True
    assert is_product_related("camisola de portugal", "camisola portugal preta") is True
    # Drops caneca de natal e linhas geography-only/ciclismo.
    assert is_product_related("camisola de portugal", "caneca de natal") is False
    assert is_product_related("camisola de portugal", "volta a portugal 2026") is False
    assert is_product_related("camisola de portugal", "bicicleta de estrada") is False
    # ``portugal camisolas`` ancora o mesmo produto.
    assert is_product_related("portugal camisolas", "camisola") is True
    assert is_product_related("portugal camisolas", "caneca de natal") is False
    # Overlap só por stopwords nunca conta.
    assert is_product_related("camisola de portugal", "decoracao") is False
    assert is_product_related("camisola de portugal", "natal") is False
    # Fonte sem tokens não é related de nada.
    assert is_product_related("", "camisola") is False
    assert is_product_related("!!!", "camisola") is False


def test_filter_camisola_de_portugal_drops_stopword_overlap_rows():
    """Defeito provado: ``caneca de natal`` entrava por overlap do stopword
    ``de``.  A fonte ``camisola de portugal`` mantém apenas candidatas com o
    token de produto (camisola/camisolas) — stopword-only e geography-only
    saem, e o filtro reutiliza ``is_product_related``."""
    filtered = filter_relevant_related_queries(
        "camisola de portugal",
        {
            "related_queries": [
                {"keyword": "camisola portugal preta", "score": 100},
                {"keyword": "camisolas de algodão", "score": 90},
                {"keyword": "caneca de natal", "score": 80},
                {"keyword": "volta a portugal 2026", "score": 70},
                {"keyword": "bicicleta de estrada", "score": 60},
            ]
        },
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == [
        "camisola portugal preta",
        "camisolas de algodão",
    ]
    audit = filtered["related_queries_filter"]
    assert audit["raw_count"] == 5
    assert audit["retained_count"] == 2
    assert audit["head_token"] == "camisola"


# ── Exact-or-single-s product equivalence (false-positive regression) ───────


def test_tokens_equivalent_allows_exact_and_single_trailing_s():
    """``tokens_equivalent`` casa exacto e variante singular/plural de um só
    ``s`` final — nunca prefixos arbitrários partilhados."""
    from pipeline.suggestions import tokens_equivalent

    # Exact equality.
    assert tokens_equivalent("vela", "vela") is True
    # The helper works on already-folded tokens; callers fold first
    # (``is_product_related``, the Apify gate), so uppercase here is not
    # auto-folded — the caller contract, not the helper, owns folding.
    assert tokens_equivalent("camisola", "CAMISOLA") is False
    # Single trailing 's' in either direction.
    assert tokens_equivalent("vela", "velas") is True
    assert tokens_equivalent("camisola", "camisolas") is True
    assert tokens_equivalent("velas", "vela") is True
    assert tokens_equivalent("mochila", "mochilas") is True
    # Shared prefixes beyond a single trailing 's' never match.
    assert tokens_equivalent("pasta", "pastelaria") is False
    assert tokens_equivalent("arte", "artesa") is False
    assert tokens_equivalent("sol", "sola") is False
    assert tokens_equivalent("sola", "sol") is False
    assert tokens_equivalent("caneca", "canecaria") is False


def test_product_related_rejects_shared_prefix_false_positives():
    """Defeito de relevância: o prefixo arbitrário bidireccional fazia entrar
    ``pastelaria`` para ``pasta``, ``artesa`` para ``arte`` e ``sola`` para
    ``sol``.  Agora só conta a igualdade exacta ou um ``s`` final."""
    assert is_product_related("pasta", "pastelaria artesanal") is False
    assert is_product_related("arte", "artesa") is False
    assert is_product_related("arte", "artesã") is False
    assert is_product_related("sol", "sola de sapato") is False
    # A variante de um só 's' continua a entrar.
    assert is_product_related("pasta", "pastas") is True
    assert is_product_related("arte", "artes") is True
    assert is_product_related("sol", "sols") is True  # sol + 's' — variante mecânica permitida
    # Casos reais do produto que devem continuar a casar.
    assert is_product_related("vela", "velas aromáticas") is True
    assert is_product_related("camisola de portugal", "camisolas de algodão") is True


def test_filter_drops_shared_prefix_candidates():
    """O filtro de related usa a mesma regra exact-or-single-s: candidatos
    que só partilham um prefixo arbitrário são removidos."""
    filtered = filter_relevant_related_queries(
        "pasta",
        {
            "related_queries": [
                {"keyword": "pastas de dentes", "score": 100},
                {"keyword": "pastelaria perto de mim", "score": 200},
                {"keyword": "pasta de amendoim", "score": 150},
            ]
        },
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == [
        "pastas de dentes",
        "pasta de amendoim",
    ]
    filtered = filter_relevant_related_queries(
        "sol",
        {"related_queries": [{"keyword": "sola de sapato", "score": 300}, {"keyword": "sol e praia", "score": 10}]},
    )
    assert [item["keyword"] for item in filtered["related_queries"]] == ["sol e praia"]
