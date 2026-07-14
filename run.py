#!/usr/bin/env python3
"""
ECOM-RESEARCH Pipeline — Fase 1

CLI:
    python run.py "keyword"        → pesquisa uma keyword e gera research-pack
    python run.py --all            → pesquisa todas as keywords em config/keywords.txt
    python run.py --all --digest   → igual, mas envia digest para Telegram

Setup:
    cp .env.example .env   # e preenche com credenciais reais
    pip install -r requirements.txt

Output:
    packs/YYYY-MM-DD--{keyword-slug}.md   (research-pack)
    data/YYYY-MM-DD--{keyword-slug}.json  (dados brutos)
"""

import argparse
import logging
import os
import sys
from datetime import date

# Garantir que o repo root está no path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

# Carregar .env antes de importar adapters
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv não instalado; o user tem de exportar as vars manualmente

from pipeline.adapters import meta_ads, trends, keyword_csv
from pipeline.scoring import score
from pipeline.output import pack

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


def run_keyword(keyword: str, csv_path: str | None = None) -> dict:
    """
    Executa o pipeline completo para uma keyword.

    Args:
        keyword: keyword a pesquisar
        csv_path: caminho para CSV do Keyword Planner (se None, procura automaticamente)

    Returns:
        dict com resultado (pack_path, score_result, etc.)
    """
    logger.info("=== INÍCIO: keyword=%r ===", keyword)

    # 1. Trends
    logger.info("[1/4] Trends (SerpAPI)...")
    trends_data = trends.fetch(keyword)
    if "error" in trends_data:
        logger.warning("Trends: %s", trends_data["error"])
    else:
        logger.info("Trends: direção=%s slope=%+.1f%%", trends_data.get("direction"), trends_data.get("slope_pct"))

    # 2. Meta Ads
    logger.info("[2/4] Meta Ads...")
    ads_data = meta_ads.fetch(keyword)
    if "error" in ads_data:
        logger.warning("Meta Ads: %s", ads_data["error"])
    else:
        logger.info("Meta Ads: total=%d saturação=%s", ads_data.get("total_ads", 0), ads_data.get("saturation"))

    # 3. Keyword Planner CSV
    logger.info("[3/4] Keyword Planner CSV...")
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

    # 4. Scoring + Output
    logger.info("[4/4] Scoring + Pack...")
    score_result = score.compute(trends_data, csv_data, ads_data)
    logger.info("Score: %d/100 (%s)", score_result["score"], score_result["label"])

    md_path = pack.generate(
        keyword=keyword,
        trends_data=trends_data,
        csv_data=csv_data,
        ads_data=ads_data,
        score_result=score_result,
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
    }


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
        help="Pesquisar todas as keywords em config/keywords.txt",
    )
    parser.add_argument(
        "--csv",
        help="Caminho para CSV do Keyword Planner (default: procura em data/)",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="Enviar digest para Telegram no final (só com --all)",
    )

    args = parser.parse_args()

    if not args.keyword and not args.all:
        parser.print_help()
        sys.exit(1)

    csv_path = args.csv

    if args.all:
        # Modo batch: todas as keywords
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
                # Degradação graciosa: continuar com a próxima keyword
                results.append({
                    "keyword": kw,
                    "error": str(e),
                    "score_result": {"score": 0, "label": "ERRO"},
                })

        # Digest
        if args.digest:
            digest_lines = []
            for r in results:
                kw = r.get("keyword", "?")
                sr = r.get("score_result", {"score": 0, "label": "ERRO"})
                digest_lines.append(pack.generate_digest_line(kw, sr))
            digest_text = "\n".join(digest_lines)
            send_digest(digest_text)
        else:
            # Sempre mostrar resumo no stdout
            print(f"\n{'='*60}")
            print("RESUMO DO DIA")
            print(f"{'='*60}")
            for r in results:
                kw = r.get("keyword", "?")
                sr = r.get("score_result", {"score": 0, "label": "ERRO"})
                print(pack.generate_digest_line(kw, sr))
            print(f"{'='*60}\n")

    else:
        # Modo keyword única
        result = run_keyword(args.keyword, csv_path=csv_path)
        # Mostrar resumo
        sr = result["score_result"]
        print(f"\n=== {args.keyword} ===")
        print(f"Score: {sr['score']}/100 ({sr['label']})")
        print(f"Pack: {result['md_path']}")
        if args.digest:
            digest_line = pack.generate_digest_line(args.keyword, sr)
            send_digest(digest_line)


if __name__ == "__main__":
    main()
