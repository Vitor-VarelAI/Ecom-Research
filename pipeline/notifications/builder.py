"""Renderer-neutral NotificationBatch builder from existing run results.

Slice 3 of the Telegram rich outbound: one deterministic
:class:`~pipeline.notifications.model.NotificationBatch` per run mode, built
from the same result dicts the pipeline already produces.  The batch carries
the exact legacy plain-text digest as ``audit_text`` (the same string run.py
used to print and persist), while each item's structured content is
renderer-neutral and derived from the result data only.

Mode shapes (unchanged contract):

* ``keyword``   — exactly one product item, no summary;
* ``sunday``/``all`` — summary first, then one product per result;
* ``wednesday`` — pulse summary first, then one compact product per result
  preserving changes, ad/advertiser retention, signals (including
  ``high_churn``/churn semantics) and advertiser deltas;
* ``monthly``   — summary first, then one product per affected result.

Absence mapping: suppliers, shipping, margins and legal evidence do not exist
in this pipeline stage, so the builder renders the canonical placeholders
(FORNECEDOR EM FALTA / POR VALIDAR / DOCUMENTAÇÃO EM FALTA / PESQUISA
NECESSÁRIA). Nothing is invented here: no supplier, margin, legal conclusion,
workflow gate or media. Public investigation URLs come only from sanitized
persisted evidence or deterministic searches on known hosts; there are no
callbacks.

:func:`notification_snapshot` extracts the minimal credential-free projection
of one result, including the safe public investigation links needed to rebuild
the exact batch
from a persisted (compact) manifest without rerunning any source.  Results
without a snapshot degrade safely to placeholders.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Sequence
from urllib.parse import urlencode

from pipeline.adapters.url_sanitize import sanitize_ad_library_link, sanitize_url

from pipeline.notifications.model import (
    KIND_PRODUCT,
    KIND_SUMMARY,
    NotificationAction,
    NotificationBatch,
    NotificationField,
    NotificationItem,
    NotificationSection,
    stable_product_id,
)
from pipeline.notifications.placeholders import (
    DOCUMENTATION_MISSING,
    MISSING,
    RESEARCH_NEEDED,
    SUPPLIER_MISSING,
    TO_VALIDATE,
)
from pipeline.output import pack

_MODE_TITLES: dict[str, Any] = {
    "sunday": lambda run_date: f"DOMINGO — {run_date.isoformat()}",
    "all": lambda run_date: f"RESUMO DO DIA — {run_date.isoformat()}",
    "wednesday": lambda run_date: f"PULSE QUARTA — {run_date.isoformat()}",
    "monthly": lambda run_date: f"RE-SCORE MENSAL — {run_date.isoformat()}",
}


def mode_title(mode: str, run_date: date) -> str | None:
    """Legacy digest title for a mode, or None (keyword has no summary)."""
    formatter = _MODE_TITLES.get(mode)
    return formatter(run_date) if formatter else None


def _safe_action_data(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    url = value.get("url")
    if not isinstance(label, str) or not isinstance(url, str):
        return None
    try:
        NotificationAction(label=label, url=url)
    except ValueError:
        return None
    return {"label": label, "url": url}


def _investigation_action_data(result: dict[str, Any]) -> list[dict[str, str]]:
    persisted = result.get("investigation_actions")
    if isinstance(persisted, list):
        actions: list[dict[str, str]] = []
        for item in persisted:
            safe = _safe_action_data(item)
            if safe is not None:
                actions.append(safe)
        return actions
    # Historical compact manifests have no action snapshot. Do not change
    # their durable payload on replay; only fresh results get the new actions.
    if isinstance(result.get("notification_snapshot"), dict):
        return []

    actions: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add(label: str, url: str | None) -> None:
        if url and url not in seen_urls:
            actions.append({"label": label, "url": url})
            seen_urls.add(url)

    ads = result.get("ads_data")
    if isinstance(ads, dict) and "error" not in ads:
        top_ads = ads.get("top_ads")
        if isinstance(top_ads, list) and top_ads:
            top = top_ads[0] if isinstance(top_ads[0], dict) else {}
            add("Ver anúncio", sanitize_ad_library_link(top.get("link")))
            add("Abrir landing", sanitize_url(top.get("landing_url")))

    keyword = result.get("research_keyword") or result.get("keyword")
    market_id = result.get("market_id")
    if not isinstance(keyword, str) or not keyword.strip() or not isinstance(market_id, str):
        return actions
    market = result.get("market") if isinstance(result.get("market"), dict) else {}
    geo = str(market.get("trends_geo") or market_id)
    gl = str(market.get("serp_gl") or ("uk" if market_id == "GB" else market_id.lower()))
    hl = str(market.get("serp_hl") or ("en" if market_id == "GB" else "pt"))
    ads_country = str(market.get("ads_country") or market_id)

    add("Google Trends", "https://trends.google.com/trends/explore?" + urlencode({
        "geo": geo,
        "q": keyword,
    }))
    add("Google Shopping", "https://www.google.com/search?" + urlencode({
        "tbm": "shop",
        "gl": gl,
        "hl": hl,
        "q": keyword,
    }))
    add("Meta Ads Library", "https://www.facebook.com/ads/library/?" + urlencode({
        "active_status": "active",
        "ad_type": "all",
        "country": ads_country,
        "is_targeted_country": "true",
        "media_type": "all",
        "q": keyword,
        "search_type": "keyword_unordered",
    }))
    supplier_terms = (
        f"{keyword} fornecedor grossista dropshipping"
        if hl == "pt" else f"{keyword} wholesale supplier dropshipping"
    )
    add("Pesquisar fornecedor", "https://www.google.com/search?" + urlencode({
        "gl": gl,
        "hl": hl,
        "q": supplier_terms,
    }))
    return actions


def _investigation_actions(result: dict[str, Any]) -> tuple[NotificationAction, ...]:
    return tuple(
        NotificationAction(label=item["label"], url=item["url"])
        for item in _investigation_action_data(result)
    )


def notification_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """Minimal credential-free projection of one result.

    Only the safe scalar/list fields the builder reads survive: advertiser
    ``page_name``/``active_ads``, ``saturation``, ``total_ads`` and the
    trends scalars (``direction``/``slope_pct``/``seasonal``/``peak``).
    Errors, raw payloads, tokens, chat ids and full adapter responses never
    reach the snapshot. The only URLs retained are sanitized evidence links
    and deterministic searches on known public hosts.
    """
    snapshot: dict[str, Any] = {}
    # Canonical product identity (safe scalars): the builder needs it to
    # rebuild the exact batch identity from a persisted compact result
    # without any source call.  Absent in historical results, so legacy
    # snapshots are byte-unchanged.
    for key in ("product_id", "market_id", "product_name", "research_keyword"):
        value = result.get(key)
        if isinstance(value, str) and value:
            snapshot[key] = value
    ads = result.get("ads_data")
    if isinstance(ads, dict) and "error" not in ads:
        safe_ads: dict[str, Any] = {}
        advertisers = ads.get("advertisers")
        if isinstance(advertisers, list):
            safe = [
                {"page_name": advertiser.get("page_name"), "active_ads": advertiser.get("active_ads")}
                for advertiser in advertisers
                if isinstance(advertiser, dict)
            ]
            if safe:
                safe_ads["advertisers"] = safe
        for key in ("saturation", "total_ads"):
            value = ads.get(key)
            if value is not None:
                safe_ads[key] = value
        if safe_ads:
            snapshot["ads_data"] = safe_ads
    actions = _investigation_action_data(result)
    if actions:
        snapshot["investigation_actions"] = actions
    signals = result.get("market_signals")
    if isinstance(signals, list) and all(isinstance(signal, str) for signal in signals):
        snapshot["market_signals"] = signals
    trends = result.get("trends_data")
    if isinstance(trends, dict) and "error" not in trends:
        safe_trends = {
            key: trends[key]
            for key in ("direction", "slope_pct", "seasonal", "peak")
            if trends.get(key) is not None
        }
        if safe_trends:
            snapshot["trends_data"] = safe_trends
    return snapshot


def _normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Merge the safe snapshot into a compact result so the builder reads the
    same keys whether given a full fresh result or a persisted compact one."""
    snapshot = result.get("notification_snapshot")
    if not isinstance(snapshot, dict):
        return result
    normalized = dict(result)
    if not isinstance(normalized.get("ads_data"), dict):
        normalized["ads_data"] = snapshot.get("ads_data")
    if not isinstance(normalized.get("trends_data"), dict):
        normalized["trends_data"] = snapshot.get("trends_data")
    for key in ("product_id", "market_id", "product_name", "research_keyword"):
        if not normalized.get(key) and snapshot.get(key):
            normalized[key] = snapshot[key]
    if not isinstance(normalized.get("market_signals"), list):
        normalized["market_signals"] = snapshot.get("market_signals")
    if not isinstance(normalized.get("investigation_actions"), list):
        normalized["investigation_actions"] = snapshot.get("investigation_actions")
    return normalized


def _proof_text(ads_data: Any) -> str:
    """The digest's proof fragment (top advertiser); always a string."""
    if not isinstance(ads_data, dict) or "error" in ads_data:
        return "sem escala detetada"
    advertisers = ads_data.get("advertisers") or []
    if not advertisers:
        return "sem escala detetada"
    first = advertisers[0]
    return f"{first.get('page_name', '?')} ({first.get('active_ads', 0)} variantes)"


def _display_keyword(result: dict[str, Any]) -> str:
    keyword = result.get("keyword") or "?"
    market_id = result.get("market_id")
    return f"{keyword} · {market_id}" if market_id else keyword


def _market_label(result: dict[str, Any], label: str) -> str:
    market_id = result.get("market_id")
    return f"{label} {market_id}" if market_id else label


def _full_item_audit(result: dict[str, Any], run_date: date, *, with_suggestions: bool) -> str:
    """Exact per-result digest contribution of the legacy full modes.

    ``with_suggestions`` mirrors the legacy behaviour: the sunday/all/monthly
    digests append the suggestions line, the keyword digest is the bare
    ``generate_digest_line`` output."""
    display_keyword = _display_keyword(result)
    if result.get("error") and result.get("preserved"):
        return f"{display_keyword} — fonte falhou; pack anterior preservado ({result.get('error', '')})"
    score_result = dict(result.get("score_result") or {"score": 0, "label": "DADOS INSUFICIENTES"})
    score_result["label"] = _market_label(result, score_result.get("label", "DADOS INSUFICIENTES"))
    lines = [
        pack.generate_digest_line(
            display_keyword,
            score_result,
            result.get("ads_data") or {},
            trends_data=result.get("trends_data") or {},
            run_date=run_date,
            market_id=result.get("market_id"),
        )
    ]
    market_signals = result.get("market_signals") or []
    if market_signals:
        lines.append("Sinais de mercado: " + ", ".join(str(signal) for signal in market_signals))
    if with_suggestions:
        suggestions = (result.get("suggested_keywords") or [])[:3]
        if suggestions:
            lines.append("Sugestões (aprovação manual): " + ", ".join(item["keyword"] for item in suggestions))
    return "\n".join(lines)


def _wednesday_item_audit(result: dict[str, Any]) -> str:
    """Exact per-result digest contribution of the legacy pulse digest."""
    display_keyword = _display_keyword(result)
    if result.get("preserved"):
        error = result.get("error", "")
        if error:
            return f"{display_keyword} — pack anterior preservado ({error})"
        return f"{display_keyword} — Apify falhou; pack anterior preservado"
    line = f"{display_keyword}: {', '.join(result.get('changes', []))}"
    cohort = result.get("ad_cohort")
    if cohort:
        retention = cohort.get("ad_retention_pct")
        retention_text = f"{retention:.1f}%" if retention is not None else "sem baseline"
        line += f" | retenção ads {retention_text}"
        signals = cohort.get("signals", [])
        if signals:
            line += " | Sinais: " + ", ".join(str(signal) for signal in signals)
    if result.get("advertiser_deltas"):
        line += " | " + ", ".join(str(delta) for delta in result["advertiser_deltas"][:2])
    return line


def _commercial_section() -> NotificationSection:
    """Explicit placeholders: the pipeline has no supplier/shipping/margin data."""
    return NotificationSection(
        heading="Comercial",
        as_table=True,
        fields=(
            NotificationField(value=SUPPLIER_MISSING, label="Dropshipping"),
            NotificationField(value=TO_VALIDATE, label="PT / GB / US"),
            NotificationField(value=TO_VALIDATE, label="Margem"),
        ),
    )


def _legal_section() -> NotificationSection:
    """Explicit placeholders: no legal evidence exists in this stage."""
    return NotificationSection(
        heading="Legal Gate — UE/Portugal",
        fields=(
            NotificationField(value=DOCUMENTATION_MISSING, label="Estado"),
            NotificationField(value=RESEARCH_NEEDED, label="Safety Gate"),
        ),
    )


def _footer_date(run_date: date) -> tuple[NotificationField, ...]:
    return (
        NotificationField(
            value=run_date.strftime("%d/%m/%Y"),
            label="Última atualização dos dados",
        ),
    )


def _score_header(result: dict[str, Any]) -> tuple[NotificationField, ...]:
    score_result = result.get("score_result") or {}
    header: list[NotificationField] = []
    if score_result.get("score") is not None:
        header.append(NotificationField(value=f"{score_result['score']}/100", label="Score"))
    if score_result.get("label"):
        header.append(NotificationField(
            value=_market_label(result, score_result["label"]),
            label="Sinal advisory",
        ))
    return tuple(header)


def _preserved_section(result: dict[str, Any]) -> NotificationSection:
    fields = [NotificationField(value="pack anterior preservado", label="Estado")]
    if result.get("error"):
        fields.append(NotificationField(value=str(result["error"]), label="Fonte"))
    return NotificationSection(heading="Fonte", fields=tuple(fields))


def _product_identity(result: dict[str, Any]) -> str:
    """The canonical product id for a result item.

    New runs carry ``product_id`` on every result (including preserved and
    technical-fatal ones); that id is authoritative.  Only a historical
    compact result without a ``product_id`` falls back to the legacy
    keyword-hash identity (``stable_product_id(keyword)``) — a read-only
    fallback that never rewrites anything.
    """
    keyword = result.get("keyword") or "?"
    product_id = result.get("product_id") or stable_product_id(keyword)
    market_id = result.get("market_id")
    return f"{product_id}:{market_id}" if market_id else product_id


def _full_product_item(result: dict[str, Any], run_date: date, *, with_suggestions: bool) -> NotificationItem:
    market_id = result.get("market_id")
    sections: list[NotificationSection] = []
    if result.get("preserved"):
        sections.append(_preserved_section(result))
    else:
        ads = result.get("ads_data") or {}
        proof_label = f"Prova {market_id}" if market_id else "Prova global"
        evidence = [NotificationField(value=_proof_text(ads), label=proof_label)]
        if isinstance(ads, dict) and "error" not in ads:
            if ads.get("total_ads") is not None:
                evidence.append(NotificationField(value=str(ads["total_ads"]), label="Ads ativos"))
            if ads.get("saturation") is not None:
                evidence.append(NotificationField(value=str(ads["saturation"]), label="Saturação"))
        evidence_heading = f"Evidência {market_id}" if market_id else "Evidência global"
        sections.append(NotificationSection(
            heading=evidence_heading,
            as_table=True,
            fields=tuple(evidence),
        ))
        trends = result.get("trends_data") or {}
        trend_fields: list[NotificationField] = []
        if isinstance(trends, dict) and "error" not in trends:
            if trends.get("direction") is not None:
                trend_fields.append(NotificationField(value=str(trends["direction"]), label="Direção"))
            if trends.get("slope_pct") is not None:
                trend_fields.append(NotificationField(value=f"{trends['slope_pct']}%", label="Variação"))
            if trends.get("seasonal") is not None:
                value = "sim" if trends["seasonal"] else "não"
                peak = trends.get("peak")
                if peak:
                    value = f"sim (pico: {peak})"
                trend_fields.append(NotificationField(value=value, label="Sazonalidade"))
        if trend_fields:
            sections.append(NotificationSection(
                heading="Tendência",
                as_table=True,
                fields=tuple(trend_fields),
            ))
    signals = [signal for signal in (result.get("market_signals") or []) if isinstance(signal, str)]
    if signals:
        heading = f"Sinais {market_id}" if market_id else "Sinais de mercado"
        sections.append(NotificationSection(
            heading=heading,
            fields=(NotificationField(value=", ".join(signals), label="Estado"),),
        ))
    suggestions = [s for s in (result.get("suggested_keywords") or []) if isinstance(s, dict)][:3]
    suggestions_text = ", ".join(str(s.get("keyword", "")) for s in suggestions) or MISSING
    sections.append(NotificationSection(
        heading="Sugestões",
        fields=(NotificationField(value=suggestions_text, label="Sugestões (aprovação manual)"),),
    ))
    sections.append(_commercial_section())
    sections.append(_legal_section())
    return NotificationItem(
        kind=KIND_PRODUCT,
        title=_display_keyword(result),
        product=_product_identity(result),
        header_fields=_score_header(result),
        sections=tuple(sections),
        actions=_investigation_actions(result),
        footer_fields=_footer_date(run_date),
        audit_text=_full_item_audit(result, run_date, with_suggestions=with_suggestions),
    )


def _wednesday_product_item(result: dict[str, Any], run_date: date) -> NotificationItem:
    sections: list[NotificationSection] = []
    if result.get("preserved"):
        sections.append(_preserved_section(result))
    else:
        cohort = result.get("ad_cohort") or {}
        pulse: list[NotificationField] = []
        changes = result.get("changes") or []
        if changes:
            pulse.append(NotificationField(value=", ".join(changes), label="Alterações"))
        ad_retention = cohort.get("ad_retention_pct")
        pulse.append(NotificationField(
            value=f"{ad_retention:.1f}%" if ad_retention is not None else "sem baseline",
            label="Retenção ads",
        ))
        advertiser_retention = cohort.get("advertiser_retention_pct")
        pulse.append(NotificationField(
            value=f"{advertiser_retention:.1f}%" if advertiser_retention is not None else "sem baseline",
            label="Retenção anunciantes",
        ))
        signals = cohort.get("signals") or []
        pulse.append(NotificationField(value=", ".join(str(s) for s in signals) or MISSING, label="Sinais"))
        deltas = result.get("advertiser_deltas") or []
        pulse.append(NotificationField(
            value=", ".join(str(d) for d in deltas[:2]) or MISSING,
            label="Deltas de anunciantes",
        ))
        sections.append(NotificationSection(
            heading="Pulse (quarta)",
            as_table=True,
            fields=tuple(pulse),
        ))
    sections.append(_commercial_section())
    sections.append(_legal_section())
    return NotificationItem(
        kind=KIND_PRODUCT,
        title=_display_keyword(result),
        product=_product_identity(result),
        header_fields=_score_header(result),
        sections=tuple(sections),
        actions=_investigation_actions(result),
        footer_fields=_footer_date(run_date),
        audit_text=_wednesday_item_audit(result),
    )


def _summary_item(
    results: Sequence[dict[str, Any]],
    run_date: date,
    mode: str,
    title: str,
) -> NotificationItem:
    counts: dict[str, int] = {}
    preserved = 0
    for result in results:
        if result.get("preserved"):
            preserved += 1
        label = (result.get("score_result") or {}).get("label")
        if label:
            counts[label] = counts.get(label, 0) + 1
    fields = [
        NotificationField(value="Total", label="Estado", is_header=True),
        NotificationField(value=str(len(results)), label="Total produtos"),
    ]
    for label in ("AVANÇAR", "OBSERVAR", "DESCARTAR", "DADOS INSUFICIENTES"):
        count = counts.get(label)
        if count:
            row_label = f"{label} advisory" if label != "DADOS INSUFICIENTES" else label
            fields.append(NotificationField(value=str(count), label=row_label))
    if preserved:
        fields.append(NotificationField(value=str(preserved), label="Preservados"))
    return NotificationItem(
        kind=KIND_SUMMARY,
        title=title,
        sections=(NotificationSection(heading="Resumo", as_table=True, fields=tuple(fields)),),
        footer_fields=(
            NotificationField(value=run_date.strftime("%d/%m/%Y"), label="Última atualização dos dados"),
            NotificationField(value=mode, label="Modo"),
        ),
        audit_text=title,
    )


def build_batch(
    *,
    mode: str,
    run_id: str,
    results: Sequence[dict[str, Any]],
    title: str | None = None,
    run_date: date | None = None,
) -> NotificationBatch:
    """Build the deterministic NotificationBatch for one run mode.

    The batch audit text is the exact legacy plain digest (title line, then
    one per-result digest contribution per product, joined by single
    newlines) so stdout, manifest and v2 delivery state keep the same
    permanent human-readable trail.  ``title`` defaults to the mode title.
    Results may be full fresh dicts or persisted compact dicts carrying a
    ``notification_snapshot``; both rebuild the same batch.
    """
    if run_date is None:
        run_date = date.today()
    normalized = [_normalize_result(result) for result in results]
    with_suggestions = mode != "keyword"
    if mode == "wednesday":
        products = [_wednesday_product_item(result, run_date) for result in normalized]
    else:
        products = [_full_product_item(result, run_date, with_suggestions=with_suggestions) for result in normalized]
    if mode == "keyword":
        if len(products) != 1:
            raise ValueError("modo keyword exige exatamente um resultado")
        batch = NotificationBatch.build(mode=mode, run_id=run_id, products=products)
    else:
        if title is None:
            title = mode_title(mode, run_date) or ""
        summary = _summary_item(normalized, run_date, mode, title)
        batch = NotificationBatch.build(mode=mode, run_id=run_id, summary=summary, products=products)
    digest = "\n".join(item.audit_text for item in batch.items)
    return replace(batch, audit_text=digest)
