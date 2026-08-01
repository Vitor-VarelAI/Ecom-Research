#!/usr/bin/env python3
"""Operational deterministic CLI for the Ecom Research pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import unicodedata
from datetime import date, timedelta
from typing import Any

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass

from pipeline import quota
from pipeline.adapters import apify_ads, keyword_csv, landing_price, serp_seo, trends
from pipeline.delivery import telegram
from pipeline.output import pack
from pipeline.scoring import score
from pipeline.suggestions import extract_keyword_suggestions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run")


def send_digest(text: str) -> bool:
    """Delegate delivery; the delivery adapter handles stdout fallback."""
    return telegram.send(text)


def _load_keywords() -> list[str]:
    path = os.path.join(REPO_ROOT, "config", "keywords.txt")
    if not os.path.exists(path):
        logger.error("Ficheiro config/keywords.txt não encontrado")
        return []
    with open(path, "r", encoding="utf-8") as source:
        return [line.strip() for line in source if line.strip() and not line.strip().startswith("#")]


def _consume_keywords() -> list[str]:
    """Read active keywords without adding suggestions or changing config."""
    keywords = _load_keywords()
    if keywords:
        logger.info("Lidas %d keywords; config/keywords.txt permanece intacto", len(keywords))
    else:
        logger.warning("Zero keywords ativas em config/keywords.txt")
    return keywords


def _find_latest_csv() -> str | None:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    files = sorted((name for name in os.listdir(data_dir) if name.endswith(".csv")), reverse=True)
    return os.path.join(data_dir, files[0]) if files else None


def _find_packs_by_label(labels: list[str], max_age_days: int | None = None) -> list[str]:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return []
    cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
    found: dict[str, date] = {}
    for filename in sorted(os.listdir(data_dir), reverse=True):
        if not filename.endswith(".json") or filename.startswith("quota"):
            continue
        try:
            with open(os.path.join(data_dir, filename), "r", encoding="utf-8") as source:
                data = json.load(source)
            pack_date = date.fromisoformat(data.get("date", ""))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        keyword = data.get("keyword", "")
        label = data.get("score", {}).get("label", "")
        if keyword and label in labels and (cutoff is None or pack_date >= cutoff):
            if keyword not in found or pack_date > found[keyword]:
                found[keyword] = pack_date
    return list(found)


def _load_previous_pack(keyword: str) -> dict[str, Any] | None:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    best: tuple[str, str] | None = None
    for filename in os.listdir(data_dir):
        if not filename.endswith(".json") or filename.startswith("quota"):
            continue
        try:
            with open(os.path.join(data_dir, filename), "r", encoding="utf-8") as source:
                data = json.load(source)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if data.get("keyword") != keyword:
            continue
        pack_date = data.get("date", "")
        if best is None or pack_date > best[0]:
            best = (pack_date, os.path.join(data_dir, filename))
    if best is None:
        return None
    with open(best[1], "r", encoding="utf-8") as source:
        return json.load(source)


def _csv_data(csv_path: str | None) -> dict[str, Any]:
    path = csv_path or _find_latest_csv()
    if path:
        return keyword_csv.parse(path)
    return {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"}


def _incomplete_score(trends_data: dict[str, Any], csv_data: dict[str, Any], ads_data: dict[str, Any]) -> dict[str, Any]:
    """Keep the unchanged numeric formula while preventing a false verdict."""
    result = score.compute(trends_data, csv_data, ads_data)
    result["label"] = "DADOS INSUFICIENTES"
    result["reason"] = "Facebook Ad Library via Apify indisponível; repetir quando a fonte estiver disponível"
    result["incomplete"] = True
    return result


def _forced_zero_score() -> dict[str, Any]:
    return {
        "score": 0,
        "label": "DESCARTAR",
        "breakdown": {
            "trend": 0.0,
            "volume": 0.0,
            "ad_longevity": 0.0,
            "saturacao_invertida": 0.0,
        },
        "reason": "zero ads ativos após filtragem determinística",
    }


def run_keyword(
    keyword: str,
    csv_path: str | None = None,
    skip_serpapi: bool = False,
) -> dict[str, Any]:
    """Run one full keyword pipeline with exactly one Apify invocation."""
    logger.info("=== INÍCIO: keyword=%r ===", keyword)
    ads_data = apify_ads.fetch(keyword)
    apify_failed = "error" in ads_data
    if apify_failed:
        logger.warning("Apify Ads: %s", ads_data["error"])
    else:
        logger.info("Apify Ads: total=%d saturação=%s", ads_data.get("total_ads", 0), ads_data.get("saturation"))

    # A source failure must continue the paid sources, but cannot create a
    # negative market decision.  A valid empty result takes the deterministic
    # zero-ads branch and spends one related-query call for alternatives.
    if apify_failed:
        landing_data = {"error": "sem dados: Apify indisponível; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword)
        csv_data = _csv_data(csv_path)
        score_result = _incomplete_score(trends_data, csv_data, ads_data)
    elif ads_data.get("total_ads", 0) == 0:
        landing_data = {"error": "sem dados: zero ads; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
        else:
            trends_data = trends.fetch_related(keyword)
        seo_data = {"error": "SEO não executado: zero ads ativos"}
        csv_data = _csv_data(csv_path)
        score_result = _forced_zero_score()
    else:
        landing_data = landing_price.fetch_prices_from_ads(ads_data)
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword)
        csv_data = _csv_data(csv_path)
        score_result = score.compute(trends_data, csv_data, ads_data)

    suggested = extract_keyword_suggestions(keyword, trends_data)
    md_path = pack.generate(
        keyword=keyword,
        trends_data=trends_data,
        csv_data=csv_data,
        ads_data=ads_data,
        score_result=score_result,
        seo_data=seo_data,
        landing_price_data=landing_data,
        suggested_keywords=suggested,
        run_date=date.today(),
    )
    return {
        "keyword": keyword,
        "md_path": md_path,
        "score_result": score_result,
        "trends_data": trends_data,
        "csv_data": csv_data,
        "ads_data": ads_data,
        "seo_data": seo_data,
        "landing_price_data": landing_data,
        "suggested_keywords": suggested,
    }


def _digest_text(results: list[dict[str, Any]], title: str | None = None) -> str:
    lines = [title] if title else []
    for result in results:
        if result.get("error") and result.get("preserved"):
            lines.append(f"{result.get('keyword', '?')} — Apify falhou; pack anterior preservado ({result['error']})")
            continue
        keyword = result.get("keyword", "?")
        score_result = result.get("score_result", {"score": 0, "label": "DADOS INSUFICIENTES"})
        lines.append(pack.generate_digest_line(keyword, score_result, result.get("ads_data", {}), trends_data=result.get("trends_data", {})))
        suggestions = result.get("suggested_keywords", [])[:3]
        if suggestions:
            lines.append("Sugestões (aprovação manual): " + ", ".join(item["keyword"] for item in suggestions))
    return "\n".join(lines)


def _run_sunday(csv_path: str | None = None, send: bool = True) -> list[dict[str, Any]]:
    keywords = _consume_keywords()
    if not keywords:
        return []
    logger.info("=== DOMINGO: deep run de %d keywords ===", len(keywords))
    results: list[dict[str, Any]] = []
    for keyword in keywords:
        try:
            results.append(run_keyword(keyword, csv_path=csv_path))
        except Exception as exc:
            logger.error("Erro fatal em keyword=%r: %s", keyword, exc, exc_info=True)
            results.append({"keyword": keyword, "error": str(exc), "score_result": {"score": 0, "label": "DADOS INSUFICIENTES"}})
    digest = _digest_text(results, f"DOMINGO — {date.today().isoformat()}")
    if send:
        send_digest(digest)
    else:
        print(digest)
    return results


def _fold_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def _advertiser_counts(data: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for advertiser in data.get("advertisers", []) if "error" not in data else []:
        key = str(advertiser.get("page_id") or _fold_name(advertiser.get("page_name")))
        result[key] = int(advertiser.get("active_ads", 0))
    return result


def _advertiser_keys(data: dict[str, Any]) -> list[str]:
    """Stable advertiser identity keys (page_id, else accent-folded name)."""
    return sorted(key for key in _advertiser_counts(data) if key)


def _ad_cohort(
    previous: dict[str, Any],
    current: dict[str, Any],
    baseline_date: str | None = None,
    current_date: str | None = None,
) -> dict[str, Any]:
    """Advisory market-persistence cohort: previous pack + current Apify result only.

    Deterministic set algebra over ad ids and advertiser identities.  Retention
    percentages are None whenever the baseline side has no comparable items;
    signals are advisory and coexist.  Never touches scores or labels.
    """
    old_ids = sorted(set(previous.get("ad_ids", []))) if "error" not in previous else []
    new_ids = sorted(set(current.get("ad_ids", []))) if "error" not in current else []
    old_adv = _advertiser_keys(previous)
    new_adv = _advertiser_keys(current)
    retained_ads = sorted(set(old_ids) & set(new_ids))
    retained_adv = sorted(set(old_adv) & set(new_adv))

    ad_retention_pct = round(len(retained_ads) / len(old_ids) * 100, 1) if old_ids else None
    advertiser_retention_pct = round(len(retained_adv) / len(old_adv) * 100, 1) if old_adv else None

    total_ads = current.get("total_ads", 0) if "error" not in current else 0
    counts = _advertiser_counts(current)
    top_share = round(max(counts.values()) / total_ads * 100, 1) if counts and total_ads else None

    signals: list[str] = []
    if ad_retention_pct is not None and ad_retention_pct >= 50 and len(retained_adv) >= 3:
        signals.append("durable_demand")
    if ad_retention_pct is not None and ad_retention_pct < 50:
        signals.append("high_churn")
    if top_share is not None and top_share >= 60:
        signals.append("concentrated_market")
    if not old_ids and not old_adv:
        signals.append("insufficient_baseline")

    return {
        "baseline_date": baseline_date,
        "current_date": current_date or date.today().isoformat(),
        "previous_ads": old_ids,
        "current_ads": new_ids,
        "retained_ads": retained_ads,
        "new_ads": sorted(set(new_ids) - set(old_ids)),
        "removed_ads": sorted(set(old_ids) - set(new_ids)),
        "ad_retention_pct": ad_retention_pct,
        "previous_advertisers": old_adv,
        "current_advertisers": new_adv,
        "retained_advertisers": retained_adv,
        "new_advertisers": sorted(set(new_adv) - set(old_adv)),
        "lost_advertisers": sorted(set(old_adv) - set(new_adv)),
        "advertiser_retention_pct": advertiser_retention_pct,
        "top_advertiser_share_pct": top_share,
        "signals": signals,
    }


def _compare_ads(previous: dict[str, Any], current: dict[str, Any]) -> tuple[list[str], list[str]]:
    old_ids = set(previous.get("ad_ids", [])) if "error" not in previous else set()
    new_ids = set(current.get("ad_ids", [])) if "error" not in current else set()
    changes: list[str] = []
    if new_ids - old_ids:
        changes.append(f"+{len(new_ids - old_ids)} ads novos")
    if old_ids - new_ids:
        changes.append(f"-{len(old_ids - new_ids)} ads removidos")
    if not changes:
        changes.append("sem alterações nos ad_ids")

    old_adv = _advertiser_counts(previous)
    new_adv = _advertiser_counts(current)
    deltas: list[str] = []
    for key in sorted(set(old_adv) | set(new_adv)):
        if old_adv.get(key, 0) != new_adv.get(key, 0):
            deltas.append(f"{key}: {old_adv.get(key, 0)}→{new_adv.get(key, 0)} variantes")
    return changes, deltas


def _run_wednesday(send: bool = True) -> list[dict[str, Any]]:
    keywords = _find_packs_by_label(["AVANÇAR", "OBSERVAR"], max_age_days=14)
    if not keywords:
        logger.info("Zero packs AVANÇAR/OBSERVAR com <14 dias. Nada a pulsar.")
        return []
    logger.info("=== QUARTA: pulse Apify em %d keywords ===", len(keywords))
    results: list[dict[str, Any]] = []
    for keyword in keywords:
        previous = _load_previous_pack(keyword)
        if not previous:
            continue
        previous_ads = previous.get("facebook_ads", {})
        current_ads = apify_ads.fetch(keyword)
        if "error" in current_ads:
            logger.warning("Pulse %s: %s; pack anterior preservado", keyword, current_ads["error"])
            results.append({
                "keyword": keyword,
                "error": current_ads["error"],
                "ads_data": current_ads,
                "preserved": True,
            })
            continue

        changes, advertiser_deltas = _compare_ads(previous_ads, current_ads)
        cohort = _ad_cohort(previous_ads, current_ads, baseline_date=previous.get("date"))
        trends_data = previous.get("trends", {})
        csv_data = previous.get("keyword_planner", {})
        seo_data = previous.get("seo", {})
        landing_data = previous.get("landing_price")
        score_result = previous.get("score", {})
        suggested = previous.get("suggested_keywords", [])
        md_path = pack.generate(
            keyword=keyword,
            trends_data=trends_data,
            csv_data=csv_data,
            ads_data=current_ads,
            score_result=score_result,
            seo_data=seo_data,
            landing_price_data=landing_data,
            suggested_keywords=suggested,
            run_date=date.today(),
            ad_cohort=cohort,
        )
        results.append({
            "keyword": keyword,
            "md_path": md_path,
            "score_result": score_result,
            "ads_data": current_ads,
            "trends_data": trends_data,
            "suggested_keywords": suggested,
            "changes": changes,
            "advertiser_deltas": advertiser_deltas,
            "ad_cohort": cohort,
        })

    lines = [f"PULSE QUARTA — {date.today().isoformat()}"]
    for result in results:
        if result.get("preserved"):
            lines.append(f"{result['keyword']} — Apify falhou; pack anterior preservado")
        else:
            line = f"{result['keyword']}: {', '.join(result.get('changes', []))}"
            cohort = result.get("ad_cohort")
            if cohort:
                retention = cohort.get("ad_retention_pct")
                retention_text = f"{retention:.1f}%" if retention is not None else "sem baseline"
                line += f" | retenção ads {retention_text}"
                signals = cohort.get("signals", [])
                if signals:
                    line += " | Sinais: " + ", ".join(signals)
            if result.get("advertiser_deltas"):
                line += " | " + ", ".join(result["advertiser_deltas"][:2])
            lines.append(line)
    digest = "\n".join(lines)
    if send:
        send_digest(digest)
    else:
        print(digest)
    return results


def _run_monthly(send: bool = True) -> list[dict[str, Any]]:
    keywords = _find_packs_by_label(["OBSERVAR"], max_age_days=None)
    if not keywords:
        logger.info("Zero packs OBSERVAR na watchlist. Nada a re-score.")
        return []
    logger.info("=== DIA 1: re-score watchlist de %d keywords ===", len(keywords))
    results: list[dict[str, Any]] = []
    for keyword in keywords:
        previous = _load_previous_pack(keyword)
        if not previous:
            continue
        trends_data = trends.fetch(keyword, timeseries_only=True, force_refresh=True)
        ads_data = previous.get("facebook_ads", {})
        csv_data = previous.get("keyword_planner", {})
        seo_data = previous.get("seo", {})
        landing_data = previous.get("landing_price")
        suggested = previous.get("suggested_keywords", [])
        if "error" in trends_data:
            score_result = dict(previous.get("score", {"score": 0, "label": "DADOS INSUFICIENTES"}))
            score_result["label"] = "DADOS INSUFICIENTES"
            score_result["reason"] = "Trends indisponível no re-score mensal"
        else:
            score_result = score.compute(trends_data, csv_data, ads_data)
        md_path = pack.generate(
            keyword=keyword,
            trends_data=trends_data,
            csv_data=csv_data,
            ads_data=ads_data,
            score_result=score_result,
            seo_data=seo_data,
            landing_price_data=landing_data,
            suggested_keywords=suggested,
            run_date=date.today(),
        )
        results.append({
            "keyword": keyword,
            "md_path": md_path,
            "score_result": score_result,
            "trends_data": trends_data,
            "ads_data": ads_data,
            "suggested_keywords": suggested,
        })
    digest = _digest_text(results, f"RE-SCORE MENSAL — {date.today().isoformat()}")
    if send:
        send_digest(digest)
    else:
        print(digest)
    return results


def _auto_dispatch(send: bool = True) -> list[dict[str, Any]]:
    """Dispatch all actions for today; day 1 always runs before weekly pulse."""
    today = date.today()
    is_sunday = today.weekday() == 6
    is_wednesday = today.weekday() == 2
    results: list[dict[str, Any]] = []
    if today.day == 1:
        results.extend(_run_monthly(send=send))
    if is_sunday:
        results.extend(_run_sunday(send=send))
    elif is_wednesday:
        results.extend(_run_wednesday(send=send))
    if not results and today.day != 1 and not is_sunday and not is_wednesday:
        logger.info("→ Dia sem ação (%s). Exit 0.", today.strftime("%A"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="ECOM-RESEARCH — pipeline determinístico de pesquisa")
    parser.add_argument("keyword", nargs="?", help="Keyword a pesquisar")
    parser.add_argument("--all", action="store_true", help="Pesquisar keywords de config/keywords.txt")
    parser.add_argument("--sunday", action="store_true", help="Deep run de domingo")
    parser.add_argument("--wednesday", action="store_true", help="Pulse de quarta: um refresh Apify por keyword")
    parser.add_argument("--monthly", action="store_true", help="Re-score mensal: uma call Trends por keyword")
    parser.add_argument("--auto", action="store_true", help="Despachar dia 1, domingo ou quarta")
    parser.add_argument("--csv", help="Caminho para CSV do Keyword Planner")
    parser.add_argument("--digest", action="store_true", help="Enviar digest para Telegram, com fallback stdout")
    args = parser.parse_args()

    if args.auto:
        _auto_dispatch(send=args.digest)
        return
    if args.sunday:
        _run_sunday(csv_path=args.csv, send=args.digest)
        return
    if args.wednesday:
        _run_wednesday(send=args.digest)
        return
    if args.monthly:
        _run_monthly(send=args.digest)
        return
    if args.all:
        keywords = _load_keywords()
        if not keywords:
            parser.error("Zero keywords em config/keywords.txt")
        results = [run_keyword(keyword, csv_path=args.csv) for keyword in keywords]
        digest = _digest_text(results, f"RESUMO DO DIA — {date.today().isoformat()}")
        if args.digest:
            send_digest(digest)
        else:
            print(digest)
        return
    if not args.keyword:
        parser.print_help()
        return

    result = run_keyword(args.keyword, csv_path=args.csv)
    score_result = result["score_result"]
    print(f"\n=== {args.keyword} ===")
    print(f"Score: {score_result.get('score', 0)}/100 ({score_result.get('label', 'DADOS INSUFICIENTES')})")
    print(f"Pack: {result['md_path']}")
    if args.digest:
        send_digest(pack.generate_digest_line(args.keyword, score_result, result.get("ads_data", {}), trends_data=result.get("trends_data", {})))


if __name__ == "__main__":
    main()
