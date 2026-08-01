"""Deterministic research-pack Markdown and raw JSON output."""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any


def _slugify(keyword: str) -> str:
    slug = keyword.lower().strip()
    slug = "".join(char if char.isalnum() or char == "-" else "-" for char in slug)
    return "-".join(slug.split("-")).strip("-")


def _pack_paths(keyword: str, run_date: date | None = None) -> tuple[str, str]:
    if run_date is None:
        run_date = date.today()
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    packs_dir = os.path.join(repo_root, "packs")
    data_dir = os.path.join(repo_root, "data")
    os.makedirs(packs_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    date_str = run_date.isoformat()
    slug = _slugify(keyword)
    return (
        os.path.join(packs_dir, f"{date_str}--{slug}.md"),
        os.path.join(data_dir, f"{date_str}--{slug}.json"),
    )


def _format_trends_section(trends_data: dict[str, Any]) -> str:
    if "error" in trends_data:
        return f"- sem dados: {trends_data['error']}"
    lines = [
        f"- Direção 90 dias: {trends_data.get('direction', '?')} ({trends_data.get('slope_pct', 0.0):+.1f}%)",
        f"- Sazonalidade: {'sim (pico: ' + str(trends_data.get('peak')) + ')' if trends_data.get('seasonal') else 'não'}",
    ]
    top_geos = trends_data.get("top_geos", [])
    if top_geos:
        lines.append("- Geos quentes: " + ", ".join(f"{item.get('geo', '?')} ({item.get('value', 0)})" for item in top_geos))
    else:
        lines.append("- Geos quentes: sem dados")
    related = trends_data.get("related_queries", [])
    if related:
        lines.append("- Queries relacionadas em subida:")
        for item in related[:5]:
            lines.append(f"  - {item.get('keyword', '?')} ({item.get('score', 0)})")
    else:
        lines.append("- Queries relacionadas em subida: sem dados")
    return "\n".join(lines)


def _format_volume_section(csv_data: dict[str, Any]) -> str:
    if "error" in csv_data:
        return f"- sem dados: {csv_data['error']}"
    volume = csv_data.get("volume")
    cpc = csv_data.get("cpc")
    volume_text = f"{volume:,}".replace(",", ".") if volume is not None else "?"
    cpc_text = f"€{cpc:.2f}".replace(".", ",") if cpc is not None else "?"
    lines = [f"- Volume mensal: {volume_text} | CPC médio: {cpc_text}"]
    related = csv_data.get("related_keywords", [])
    if related:
        lines.append("- Top 5 keywords relacionadas com volume:")
        lines.extend(
            f"  {index}. {item.get('keyword', '?')} ({item.get('volume', 0):,})".replace(",", ".")
            for index, item in enumerate(related[:5], 1)
        )
    else:
        lines.append("- Top 5 keywords relacionadas com volume: sem dados")
    return "\n".join(lines)


def _format_ads_section(ads_data: dict[str, Any]) -> str:
    if "error" in ads_data:
        return f"- sem dados: {ads_data['error']}"
    lines = [
        f"- Fonte: {ads_data.get('source', 'Facebook Ad Library via Apify')}",
        f"- Total de ads ativos para o nicho: {ads_data.get('total_ads', 0)}",
    ]
    top_ads = ads_data.get("top_ads", [])
    if top_ads:
        lines.append("- Top 3 ads por longevidade:")
        for ad in top_ads[:3]:
            link = f" | {ad.get('link')}" if ad.get("link") else ""
            lines.append(
                f"  - {ad.get('advertiser', '?')} | ativo há {ad.get('days_active', 0)} dias{link}"
            )
    else:
        lines.append("- Top 3 ads por longevidade: sem dados")
    copies = ads_data.get("copy_examples", ads_data.get("copy_angles", []))
    if copies:
        lines.append("- Exemplos de copy:")
        lines.extend(f"  - {copy}" for copy in copies[:5])
    else:
        lines.append("- Exemplos de copy: sem dados")
    lines.append(f"- Saturação: {ads_data.get('saturation', '?')}")
    lines.append(f"- Amostra: {ads_data.get('raw_ads_count', 0)}/{ads_data.get('sample_limit', 100)}; capped={ads_data.get('sample_capped', False)}")
    return "\n".join(lines)


def _format_seo_section(seo_data: dict[str, Any]) -> str:
    if "error" in seo_data:
        return f"- sem dados: {seo_data['error']}"
    organic = seo_data.get("organic_results", [])
    lines = ["- Top 10 orgânicos: sem dados"]
    if organic:
        lines = ["- Top 10 orgânicos:"]
        for item in organic[:10]:
            lines.append(f"  - {item.get('domain', '?')} (#{item.get('position', 0)}) — {item.get('title', '')[:60]}")
    lines.append(f"- Ads no SERP: {seo_data.get('ads_count', 0)} (proxy de competição paga)")
    paa = seo_data.get("paa_questions", [])
    if paa:
        lines.append("- PAA questions (matéria-prima para conteúdo):")
        lines.extend(f"  {index}. {question}" for index, question in enumerate(paa[:3], 1))
    else:
        lines.append("- PAA questions: sem dados")
    lines.append(f"- Oportunidade SEO: {seo_data.get('seo_opportunity', '?')}")
    return "\n".join(lines)


def _format_feedback_section(feedback_data: dict[str, Any] | None = None) -> str:
    return "- sem dados: Fase 2 (Reddit/YouTube) ainda não implementada"


def _market_countries(trends_data: dict[str, Any]) -> list[str]:
    countries = ["PT", "ES", "FR"]
    if "error" not in trends_data:
        for item in trends_data.get("top_geos", [])[:3]:
            country = str(item.get("geo", "")).upper().strip()
            if country and country not in countries:
                countries.append(country)
    return countries


def _build_market_rows(trends_data: dict[str, Any], ads_data: dict[str, Any]) -> list[dict[str, Any]]:
    geos: dict[str, Any] = {}
    if "error" not in trends_data:
        geos = {str(item.get("geo", "")).upper(): item.get("value") for item in trends_data.get("top_geos", [])}

    advertiser_sets = {country: set() for country in _market_countries(trends_data)}
    ad_counts = {country: 0 for country in _market_countries(trends_data)}
    if "error" not in ads_data:
        for ad in ads_data.get("top_ads", []):
            countries = ad.get("countries", [])
            if not countries:
                countries = [item.get("region", "") for item in ad.get("delivery_by_region", []) if isinstance(item, dict)]
            seen_for_ad: set[str] = set()
            for country in countries:
                country = str(country).upper().strip()
                if country not in ad_counts or country in seen_for_ad:
                    continue
                seen_for_ad.add(country)
                ad_counts[country] += 1
                advertiser_sets[country].add(str(ad.get("page_id") or ad.get("advertiser") or "?"))

    rows = []
    for country in _market_countries(trends_data):
        rows.append({
            "country": country,
            "trends_demand": geos.get(country, "—"),
            "advertisers_observed": len(advertiser_sets[country]) or "—",
            "ads_observed": ad_counts[country] or "—",
        })
    return rows


def _format_market_signals(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| country | Trends demand | advertisers observed | ads observed |",
        "|---------|----------------|----------------------|--------------|",
    ]
    lines.extend(
        f"| {row['country']} | {row['trends_demand']} | {row['advertisers_observed']} | {row['ads_observed']} |"
        for row in rows
    )
    return "\n".join(lines)


def _format_cohort_section(ad_cohort: dict[str, Any] | None) -> str:
    if not ad_cohort:
        return "- Pulse de persistência: sem baseline (seção preenchida no pulse de quarta-feira)"
    baseline = ad_cohort.get("baseline_date") or "?"
    current = ad_cohort.get("current_date") or "?"
    retention = ad_cohort.get("ad_retention_pct")
    adv_retention = ad_cohort.get("advertiser_retention_pct")
    top_share = ad_cohort.get("top_advertiser_share_pct")
    retention_text = f"{retention:.1f}%" if retention is not None else "sem baseline"
    adv_retention_text = f"{adv_retention:.1f}%" if adv_retention is not None else "sem baseline"
    top_share_text = f"{top_share:.1f}%" if top_share is not None else "sem dados"
    lines = [
        f"- Janela: {baseline} → {current}",
        f"- Retenção de ads: {retention_text} ({len(ad_cohort.get('retained_ads', []))} de {len(ad_cohort.get('previous_ads', []))}) | novos: +{len(ad_cohort.get('new_ads', []))} | removidos: -{len(ad_cohort.get('removed_ads', []))}",
        f"- Retenção de anunciantes: {adv_retention_text} ({len(ad_cohort.get('retained_advertisers', []))} de {len(ad_cohort.get('previous_advertisers', []))}) | novos: +{len(ad_cohort.get('new_advertisers', []))} | perdidos: -{len(ad_cohort.get('lost_advertisers', []))}",
        f"- Concentração: maior anunciante com {top_share_text} das variantes atuais",
    ]
    signals = ad_cohort.get("signals", [])
    if signals:
        lines.append("- Sinais: " + ", ".join(signals))
    else:
        lines.append("- Sinais: sem sinal advisory")
    return "\n".join(lines)


def _format_decision(
    keyword: str,
    score_result: dict[str, Any],
    trends_data: dict[str, Any],
    ads_data: dict[str, Any],
    landing_price_data: dict[str, Any] | None,
) -> str:
    label = score_result.get("label", "DADOS INSUFICIENTES")
    if label == "AVANÇAR":
        vender = "SIM"
    elif label == "OBSERVAR":
        vender = "OBSERVAR"
    elif label == "DESCARTAR":
        vender = "NÃO"
    else:
        vender = "DADOS INSUFICIENTES"

    seasonal = ""
    if "error" not in trends_data and trends_data.get("seasonal"):
        seasonal = f" — lançar antes de {trends_data.get('peak', 'pico')}"
    lines = [f"{keyword} — DECISÃO", f"Vender?    {vender}{seasonal}", "Mercado:   decisão manual"]

    if landing_price_data and "price_range" in landing_price_data:
        price = landing_price_data["price_range"]
        target = round(price["min"] / 3, 2)
        lines.append(f"Preço:     €{price['min']:.2f}–€{price['max']:.2f} → sourcing manual: margem ok se custo < €{target:.2f}")
    elif landing_price_data and "error" in landing_price_data:
        lines.append(f"Preço:     sem dados ({landing_price_data['error']})")
    else:
        lines.append("Preço:     sem dados")

    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    if advertisers:
        top = advertisers[0]
        lines.append(f"Prova:     {top.get('page_name', '?')}: {top.get('active_ads', 0)} variantes ativas, anúncio mais antigo com {top.get('oldest_ad_days', 0)} dias")
    else:
        lines.append("Prova:     sem escala detetada")

    if label == "DADOS INSUFICIENTES" or "error" in ads_data:
        risk = "repetir quando o Apify estiver disponível"
    elif ads_data.get("total_ads", 0) == 0:
        risk = "zero ads ativos — sem sinal de mercado"
    elif ads_data.get("saturation") == "alta":
        risk = "mercado com competição publicitária intensa"
    else:
        risk = f"saturação {ads_data.get('saturation', '?')}"
    lines.append(f"Risco:     {risk}")
    return "\n".join(lines)


def _format_next_step(label: str) -> str:
    if label == "AVANÇAR":
        return "AVANÇAR: preparar ad-machine após aprovação manual"
    if label == "OBSERVAR":
        return "OBSERVAR: manter em watchlist e voltar a verificar"
    if label == "DESCARTAR":
        return "DESCARTAR: arquivar salvo nova evidência"
    return "DADOS INSUFICIENTES: repetir quando o Apify estiver disponível"


def _format_suggestions(suggestions: list[dict[str, Any]]) -> str:
    lines = ["Nada foi adicionado a config/keywords.txt; estas sugestões exigem aprovação manual."]
    if suggestions:
        lines.extend(
            f"- {item.get('keyword', '?')} ({item.get('score', 0)}) — status: {item.get('status', 'proposed')}"
            for item in suggestions
        )
    else:
        lines.append("- Sem sugestões nesta execução.")
    return "\n".join(lines)


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
    suggested_keywords: list[dict[str, Any]] | None = None,
    ad_cohort: dict[str, Any] | None = None,
) -> str:
    if run_date is None:
        run_date = date.today()
    seo_data = seo_data or {"error": "SEO não executado — modo reduzido"}
    suggested_keywords = suggested_keywords or []
    md_path, json_path = _pack_paths(keyword, run_date)
    rows = _build_market_rows(trends_data, ads_data)
    breakdown = score_result.get("breakdown", {})

    md_content = f"""# RESEARCH PACK — {keyword} — {run_date.isoformat()}

## DECISÃO
{_format_decision(keyword, score_result, trends_data, ads_data, landing_price_data)}

## Trend Signal (Google Trends via SerpAPI)
{_format_trends_section(trends_data)}

## Sugestões de keywords — aprovação manual
{_format_suggestions(suggested_keywords)}

## Volume & CPC (Keyword Planner CSV)
{_format_volume_section(csv_data)}

## Ads Ativos (Facebook Ad Library via Apify)
{_format_ads_section(ads_data)}

## Persistência de mercado — pulse quarta (advisory)
{_format_cohort_section(ad_cohort)}

## Sinais por mercado — decisão manual
{_format_market_signals(rows)}

## SEO snapshot (SerpAPI engine google, PT)
{_format_seo_section(seo_data)}

## Feedback (Fase 2 — Reddit/YouTube)
{_format_feedback_section(feedback_data)}

## Score breakdown
trend({breakdown.get('trend', 0)} × 0.3) + volume({breakdown.get('volume', 0)} × 0.2) + ad_longevity({breakdown.get('ad_longevity', 0)} × 0.3) + saturação_invertida({breakdown.get('saturacao_invertida', 0)} × 0.2) = {score_result.get('score', 0)}
Fórmula determinística. Sem LLM no score.

## Próximo passo sugerido
{_format_next_step(score_result.get('label', 'DADOS INSUFICIENTES'))}
"""
    with open(md_path, "w", encoding="utf-8") as output:
        output.write(md_content)

    raw_pack = {
        "keyword": keyword,
        "date": run_date.isoformat(),
        "score": score_result,
        "trends": trends_data,
        "keyword_planner": csv_data,
        "facebook_ads": ads_data,
        "seo": seo_data,
        "feedback": feedback_data,
        "landing_price": landing_price_data,
        "suggested_keywords": suggested_keywords,
    }
    if ad_cohort is not None:
        raw_pack["ad_cohort"] = ad_cohort
    with open(json_path, "w", encoding="utf-8") as output:
        json.dump(raw_pack, output, ensure_ascii=False, indent=2)
    return md_path


def generate_digest_line(
    keyword: str,
    score_result: dict[str, Any],
    ads_data: dict[str, Any] | None = None,
    landing_price_data: dict[str, Any] | None = None,
    trends_data: dict[str, Any] | None = None,
) -> str:
    score_value = score_result.get("score", 0)
    label = score_result.get("label", "DADOS INSUFICIENTES")
    if ads_data is None:
        return f"{keyword} — Score: {score_value}/100 ({label})"
    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    proof = f"{advertisers[0].get('page_name', '?')} ({advertisers[0].get('active_ads', 0)} variantes)" if advertisers else "sem escala detetada"
    seasonal = ""
    if trends_data and "error" not in trends_data and trends_data.get("seasonal"):
        seasonal = f" (lançar antes de {trends_data.get('peak', 'pico')})"
    return (
        f"{keyword} — Score: {score_value}/100 ({label})\n"
        f"Vender? {('SIM' if label == 'AVANÇAR' else 'NÃO' if label == 'DESCARTAR' else label)}{seasonal} | "
        f"Mercado: decisão manual | Prova: {proof}"
    )
