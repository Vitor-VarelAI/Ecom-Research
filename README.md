# ECOM-RESEARCH — Pipeline de Research de Nichos

Pipeline CLI para pesquisa automatizada de keywords para e-commerce.
Input: keyword → Output: research-pack.md com score determinístico.

## Setup (< 10 passos)

```bash
# 1. Clonar o repo
git clone https://github.com/Vitor-VarelAI/Ecom-Research.git
cd Ecom-Research

# 2. Python 3.12+ (já deve estar no Contabo)
python3 --version

# 3. Criar virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 4. Instalar dependências
pip install -r requirements.txt

# 5. Configurar .env
cp .env.example .env
# Editar .env com as credenciais reais:
#   META_ACCESS_TOKEN  — https://developers.facebook.com/ → criar app → token ads_read
#   SERPAPI_KEY        — https://serpapi.com/ (free tier: 250 searches/mês)
#   TELEGRAM_BOT_TOKEN — (opcional) token do bot Telegram
#   TELEGRAM_CHAT_ID   — (opcional) ID do chat Telegram

# 6. Exportar CSV do Google Keyword Planner
#    No Google Ads → Keyword Planner → descarregar CSV
#    Colocar em: data/seu-export.csv

# 7. Configurar keywords
#    Editar config/keywords.txt (uma keyword por linha)

# 8. Testar
python run.py "mochila personalizada"
# Verificar output em: packs/

# 9. Correr testes
pip install pytest
pytest tests/ -v
```

## Uso

```bash
# Keyword única (modo full: Trends + SEO + Meta + CSV)
python run.py "mochila personalizada"

# Batch — todas as keywords em config/keywords.txt (sem consumir a lista)
python run.py --all

# Batch com digest no stdout (ligar ao Telegram bridge depois)
python run.py --all --digest

# Modos agendados (cron):
python run.py --sunday       # Deep run: consome keywords.txt, Full mode
python run.py --wednesday    # Pulse Meta: refresh ads, 0 calls SerpAPI
python run.py --monthly      # Re-score watchlist OBSERVAR (1 call/kw)
python run.py --auto         # Auto-detecta o dia e despacha

# Especificar CSV manual
python run.py "keyword" --csv data/meu-export.csv
```

## Quota SerpAPI

Free tier: **250 searches/mês**. Hard stop interno: **230 calls** (margem de segurança).

### Custo por operação

| Operação | Calls SerpAPI | Modo |
|---|---|---|
| Keyword novo (Full) | 3 | Trends timeseries + Related queries + SEO SERP |
| --wednesday (pulse Meta) | 0 | Só Meta Ad Library (grátis) |
| --monthly (re-score OBSERVAR) | 1 por keyword | Só Trends timeseries |
| Cache hit (mesma keyword <30d) | 0 | Dados em /data/cache/ |

### Orçamento mensal (250 calls)

| Rubrica | Calls | Notas |
|---|---|---|
| 4 domingos × ~8-10 keywords | ~120 | 24-30 calls por domingo |
| Watchlist mensal (day 1) | ~20 | Máx 20 keywords em OBSERVAR |
| **Buffer livre** | **~110** | Curiosidade manual, falhas, re-runs |
| **Total** | **250** | |

### Regras de proteção

1. **Cache 30 dias.** Re-run da mesma keyword = zero calls. Trends/SEO não mudam em semanas.
2. **Contador local** em `data/quota.json`. Reset dia 1 do mês. Hard stop aos 230.
3. **Meta como pre-filter.** Se keyword tem 0 ads ativos → DESCARTAR sem gastar SerpAPI.
4. **Pulse de quarta:** 0 calls SerpAPI. Só refresh Meta (fonte grátis).

## Output

```
packs/YYYY-MM-DD--{keyword-slug}.md   ← research-pack (schema PLAN.md)
data/YYYY-MM-DD--{keyword-slug}.json  ← dados brutos
data/cache/                           ← cache SerpAPI (30 dias)
data/quota.json                       ← contador de calls do mês
```

## Cron (instalar no Contabo)

```bash
# Editar crontab
crontab -e

# Adicionar (corre todos os dias às 08:00 UTC)
0 8 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --auto --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

O `--auto` deteta o dia:
- **Domingo:** deep run (consome keywords.txt, move para keywords_done.txt)
- **Quarta:** pulse Meta nos packs AVANÇAR/OBSERVAR (<14d)
- **Dia 1:** re-score watchlist OBSERVAR
- **Outros dias:** exit 0

## Telegram Bridge

A função `send_digest()` em `run.py` é um **stub** — faz print para stdout.
Para ligar ao Telegram, substituir o corpo da função por:

```python
def send_digest(text: str) -> None:
    import requests
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.warning("Telegram não configurado — a enviar para stdout")
        print(text)
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
```

## Estrutura

```
.
├── run.py                    # CLI principal
├── pipeline/
│   ├── quota.py              # Contador de quota SerpAPI (hard stop 230/mês)
│   ├── adapters/
│   │   ├── meta_ads.py       # Meta Ad Library API
│   │   ├── trends.py         # Google Trends (SerpAPI — 2 calls: timeseries + related)
│   │   ├── serp_seo.py       # Google SERP snapshot (SerpAPI — 1 call)
│   │   └── keyword_csv.py    # Parser CSV Keyword Planner
│   ├── scoring/
│   │   └── score.py          # Score determinístico (0-100)
│   └── output/
│       └── pack.py           # Gerador de research-pack.md + .json
├── config/
│   ├── keywords.txt          # Lista de keywords a processar (1 por linha)
│   └── keywords_done.txt     # Keywords já processadas (domingos)
├── packs/                    # Output: research-packs .md
├── data/                     # Dados brutos .json + cache + quota.json
├── tests/                    # Testes pytest
├── .env.example              # Template de credenciais
└── PLAN.md                   # Documento de arquitectura
```

## Design decisions

- **Zero dependências web** — CLI puro, nada de FastAPI/Flask
- **Zero LLM calls** na Fase 1 — score é matemática pura
- **Degradação graciosa** — se um adapter falha, os outros continuam
- **Cache 30 dias** no SerpAPI — 3 calls por keyword novo, 0 em re-runs
- **Meta pre-filter** — Meta primeiro (grátis), 0 ads = DESCARTAR sem gastar quota
- **Credenciais via .env** — nunca hardcoded
- **Hard stop 230 calls/mês** — contador local em data/quota.json
