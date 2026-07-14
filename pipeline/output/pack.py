"""
Gerador de research-pack.md e .json bruto.

Segue o schema exacto definido em PLAN.md.
Secções sem dados escrevem "sem dados: {razão}", nunca inventam.
"""

import json
import os
from datetime import date
from typing import Any


def _slugify(keyword: str) -> str:
    slug = keyword.lower().strip()
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    slug = "-".join(slug.split("-")).strip("-")
    return slug


def _pack_paths(keyword: str, run_date: date | None = None) -> tuple[str, str]:
    """Devolve (md_path, json_path) para uma keyword."""
    if run_date is None:
        run_date = date.today()
    slug = _slugify(keyword)
    date_str = run_date.isoformat()

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    packs_dir = os.path.join(repo_root, "packs")
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(packs_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    md_path = os.path.join(packs_dir, f"{date_str}--{slug}.md")
    json_path = os.path.join(data_dir, f"{date_str}--{slug}.json")
    return md_path, json_path


def _format_trends_section(trends_data: dict[str, Any]) -> str:
    if "error" in trends_data:
        return f"- sem dados: {trends_data['error']}"

    direction = trends_data.get("direction", "?")
    slope = trends_data.get("slope_pct", 0.0)
    seasonal = trends_data.get("seasonal", False)
    peak = trends_data.get("peak")
    top_geos = trends_data.get("top_geos", [])
    related_queries = trends_data.get("related_queries", [])

    lines = [
        f"- Direção 90 dias: {direction} ({slope:+.1f}%)",
    ]

    if seasonal:
        lines.append(f"- Sazonalidade: sim (pico: {peak})")
    else:
        lines.append("- Sazonalidade: não")

    if top_geos:
        geos_str = ", ".join(f"{g['geo']} ({g['value']})" for g in top_geos)
        lines.append(f"- Geos quentes: {geos_str}")
    else:
        lines.append("- Geos quentes: sem dados")

    if related_queries:
        lines.append("- Queries relacionadas em subida:")
        for rq in related_queries[:5]:
            kw = rq.get("keyword", "?")
            sc = rq.get("score", 0)
            lines.append(f"  - {kw} (+{sc})")
    else:
        lines.append("- Queries relacionadas em subida: sem dados")

    return "\n".join(lines)


def _format_volume_section(csv_data: dict[str, Any]) -> str:
    if "error" in csv_data:
        return f"- sem dados: {csv_data['error']}"

    volume = csv_data.get("volume")
    cpc = csv_data.get("cpc")
    related = csv_data.get("related_keywords", [])

    lines = []
    vol_str = f"{volume:,}".replace(",", ".") if volume is not None else "?"
    cpc_str = f"€{cpc:.2f}".replace(".", ",") if cpc is not None else "?"
    lines.append(f"- Volume mensal: {vol_str} | CPC médio: {cpc_str}")

    if related:
        lines.append("- Top 5 keywords relacionadas com volume:")
        for i, r in enumerate(related, 1):
            kw = r["keyword"]
            vol = r["volume"]
            lines.append(f"  {i}. {kw} ({vol:,})".replace(",", "."))
    else:
        lines.append("- Top 5 keywords relacionadas com volume: sem dados")

    return "\n".join(lines)


def _format_ads_section(ads_data: dict[str, Any]) -> str:
    if "error" in ads_data:
        return f"- sem dados: {ads_data['error']}"

    total = ads_data.get("total_ads", 0)
    top_ads = ads_data.get("top_ads", [])
    copy_angles = ads_data.get("copy_angles", [])
    saturation = ads_data.get("saturation", "?")

    lines = [f"- Total de ads ativos para o nicho: {total}"]

    if top_ads:
        lines.append("- Top 3 ads por longevidade (ad a correr há semanas = ad que paga):")
        for ad in top_ads:
            adv = ad.get("advertiser", "?")
            days = ad.get("days_active", 0)
            reach = ad.get("reach_eu", 0)
            link = ad.get("link", "")
            link_str = f" | {link}" if link else ""
            lines.append(f"  - {adv} | ativo há {days} dias | alcance UE: {reach}{link_str}")
    else:
        lines.append("- Top 3 ads por longevidade: sem dados")

    if copy_angles:
        lines.append("- Ângulos de copy dominantes:")
        for angle in copy_angles:
            lines.append(f"  - {angle}")
    else:
        lines.append("- Ângulos de copy dominantes: sem dados")

    lines.append(f"- Saturação: {saturation}")

    return "\n".join(lines)


def _format_seo_section(seo_data: dict[str, Any]) -> str:
    if "error" in seo_data:
        return f"- sem dados: {seo_data['error']}"

    organic = seo_data.get("organic_results", [])
    ads_count = seo_data.get("ads_count", 0)
    paa = seo_data.get("paa_questions", [])
    opportunity = seo_data.get("seo_opportunity", "?")

    lines = []

    # Top 10 orgânicos — resumo de domínios
    if organic:
        # Agrupar por domínio para mostrar quem domina
        domain_positions: dict[str, list[int]] = {}
        for r in organic:
            domain = r.get("domain", "?")
            pos = r.get("position", 0)
            if domain not in domain_positions:
                domain_positions[domain] = []
            domain_positions[domain].append(pos)

        # Mostrar domínios com posições
        lines.append("- Top 10 orgânicos:")
        for domain, positions in domain_positions.items():
            pos_str = ", ".join(f"#{p}" for p in sorted(positions))
            first_title = next((r.get("title", "")[:60] for r in organic if r.get("domain") == domain), "")
            lines.append(f"  - {domain} ({pos_str}) — {first_title}")
    else:
        lines.append("- Top 10 orgânicos: sem dados")

    # Ads no SERP
    lines.append(f"- Ads no SERP: {ads_count} (proxy de competição paga)")

    # PAA questions
    if paa:
        lines.append("- PAA questions (matéria-prima para conteúdo):")
        for i, q in enumerate(paa, 1):
            lines.append(f"  {i}. {q}")
    else:
        lines.append("- PAA questions: sem dados")

    # Oportunidade SEO
    lines.append(f"- Oportunidade SEO: {opportunity}")

    return "\n".join(lines)


def _format_feedback_section(feedback_data: dict[str, Any] | None = None) -> str:
    """Fase 2. Por agora, sempre sem dados."""
    return "- sem dados: Fase 2 (Reddit/YouTube) ainda não implementada"


def _format_verdict(score_result: dict[str, Any], keyword: str) -> str:
    """Gera 1-2 linhas de veredicto em linguagem de decisão. (mantida para compatibilidade)"""
    score_val = score_result["score"]
    label = score_result["label"]
    bd = score_result["breakdown"]
    reason = score_result.get("reason")

    if reason:
        return f"Score: {score_val} | {label}\n{reason}"

    if label == "AVANÇAR":
        verdict = (
            f"Score: {score_val} | {label}\n"
            f"Nicho com sinal forte. Tendência {bd['trend']:.0f}/100, "
            f"longevidade de ads {bd['ad_longevity']:.0f}/100. "
            f"Recomendado alimentar ad-machine."
        )
    elif label == "OBSERVAR":
        verdict = (
            f"Score: {score_val} | {label}\n"
            f"Nicho com potencial mas sinais mistos. "
            f"Reavaliar em 7-14 dias para confirmar tendência."
        )
    else:
        verdict = (
            f"Score: {score_val} | {label}\n"
            f"Nicho com sinais fracos — saturação alta ou volume baixo. "
            f"Não recomendado neste momento."
        )

    return verdict


def _build_onde_vender_rows(
    trends_data: dict[str, Any],
    ads_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Constrói as linhas da tabela 'Onde vender' cruzando top_geos do Trends
    com delivery_by_region dos ads da Meta.

    Linhas: PT, ES, FR + qualquer país no top 3 do Trends.

    Returns:
        Lista de dicts com {pais, procura_val, procura_str, advertisers_ativos, alcance_ads_str}
    """
    # Países base sempre presentes
    base_countries = ["PT", "ES", "FR"]

    # Extrair top_geos do trends
    top_geos = []
    if "error" not in trends_data:
        top_geos = trends_data.get("top_geos", [])

    # Extrair delivery_by_region agregado de todos os ads
    # Agregar alcance (%) por país
    country_reach: dict[str, float] = {}
    country_advertisers: dict[str, set[str]] = {}

    if "error" not in ads_data:
        top_ads = ads_data.get("top_ads", [])
        for ad in top_ads:
            advertiser = ad.get("advertiser", "?")
            regions = ad.get("delivery_by_region", [])
            if isinstance(regions, list):
                for r in regions:
                    if isinstance(r, dict):
                        region_name = r.get("region", "").upper().strip()
                        if region_name and len(region_name) <= 3:  # só códigos de país
                            pct = r.get("percentage", 0)
                            country_reach[region_name] = country_reach.get(region_name, 0) + pct
                            if region_name not in country_advertisers:
                                country_advertisers[region_name] = set()
                            country_advertisers[region_name].add(advertiser)

    # Construir lookup de procura por país a partir dos top_geos
    geo_values: dict[str, int] = {}
    for g in top_geos:
        geo = g.get("geo", "").upper().strip()
        val = g.get("value", 0)
        if geo:
            geo_values[geo] = val

    # Determinar o conjunto de países a mostrar
    # PT, ES, FR + top 3 geos que não sejam já os base
    countries_order: list[str] = list(dict.fromkeys(base_countries))  # preserve order, dedupe

    for g in top_geos[:3]:
        geo = g.get("geo", "").upper().strip()
        if geo and geo not in countries_order:
            countries_order.append(geo)

    rows = []
    for country in countries_order:
        procura_val = geo_values.get(country)
        procura_str = str(procura_val) if procura_val is not None else "—"
        alcance = country_reach.get(country)
        alcance_str = f"{alcance:.0f}%" if alcance is not None else "—"
        n_adv = len(country_advertisers.get(country, set()))
        adv_str = str(n_adv) if n_adv > 0 else "—"

        rows.append({
            "pais": country,
            "procura_str": procura_str,
            "procura_val": procura_val,
            "advertisers_ativos_str": adv_str,
            "advertisers_ativos_val": n_adv,
            "alcance_ads_str": alcance_str,
        })

    return rows


def _format_onde_vender(rows: list[dict[str, Any]]) -> str:
    """Formata a secção 'Onde vender' como tabela markdown."""
    if not rows:
        return "- sem dados: zero países para cruzar (Trends e Ads sem dados)"

    lines = [
        "| País | Procura (Trends 0-100) | Advertisers ativos | Alcance ads |",
        "|------|-------------------------|-------------------|-------------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['pais']} | {r['procura_str']} | {r['advertisers_ativos_str']} | {r['alcance_ads_str']} |"
        )
    return "\n".join(lines)


def _pick_best_country(rows: list[dict[str, Any]]) -> str:
    """
    Escolhe o melhor país da tabela 'Onde vender':
    procura alta + advertisers moderados.
    Heurística: país com maior procura que tenha advertisers_ativos > 0 mas não excessivos.
    """
    if not rows:
        return "sem dados"

    # Filtrar países com dados de procura
    with_data = [r for r in rows if r["procura_val"] is not None]
    if not with_data:
        return rows[0]["pais"] if rows else "sem dados"

    # Ordenar por procura desc, e entre os de alta procura preferir advertisers moderados (1-5)
    def score_row(r: dict) -> tuple:
        procura = r.get("procura_val", 0) or 0
        adv = r.get("advertisers_ativos_val", 0) or 0
        # Penalizar muitos advertisers (> 10) e zero advertisers
        if adv == 0:
            adv_penalty = -50
        elif adv > 10:
            adv_penalty = -(adv - 10) * 2
        else:
            adv_penalty = 0
        return (-procura, adv_penalty)

    with_data.sort(key=score_row)
    return with_data[0]["pais"]


def _format_decision(
    keyword: str,
    score_result: dict[str, Any],
    trends_data: dict[str, Any],
    ads_data: dict[str, Any],
    landing_price_data: dict[str, Any] | None,
    onde_vender_rows: list[dict[str, Any]],
) -> str:
    """
    Gera o bloco DECISÃO no topo do pack (substitui Veredicto).

    Formato fixo de 5 linhas, cada uma derivada de dados, zero adjetivos.
    """
    label = score_result["label"]

    # ── Vender? ──
    vender_map = {"AVANÇAR": "SIM", "OBSERVAR": "OBSERVAR", "DESCARTAR": "NÃO"}
    vender = vender_map.get(label, label)

    seasonal_note = ""
    if "error" not in trends_data:
        if trends_data.get("seasonal"):
            peak = trends_data.get("peak", "pico")
            seasonal_note = f" — lançar antes de {peak}"

    vender_line = f"Vender?    {vender}{seasonal_note}"

    # ── Onde? ──
    best_country = _pick_best_country(onde_vender_rows)
    onde_line = f"Onde?      {best_country}"

    # ── Preço ──
    if landing_price_data and "price_range" in landing_price_data:
        pr = landing_price_data["price_range"]
        price_min = pr["min"]
        price_max = pr["max"]
        sourcing_target = round(price_min / 3, 2)
        preco_line = (
            f"Preço:     €{price_min:.2f}–€{price_max:.2f} "
            f"→ sourcing manual: margem ok se custo < €{sourcing_target:.2f}"
        )
    elif landing_price_data and "error" in landing_price_data:
        preco_line = f"Preço:     sem dados ({landing_price_data['error']})"
    else:
        preco_line = "Preço:     sem dados"

    # ── Prova ──
    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    if advertisers:
        top = advertisers[0]
        name = top.get("page_name", "Desconhecido")
        variants = top.get("active_ads", 0)
        days = top.get("oldest_ad_days", 0)
        prova_line = f"Prova:     {name}: {variants} variantes ativas, anúncio mais antigo com {days} dias"
    else:
        prova_line = "Prova:     sem escala detetada"

    # ── Risco ──
    saturation = ads_data.get("saturation", "?") if "error" not in ads_data else "?"
    # Derivar 1 linha adicional dos dados
    if "error" not in ads_data:
        total_ads = ads_data.get("total_ads", 0)
        market_proven = ads_data.get("market_proven", False)
        if not market_proven and total_ads > 0:
            risco_extra = "nenhum advertiser com ≥5 variantes ativas — validação de mercado incerta"
        elif total_ads == 0:
            risco_extra = "zero ads ativos — sem sinal de mercado"
        elif saturation == "alta":
            risco_extra = "mercado com competição publicitária intensa — diferenciação obrigatória"
        elif saturation == "baixa":
            risco_extra = "mercado pouco disputado — janela de oportunidade aberta"
        else:
            risco_extra = "competição moderada — monitorizar evolução"
    else:
        risco_extra = "sem dados de ads para avaliar risco"

    # Saturação já está em português no ads_data
    risco_line = f"Risco:     saturação {saturation} — {risco_extra}"

    decision_block = f"{keyword} — DECISÃO\n{vender_line}\n{onde_line}\n{preco_line}\n{prova_line}\n{risco_line}"

    return decision_block


def _format_next_step(label: str) -> str:
    if label == "AVANÇAR":
        return "alimentar ad-machine com este pack"
    elif label == "OBSERVAR":
        return "reavaliar em 14 dias"
    else:
        return "arquivar; reavaliar apenas se tendência inverter"


def generate(
    keyword: str,
    trends_data: dict[str, Any],
    csv_data: dict[str, Any],
    ads_data: dict[str, Any],
    score_result: dict[str, Any],
    seo_data: dict[str, Any] | None = None,
    feedback_data: dict[str, Any] | None = None,
    landing_price_data: dict[str, Any] | None = None,
    run_date: date | None = None,
) -> str:
    """
    Gera o research-pack.md e o .json bruto.

    Args:
        keyword: keyword pesquisada
        trends_data: dict do adapter trends.py
        csv_data: dict do adapter keyword_csv.py
        ads_data: dict do adapter meta_ads.py
        score_result: dict do scoring/score.py
        seo_data: dict do adapter serp_seo.py, ou None
        feedback_data: dados de Reddit/YouTube (Fase 2), ou None
        landing_price_data: dict do adapter landing_price.py, ou None (Delta B)
        run_date: data do run (default: hoje)

    Returns:
        Caminho do ficheiro .md gerado
    """
    if run_date is None:
        run_date = date.today()

    if seo_data is None:
        seo_data = {"error": "SEO não executado — modo reduzido (ex: --wednesday, --monthly)"}

    md_path, json_path = _pack_paths(keyword, run_date)

    # ── Construir tabela Onde vender ──
    onde_vender_rows = _build_onde_vender_rows(trends_data, ads_data)

    # ── Bloco DECISÃO (substitui Veredicto) ──
    decision_text = _format_decision(
        keyword, score_result, trends_data, ads_data,
        landing_price_data, onde_vender_rows,
    )

    # --- Gerar .md ---
    trends_text = _format_trends_section(trends_data)
    volume_text = _format_volume_section(csv_data)
    ads_text = _format_ads_section(ads_data)
    onde_vender_text = _format_onde_vender(onde_vender_rows)
    seo_text = _format_seo_section(seo_data)
    feedback_text = _format_feedback_section(feedback_data)
    bd = score_result["breakdown"]
    next_step = _format_next_step(score_result["label"])

    md_content = f"""# RESEARCH PACK — {keyword} — {run_date.isoformat()}

## DECISÃO
{decision_text}

## Trend Signal (Google Trends via SerpAPI)
{trends_text}

## Volume & CPC (Keyword Planner CSV)
{volume_text}

## Ads Ativos (Meta Ad Library)
{ads_text}

## Onde vender
{onde_vender_text}

## SEO snapshot (SerpAPI engine google, PT)
{seo_text}

## Feedback (Fase 2 — Reddit/YouTube)
{feedback_text}

## Score breakdown
trend({bd['trend']} × 0.3) + volume({bd['volume']} × 0.2) + ad_longevity({bd['ad_longevity']} × 0.3) + saturação_invertida({bd['saturacao_invertida']} × 0.2) = {score_result['score']}
Fórmula determinística. Sem LLM no score.

## Próximo passo sugerido
{next_step}
"""

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # --- Gerar .json bruto ---
    raw_pack = {
        "keyword": keyword,
        "date": run_date.isoformat(),
        "score": score_result,
        "trends": trends_data,
        "keyword_planner": csv_data,
        "meta_ads": ads_data,
        "seo": seo_data,
        "feedback": feedback_data,
        "landing_price": landing_price_data,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(raw_pack, f, ensure_ascii=False, indent=2)

    return md_path


def generate_digest_line(
    keyword: str,
    score_result: dict[str, Any],
    ads_data: dict[str, Any] | None = None,
    landing_price_data: dict[str, Any] | None = None,
    trends_data: dict[str, Any] | None = None,
) -> str:
    """
    Gera uma linha de digest para o Telegram usando o formato DECISÃO.
    Usa as linhas Vender?/Onde?/Prova do bloco DECISÃO como 3 linhas.

    Com dados completos, devolve formato rico. Sem dados, fallback ao formato simples.
    """
    score = score_result["score"]
    label = score_result["label"]
    emoji = {"AVANÇAR": "🟢", "OBSERVAR": "🟡", "DESCARTAR": "🔴"}.get(label, "⚪")

    # Fallback simples quando não há dados adicionais
    if ads_data is None:
        return f"{emoji} *{keyword}* — Score: {score}/100 ({label})"

    lines: list[str] = []

    # ── Vender? ──
    vender_map = {"AVANÇAR": "SIM", "OBSERVAR": "OBSERVAR", "DESCARTAR": "NÃO"}
    vender = vender_map.get(label, label)

    seasonal_note = ""
    if trends_data and "error" not in trends_data and trends_data.get("seasonal"):
        peak = trends_data.get("peak", "pico")
        seasonal_note = f" (lançar antes de {peak})"

    lines.append(f"Vender? {vender}{seasonal_note}")

    # ── Onde? ──
    # Simplificado: usa os advertisers como proxy
    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    if advertisers:
        # Usar delivery_by_region dos top ads para escolher o melhor país
        top_ads = ads_data.get("top_ads", [])
        country_count: dict[str, int] = {}
        for ad in top_ads:
            regions = ad.get("delivery_by_region", [])
            if isinstance(regions, list):
                for r in regions:
                    region = r.get("region", "").upper().strip()
                    if region and len(region) <= 3:
                        country_count[region] = country_count.get(region, 0) + 1
        if country_count:
            best = max(country_count, key=lambda k: country_count[k])
            lines.append(f"Onde? {best}")
        else:
            lines.append("Onde? sem dados")
    else:
        lines.append("Onde? sem dados")

    # ── Prova ──
    if advertisers:
        top = advertisers[0]
        name = top.get("page_name", "?")
        variants = top.get("active_ads", 0)
        lines.append(f"Prova: {name} ({variants} variantes)")
    else:
        lines.append(f"Prova: sem escala detetada")

    header = f"{emoji} *{keyword}* — Score: {score}/100 ({label})"
    return header + "\n" + " | ".join(lines)
