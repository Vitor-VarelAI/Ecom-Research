"""CSV fail-closed semantics (Part A).

In ``run_keyword``'s positive matched-ads branch, *any* Keyword Planner CSV
error must produce ``DADOS INSUFICIENTES`` via ``_incomplete_score`` with the
CSV error as reason — the numeric score/breakdown are preserved but a false
verdict (volume silently zero) is never emitted.  Valid CSV stays
byte-compatible with the plain ``score.compute`` result.  The existing
zero-candidates / all-rejected DESCARTAR policy and the
needs_review/Apify-failure incomplete policy are preserved.

``_source_states`` classifies both Portuguese row-missing messages
(``não encontrado`` / ``não encontrada``) as ``missing``; other parse errors
remain ``failed``.
"""

from __future__ import annotations

import run as run_module
from pipeline.scoring import score
from tests.fixtures.ads_helpers import classified_ads_data
from tests.fixtures.products_helpers import product

TRENDS = {"direction": "subida", "slope_pct": 50.0, "seasonal": False, "top_geos": [], "related_queries": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}
VALID_CSV = {"volume": 5400, "cpc": 0.35, "related_keywords": []}
ADS = classified_ads_data()  # 1 matched candidate -> total_ads=1

NO_CSV_ERROR = "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"
COPPER_ERROR = "Keyword 'copper bottles' não encontrada no CSV (matching exacto normalizado)"


def _copper_adapter_result():
    """Unclassified adapter result whose candidate matches the ``copper
    bottles`` product head (strong commercial proof), so the positive
    matched-ads branch is exercised for the copper-shaped keyword."""
    return {
        "source": "Facebook Ad Library via Apify",
        "query": "copper bottles",
        "evidence_scope": "global",
        "query_country": "ALL",
        "sample_limit": 100,
        "sample_capped": False,
        "raw_ads_count": 1,
        "filtered_out_count": 0,
        "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0,
        "candidate_count": 1,
        "duplicates_removed": 0,
        "relevance_policy": "gate1",
        "matching_schema_version": 1,
        "candidates": [{
            "ad_archive_id": "c1",
            "advertiser": "CopperShop",
            "page_id": "p9",
            "days_active": 30,
            "reported_active_time": "30 days",
            "link": "https://www.facebook.com/ads/library/?id=c1",
            "landing_url": "https://shop.example/c1",
            "copy": "Copper bottles premium",
            "publisher_platforms": ["facebook"],
            "countries": ["PT"],
            "match_evidence": {
                "commercial": "copper bottles water bottle",
                "geo": "copper bottles",
                "weak": "copper water bottle premium",
            },
        }],
    }


def _install_positive_stack(monkeypatch, csv_result, ads_result=None):
    """Positive matched-ads stack: apify returns classified ads (1 matched),
    trends/SEO valid, landing consumed, CSV injected."""
    calls = {"landing": 0}
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: dict(ads_result or ADS))
    monkeypatch.setattr(run_module.trends, "fetch", lambda keyword: dict(TRENDS))
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: dict(SEO))
    monkeypatch.setattr(
        run_module.landing_price, "fetch_prices_from_ads",
        lambda data: calls.__setitem__("landing", calls["landing"] + 1) or {},
    )
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: csv_result)
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: "pack.md")
    return calls


def test_positive_matched_ads_no_csv_is_incomplete_not_false_verdict(monkeypatch):
    """Matched ads + CSV em falta: score numérico/breakdown preservados, mas o
    label é forçado a DADOS INSUFICIENTES com reason do CSV — nunca um falso
    AVANÇAR/OBSERVAR por volume zero silencioso."""
    _install_positive_stack(monkeypatch, {"error": NO_CSV_ERROR})
    result = run_module.run_keyword(product("mochila personalizada"))
    numeric = score.compute(TRENDS, {"error": NO_CSV_ERROR}, ADS)
    assert result["score_result"]["score"] == numeric["score"]
    assert result["score_result"]["breakdown"] == numeric["breakdown"]
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["score_result"]["incomplete"] is True
    assert result["score_result"]["reason"] == NO_CSV_ERROR


def test_positive_matched_ads_copper_row_missing_is_incomplete(monkeypatch):
    """Caso copper-shaped: a keyword não existe no CSV (``não encontrada``) —
    mesmo comportamento fail-closed com o erro do CSV como reason."""
    _install_positive_stack(monkeypatch, {"error": COPPER_ERROR}, ads_result=_copper_adapter_result())
    result = run_module.run_keyword(product("copper bottles"))
    numeric = score.compute(TRENDS, {"error": COPPER_ERROR}, ADS)
    assert result["score_result"]["score"] == numeric["score"]
    assert result["score_result"]["breakdown"] == numeric["breakdown"]
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["score_result"]["incomplete"] is True
    assert result["score_result"]["reason"] == COPPER_ERROR


def test_positive_matched_ads_valid_csv_is_byte_compatible(monkeypatch):
    """CSV válido: a branch positiva mantém exactamente o resultado do
    ``score.compute`` (label incluído) — nenhuma mudança de bytes/shape."""
    _install_positive_stack(monkeypatch, dict(VALID_CSV))
    result = run_module.run_keyword(product("mochila personalizada"))
    expected = score.compute(TRENDS, VALID_CSV, ADS)
    assert result["score_result"] == expected
    assert "incomplete" not in result["score_result"]
    assert "reason" not in result["score_result"]


def test_zero_candidates_stay_descartar_with_missing_csv(monkeypatch):
    """Política preservada: zero candidatos canónicos permanece DESCARTAR mesmo
    com CSV em falta — a falta de CSV nunca converte uma decisão negativa em
    DADOS INSUFICIENTES nem vice-versa."""
    zero = classified_ads_data(
        candidates=[], candidate_count=0, matched_count=0, needs_review_count=0,
        rejected_count=0, total_ads=0, ad_ids=[], top_ads=[], advertisers=[],
        copy_examples=[], saturation="baixa",
    )
    calls = {"related": 0, "seo": 0, "landing": 0}
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: dict(zero))
    monkeypatch.setattr(
        run_module.trends, "fetch_related",
        lambda keyword: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": []},
    )
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: calls.__setitem__("seo", 1))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"error": NO_CSV_ERROR})
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: "pack.md")

    result = run_module.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["score_result"]["score"] == 0
    assert "incomplete" not in result["score_result"]


def _rejected_adapter_result():
    """Unclassified adapter result whose single candidate never matches the
    product head (oxygen concentrator mentioning a backpack only in body)."""
    return {
        "source": "Facebook Ad Library via Apify",
        "query": "mochila personalizada",
        "evidence_scope": "global",
        "query_country": "ALL",
        "sample_limit": 100,
        "sample_capped": False,
        "raw_ads_count": 1,
        "filtered_out_count": 0,
        "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0,
        "candidate_count": 1,
        "duplicates_removed": 0,
        "relevance_policy": "gate1",
        "matching_schema_version": 1,
        "candidates": [{
            "ad_archive_id": "x1",
            "advertiser": "Loja",
            "page_id": "p1",
            "days_active": 30,
            "reported_active_time": "30 days",
            "link": "https://www.facebook.com/ads/library/?id=x1",
            "landing_url": "https://shop.example/x1",
            "copy": "Concentrador com mochila incluída",
            "publisher_platforms": ["facebook"],
            "countries": ["PT"],
            "match_evidence": {
                "commercial": "concentrador de oxigenio portatil",
                "geo": "concentrador de oxigenio",
                "weak": "concentrador portatil com mochila incluida",
            },
        }],
    }


def test_all_rejected_stay_descartar_with_missing_csv(monkeypatch):
    """Política preservada: zero matched (todos rejeitados) permanece DESCARTAR
    mesmo com CSV em falta."""
    calls = {"related": 0, "landing": 0}
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: _rejected_adapter_result())
    monkeypatch.setattr(
        run_module.trends, "fetch_related",
        lambda keyword: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": []},
    )
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: {})
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"error": NO_CSV_ERROR})
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: "pack.md")

    result = run_module.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["score_result"]["score"] == 0
    assert "incomplete" not in result["score_result"]


def test_needs_review_stays_incomplete_with_missing_csv(monkeypatch):
    """Política preservada: zero matched com candidatos needs_review mantém o
    reason de revisão manual (não o do CSV) e continua DADOS INSUFICIENTES."""
    candidate = {
        "ad_archive_id": "r1",
        "advertiser": "Loja",
        "page_id": "p1",
        "days_active": 30,
        "reported_active_time": "30 days",
        "link": "https://www.facebook.com/ads/library/?id=r1",
        "landing_url": "https://shop.example/r1",
        "copy": "Mochila escolar com divisões",
        "publisher_platforms": ["facebook"],
        "countries": ["PT"],
        "match_evidence": {
            "commercial": "mochilas escolares com divisoes",
            "geo": "mochilas escolares",
            "weak": "mochila personalizada escolar com bolso",
        },
    }
    adapter = {
        "source": "Facebook Ad Library via Apify",
        "query": "mochila personalizada",
        "sample_limit": 100, "sample_capped": False, "raw_ads_count": 1,
        "filtered_out_count": 0, "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0, "candidate_count": 1, "duplicates_removed": 0,
        "relevance_policy": "gate1", "matching_schema_version": 1,
        "candidates": [candidate],
    }
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: adapter)
    monkeypatch.setattr(run_module.trends, "fetch", lambda keyword: dict(TRENDS))
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: dict(SEO))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: {})
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"error": NO_CSV_ERROR})
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: "pack.md")

    result = run_module.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["score_result"]["incomplete"] is True
    assert "needs_review" in result["score_result"]["reason"]


def test_apify_failure_stays_incomplete_with_missing_csv(monkeypatch):
    """Política preservada: falha Apify mantém o reason default (Apify) e
    DADOS INSUFICIENTES — a falta de CSV não altera essa branch."""
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: {"error": "Apify offline"})
    monkeypatch.setattr(run_module.trends, "fetch", lambda keyword: dict(TRENDS))
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: dict(SEO))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: {})
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"error": NO_CSV_ERROR})
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: "pack.md")

    result = run_module.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["score_result"]["incomplete"] is True
    assert "Apify" in result["score_result"]["reason"]


# ── _source_states: row-missing messages are missing, not failed ──────────


def test_source_state_feminine_row_missing_is_missing():
    result = {"ads_data": dict(ADS), "trends_data": dict(TRENDS),
              "csv_data": {"error": COPPER_ERROR}, "seo_data": dict(SEO),
              "landing_price_data": None}
    states = run_module._source_states("keyword", result)
    assert states["keyword_csv"]["status"] == "missing"


def test_source_state_masculine_row_missing_is_missing():
    result = {"ads_data": dict(ADS), "trends_data": dict(TRENDS),
              "csv_data": {"error": "Keyword 'tapete yoga' não encontrado no CSV (matching exacto normalizado)"},
              "seo_data": dict(SEO), "landing_price_data": None}
    states = run_module._source_states("keyword", result)
    assert states["keyword_csv"]["status"] == "missing"


def test_source_state_other_parse_errors_remain_failed():
    for error in (
        "CSV: headers não reconhecidos (tab nem vírgula)",
        "CSV: coluna Keyword não encontrada no cabeçalho",
    ):
        result = {"ads_data": dict(ADS), "trends_data": dict(TRENDS),
                  "csv_data": {"error": error},
                  "seo_data": dict(SEO), "landing_price_data": None}
        states = run_module._source_states("keyword", result)
        assert states["keyword_csv"]["status"] == "failed"


def test_source_state_missing_file_message_is_missing():
    result = {"ads_data": dict(ADS), "trends_data": dict(TRENDS),
              "csv_data": {"error": NO_CSV_ERROR}, "seo_data": dict(SEO),
              "landing_price_data": None}
    states = run_module._source_states("keyword", result)
    assert states["keyword_csv"]["status"] == "missing"
