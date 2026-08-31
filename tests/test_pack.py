"""Pack schema tests for manual-market and advisory output."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date

import pytest

from pipeline.output import pack
from pipeline.scoring import score
from pipeline.suggestions import filter_relevant_related_queries


TRENDS_DATA = {
    "direction": "subida",
    "slope_pct": 15.5,
    "seasonal": True,
    "peak": "2026-11",
    "top_geos": [{"geo": "PT", "value": 100}, {"geo": "ES", "value": 72}, {"geo": "BR", "value": 45}],
    "related_queries": [{"keyword": "mochila escolar", "score": 120}],
}
ADS_DATA = {
    "source": "Facebook Ad Library via Apify",
    "total_ads": 12,
    "top_ads": [
        {"ad_archive_id": "1", "advertiser": "MochilasXPTO", "page_id": "p1", "days_active": 180, "link": "https://www.facebook.com/ads/library/?id=1", "landing_url": "https://shop.example/1", "countries": ["PT", "ES"]},
        {"ad_archive_id": "2", "advertiser": "BagMaster", "page_id": "p2", "days_active": 90, "link": "https://www.facebook.com/ads/library/?id=2", "landing_url": "https://shop.example/2", "countries": ["FR"]},
    ],
    "advertisers": [{"page_id": "p1", "page_name": "MochilasXPTO", "active_ads": 6, "oldest_ad_days": 180}],
    "global_market_proven": True,
    "copy_examples": ["Mochila personalizada premium"],
    "saturation": "baixa",
}
CSV_DATA = {"volume": 5400, "cpc": 0.35, "related_keywords": [], "total_keywords": 1}
SEO_DATA = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


@pytest.fixture
def temp_pack_dirs(monkeypatch):
    tmp = tempfile.mkdtemp()
    packs_dir, data_dir = os.path.join(tmp, "packs"), os.path.join(tmp, "data")
    os.makedirs(packs_dir)
    os.makedirs(data_dir)

    def temp_paths(keyword, run_date=None):
        run_date = run_date or date.today()
        slug = pack._slugify(keyword)
        return os.path.join(packs_dir, f"{run_date.isoformat()}--{slug}.md"), os.path.join(data_dir, f"{run_date.isoformat()}--{slug}.json")

    monkeypatch.setattr(pack, "_pack_paths", temp_paths)
    return tmp


def test_pack_has_manual_decision_market_and_advisory_suggestions(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate(
        "mochila personalizada",
        TRENDS_DATA,
        CSV_DATA,
        ADS_DATA,
        result,
        seo_data=SEO_DATA,
        landing_price_data={"price_range": {"min": 29.99, "max": 59.99}},
        suggested_keywords=[{"keyword": "mochila escolar", "score": 120, "status": "proposed"}],
    )
    content = open(path, encoding="utf-8").read()
    assert "Mercado:   decisão manual" in content
    assert "## Sinais por mercado — decisão manual" in content
    assert "| country | Trends demand | advertisers observed (top-ad sample) | ads observed (top-ad sample) |" in content
    assert "| PT | 100 | 1 | 1 |" in content
    assert "| BR | 45 | — | — |" in content
    assert all(f"| {country} |" in content for country in ("PT", "ES", "FR", "BR"))
    assert "Prova global:" in content
    assert "contagens de anunciantes e ads vêm apenas de top_ads (top-ad sample)" in content
    assert "## Sugestões de keywords — aprovação manual" in content
    assert "Nada foi adicionado a config/keywords.txt" in content
    assert "AVANÇAR: preparar ad-machine após aprovação manual" in content
    assert "Facebook Ad Library via Apify" in content


def test_raw_json_uses_facebook_ads_only(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    pack.generate("test", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert raw["facebook_ads"] == ADS_DATA
    assert set(raw) >= {"facebook_ads", "suggested_keywords"}
    assert raw["suggested_keywords"] == []


def test_incomplete_recommendation_is_not_descartar(temp_pack_dirs):
    result = {"score": 0, "label": "DADOS INSUFICIENTES", "breakdown": {}}
    path = pack.generate("test", {"error": "offline"}, {"error": "missing"}, {"error": "Apify offline"}, result)
    content = open(path, encoding="utf-8").read()
    assert "Sinal advisory: DADOS INSUFICIENTES" in content
    assert "Validação: revisão manual obrigatória antes de qualquer ação" in content
    assert "repetir quando o Apify estiver disponível" in content
    assert "DESCARTAR" not in content


def test_digest_does_not_choose_a_country():
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=TRENDS_DATA,
                                      run_date=date(2026, 8, 10))
    assert "Mercado: decisão manual" in digest
    assert "Prova global:" in digest
    assert "Onde?" not in digest


def test_decision_and_digest_are_advisory_not_vender():
    """A decisão e o digest falam em sinal advisory (AVANÇAR/OBSERVAR/
    DESCARTAR/DADOS INSUFICIENTES) e exigem validação manual — nunca em
    SIM/NÃO, preservando os scores e a semântica dos labels."""
    for label in ("AVANÇAR", "OBSERVAR", "DESCARTAR", "DADOS INSUFICIENTES"):
        result = {"score": 50, "label": label, "breakdown": {}}
        path = pack.generate("mochila", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
        content = open(path, encoding="utf-8").read()
        assert f"Sinal advisory: {label}" in content
        assert "Vender?" not in content
        assert "Validação: revisão manual obrigatória antes de qualquer ação" in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA)
        assert f"Sinal advisory: {label}" in digest
        assert "Ação: validação manual" in digest
        assert "Vender?" not in digest
        assert f"Score: 50/100 ({label})" in digest


def test_ads_section_audits_filtered_breakdown(temp_pack_dirs):
    """O pack expõe porquê a amostra encolheu: inativos vs irrelevantes, a
    política de relevância e a classificação completa (candidatos/matched/
    needs_review/rejected), sem guardar payloads brutos."""
    ads = {
        **ADS_DATA,
        "raw_ads_count": 100,
        "sample_capped": True,
        "filtered_out_count": 54,
        "filtered_inactive_count": 12,
        "filtered_irrelevant_count": 42,
        "candidate_count": 46,
        "matched_count": 12,
        "needs_review_count": 20,
        "rejected_count": 14,
        "duplicates_removed": 3,
        "relevance_policy": "gate1: phrase/coverage; matching: matched/needs_review/rejected",
    }
    result = score.compute(TRENDS_DATA, CSV_DATA, ads)
    path = pack.generate("tapete yoga", TRENDS_DATA, CSV_DATA, ads, result)
    content = open(path, encoding="utf-8").read()
    assert "- Amostra: 100/100; capped=True" in content
    assert "inativos=12 | irrelevantes=42 | total excluídos=54 | duplicados=3" in content
    assert "candidatos=46 | matched=12 | needs_review=20 | rejected=14" in content
    assert "(matched)" in content


class TestPeakDeadline:
    """A linha de decisão e o digest só mostram ``lançar antes de`` quando o
    pico sazonal observado é estritamente posterior à data de execução.
    Picos passados/iguais ou não parseáveis não produzem prazo — o pico
    factual continua na evidência de sazonalidade."""

    RUN_DATE = date(2026, 8, 10)

    def test_future_peak_renders_deadline_in_decision_and_digest(self, temp_pack_dirs):
        result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", TRENDS_DATA, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "Sinal advisory: AVANÇAR — lançar antes de 2026-11-01" in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=TRENDS_DATA,
                                           run_date=self.RUN_DATE)
        assert "(lançar antes de 2026-11-01)" in digest

    def test_past_and_equal_peak_have_no_deadline_but_keep_evidence(self, temp_pack_dirs):
        for peak in ("2026-01-15", "2026-08-10"):
            trends = {**TRENDS_DATA, "peak": peak}
            result = score.compute(trends, CSV_DATA, ADS_DATA)
            path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                                 run_date=self.RUN_DATE)
            content = open(path, encoding="utf-8").read()
            assert "lançar antes de" not in content
            # O pico factual continua na evidência de sazonalidade.
            assert f"pico: {peak}" in content
            digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                               run_date=self.RUN_DATE)
            assert "lançar antes de" not in digest

    def test_unparseable_peak_has_no_deadline(self, temp_pack_dirs):
        trends = {**TRENDS_DATA, "peak": "meados do ano"}
        result = score.compute(trends, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "lançar antes de" not in content
        assert "pico: meados do ano" in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                           run_date=self.RUN_DATE)
        assert "lançar antes de" not in digest

    def test_past_peak_is_rendered_as_historical_evidence_not_upcoming(self, temp_pack_dirs):
        """Um pico já passado é evidência histórica explícita na secção de
        sazonalidade — nunca um evento futuro implícito nem um prazo."""
        trends = {**TRENDS_DATA, "peak": "2026-01-15"}
        result = score.compute(trends, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "pico: 2026-01-15, histórico — pico já passado" in content
        assert "lançar antes de" not in content
        assert "futuro" not in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                           run_date=self.RUN_DATE)
        assert "lançar antes de" not in digest

    def test_equal_peak_is_historical_too(self, temp_pack_dirs):
        """O pico no próprio dia da execução já ocorreu — histórico, sem prazo."""
        trends = {**TRENDS_DATA, "peak": self.RUN_DATE.isoformat()}
        result = score.compute(trends, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "histórico — pico já passado" in content
        assert "lançar antes de" not in content

    def test_future_peak_stays_labelled_future_with_deadline(self, temp_pack_dirs):
        """Picos futuros mantêm o rótulo de futuro e o prazo de lançamento."""
        trends = {**TRENDS_DATA, "peak": "2026-11"}
        result = score.compute(trends, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "pico: 2026-11, futuro" in content
        assert "lançar antes de 2026-11-01" in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                           run_date=self.RUN_DATE)
        assert "(lançar antes de 2026-11-01)" in digest

    def test_unparseable_peak_is_labelled_unverified_without_timing(self, temp_pack_dirs):
        """Picos não parseáveis ficam 'não verificado — sem data de pico':
        observados mas sem qualquer conselho de timing."""
        trends = {**TRENDS_DATA, "peak": "meados do ano"}
        result = score.compute(trends, CSV_DATA, ADS_DATA)
        path = pack.generate("mochila", trends, CSV_DATA, ADS_DATA, result,
                             run_date=self.RUN_DATE)
        content = open(path, encoding="utf-8").read()
        assert "pico: meados do ano, não verificado — sem data de pico" in content
        assert "lançar antes de" not in content
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                           run_date=self.RUN_DATE)
        assert "lançar antes de" not in digest

    def test_peak_status_labels_relative_to_run_date(self):
        run_date = date(2026, 8, 10)
        assert pack._peak_status({"seasonal": True, "peak": "2026-11"}, run_date) == "futuro"
        assert pack._peak_status({"seasonal": True, "peak": "2026-08-10"}, run_date) == "histórico"
        assert pack._peak_status({"seasonal": True, "peak": "2026-01-15"}, run_date) == "histórico"
        assert pack._peak_status({"seasonal": True, "peak": "Aug 6, 2026"}, run_date) == "histórico"
        assert pack._peak_status({"seasonal": True, "peak": "meados do ano"}, run_date) == "não verificado"
        assert pack._peak_status({"seasonal": False, "peak": "2026-11"}, run_date) is None
        assert pack._peak_status({"error": "offline"}, run_date) is None
        assert pack._peak_status(None, run_date) is None
        assert pack._peak_status({"seasonal": True}, run_date) == "não verificado"

    def test_english_and_iso_peak_labels_parse(self, temp_pack_dirs):
        result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
        for peak in ("Aug 6, 2027", "2027-08-06", "August 6, 2027", "Aug 2027"):
            trends = {**TRENDS_DATA, "peak": peak}
            digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=trends,
                                               run_date=self.RUN_DATE)
            assert "lançar antes de" in digest, peak

    def test_non_seasonal_never_renders_deadline(self, temp_pack_dirs):
        flat = {**TRENDS_DATA, "seasonal": False}
        result = score.compute(flat, CSV_DATA, ADS_DATA)
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=flat,
                                           run_date=self.RUN_DATE)
        assert "lançar antes de" not in digest

    def test_trends_error_never_renders_deadline(self, temp_pack_dirs):
        result = score.compute({"error": "offline"}, CSV_DATA, ADS_DATA)
        digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data={"error": "offline"},
                                           run_date=self.RUN_DATE)
        assert "lançar antes de" not in digest


def test_parse_peak_formats():
    assert pack._parse_peak("2026-08-06") == date(2026, 8, 6)
    assert pack._parse_peak("2026-11") == date(2026, 11, 1)
    assert pack._parse_peak("Aug 6, 2026") == date(2026, 8, 6)
    assert pack._parse_peak("August 6, 2026") == date(2026, 8, 6)
    assert pack._parse_peak("Aug 2026") == date(2026, 8, 1)
    assert pack._parse_peak("não é uma data") is None
    assert pack._parse_peak("") is None
    assert pack._parse_peak(None) is None


def test_launch_deadline_strictly_after_run_date():
    run_date = date(2026, 8, 10)
    assert pack._launch_deadline({"seasonal": True, "peak": "2026-11"}, run_date) == "lançar antes de 2026-11-01"
    assert pack._launch_deadline({"seasonal": True, "peak": "2026-08-10"}, run_date) is None
    assert pack._launch_deadline({"seasonal": True, "peak": "2026-01-01"}, run_date) is None
    assert pack._launch_deadline({"seasonal": True, "peak": "??"}, run_date) is None
    assert pack._launch_deadline({"seasonal": False, "peak": "2026-11"}, run_date) is None
    assert pack._launch_deadline({"error": "offline"}, run_date) is None
    assert pack._launch_deadline(None, run_date) is None


def test_pack_renders_only_filtered_related_queries_and_persists_audit(temp_pack_dirs):
    """A trends payload filtered by the product-relevance gate renders only
    the retained queries and round-trips the bounded audit into the JSON run
    artifact — the excluded query text is never stored anywhere."""
    filtered = filter_relevant_related_queries(
        "camisolas portugal",
        {
            "direction": "subida",
            "slope_pct": 15.5,
            "seasonal": False,
            "top_geos": [],
            "related_queries": [
                {"keyword": "camisola portugal preta", "score": 100},
                {"keyword": "volta a portugal 2026", "score": 90},
                {"keyword": "camisolas de algodão", "score": 80},
            ],
        },
    )
    result = score.compute(filtered, CSV_DATA, ADS_DATA)
    path = pack.generate("camisolas portugal", filtered, CSV_DATA, ADS_DATA, result)

    content = open(path, encoding="utf-8").read()
    assert "camisola portugal preta" in content
    assert "camisolas de algodão" in content
    assert "volta a portugal 2026" not in content

    raw_path = next(
        os.path.join(temp_pack_dirs, "data", name)
        for name in os.listdir(os.path.join(temp_pack_dirs, "data"))
    )
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert [q["keyword"] for q in raw["trends"]["related_queries"]] == [
        "camisola portugal preta",
        "camisolas de algodão",
    ]
    audit = raw["trends"]["related_queries_filter"]
    assert audit["raw_count"] == 3
    assert audit["retained_count"] == 2
    assert audit["head_token"] == "camisolas"
    assert audit["policy"]
    assert "volta a portugal 2026" not in json.dumps(raw)


def test_market_signals_explain_global_discovery_scope(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate("mochila", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
    content = open(path, encoding="utf-8").read()
    assert "descoberta global (query country=ALL)" in content
    assert "não prova fit PT/ES/FR" in content
    assert "contagens de anunciantes e ads vêm apenas de top_ads (top-ad sample)" in content
    assert "desconhecido, não zero" in content


COHORT = {
    "baseline_date": "2026-07-08",
    "current_date": "2026-07-15",
    "previous_ads": ["a1", "a2", "a3", "a4"],
    "current_ads": ["a2", "a3", "a4", "a5"],
    "retained_ads": ["a2", "a3", "a4"],
    "new_ads": ["a5"],
    "removed_ads": ["a1"],
    "ad_retention_pct": 75.0,
    "previous_advertisers": ["p1", "p2", "p3"],
    "current_advertisers": ["p1", "p2", "p3"],
    "retained_advertisers": ["p1", "p2", "p3"],
    "new_advertisers": [],
    "lost_advertisers": [],
    "advertiser_retention_pct": 100.0,
    "top_advertiser_share_pct": 50.0,
    "signals": ["durable_demand"],
}


def test_cohort_renders_markdown_section_and_persists_json(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate(
        "mochila personalizada",
        TRENDS_DATA,
        CSV_DATA,
        ADS_DATA,
        result,
        ad_cohort=COHORT,
    )
    content = open(path, encoding="utf-8").read()
    assert "## Persistência de mercado — pulse quarta (advisory)" in content
    assert "- Janela: 2026-07-08 → 2026-07-15" in content
    assert "- Retenção de ads: 75.0% (3 de 4) | novos: +1 | removidos: -1" in content
    assert "- Retenção de anunciantes: 100.0% (3 de 3) | novos: +0 | perdidos: -0" in content
    assert "- Concentração: maior anunciante com 50.0% das variantes atuais" in content
    assert "- Sinais: durable_demand" in content

    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert raw["ad_cohort"] == COHORT


def test_no_cohort_renders_fallback_and_omits_json_key(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate("mochila", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
    content = open(path, encoding="utf-8").read()
    assert "## Persistência de mercado — pulse quarta (advisory)" in content
    assert "sem baseline" in content

    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert "ad_cohort" not in raw


class TestLandingPricePresentation:
    """A linha de Preço do pack respeita a moeda observada — ARS nunca é
    apresentado como € nem comparado a EUR/Portugal sem aviso."""

    RESULT = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

    def _decision(self, temp_pack_dirs, landing_data):
        path = pack.generate("velas aromáticas", TRENDS_DATA, CSV_DATA, ADS_DATA,
                             self.RESULT, landing_price_data=landing_data)
        return open(path, encoding="utf-8").read()

    def test_non_euro_range_is_labelled_as_source_market_currency(self, temp_pack_dirs):
        content = self._decision(temp_pack_dirs, {
            "price_range": {"min": 14000.0, "max": 45000.0, "currency": "ARS", "sources": 2},
        })
        assert "Preço:     ARS 14000.00–ARS 45000.00 (moeda do mercado de origem)" in content
        assert "margem: custo < ARS 4666.67" in content
        assert "não comparar diretamente com EUR/Portugal sem conversão" in content
        # Nunca apresenta € para preços observados em ARS.
        assert "€14000.00" not in content and "€45000.00" not in content

    def test_euro_range_keeps_legacy_euro_presentation(self, temp_pack_dirs):
        content = self._decision(temp_pack_dirs, {
            "price_range": {"min": 29.99, "max": 59.99, "currency": "EUR", "sources": 2},
        })
        assert "Preço:     €29.99–€59.99 → sourcing manual: margem ok se custo < €10.00" in content
        assert "não comparar diretamente com EUR/Portugal" not in content

    def test_missing_currency_renders_sem_dados_not_euro(self, temp_pack_dirs):
        """Ranges sem currency nunca assumem EUR — renders 'sem dados'."""
        content = self._decision(temp_pack_dirs, {
            "price_range": {"min": 10.0, "max": 20.0},
        })
        assert "Preço:     sem dados" in content
        assert "€10.00" not in content

    @pytest.mark.parametrize(
        "price_range",
        [
            {"min": 10.0, "max": 20.0, "currency": "EURO"},       # 4 letras
            {"min": 10.0, "max": 20.0, "currency": "eu"},         # minúsculas
            {"min": 10.0, "max": 20.0, "currency": ""},           # vazio
            {"min": 10.0, "max": 20.0, "currency": "€"},          # símbolo
            {"min": "n/a", "max": 20.0, "currency": "EUR"},      # min não numérico
            {"min": 10.0, "max": float("nan"), "currency": "EUR"},  # NaN
            {"min": 10.0, "max": float("inf"), "currency": "EUR"},  # Inf
            {"min": -5.0, "max": 20.0, "currency": "EUR"},         # min não positivo
            {"min": 20.0, "max": 10.0, "currency": "EUR"},          # min > max (range invertido)
            {"max": 20.0, "currency": "EUR"},                      # min ausente
            {"min": 10.0, "currency": "EUR"},                      # max ausente
            {"min": 10.0, "max": 20.0, "currency": True},         # currency não str
        ],
    )
    def test_malformed_price_range_renders_sem_dados(self, temp_pack_dirs, price_range):
        """Moeda/min/max ausentes ou malformados → 'Preço: sem dados', nunca
        crash e nunca inventa EUR."""
        content = self._decision(temp_pack_dirs, {"price_range": price_range})
        assert "Preço:     sem dados" in content
        assert "€" not in content.split("Preço:")[1].split("\n")[0]

    def test_mixed_currency_error_is_shown_as_sem_dados(self, temp_pack_dirs):
        """Erro de moedas mistas nunca é confundido com um price_range."""
        content = self._decision(temp_pack_dirs, {
            "error": "sem dados: landings reportam moedas diferentes (ARS, EUR) — ranges não combináveis",
            "mixed_currencies": ["ARS", "EUR"],
        })
        assert "Preço:     sem dados" in content
        assert "ARS, EUR" in content

    def test_reversed_range_renders_sem_dados_not_reversed(self, temp_pack_dirs):
        """min > max é inválido: nunca renderiza um range invertido — a linha
        de Preço fica 'sem dados' e o range não aparece em lado nenhum."""
        content = self._decision(temp_pack_dirs, {
            "price_range": {"min": 59.99, "max": 29.99, "currency": "EUR", "sources": 2},
        })
        assert "Preço:     sem dados" in content
        assert "€59.99" not in content
        assert "€29.99" not in content
        assert "59.99–€29.99" not in content
        assert "59.99–29.99" not in content

    def test_valid_range_unchanged(self, temp_pack_dirs):
        """O caso válido (min < max) continua a renderizar o range."""
        content = self._decision(temp_pack_dirs, {
            "price_range": {"min": 29.99, "max": 59.99, "currency": "EUR", "sources": 2},
        })
        assert "Preço:     €29.99–€59.99" in content


def test_volume_section_renders_gbp_cpc_without_euro_label():
    content = pack._format_volume_section({
        "volume": 5400,
        "cpc": 1.2,
        "currency": "GBP",
        "related_keywords": [],
    })

    assert "CPC médio: £1,20" in content
    assert "€" not in content
