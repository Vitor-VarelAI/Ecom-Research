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


def _format_feedback_section(feedback_data: dict[str, Any] | None = None) -> str:
    """Fase 2. Por agora, sempre sem dados."""
    return "- sem dados: Fase 2 (Reddit/YouTube) ainda não implementada"


def _format_verdict(score_result: dict[str, Any], keyword: str) -> str:
    """Gera 1-2 linhas de veredicto em linguagem de decisão."""
    score = score_result["score"]
    label = score_result["label"]
    bd = score_result["breakdown"]

    if label == "AVANÇAR":
        verdict = (
            f"Score: {score} | {label}\n"
            f"Nicho com sinal forte. Tendência {bd['trend']:.0f}/100, "
            f"longevidade de ads {bd['ad_longevity']:.0f}/100. "
            f"Recomendado alimentar ad-machine."
        )
    elif label == "OBSERVAR":
        verdict = (
            f"Score: {score} | {label}\n"
            f"Nicho com potencial mas sinais mistos. "
            f"Reavaliar em 7-14 dias para confirmar tendência."
        )
    else:
        verdict = (
            f"Score: {score} | {label}\n"
            f"Nicho com sinais fracos — saturação alta ou volume baixo. "
            f"Não recomendado neste momento."
        )

    return verdict


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
    feedback_data: dict[str, Any] | None = None,
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
        feedback_data: dados de Reddit/YouTube (Fase 2), ou None
        run_date: data do run (default: hoje)

    Returns:
        Caminho do ficheiro .md gerado
    """
    if run_date is None:
        run_date = date.today()

    md_path, json_path = _pack_paths(keyword, run_date)

    # --- Gerar .md ---
    verdict_text = _format_verdict(score_result, keyword)
    trends_text = _format_trends_section(trends_data)
    volume_text = _format_volume_section(csv_data)
    ads_text = _format_ads_section(ads_data)
    feedback_text = _format_feedback_section(feedback_data)
    bd = score_result["breakdown"]
    next_step = _format_next_step(score_result["label"])

    md_content = f"""# RESEARCH PACK — {keyword} — {run_date.isoformat()}

## Veredicto
{verdict_text}

## Trend Signal (Google Trends via SerpAPI)
{trends_text}

## Volume & CPC (Keyword Planner CSV)
{volume_text}

## Ads Ativos (Meta Ad Library)
{ads_text}

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
        "feedback": feedback_data,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(raw_pack, f, ensure_ascii=False, indent=2)

    return md_path


def generate_digest_line(keyword: str, score_result: dict[str, Any]) -> str:
    """
    Gera uma linha de digest para o Telegram (3 linhas por keyword).
    Usada pelo send_digest() no final de --all.
    """
    score = score_result["score"]
    label = score_result["label"]
    emoji = {"AVANÇAR": "🟢", "OBSERVAR": "🟡", "DESCARTAR": "🔴"}.get(label, "⚪")
    return f"{emoji} *{keyword}* — Score: {score}/100 ({label})"
