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
NECESSÁRIA).  Nothing is invented here: no supplier, margin, legal
conclusion, workflow gate, image, media, URL or button.

:func:`notification_snapshot` extracts the minimal credential-free, URL-free
projection of one result that the builder needs to rebuild the exact batch
from a persisted (compact) manifest without rerunning any source.  Results
without a snapshot degrade safely to placeholders.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Sequence

from pipeline.notifications.model import (
    KIND_PRODUCT,
    KIND_SUMMARY,
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


def notification_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """Minimal credential-free, URL-free projection of one result.

    Only the safe scalar/list fields the builder reads survive: advertiser
    ``page_name``/``active_ads``, ``saturation``, ``total_ads`` and the
    trends scalars (``direction``/``slope_pct``/``seasonal``/``peak``).
    Errors, raw payloads, tokens, chat ids, URLs and full adapter responses
    never reach the snapshot; a result without evidence simply yields an
    empty snapshot and rebuilds degrade to placeholders.
    """
    snapshot: dict[str, Any] = {}
    # Canonical product identity (safe scalars): the builder needs it to
    # rebuild the exact batch identity from a persisted compact result
    # without any source call.  Absent in historical results, so legacy
    # snapshots are byte-unchanged.
    for key in ("product_id", "product_name", "research_keyword"):
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


def _full_item_audit(result: dict[str, Any], run_date: date, *, with_suggestions: bool) -> str:
    """Exact per-result digest contribution of the legacy full modes.

    ``with_suggestions`` mirrors the legacy behaviour: the sunday/all/monthly
    digests append the suggestions line, the keyword digest is the bare
    ``generate_digest_line`` output."""
    if result.get("error") and result.get("preserved"):
        return f"{result.get('keyword', '?')} — fonte falhou; pack anterior preservado ({result.get('error', '')})"
    keyword = result.get("keyword", "?")
    score_result = result.get("score_result") or {"score": 0, "label": "DADOS INSUFICIENTES"}
    lines = [
        pack.generate_digest_line(
            keyword,
            score_result,
            result.get("ads_data") or {},
            trends_data=result.get("trends_data") or {},
            run_date=run_date,
        )
    ]
    if with_suggestions:
        suggestions = (result.get("suggested_keywords") or [])[:3]
        if suggestions:
            lines.append("Sugestões (aprovação manual): " + ", ".join(item["keyword"] for item in suggestions))
    return "\n".join(lines)


def _wednesday_item_audit(result: dict[str, Any]) -> str:
    """Exact per-result digest contribution of the legacy pulse digest."""
    if result.get("preserved"):
        error = result.get("error", "")
        if error:
            return f"{result.get('keyword', '?')} — pack anterior preservado ({error})"
        return f"{result.get('keyword', '?')} — Apify falhou; pack anterior preservado"
    keyword = result.get("keyword", "?")
    line = f"{keyword}: {', '.join(result.get('changes', []))}"
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
        header.append(NotificationField(value=score_result["label"], label="Sinal advisory"))
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
    return result.get("product_id") or stable_product_id(keyword)


def _full_product_item(result: dict[str, Any], run_date: date, *, with_suggestions: bool) -> NotificationItem:
    keyword = result.get("keyword") or "?"
    sections: list[NotificationSection] = []
    if result.get("preserved"):
        sections.append(_preserved_section(result))
    else:
        ads = result.get("ads_data") or {}
        evidence = [NotificationField(value=_proof_text(ads), label="Prova global")]
        if isinstance(ads, dict) and "error" not in ads:
            if ads.get("total_ads") is not None:
                evidence.append(NotificationField(value=str(ads["total_ads"]), label="Ads ativos"))
            if ads.get("saturation") is not None:
                evidence.append(NotificationField(value=str(ads["saturation"]), label="Saturação"))
        sections.append(NotificationSection(heading="Evidência global", fields=tuple(evidence)))
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
            sections.append(NotificationSection(heading="Tendência", fields=tuple(trend_fields)))
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
        title=keyword,
        product=_product_identity(result),
        header_fields=_score_header(result),
        sections=tuple(sections),
        footer_fields=_footer_date(run_date),
        audit_text=_full_item_audit(result, run_date, with_suggestions=with_suggestions),
    )


def _wednesday_product_item(result: dict[str, Any], run_date: date) -> NotificationItem:
    keyword = result.get("keyword") or "?"
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
        sections.append(NotificationSection(heading="Pulse (quarta)", fields=tuple(pulse)))
    sections.append(_commercial_section())
    sections.append(_legal_section())
    return NotificationItem(
        kind=KIND_PRODUCT,
        title=keyword,
        product=_product_identity(result),
        header_fields=_score_header(result),
        sections=tuple(sections),
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
