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
#   SERPAPI_KEY        — https://serpapi.com/ (free tier: 100 searches/mês)
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
# Keyword única
python run.py "mochila personalizada"

# Batch — todas as keywords em config/keywords.txt
python run.py --all

# Batch com digest no stdout (ligar ao Telegram bridge depois)
python run.py --all --digest

# Especificar CSV manual
python run.py "keyword" --csv data/meu-export.csv
```

## Output

```
packs/YYYY-MM-DD--{keyword-slug}.md   ← research-pack (schema PLAN.md)
data/YYYY-MM-DD--{keyword-slug}.json  ← dados brutos
data/cache/                           ← cache SerpAPI (7 dias)
```

## Cron (instalar no Contabo)

```bash
# Editar crontab
crontab -e

# Adicionar (corre todos os dias às 08:00 UTC)
0 8 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --all --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

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
│   ├── adapters/
│   │   ├── meta_ads.py       # Meta Ad Library API
│   │   ├── trends.py         # Google Trends (SerpAPI + cache 7d)
│   │   └── keyword_csv.py    # Parser CSV Keyword Planner
│   ├── scoring/
│   │   └── score.py          # Score determinístico (0-100)
│   └── output/
│       └── pack.py           # Gerador de research-pack.md + .json
├── config/
│   └── keywords.txt          # Lista de keywords (1 por linha)
├── packs/                    # Output: research-packs .md
├── data/                     # Dados brutos .json + cache
├── tests/                    # Testes pytest
├── .env.example              # Template de credenciais
└── PLAN.md                   # Documento de arquitectura
```

## Design decisions

- **Zero dependências web** — CLI puro, nada de FastAPI/Flask
- **Zero LLM calls** na Fase 1 — score é matemática pura
- **Degradação graciosa** — se um adapter falha, os outros continuam
- **Cache 7 dias** no SerpAPI — 1 call por keyword por semana
- **Credenciais via .env** — nunca hardcoded
