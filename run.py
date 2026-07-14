#!/usr/bin/env python3
"""
ECOM-RESEARCH Pipeline — Fase 1

CLI:
    python run.py "keyword"        → pesquisa uma keyword e gera research-pack (modo full)
    python run.py --all            → pesquisa todas as keywords em config/keywords.txt
    python run.py --all --digest   → igual, mas envia digest para Telegram

    python run.py --sunday         → deep run: consome config/keywords.txt, move para keywords_done.txt
    python run.py --wednesday      → pulse Meta nos packs AVANÇAR/OBSERVAR (<14 dias), 0 calls SerpAPI
    python run.py --monthly        → re-score watchlist OBSERVAR (1 call timeseries só por keyword)
    python run.py --auto           → deteta dia (domingo/quarta/dia 1) e despacha; outros dias exit 0

Setup:
    cp .env.example .env   # e preenche com credenciais reais
    pip install -r requirements.txt

Output:
    packs/YYYY-MM-DD--{keyword-slug}.md   (research-pack)
    data/YYYY-MM-DD--{keyword-slug}.json  (dados brutos)
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

# Garantir que o repo root está no path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

# Carregar .env antes de importar adapters
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv não instalado; o user tem de exportar as vars manualmente

from pipeline.adapters import meta_ads, trends, keyword_csv, serp_seo
from pipeline.scoring import score
from pipeline.output import pack
from pipeline import quota

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run")


def send_digest(text: str) -> None:
    """
    Stub: envia digest para o Telegram bridge.
    O Vitor liga ao bridge depois.

    Por agora, faz print para stdout.
    """
    print(f"\n{'='*60}")
    print("DIGEST DIÁRIO — ECOM-RESEARCH")
    print(f"{'='*60}")
    print(text)
    print(f"{'='*60}\n")
    logger.info("Digest impresso no stdout. Ligar Telegram bridge em send_digest().")


def _load_keywords() -> list[str]:
    """Carrega keywords de config/keywords.txt."""
    keywords_path = os.path.join(REPO_ROOT, "config", "keywords.txt")
    if not os.path.exists(keywords_path):
        logger.error("Ficheiro config/keywords.txt não encontrado")
        return []

    keywords: list[str] = []
    with open(keywords_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                keywords.append(line)
    return keywords


def _consume_keywords() -> list[str]:
    """
    Lê config/keywords.txt, processa as keywords e move-as para keywords_done.txt.
    Devolve a lista de keywords a processar.
    """
    keywords_path = os.path.join(REPO_ROOT, "config", "keywords.txt")
    done_path = os.path.join(REPO_ROOT, "config", "keywords_done.txt")

    if not os.path.exists(keywords_path):
        logger.error("Ficheiro config/keywords.txt não encontrado")
        return []

    keywords: list[str] = []
    remaining: list[str] = []

    with open(keywords_path, "r") as f:
        for line in f:
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith("#"):
                keywords.append(line_stripped)
            else:
                remaining.append(line.rstrip("\n"))

    if not keywords:
        logger.warning("Zero keywords ativas em config/keywords.txt")
        return []

    today_str = date.today().isoformat()

    # Append processed keywords to done file
    os.makedirs(os.path.dirname(done_path), exist_ok=True)
    with open(done_path, "a") as f:
        for kw in keywords:
            f.write(f"{today_str} | {kw}\n")

    # Rewrite keywords.txt with only remaining lines (comments + empty lines)
    with open(keywords_path, "w") as f:
        for line in remaining:
            f.write(line + "\n")

    logger.info("Consumidas %d keywords → movidas para keywords_done.txt", len(keywords))
    return keywords


def _find_latest_csv() -> str | None:
    """
    Procura o CSV mais recente em data/ que pareça ser um export
    do Keyword Planner.
    """
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    csv_files = sorted(
        [f for f in os.listdir(data_dir) if f.endswith(".csv")],
        reverse=True,
    )
    return os.path.join(data_dir, csv_files[0]) if csv_files else None


def _find_packs_by_label(labels: list[str], max_age_days: int | None = None) -> list[str]:
    """
    Procura em packs/ keywords com um determinado veredicto.
    Lê os JSONs para extrair label e data.

    Args:
        labels: lista de labels a filtrar (ex: ["AVANÇAR", "OBSERVAR"])
        max_age_days: se definido, só devolve packs com <= N dias

    Returns:
        Lista de keywords únicas
    """
    packs_dir = os.path.join(REPO_ROOT, "packs")
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(packs_dir) and not os.path.isdir(data_dir):
        return []

    keywords_found: dict[str, date] = {}  # keyword → data mais recente
    cutoff = date.today() - timedelta(days=max_age_days) if max_age_days else None

    # Procurar JSONs (mais fiáveis que parse de .md)
    if os.path.isdir(data_dir):
        for fname in sorted(os.listdir(data_dir), reverse=True):
            if not fname.endswith(".json") or fname.startswith("quota"):
                continue
            fpath = os.path.join(data_dir, fname)
            try:
                with open(fpath, "r") as f:
                    jdata = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            kw = jdata.get("keyword", "")
            label = jdata.get("score", {}).get("label", "")
            try:
                pack_date = date.fromisoformat(jdata.get("date", ""))
            except (ValueError, TypeError):
                continue

            if label in labels and kw:
                if cutoff and pack_date < cutoff:
                    continue
                if kw not in keywords_found or pack_date > keywords_found[kw]:
                    keywords_found[kw] = pack_date

    return list(keywords_found.keys())


def _load_previous_pack(keyword: str) -> dict | None:
    """Carrega o JSON do pack anterior mais recente para uma keyword."""
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None

    best_path: str | None = None
    best_date: str = ""

    for fname in sorted(os.listdir(data_dir), reverse=True):
        if not fname.endswith(".json") or fname.startswith("quota"):
            continue
        # Verificar se o nome contém a keyword (slug)
        fpath = os.path.join(data_dir, fname)
        try:
            with open(fpath, "r") as f:
                jdata = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if jdata.get("keyword") == keyword:
            pack_date = jdata.get("date", "")
            if pack_date > best_date:
                best_date = pack_date
                best_path = fpath

    if best_path:
        with open(best_path, "r") as f:
            return json.load(f)
    return None


def run_keyword(
    keyword: str,
    csv_path: str | None = None,
    skip_serpapi: bool = False,
    meta_only: bool = False,
    trends_only: bool = False,
) -> dict:
    """
    Executa o pipeline completo para uma keyword.

    Args:
        keyword: keyword a pesquisar
        csv_path: caminho para CSV do Keyword Planner (se None, procura automaticamente)
        skip_serpapi: se True, salta todas as calls SerpAPI (trends + SEO)
        meta_only: se True, só corre Meta Ads (para --wednesday pulse)
        trends_only: se True, só corre Trends timeseries (para --monthly re-score)

    Returns:
        dict com resultado (pack_path, score_result, etc.)
    """
    logger.info("=== INÍCIO: keyword=%r ===", keyword)

    # 1. Meta Ads (SEMPRE primeiro — é grátis e serve de pre-filter)
    logger.info("[1/5] Meta Ads...")
    ads_data = meta_ads.fetch(keyword)
    if "error" in ads_data:
        logger.warning("Meta Ads: %s", ads_data["error"])
    else:
        logger.info("Meta Ads: total=%d saturação=%s", ads_data.get("total_ads", 0), ads_data.get("saturation"))

    # Modo meta_only (--wednesday pulse): para aqui
    if meta_only:
        csv_data = {"error": "Modo --wednesday: CSV não carregado"}
        trends_data = {"error": "Modo --wednesday: Trends não carregados"}
        seo_data = {"error": "Modo --wednesday: SEO não executado"}

        # Carregar pack anterior para score persistente
        prev = _load_previous_pack(keyword)
        prev_score = prev.get("score", {}) if prev else {}
        score_result = prev_score.get("score", {})
        if not score_result:
            score_result = score.compute(trends_data, csv_data, ads_data)

        md_path = pack.generate(
            keyword=keyword,
            trends_data=trends_data,
            csv_data=csv_data,
            ads_data=ads_data,
            score_result=score_result,
            seo_data=seo_data,
            run_date=date.today(),
        )
        logger.info("Pack gerado (pulse): %s", md_path)
        return {
            "keyword": keyword,
            "md_path": md_path,
            "score_result": score_result,
            "trends_data": trends_data,
            "csv_data": csv_data,
            "ads_data": ads_data,
            "seo_data": seo_data,
        }

    # Pre-filter Meta: se 0 ads ativos → DESCARTAR sem gastar SerpAPI
    if not ads_data.get("error") and ads_data.get("total_ads", 0) == 0 and not trends_only:
        logger.info("PRE-FILTER: 0 ads ativos → DESCARTAR sem SerpAPI")
        trends_data = {"error": "SerpAPI não executada: 0 ads ativos (pre-filter Meta)"}
        seo_data = {"error": "SerpAPI não executada: 0 ads ativos (pre-filter Meta)"}
        csv_data = keyword_csv.parse(csv_path) if csv_path else {"error": "Nenhum CSV encontrado em data/"}
        if csv_path is None:
            alt_csv = _find_latest_csv()
            if alt_csv:
                csv_data = keyword_csv.parse(alt_csv)
            else:
                csv_data = {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"}

        score_result = score.compute(trends_data, csv_data, ads_data)
        md_path = pack.generate(
            keyword=keyword,
            trends_data=trends_data,
            csv_data=csv_data,
            ads_data=ads_data,
            score_result=score_result,
            seo_data=seo_data,
            run_date=date.today(),
        )
        logger.info("Pack DESCARTAR gerado: %s", md_path)
        return {
            "keyword": keyword,
            "md_path": md_path,
            "score_result": score_result,
            "trends_data": trends_data,
            "csv_data": csv_data,
            "ads_data": ads_data,
            "seo_data": seo_data,
        }

    # 2. Trends
    if skip_serpapi:
        trends_data = {"error": "SerpAPI não executada — modo sem rede"}
        logger.info("[2/5] Trends: SKIP (modo sem SerpAPI)")
    else:
        logger.info("[2/5] Trends (SerpAPI)...")
        if trends_only:
            # --monthly: 1 call timeseries só, sem related_queries
            trends_data = trends.fetch(keyword)
        else:
            trends_data = trends.fetch(keyword)

    if "error" in trends_data:
        logger.warning("Trends: %s", trends_data["error"])
    else:
        logger.info("Trends: direção=%s slope=%+.1f%%", trends_data.get("direction"), trends_data.get("slope_pct"))

    # 3. SEO (SerpAPI engine google)
    if skip_serpapi or trends_only:
        seo_data = {"error": "SEO não executado — modo reduzido"}
        logger.info("[3/5] SEO: SKIP")
    else:
        logger.info("[3/5] SEO (SerpAPI google)...")
        seo_data = serp_seo.fetch(keyword)
        if "error" in seo_data:
            logger.warning("SEO: %s", seo_data["error"])
        else:
            logger.info("SEO: oportunidade=%s ads_serp=%d", seo_data.get("seo_opportunity"), seo_data.get("ads_count", 0))

    # 4. Keyword Planner CSV
    logger.info("[4/5] Keyword Planner CSV...")
    if csv_path is None:
        csv_path = _find_latest_csv()
    if csv_path:
        csv_data = keyword_csv.parse(csv_path)
    else:
        csv_data = {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"}
    if "error" in csv_data:
        logger.warning("CSV: %s", csv_data["error"])
    else:
        logger.info("CSV: volume=%s keywords=%d", csv_data.get("volume"), csv_data.get("total_keywords", 0))

    # 5. Scoring + Output
    logger.info("[5/5] Scoring + Pack...")
    score_result = score.compute(trends_data, csv_data, ads_data)
    logger.info("Score: %d/100 (%s)", score_result["score"], score_result["label"])

    md_path = pack.generate(
        keyword=keyword,
        trends_data=trends_data,
        csv_data=csv_data,
        ads_data=ads_data,
        score_result=score_result,
        seo_data=seo_data,
        run_date=date.today(),
    )
    logger.info("Pack gerado: %s", md_path)

    return {
        "keyword": keyword,
        "md_path": md_path,
        "score_result": score_result,
        "trends_data": trends_data,
        "csv_data": csv_data,
        "ads_data": ads_data,
        "seo_data": seo_data,
    }


def _run_sunday(csv_path: str | None = None, send: bool = True) -> list[dict]:
    """
    Modo --sunday: deep run de config/keywords.txt.
    Consome a lista e move processados para keywords_done.txt.
    """
    keywords = _consume_keywords()
    if not keywords:
        logger.info("Zero keywords para processar no domingo.")
        return []

    logger.info("=== DOMINGO: deep run de %d keywords ===", len(keywords))
    results = []
    for kw in keywords:
        try:
            result = run_keyword(kw, csv_path=csv_path, skip_serpapi=False)
            results.append(result)
        except Exception as e:
            logger.error("Erro fatal em keyword=%r: %s", kw, e, exc_info=True)
            results.append({
                "keyword": kw,
                "error": str(e),
                "score_result": {"score": 0, "label": "ERRO"},
            })

    # Digest
    digest_lines = []
    for r in results:
        kw = r.get("keyword", "?")
        sr = r.get("score_result", {"score": 0, "label": "ERRO"})
        digest_lines.append(pack.generate_digest_line(kw, sr))
    digest_text = "\n".join(digest_lines)

    if send:
        send_digest(digest_text)
    else:
        print(f"\n{'='*60}")
        print("RESUMO DE DOMINGO")
        print(f"{'='*60}")
        for line in digest_lines:
            print(line)
        print(f"{'='*60}\n")

    return results


def _run_wednesday(send: bool = True) -> list[dict]:
    """
    Modo --wednesday: pulse Meta nos packs AVANÇAR/OBSERVAR com <14 dias.
    Detecta ads novos/mortos vs pack anterior.
    0 calls SerpAPI.
    """
    keywords = _find_packs_by_label(["AVANÇAR", "OBSERVAR"], max_age_days=14)
    if not keywords:
        logger.info("Zero packs AVANÇAR/OBSERVAR com <14 dias. Nada a pulsar.")
        return []

    logger.info("=== QUARTA: pulse Meta em %d keywords ===", len(keywords))
    results = []
    for kw in keywords:
        try:
            prev = _load_previous_pack(kw)
            prev_ads = prev.get("meta_ads", {}) if prev else {}
            prev_total = prev_ads.get("total_ads", 0)

            # Re-fetch Meta (grátis)
            ads_data = meta_ads.fetch(kw)
            new_total = ads_data.get("total_ads", 0)

            # Detectar mudanças
            changes: list[str] = []
            if prev_total > 0:
                diff = new_total - prev_total
                if diff > 0:
                    changes.append(f"+{diff} ads novos")
                elif diff < 0:
                    changes.append(f"{diff} ads morreram")
                else:
                    changes.append("sem alterações no nº de ads")
            else:
                changes.append(f"{new_total} ads (primeira leitura)")

            logger.info("Pulse %s: %d → %d ads (%s)", kw, prev_total, new_total, ", ".join(changes))

            # Gerar pack ligeiro (só Meta, sem SerpAPI)
            trends_data = {"error": "Modo --wednesday: 0 calls SerpAPI"}
            csv_data = {"error": "Modo --wednesday: CSV não carregado"}
            seo_data = {"error": "Modo --wednesday: 0 calls SerpAPI"}

            # Score do pack anterior (persistente)
            prev_score = prev.get("score", {}) if prev else {}
            # Se não houver score anterior, recalcular
            if prev_score:
                score_result = prev_score
            else:
                score_result = score.compute(trends_data, csv_data, ads_data)

            md_path = pack.generate(
                keyword=kw,
                trends_data=trends_data,
                csv_data=csv_data,
                ads_data=ads_data,
                score_result=score_result,
                seo_data=seo_data,
                run_date=date.today(),
            )
            logger.info("Pack pulse gerado: %s", md_path)

            results.append({
                "keyword": kw,
                "md_path": md_path,
                "score_result": score_result,
                "ads_data": ads_data,
                "changes": changes,
            })
        except Exception as e:
            logger.error("Erro fatal em keyword=%r: %s", kw, e, exc_info=True)
            results.append({
                "keyword": kw,
                "error": str(e),
                "score_result": {"score": 0, "label": "ERRO"},
            })

    # Digest curto
    digest_lines = [f"📊 PULSE QUARTA — {date.today().isoformat()}"]
    digest_lines.append(f"{len(results)} keywords pulsadas:\n")
    for r in results:
        kw = r.get("keyword", "?")
        changes = r.get("changes", ["erro"])
        digest_lines.append(f"• *{kw}*: {', '.join(changes)}")
    digest_text = "\n".join(digest_lines)

    if send:
        send_digest(digest_text)
    else:
        print(f"\n{'='*60}")
        print("PULSE DE QUARTA")
        print(f"{'='*60}")
        for line in digest_lines:
            print(line)
        print(f"{'='*60}\n")

    return results


def _run_monthly(send: bool = True) -> list[dict]:
    """
    Modo --monthly: re-score watchlist OBSERVAR.
    1 call SerpAPI (timeseries só) por keyword.
    """
    keywords = _find_packs_by_label(["OBSERVAR"], max_age_days=None)
    if not keywords:
        logger.info("Zero packs OBSERVAR na watchlist. Nada a re-score.")
        return []

    logger.info("=== DIA 1: re-score watchlist de %d keywords ===", len(keywords))
    results = []
    for kw in keywords:
        try:
            result = run_keyword(kw, csv_path=None, skip_serpapi=False, trends_only=True)
            results.append(result)
        except Exception as e:
            logger.error("Erro fatal em keyword=%r: %s", kw, e, exc_info=True)
            results.append({
                "keyword": kw,
                "error": str(e),
                "score_result": {"score": 0, "label": "ERRO"},
            })

    digest_lines = [f"🔄 RE-SCORE WATCHLIST — {date.today().isoformat()}"]
    digest_lines.append(f"{len(results)} keywords re-scoreadas:\n")
    for r in results:
        kw = r.get("keyword", "?")
        sr = r.get("score_result", {"score": 0, "label": "ERRO"})
        digest_lines.append(pack.generate_digest_line(kw, sr))
    digest_text = "\n".join(digest_lines)

    if send:
        send_digest(digest_text)
    else:
        print(f"\n{'='*60}")
        print("RE-SCORE MENSAL")
        print(f"{'='*60}")
        for line in digest_lines:
            print(line)
        print(f"{'='*60}\n")

    return results


def _auto_dispatch(send: bool = True) -> list[dict]:
    """
    Detecta o dia corrente e despacha:
    - Domingo → deep run (--sunday)
    - Quarta → pulse Meta (--wednesday)
    - Dia 1 → watchlist re-score (--monthly)
    - Outros dias → exit 0
    """
    today = date.today()
    weekday = today.weekday()  # 0=segunda, 6=domingo
    is_monday = weekday == 0
    is_wednesday = weekday == 2
    is_sunday = weekday == 6
    is_first = today.day == 1

    logger.info("--auto: hoje é %s (dia %d, weekday=%d)", today, today.day, weekday)

    if is_sunday:
        logger.info("→ Domingo: deep run")
        return _run_sunday(send=send)
    elif is_wednesday:
        logger.info("→ Quarta: pulse Meta")
        return _run_wednesday(send=send)
    elif is_first:
        logger.info("→ Dia 1: re-score watchlist")
        return _run_monthly(send=send)
    else:
        logger.info("→ Dia sem ação (%s). Exit 0.", today.strftime("%A"))
        return []


def main():
    parser = argparse.ArgumentParser(
        description="ECOM-RESEARCH Pipeline — pesquisa keywords e gera research-packs",
    )
    parser.add_argument(
        "keyword",
        nargs="?",
        help="Keyword a pesquisar (ex: 'mochila personalizada')",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Pesquisar todas as keywords em config/keywords.txt (batch, sem consumir)",
    )
    parser.add_argument(
        "--sunday",
        action="store_true",
        help="Deep run domingo: consome config/keywords.txt, move para keywords_done.txt",
    )
    parser.add_argument(
        "--wednesday",
        action="store_true",
        help="Pulse quarta: refresh Meta Ads nos packs AVANÇAR/OBSERVAR (<14d), 0 calls SerpAPI",
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="Re-score watchlist OBSERVAR (1 call timeseries só por keyword)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Deteta dia da semana e despacha automaticamente",
    )
    parser.add_argument(
        "--csv",
        help="Caminho para CSV do Keyword Planner (default: procura em data/)",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="Enviar digest para Telegram no final",
    )

    args = parser.parse_args()

    csv_path = args.csv
    should_send = args.digest

    # ── --auto ──
    if args.auto:
        results = _auto_dispatch(send=should_send)
        if not results:
            sys.exit(0)
        return

    # ── --sunday ──
    if args.sunday:
        _run_sunday(csv_path=csv_path, send=should_send)
        return

    # ── --wednesday ──
    if args.wednesday:
        _run_wednesday(send=should_send)
        return

    # ── --monthly ──
    if args.monthly:
        _run_monthly(send=should_send)
        return

    # ── --all (batch sem consumir) ──
    if args.all:
        keywords = _load_keywords()
        if not keywords:
            logger.error("Zero keywords em config/keywords.txt")
            sys.exit(1)

        logger.info("=== MODO BATCH: %d keywords ===", len(keywords))
        results = []
        for kw in keywords:
            try:
                result = run_keyword(kw, csv_path=csv_path)
                results.append(result)
            except Exception as e:
                logger.error("Erro fatal em keyword=%r: %s", kw, e, exc_info=True)
                results.append({
                    "keyword": kw,
                    "error": str(e),
                    "score_result": {"score": 0, "label": "ERRO"},
                })

        digest_lines = []
        for r in results:
            kw = r.get("keyword", "?")
            sr = r.get("score_result", {"score": 0, "label": "ERRO"})
            digest_lines.append(pack.generate_digest_line(kw, sr))
        digest_text = "\n".join(digest_lines)

        if should_send:
            send_digest(digest_text)
        else:
            print(f"\n{'='*60}")
            print("RESUMO DO DIA")
            print(f"{'='*60}")
            for line in digest_lines:
                print(line)
            print(f"{'='*60}\n")

        return

    # ── Modo keyword única (full) ──
    if not args.keyword:
        parser.print_help()
        sys.exit(1)

    result = run_keyword(args.keyword, csv_path=csv_path)
    sr = result["score_result"]
    print(f"\n=== {args.keyword} ===")
    print(f"Score: {sr['score']}/100 ({sr['label']})")
    print(f"Pack: {result['md_path']}")
    if should_send:
        digest_line = pack.generate_digest_line(args.keyword, sr)
        send_digest(digest_line)


if __name__ == "__main__":
    main()
