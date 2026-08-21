"""Deterministic product↔ad matching over canonical candidates.

Covers the resolved matching contract with focused, behavior-level tests:
the four core ``mochila personalizada`` fixtures that motivated the
delivery (genuine -> matched; carrier/generic/weak-modifier ->
needs_review; oxygen body-only mention -> rejected), the strict
strong-vs-weak geographic proof, head-token equivalence rules, the
matched-only projections/full counts (including top_ads sorting), the
saturation/global-market thresholds, the empty/error passthrough and one
determinism/immutability contract test.  Implementation-detail assertions
(exact constant lists, verdict key sets, public helper exposure) and
overlapping immutability/determinism tests are deliberately not kept here.
"""

from __future__ import annotations

import copy
import json

from pipeline.matching import product_ads
from pipeline.matching.product_ads import (
    classify_ads_data,
    classify_candidate,
)


def candidate(
    ad_id="1",
    *,
    advertiser="Loja Ágil",
    page_id="page-1",
    days_active=30,
    landing_url="https://loja.example/produto",
    copy="",
    commercial="",
    geo="",
    weak="",
    countries=("PT", "ES"),
):
    """A canonical candidate with explicit folded match_evidence strings."""
    return {
        "ad_archive_id": ad_id,
        "advertiser": advertiser,
        "page_id": page_id,
        "days_active": days_active,
        "reported_active_time": f"{days_active} days",
        "link": f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "",
        "landing_url": landing_url,
        "copy": copy,
        "publisher_platforms": ["facebook"],
        "countries": list(countries),
        "match_evidence": {
            "commercial": commercial,
            "geo": geo,
            "weak": weak,
        },
    }


def _verdict(keyword, ad):
    return classify_candidate(keyword, ad)


# ── Fixtures reais de mochila personalizada (falsos positivos do passado) ──


class TestBackpackFixtures:
    """O defeito de qualidade que motivou a entrega: ``mochila
    personalizada`` aceitava porta-bebés, mochilas escolares genéricas e um
    concentrador de oxigénio cujo copy apenas mencionava uma mochila."""

    KEYWORD = "mochila personalizada"

    def test_genuine_personalized_backpack_matched(self):
        ad = candidate(
            "real",
            copy="Mochila personalizada com nome e entrega rápida",
            commercial="mochilas personalizadas premium oferta",
            geo="mochilas personalizadas",
            weak="mochila personalizada com nome e entrega rapida envio",
        )
        verdict = _verdict(self.KEYWORD, ad)
        assert verdict["label"] == "matched"
        assert verdict["confidence"] == "high"
        assert verdict["reasons"] == ["head_proven", "all_tokens_proven"]
        assert verdict["head_token"] == "mochila"
        assert verdict["missing_tokens"] == []
        assert verdict["weak_only_tokens"] == []

    def test_baby_carrier_with_backpack_head_is_needs_review(self):
        """Porta-bebé: o head (mochila) está provado no título, mas
        ``personalizada`` não tem prova forte → precisa de revisão."""
        ad = candidate(
            "carrier",
            copy="Mochila porta-bebé ergonómica 3 em 1",
            commercial="mochila porta bebe ergonomica",
            geo="mochila porta bebe",
            weak="mochila porta bebe ergonomica 3 em 1",
        )
        verdict = _verdict(self.KEYWORD, ad)
        assert verdict["label"] == "needs_review"
        assert verdict["confidence"] == "medium"
        assert verdict["head_token"] == "mochila"
        assert verdict["missing_tokens"] == ["personalizada"]
        assert verdict["weak_only_tokens"] == []
        assert verdict["reasons"] == ["head_proven", "token_unproven"]

    def test_generic_school_backpack_is_needs_review(self):
        """Mochila escolar genérica: head provado, modifier ausente."""
        ad = candidate(
            "school",
            copy="Mochila escolar com divisões e bolso para portátil",
            commercial="mochilas escolares com divisoes",
            geo="mochilas escolares",
            weak="mochila escolar com divisoes e bolso",
        )
        verdict = _verdict(self.KEYWORD, ad)
        assert verdict["label"] == "needs_review"
        assert verdict["missing_tokens"] == ["personalizada"]

    def test_generic_backpack_with_modifier_only_in_weak_copy_is_needs_review(self):
        """O modifier em copy fraca não é prova forte: ``personalizada`` só
        no corpo → weak_only → needs_review (ex-emissão deliberadamente
        retirada)."""
        ad = candidate(
            "weak-modifier",
            copy="Mochila personalizada premium — oferta",
            commercial="mochilas escolares com divisoes",
            geo="mochilas escolares",
            weak="mochila personalizada premium oferta",
        )
        verdict = _verdict(self.KEYWORD, ad)
        assert verdict["label"] == "needs_review"
        assert verdict["head_token"] == "mochila"
        assert verdict["weak_only_tokens"] == ["personalizada"]
        assert verdict["reasons"] == ["head_proven", "token_weak_only"]

    def test_oxygen_concentrator_mentioning_backpack_is_rejected(self):
        """Concentrador de oxigénio cujo copy menciona uma mochila como
        acessório: o head não está em nenhum campo comercial forte → rejeitado
        com confiança baixa (evidência apenas fraca)."""
        ad = candidate(
            "oxygen",
            copy="Concentrador de oxigénio portátil com mochila incluída",
            commercial="concentrador de oxigenio portatil",
            geo="concentrador de oxigenio",
            weak="concentrador de oxigenio portatil com mochila incluida",
        )
        verdict = _verdict(self.KEYWORD, ad)
        assert verdict["label"] == "rejected"
        assert verdict["confidence"] == "low"
        assert verdict["reasons"] == ["head_unproven"]
        assert verdict["head_token"] == "mochila"


# ── Geografia: prova estrita nos campos geo-proof ──────────────────────────


class TestGeographicProof:
    """Os qualificadores geográficos mantêm os campos geo-proof estritos:
    head forte com geografia provada no título/path da URL é matched; a
    geografia só em link_description/contexto fraco é needs_review (nunca
    matched nem rejected)."""

    def test_strong_title_proof_matches_weak_context_is_needs_review(self):
        strong = candidate(
            copy="Camisola de Portugal premium em algodão",
            commercial="camisolas de portugal premium",
            geo="camisolas de portugal",
            weak="camisola de portugal premium em algodao",
        )
        assert _verdict("camisola portugal", strong)["label"] == "matched"

        # Path da URL sanitizada prova produto e geografia.
        url_proof = candidate(
            copy="Camisola de Portugal oficial",
            commercial="oferta da semana https://sporta.example/collections/portugal/products/camisola-portugal",
            geo="oferta da semana https://sporta.example/collections/portugal/products/camisola-portugal",
            weak="camisola de portugal oficial",
        )
        assert _verdict("camisola portugal", url_proof)["label"] == "matched"

        # Portugal só em link_description (campo forte mas não geo-proof).
        weak = candidate(
            copy="Camisola oficial da seleção",
            commercial="camisola oficial da selecao entrega em portugal em 48h",
            geo="camisola oficial da selecao",
            weak="camisola oficial da selecao entrega em portugal",
        )
        verdict = _verdict("camisola portugal", weak)
        assert verdict["label"] == "needs_review"
        assert verdict["missing_tokens"] == ["portugal"]
        assert verdict["reasons"] == ["head_proven", "geo_unproven"]

        # Benfica: a mesma geografia fraca continua needs_review.
        benfica = candidate(
            copy="Camisola oficial do Benfica com entrega em Portugal",
            commercial="camisola oficial do benfica entrega em portugal em 48h",
            geo="camisola oficial do benfica",
            weak="camisola oficial do benfica entrega em portugal",
        )
        assert _verdict("camisola portugal", benfica)["label"] == "needs_review"

    def test_geography_requirement_is_driven_by_the_query_not_the_ad(self):
        # Query sem qualificador geográfico: sem exigência geo, mesmo com o
        # corpo a mencionar Portugal.
        compression = candidate(
            copy="Camisola de compressão com envio para Portugal",
            commercial="camisolas de compressao",
            geo="camisolas de compressao",
            weak="camisola de compressao com envio para portugal",
        )
        assert _verdict("camisola", compression)["label"] == "matched"

        # Query geo-primeiro: o head continua a ser o token não geográfico.
        travel = candidate(
            "travel",
            copy="Viaja por Portugal; camisolas não incluídas no passeio",
            commercial="descobre portugal em sete dias",
            geo="descobre portugal em sete dias",
            weak="viaja por portugal camisolas nao incluidas",
        )
        shirt = candidate(
            "shirt",
            copy="Camisolas de Portugal em algodão premium",
            commercial="camisolas de portugal",
            geo="camisolas de portugal",
            weak="camisolas de portugal em algodao premium",
        )
        assert _verdict("portugal camisolas", travel)["label"] == "rejected"
        assert _verdict("portugal camisolas", shirt)["label"] == "matched"

        # Accent/case folding cobre os qualificadores e o head.
        assert _verdict("camisola PORTUGAL", candidate(
            commercial="camisolas de portugal", geo="camisolas de portugal"))["label"] == "matched"
        assert _verdict("camisola França", candidate(
            commercial="camisola oficial da selecao de franca",
            geo="camisola oficial da selecao de franca"))["label"] == "matched"
        assert _verdict("camisola brasil", candidate(
            commercial="camisola de compressao", geo="camisola de compressao",
            weak="camisola com envio para o brasil"))["label"] == "needs_review"


# ── Regras de equivalência de tokens do head ───────────────────────────────


class TestHeadRules:
    def test_shared_prefix_never_matches_head_but_plural_s_does(self):
        # ``pasta``~``pastelaria``: prefixo partilhado não é equivalência.
        ad = candidate(commercial="pastelaria artesanal bolos e doces",
                       geo="pastelaria artesanal",
                       weak="receita de pasta com legumes")
        assert _verdict("pasta", ad)["label"] == "rejected"
        # Variante plural de um só ``s`` continua a equivaler.
        ad2 = candidate(commercial="pastas frescas artesanais",
                        geo="pastas frescas", weak="pasta fresca artesanal")
        assert _verdict("pasta", ad2)["label"] == "matched"

    def test_keyword_without_tokens_is_rejected(self):
        assert _verdict("???", candidate(commercial="qualquer coisa"))["label"] == "rejected"


# ── Contrato: determinismo e imutabilidade ─────────────────────────────────


class TestClassificationContract:
    def test_classify_ads_data_is_deterministic_and_never_mutates_input(self):
        ads = TestClassifyAdsData()._ads()
        snapshot = json_dumps(ads)

        first = classify_ads_data("mochila personalizada", ads)
        second = classify_ads_data("mochila personalizada", ads)

        assert json_dumps(first) == json_dumps(second)  # determinístico
        assert json_dumps(ads) == snapshot  # input nunca é mutado
        assert first is not ads
        assert first["candidates"][0] is not ads["candidates"][0]  # deep copies


# ── classify_ads_data: projeções matched-only e contagens completas ────────


class TestClassifyAdsData:
    def _ads(self, **overrides):
        base = {
            "source": "Facebook Ad Library via Apify",
            "query": "mochila personalizada",
            "evidence_scope": "global",
            "query_country": "ALL",
            "sample_limit": 100,
            "sample_capped": False,
            "raw_ads_count": 5,
            "filtered_out_count": 0,
            "filtered_inactive_count": 0,
            "filtered_irrelevant_count": 0,
            "candidate_count": 5,
            "duplicates_removed": 0,
            "relevance_policy": "gate1",
            "matching_schema_version": 1,
            "candidates": [
                candidate("m0", days_active=90, advertiser="Loja A", page_id="p1",
                          copy="Mochila personalizada premium",
                          commercial="mochilas personalizadas premium", geo="mochilas personalizadas",
                          weak="mochila personalizada premium envio rapido"),
                candidate("m1", days_active=60, advertiser="Loja A", page_id="p1",
                          copy="Mochila personalizada com nome",
                          commercial="mochilas personalizadas com nome", geo="mochilas personalizadas",
                          weak="mochila personalizada com nome"),
                candidate("m2", days_active=30, advertiser="Loja B", page_id="p2",
                          copy="Mochila personalizada",
                          commercial="mochilas personalizadas", geo="mochilas personalizadas",
                          weak="mochila personalizada"),
                candidate("r0", days_active=20, copy="Mochila porta-bebé",
                          commercial="mochila porta bebe ergonomica", geo="mochila porta bebe",
                          weak="mochila porta bebe ergonomica"),
                candidate("x0", days_active=10, copy="Concentrador com mochila",
                          commercial="concentrador de oxigenio", geo="concentrador de oxigenio",
                          weak="concentrador portatil com mochila"),
            ],
        }
        base.update(overrides)
        return base

    def test_matched_only_projections_full_counts_and_top_ads_sorting(self):
        result = classify_ads_data("mochila personalizada", self._ads())

        assert result["candidate_count"] == 5
        assert result["matched_count"] == 3
        assert result["needs_review_count"] == 1
        assert result["rejected_count"] == 1
        assert result["total_ads"] == 3  # total_ads = matched count
        assert result["ad_ids"] == ["m0", "m1", "m2"]
        # top_ads ordenado por longevidade desc (não vacuo: 3 matched).
        assert [ad["ad_archive_id"] for ad in result["top_ads"]] == ["m0", "m1", "m2"]
        assert [ad["days_active"] for ad in result["top_ads"]] == [90, 60, 30]
        # Advertisers agrupados só dos matched, shape histórica preservada.
        adv = result["advertisers"]
        assert set(adv[0]) == {"page_id", "page_name", "active_ads", "oldest_ad_days"}
        assert adv[0] == {"page_id": "p1", "page_name": "Loja A", "active_ads": 2, "oldest_ad_days": 90}
        assert adv[1] == {"page_id": "p2", "page_name": "Loja B", "active_ads": 1, "oldest_ad_days": 30}
        # copy_examples apenas dos matched (nunca needs_review/rejected).
        assert result["copy_examples"] == [
            "Mochila personalizada premium",
            "Mochila personalizada com nome",
            "Mochila personalizada",
        ]
        assert result["saturation"] == "baixa"
        assert result["global_market_proven"] is False
        assert result["matcher_version"] == product_ads.MATCHER_VERSION

        labels = {c["ad_archive_id"]: c["match"]["label"] for c in result["candidates"]}
        assert labels == {"m0": "matched", "m1": "matched", "m2": "matched",
                          "r0": "needs_review", "x0": "rejected"}

    def test_saturation_and_global_proof_follow_matched_counts(self):
        many = [
            candidate(str(i), days_active=10, commercial="mochilas personalizadas",
                      geo="mochilas personalizadas", weak="x")
            for i in range(6)
        ]
        result = classify_ads_data("mochila personalizada", self._ads(candidates=many, raw_ads_count=6))
        assert result["matched_count"] == 6
        assert result["total_ads"] == 6
        assert result["global_market_proven"] is True  # 6 >= 5
        assert result["saturation"] == "baixa"  # < 10
        assert len(result["top_ads"]) == 3  # top 3, não 6

        media = self._ads()
        media["candidates"] = [candidate(str(i), commercial="mochilas personalizadas",
                                         geo="mochilas personalizadas", weak="x") for i in range(10)]
        assert classify_ads_data("mochila personalizada", media)["saturation"] == "média"

        alta = self._ads()
        alta["candidates"] = [candidate(str(i), commercial="mochilas personalizadas",
                                        geo="mochilas personalizadas", weak="x") for i in range(51)]
        assert classify_ads_data("mochila personalizada", alta)["saturation"] == "alta"

    def test_empty_candidates_produces_zero_everything(self):
        result = classify_ads_data("mochila personalizada", self._ads(candidates=[], raw_ads_count=0))
        assert result["total_ads"] == 0
        assert result["matched_count"] == result["needs_review_count"] == result["rejected_count"] == 0
        assert result["ad_ids"] == []
        assert result["top_ads"] == []
        assert result["advertisers"] == []
        assert result["saturation"] == "baixa"

    def test_error_payload_and_missing_candidates_pass_through(self):
        error = {"error": "Apify offline"}
        assert classify_ads_data("kw", error) == error
        no_candidates = {"total_ads": 1, "top_ads": [], "advertisers": []}
        assert classify_ads_data("kw", no_candidates) == no_candidates

    def test_malformed_candidates_are_dropped_and_counts_agree(self):
        """Entradas não-dict na lista de candidatos são ignoradas (nunca
        copiadas para valores que depois crasham no ``.get``); as contagens
        persistidas refletem apenas os candidatos reais."""
        ads = self._ads()
        ads["candidates"] = [None, "texto", 42, ["lista"], ads["candidates"][0]]
        result = classify_ads_data("mochila personalizada", ads)
        assert len(result["candidates"]) == 1
        assert result["candidate_count"] == 1
        assert all(isinstance(item, dict) for item in result["candidates"])
        assert result["matched_count"] == 1

    def test_malformed_dict_candidates_with_strong_evidence_are_dropped(self):
        """Regression: um dict com prova forte mas sem os campos mínimos de
        projeção (ou com tipos errados) é descartado como um não-dict — nunca
        pode ficar ``matched`` e crashar ``_matched_projections`` no sort/group
        de ``days_active``/``ad_archive_id``/``advertiser``/``page_id``.  O
        ``candidate_count`` persistido é recomputado da lista classificada,
        por isso concorda sempre com ``candidates``."""
        strong = candidate(
            "strong",
            copy="Mochila personalizada premium",
            commercial="mochilas personalizadas premium",
            geo="mochilas personalizadas",
            weak="mochila personalizada premium",
        )
        malformed = [
            ("sem days_active", lambda c: c.pop("days_active")),
            ("days_active string", lambda c: c.update(days_active="30")),
            ("days_active bool", lambda c: c.update(days_active=True)),
            ("ad_archive_id int", lambda c: c.update(ad_archive_id=42)),
            ("sem advertiser", lambda c: c.pop("advertiser")),
            ("sem copy", lambda c: c.pop("copy")),
            ("match_evidence lista", lambda c: c.update(match_evidence=["x"])),
            ("landing_url dict", lambda c: c.update(landing_url={"url": "https://x.test/p"})),
            ("landing_url int", lambda c: c.update(landing_url=42)),
            ("landing_url list", lambda c: c.update(landing_url=["https://x.test/p"])),
            ("landing_url bool", lambda c: c.update(landing_url=True)),
            ("sem landing_url", lambda c: c.pop("landing_url")),
            ("link dict", lambda c: c.update(link={"url": "https://x.test"})),
            ("link int", lambda c: c.update(link=42)),
            ("sem link", lambda c: c.pop("link")),
        ]
        for name, mutate in malformed:
            bad = copy.deepcopy(strong)
            mutate(bad)
            ads = self._ads()
            ads["candidates"] = [bad, ads["candidates"][0]]
            result = classify_ads_data("mochila personalizada", ads)
            assert len(result["candidates"]) == 1, name
            assert result["candidate_count"] == 1, name
            assert result["candidates"][0]["ad_archive_id"] == "m0", name
            assert result["matched_count"] == 1, name

    def test_over_cap_candidates_are_capped_and_count_agrees(self):
        """Mais de 100 candidatos são limitados ao cap e o
        ``candidate_count`` persistido é recomputado a partir da lista
        classificada (concorda com ``candidates``)."""
        many = [
            candidate(str(i), commercial="mochilas personalizadas",
                      geo="mochilas personalizadas", weak="x")
            for i in range(150)
        ]
        result = classify_ads_data("mochila personalizada", self._ads(candidates=many, raw_ads_count=150))
        assert len(result["candidates"]) == product_ads.CANDIDATE_CAP
        assert result["candidate_count"] == product_ads.CANDIDATE_CAP
        assert result["matched_count"] == product_ads.CANDIDATE_CAP


# ── Query liderada por cidade: head e geo_tokens canónicos ─────────────────


class TestCityLedQuery:
    """Um qualificador geográfico de suggestions (ex.: Lisboa) nunca é o head
    nem um geo_token estrito — só os quatro mercados core (PT/ES/FR/BR) são
    ``geo_tokens`` exigidos; a cidade também não fica como token meaningful
    que precise de prova."""

    def test_city_led_query_selects_product_head_and_matches(self):
        ad = candidate(
            "city",
            copy="Mochila personalizada com entrega em Lisboa",
            commercial="mochilas personalizadas",
            geo="mochilas personalizadas",
            weak="mochila personalizada com entrega em lisboa",
        )
        verdict = _verdict("lisboa mochila", ad)
        assert verdict["head_token"] == "mochila"
        assert verdict["geo_tokens"] == []
        assert verdict["missing_tokens"] == []
        assert verdict["label"] == "matched"

    def test_city_token_is_never_a_meaningful_requirement(self):
        """Um anúncio que prova o produto mas não menciona Lisboa não é
        rejeitado por a cidade faltar: ``lisboa`` sai dos tokens
        significativos (qualificador geográfico) e não é geo_token estrito."""
        ad = candidate(
            commercial="mochilas personalizadas premium",
            geo="mochilas personalizadas",
            weak="mochila personalizada premium",
        )
        verdict = _verdict("mochila lisboa", ad)
        assert verdict["head_token"] == "mochila"
        assert verdict["geo_tokens"] == []
        assert verdict["missing_tokens"] == []
        assert verdict["label"] == "matched"


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
