"""Deterministic research-pack Markdown and raw JSON output."""

from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import date, datetime
from typing import Any

from pipeline.run_state import atomic_write_json, atomic_write_text, utc_now_iso

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

# Scheduled modes write a deterministic ``--<mode>`` suffix so that day-1
# monthly + Sunday/Wednesday artifacts for the same keyword and date coexist
# without clobbering each other or legacy manual/full names.
SCHEDULED_MODES = frozenset({"sunday", "wednesday", "monthly"})


def _slugify(keyword: str) -> str:
    slug = keyword.lower().strip()
    slug = "".join(char if char.isalnum() or char == "-" else "-" for char in slug)
    return "-".join(slug.split("-")).strip("-")


def _keyword_hash(keyword: str) -> str:
    """Stable SHA-256 (12 hex chars) of the raw keyword for filename identity."""
    return hashlib.sha256(keyword.encode("utf-8")).hexdigest()[:12]


# Observed peak labels arrive as English timeline dates ("Aug 6, 2026") or,
# in fixtures/ISO exports, as ISO dates / year-month strings.  Month
# abbreviations map explicitly so parsing is locale-independent.
_PEAK_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_DAY_YEAR = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_MONTH_YEAR = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")


def _parse_peak(value: Any) -> date | None:
    """Parse an observed Trends peak label into a date.

    Accepts ISO dates (``2026-08-06``), year-month (``2026-11``) and the
    English timeline labels SerpAPI emits (``Aug 6, 2026``, ``Aug 2026``).
    Returns None for anything unparseable, so callers never invent a deadline.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = _MONTH_DAY_YEAR.fullmatch(text)
    if match:
        month = _PEAK_MONTHS.get(match.group(1).casefold()[:3])
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(2)))
            except ValueError:
                return None
    match = _MONTH_YEAR.fullmatch(text)
    if match:
        month = _PEAK_MONTHS.get(match.group(1).casefold()[:3])
        if month:
            return date(int(match.group(2)), month, 1)
    return None


def _launch_deadline(trends_data: dict[str, Any] | None, run_date: date) -> str | None:
    """Deadline text when a seasonal peak is strictly after ``run_date``.

    Unparseable, past or equal peaks produce no deadline — the raw observed
    peak stays visible in the seasonality evidence instead of being echoed as
    an actionable date.
    """
    if not isinstance(trends_data, dict) or "error" in trends_data or not trends_data.get("seasonal"):
        return None
    peak_date = _parse_peak(trends_data.get("peak"))
    if peak_date is None or peak_date <= run_date:
        return None
    return f"lançar antes de {peak_date.isoformat()}"


def _peak_status(trends_data: dict[str, Any] | None, run_date: date) -> str | None:
    """Timing label for an observed seasonal peak relative to ``run_date``.

    - parsed strictly after ``run_date`` → ``futuro`` (upcoming window);
    - parsed on or before ``run_date`` → ``histórico`` (already observed);
    - unparseable → ``não verificado`` (observed, but no timing evidence).

    Returns None when there is no seasonal-peak evidence, so callers render
    nothing instead of inventing timing advice.
    """
    if not isinstance(trends_data, dict) or "error" in trends_data or not trends_data.get("seasonal"):
        return None
    peak_date = _parse_peak(trends_data.get("peak"))
    if peak_date is None:
        return "não verificado"
    if peak_date > run_date:
        return "futuro"
    return "histórico"


def _pack_paths(
    keyword: str,
    run_date: date | None = None,
    mode: str | None = None,
    run_id: str | None = None,
    product_id: str | None = None,
    market_id: str | None = None,
) -> tuple[str, str]:
    """Resolve the deterministic paths for a pack.

    Legacy (no ``run_id``): ``YYYY-MM-DD--<slug>[--<mode>]`` — unchanged and
    still read by every reader.

    Run-scoped (``run_id`` present): ``YYYY-MM-DD--<mode>--<id12>--
    <slug>--<run_id[:12]>``.  The 12-id segment is the stable
    ``product_id`` when one is supplied (the canonical identity survives
    query renames), falling back to the keyword hash for legacy callers
    that predate product identity.  The slug remains the current research
    query, so renaming a query changes the filename while the product id
    stays stable.  The mode separates keyword/all/sunday/wednesday/monthly
    artifacts and the run-id prefix makes repeated same runs resolve to
    the same path.  Because every run writes unique paths, a crash can
    leave at most an orphan Markdown: it can never replace an older
    complete JSON.
    """
    if run_date is None:
        run_date = date.today()
    packs_dir = os.path.join(_REPO_ROOT, "packs")
    data_dir = os.path.join(_REPO_ROOT, "data")
    os.makedirs(packs_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    date_str = run_date.isoformat()
    slug = _slugify(keyword)
    if run_id:
        mode_part = mode or "manual"
        id_part = product_id or _keyword_hash(keyword)
        rid = run_id[:12]
        market_part = f"--{market_id.lower()}" if market_id else ""
        base = f"{date_str}--{mode_part}--{id_part}{market_part}--{slug}--{rid}"
    else:
        market_part = f"--{market_id.lower()}" if market_id else ""
        base = f"{date_str}{market_part}--{slug}"
        if mode in SCHEDULED_MODES:
            base = f"{base}--{mode}"
    return (
        os.path.join(packs_dir, f"{base}.md"),
        os.path.join(data_dir, f"{base}.json"),
    )


def _format_trends_section(trends_data: dict[str, Any], run_date: date) -> str:
    if "error" in trends_data:
        return f"- sem dados: {trends_data['error']}"
    lines = [
        f"- Direção 90 dias: {trends_data.get('direction', '?')} ({trends_data.get('slope_pct', 0.0):+.1f}%)",
    ]
    if trends_data.get("seasonal"):
        peak = str(trends_data.get("peak"))
        status = _peak_status(trends_data, run_date)
        if status == "futuro":
            lines.append(f"- Sazonalidade: sim (pico: {peak}, futuro)")
        elif status == "histórico":
            lines.append(f"- Sazonalidade: sim (pico: {peak}, histórico — pico já passado)")
        else:
            lines.append(f"- Sazonalidade: sim (pico: {peak}, não verificado — sem data de pico)")
    else:
        lines.append("- Sazonalidade: não")
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
    currency = csv_data.get("currency") or "EUR"
    volume_text = f"{volume:,}".replace(",", ".") if volume is not None else "?"
    if cpc is None:
        cpc_text = "?"
    else:
        amount = f"{cpc:.2f}".replace(".", ",")
        cpc_text = f"€{amount}" if currency == "EUR" else f"£{amount}" if currency == "GBP" else f"{currency} {amount}"
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
    if ads_data.get("evidence_scope") == "market":
        evidence_label = f"Evidência local ({ads_data.get('query_country', '?')})"
    else:
        evidence_label = "Evidência global"
    lines = [
        f"- Fonte: {ads_data.get('source', 'Facebook Ad Library via Apify')}",
        f"- {evidence_label} de ads ativos para o nicho: {ads_data.get('total_ads', 0)} (matched)",
    ]
    top_ads = ads_data.get("top_ads", [])
    if top_ads:
        lines.append("- Top 3 ads por longevidade (matched):")
        for ad in top_ads[:3]:
            link = f" | {ad.get('link')}" if ad.get("link") else ""
            lines.append(
                f"  - {ad.get('advertiser', '?')} | ativo há {ad.get('days_active', 0)} dias{link}"
            )
    else:
        lines.append("- Top 3 ads por longevidade (matched): sem dados")
    copies = ads_data.get("copy_examples", ads_data.get("copy_angles", []))
    if copies:
        lines.append("- Exemplos de copy (matched):")
        lines.extend(f"  - {copy}" for copy in copies[:5])
    else:
        lines.append("- Exemplos de copy (matched): sem dados")
    lines.append(f"- Saturação (matched): {ads_data.get('saturation', '?')}")
    lines.append(f"- Amostra: {ads_data.get('raw_ads_count', 0)}/{ads_data.get('sample_limit', 100)}; capped={ads_data.get('sample_capped', False)}")
    lines.append(
        f"- Filtrados: inativos={ads_data.get('filtered_inactive_count', 0)} | "
        f"irrelevantes={ads_data.get('filtered_irrelevant_count', 0)} | "
        f"total excluídos={ads_data.get('filtered_out_count', 0)} | "
        f"duplicados={ads_data.get('duplicates_removed', 0)}"
    )
    lines.append(
        f"- Classificação: candidatos={ads_data.get('candidate_count', 0)} | "
        f"matched={ads_data.get('matched_count', 0)} | "
        f"needs_review={ads_data.get('needs_review_count', 0)} | "
        f"rejected={ads_data.get('rejected_count', 0)}"
    )
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
        "| country | Trends demand | advertisers observed (top-ad sample) | ads observed (top-ad sample) |",
        "|---------|----------------|---------------------------------------|-----------------------------|",
    ]
    lines.extend(
        f"| {row['country']} | {row['trends_demand']} | {row['advertisers_observed']} | {row['ads_observed']} |"
        for row in rows
    )
    lines.append(
        "- Nota: a evidência de Ads Library é descoberta global (query country=ALL) — "
        "não prova fit PT/ES/FR; contagens de anunciantes e ads vêm apenas de "
        "top_ads (top-ad sample), não da coorte global retida; células em branco "
        "('—') significam desconhecido, não zero."
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


_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _valid_price_range(price: Any) -> bool:
    """True quando um price_range tem moeda ISO de 3 letras e min/max finitos
    e positivos, com min <= max.  Nunca se assume moeda ausente (nem EUR);
    min/max ausentes, não numéricos, NaN/Inf, não positivos ou invertidos
    (min > max) tornam o range inválido — nunca se renderiza um range
    invertido."""
    if not isinstance(price, dict):
        return False
    currency = price.get("currency")
    if not isinstance(currency, str) or not _ISO_CURRENCY_RE.fullmatch(currency):
        return False
    for key in ("min", "max"):
        value = price.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)) or float(value) <= 0:
            return False
    if float(price["min"]) > float(price["max"]):
        return False
    return True


def _format_decision(
    keyword: str,
    score_result: dict[str, Any],
    trends_data: dict[str, Any],
    ads_data: dict[str, Any],
    landing_price_data: dict[str, Any] | None,
    run_date: date,
    market_id: str | None = None,
) -> str:
    label = score_result.get("label", "DADOS INSUFICIENTES")
    display_label = f"{label} {market_id}" if market_id else label

    seasonal = ""
    deadline = _launch_deadline(trends_data, run_date)
    if deadline:
        seasonal = f" — {deadline}"
    lines = [
        f"{keyword}{f' · {market_id}' if market_id else ''} — DECISÃO",
        f"Sinal advisory: {display_label}{seasonal}",
        f"Mercado:   {market_id}" if market_id else "Mercado:   decisão manual",
        "Validação: revisão manual obrigatória antes de qualquer ação (produto, mercado, margem, campanha)",
    ]

    if landing_price_data and "price_range" in landing_price_data and _valid_price_range(landing_price_data.get("price_range")):
        price = landing_price_data["price_range"]
        currency = str(price["currency"]).upper()
        target = round(float(price["min"]) / 3, 2)
        min_v = float(price["min"])
        max_v = float(price["max"])
        if currency == "EUR":
            lines.append(f"Preço:     €{min_v:.2f}–€{max_v:.2f} → sourcing manual: margem ok se custo < €{target:.2f}")
        else:
            lines.append(
                f"Preço:     {currency} {min_v:.2f}–{currency} {max_v:.2f} "
                f"(moeda do mercado de origem) → margem: custo < {currency} {target:.2f}"
            )
            lines.append(
                "Nota:      preços observados em moeda do mercado de origem (não EUR) — "
                "não comparar diretamente com EUR/Portugal sem conversão"
            )
    elif landing_price_data and "error" in landing_price_data:
        lines.append(f"Preço:     sem dados ({landing_price_data['error']})")
    else:
        lines.append("Preço:     sem dados")

    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    if advertisers:
        top = advertisers[0]
        proof_scope = market_id or "global"
        lines.append(
            f"Prova {proof_scope}: {top.get('page_name', '?')}: "
            f"{top.get('active_ads', 0)} variantes ativas na coorte {proof_scope}, "
            f"anúncio mais antigo com {top.get('oldest_ad_days', 0)} dias"
        )
    else:
        lines.append(f"Prova {market_id or 'global'}: sem escala detetada")

    if label == "DADOS INSUFICIENTES" or "error" in ads_data:
        risk = score_result.get("reason") or "repetir quando o Apify estiver disponível"
    elif ads_data.get("total_ads", 0) == 0:
        risk = "zero matched — sem sinal de mercado"
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


def _format_provenance_section(
    *,
    mode: str | None,
    run_id: str | None,
    scoring_version: str | None,
    matcher_version: str | None,
    generated_at: str | None,
    completed_at: str | None,
    source_states: dict[str, Any] | None,
) -> str:
    """Concise provenance block; only rendered when run metadata is present."""
    if not (mode or run_id):
        return ""
    lines = ["## Proveniência"]
    bits = []
    if mode:
        bits.append(f"modo {mode}")
    if run_id:
        bits.append(f"run {run_id[:12]}")
    if scoring_version:
        bits.append(f"scoring v{scoring_version}")
    if matcher_version:
        bits.append(f"matcher v{matcher_version}")
    if bits:
        lines.append("- " + ", ".join(bits))
    if generated_at:
        lines.append(f"- Gerado: {generated_at}")
    if completed_at and completed_at != generated_at:
        lines.append(f"- Concluído: {completed_at}")
    if source_states:
        rendered = []
        for name, entry in sorted(source_states.items()):
            status = entry.get("status", "?")
            if status == "valid" and entry.get("origin") == "fresh":
                rendered.append(f"{name}=valid")
            else:
                rendered.append(f"{name}={status}({entry.get('origin', '?')})")
        lines.append("- Fontes: " + ", ".join(rendered))
    lines.append("- Políticas de fontes e evidência usada: ver JSON (source_policies / source_states)")
    return "\n".join(lines) + "\n"


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
    mode: str | None = None,
    run_id: str | None = None,
    product_id: str | None = None,
    product_name: str | None = None,
    research_keyword: str | None = None,
    market: dict[str, str] | None = None,
    market_signals: list[str] | None = None,
    source_states: dict[str, Any] | None = None,
    source_policies: dict[str, Any] | None = None,
    scoring_version: str | None = None,
    scoring_config: dict[str, Any] | None = None,
    matcher_version: str | None = None,
    matching_schema_version: int | None = None,
    generated_at: str | None = None,
    completed_at: str | None = None,
) -> str:
    if run_date is None:
        run_date = date.today()
    seo_data = seo_data or {"error": "SEO não executado — modo reduzido"}
    suggested_keywords = suggested_keywords or []
    if market is None:
        if mode is None:
            md_path, json_path = _pack_paths(keyword, run_date)
        else:
            md_path, json_path = _pack_paths(keyword, run_date, mode, run_id, product_id=product_id)
    else:
        md_path, json_path = _pack_paths(
            keyword,
            run_date,
            mode,
            run_id,
            product_id=product_id,
            market_id=market["market_id"],
        )
    rows = _build_market_rows(trends_data, ads_data)
    breakdown = score_result.get("breakdown", {})
    now = generated_at or utc_now_iso()
    done_at = completed_at or now

    market_id = market.get("market_id") if market else None
    title_market = f" — {market_id}" if market_id else ""
    seo_market = market_id or "PT"
    if market_id:
        signal_text = ", ".join(market_signals or []) or "nenhum sinal adicional confirmado"
        market_section = f"## Sinais {market_id}\n{signal_text}"
    else:
        market_section = "## Sinais por mercado — decisão manual\n" + _format_market_signals(rows)

    provenance = _format_provenance_section(
        mode=mode,
        run_id=run_id,
        scoring_version=scoring_version,
        matcher_version=matcher_version,
        generated_at=now,
        completed_at=done_at,
        source_states=source_states,
    )

    md_content = f"""# RESEARCH PACK — {keyword}{title_market} — {run_date.isoformat()}

## DECISÃO
{_format_decision(
    keyword,
    score_result,
    trends_data,
    ads_data,
    landing_price_data,
    run_date,
    market_id=market_id,
)}

## Trend Signal (Google Trends via SerpAPI)
{_format_trends_section(trends_data, run_date)}

## Sugestões de keywords — aprovação manual
{_format_suggestions(suggested_keywords)}

## Volume & CPC (Keyword Planner CSV)
{_format_volume_section(csv_data)}

## Ads Ativos (Facebook Ad Library via Apify)
{_format_ads_section(ads_data)}

## Persistência de mercado — pulse quarta (advisory)
{_format_cohort_section(ad_cohort)}

{market_section}

## SEO snapshot (SerpAPI engine google, {seo_market})
{_format_seo_section(seo_data)}

## Feedback (Fase 2 — Reddit/YouTube)
{_format_feedback_section(feedback_data)}

## Score breakdown
trend({breakdown.get('trend', 0)} × 0.3) + volume({breakdown.get('volume', 0)} × 0.2) + ad_longevity({breakdown.get('ad_longevity', 0)} × 0.3) + saturação_invertida({breakdown.get('saturacao_invertida', 0)} × 0.2) = {score_result.get('score', 0)}
Fórmula determinística. Sem LLM no score.

## Próximo passo sugerido
{_format_next_step(score_result.get('label', 'DADOS INSUFICIENTES'))}

{provenance}"""
    atomic_write_text(md_path, md_content)

    raw_pack: dict[str, Any] = {
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
    # Canonical product identity (additive): ``keyword`` stays the actual
    # research query; product identity survives query renames.  Only written
    # when supplied, so legacy/no-identity packs are unchanged bytes.
    if product_id is not None:
        raw_pack["product_id"] = product_id
    if product_name is not None:
        raw_pack["product_name"] = product_name
    if research_keyword is not None:
        raw_pack["research_keyword"] = research_keyword
    if market is not None:
        raw_pack["market_id"] = market["market_id"]
        raw_pack["market"] = market
    if market_signals is not None:
        raw_pack["market_signals"] = market_signals
    if ad_cohort is not None:
        raw_pack["ad_cohort"] = ad_cohort
    # Additive run/provenance metadata; legacy top-level keys stay as aliases.
    if run_id is not None:
        raw_pack["run_id"] = run_id
    if mode is not None:
        raw_pack["mode"] = mode
    raw_pack["generated_at"] = now
    raw_pack["completed_at"] = done_at
    if scoring_version is not None:
        raw_pack["scoring_version"] = scoring_version
    if scoring_config is not None:
        raw_pack["scoring_config"] = scoring_config
    if matcher_version is not None:
        raw_pack["matcher_version"] = matcher_version
    if matching_schema_version is not None:
        raw_pack["matching_schema_version"] = matching_schema_version
    if source_states is not None:
        raw_pack["source_states"] = source_states
    if source_policies is not None:
        raw_pack["source_policies"] = source_policies
    # Written last: a run-scoped JSON without this flag is an incomplete
    # artifact and readers ignore it (an orphan Markdown without a JSON can
    # never replace an older complete JSON, thanks to unique run paths).
    raw_pack["artifact_complete"] = True
    atomic_write_json(json_path, raw_pack)
    return md_path


def generate_digest_line(
    keyword: str,
    score_result: dict[str, Any],
    ads_data: dict[str, Any] | None = None,
    landing_price_data: dict[str, Any] | None = None,
    trends_data: dict[str, Any] | None = None,
    run_date: date | None = None,
    market_id: str | None = None,
) -> str:
    if run_date is None:
        run_date = date.today()
    score_value = score_result.get("score", 0)
    label = score_result.get("label", "DADOS INSUFICIENTES")
    if ads_data is None:
        return f"{keyword} — Score: {score_value}/100 ({label})"
    advertisers = ads_data.get("advertisers", []) if "error" not in ads_data else []
    proof = f"{advertisers[0].get('page_name', '?')} ({advertisers[0].get('active_ads', 0)} variantes)" if advertisers else "sem escala detetada"
    seasonal = ""
    deadline = _launch_deadline(trends_data, run_date)
    if deadline:
        seasonal = f" ({deadline})"
    market_text = market_id or "decisão manual"
    proof_scope = market_id or "global"
    return (
        f"{keyword} — Score: {score_value}/100 ({label})\n"
        f"Sinal advisory: {label}{seasonal} | "
        f"Mercado: {market_text} | Prova {proof_scope}: {proof} "
        f"(coorte {proof_scope}) | Ação: validação manual"
    )
