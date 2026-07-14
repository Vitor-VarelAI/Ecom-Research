# HANDOFF — ecom-research — 2026-07-14

## O que é este projeto

Pipeline CLI no Contabo que pesquisa nichos de e-commerce: keyword → multi-source (Meta Ad Library, Google Trends via SerpAPI, Keyword Planner CSV, SEO SERP) → score determinístico → research-pack.md. Regime 80/20: deep run ao domingo, pulse Meta à quarta, re-score da watchlist no dia 1. Digest via Telegram.

Repo: https://github.com/Vitor-VarelAI/Ecom-Research
Local: /home/vitor/projects/Ecom-Research
Commit atual: 90945c7 (main)
Python: 3.12, venv em .venv/
Testes: 120/120 verdes, zero network

## Estado

Fase 1 + Delta 2 completos e shipped. O pipeline está funcional mas sem credenciais (Fase 0 pendente — o Vitor tem de criar as apps).

### Veredicto das 4 correções do review de 2026-07-14: APLICADAS (commit bd70f5a)

1. --monthly gasta 1x quota — APLICADO. trends.fetch(kw, timeseries_only=True) faz 1 call, salta RELATED_QUERIES. Teste confirma spend_count == 1.
2. Re-score mensal não apanha cache — APLICADO. trends.fetch(kw, force_refresh=True) ignora cache. Teste confirma 3ª call com force_refresh faz +2 API calls.
3. Pre-filter Meta força DESCARTAR — APLICADO. Score forçado {score:0, label:"DESCARTAR", reason:"sem mercado de ads ativo (pre-filter Meta)"} em vez de score.compute(). pack.py mostra reason no DECISÃO. 0 ads + CSV com volume não dá OBSERVAR.
4. Pulse quarta reutiliza pack anterior — APLICADO. Em vez de "sem dados", carrega _load_previous_pack() e reutiliza trends/csv/seo. O pack mais recente é o mais completo.

### Delta 2 (decisão de produto + Firecrawl): APLICADO (commit 90945c7)

- Delta A (advertisers + market_proven): APLICADO em meta_ads.py
- Delta B (landing_price.py hybrid HTTP→Firecrawl): APLICADO, com contador firecrawl_credits no quota.py
- Delta C (tabela Onde vender): APLICADO em pack.py
- Delta D (bloco DECISÃO): APLICADO em pack.py, substitui Veredicto
- Firecrawl upgrade: APLICADO por cima do Delta B do subagent (nível 2, pausa 6s, soft warning 800)

### Pendente

- Fase 0: credenciais (Vitor cria apps FB, SerpAPI, Firecrawl já tem chave)
- Fase 2: Reddit/YouTube/TikTok (não iniciada)
- Fase 3: Clustering IA (não iniciada)
- Skill no vault: ecom-research skill (não iniciada)
- Cron: não instalado (Vitor instala após Fase 0)
- send_digest(): stub, liga ao Telegram bridge

O que falta para o pipeline ficar live:
1. Vitor cria app FB + token Meta Ad Library (developers.facebook.com, permissão ads_read)
2. Vitor cria conta SerpAPI free tier (serpapi.com, 250 searches/mês)
3. Vitor tem chave Firecrawl (firecrawl.dev, 1000 credits/mês)
4. cp .env.example .env e preencher: META_ACCESS_TOKEN, SERPAPI_KEY, FIRECRAWL_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
5. Exportar CSV do Google Keyword Planner → data/
6. Ligar send_digest() em run.py ao Telegram bridge (código exemplo no README)
7. Instalar cron: 0 8 * * * cd /home/vitor/projects/Ecom-Research && .venv/bin/python run.py --auto --digest

## Estrutura do repo

```
run.py                    # CLI: keyword / --sunday / --wednesday / --monthly / --auto
pipeline/
  adapters/
    meta_ads.py           # Meta Ad Library API (agrupa por advertiser, market_proven)
    trends.py             # SerpAPI Google Trends (timeseries + related, cache 30d, timeseries_only, force_refresh)
    serp_seo.py            # SerpAPI Google SERP (top 10, ads, PAA, oportunidade SEO)
    keyword_csv.py        # Parser CSV Keyword Planner (resiliente EN/PT)
    landing_price.py      # Preço de landings: HTTP → Firecrawl fallback, JSON-LD + regex
  scoring/score.py        # Score determinístico (trend 0.3 + volume 0.2 + ad_longevity 0.3 + saturação 0.2)
  output/pack.py          # Gerador de research-pack.md + .json (bloco DECISÃO, tabela Onde vender)
  quota.py                # Contador SerpAPI (230 hard stop) + Firecrawl (1000, warning 800), reset dia 1
config/keywords.txt       # Keywords para deep run de domingo
packs/                    # Output: YYYY-MM-DD--{slug}.md (gitignored)
data/                     # JSONs brutos + cache + quota.json (gitignored)
tests/                    # 120 testes, zero network
PLAN.md                   # Documento de arquitetura completo (lê primeiro)
README.md                 # Setup em 9 passos + cron + tabela de quota
.env.example              # Template de credenciais
```

## Arquitetura key

- Score NÃO muda. Pesos: trend(0.3) + volume(0.2) + ad_longevity(0.3) + saturação_invertida(0.2). Labels: AVANÇAR ≥70, OBSERVAR 40-69, DESCARTAR <40.
- Pre-filter Meta: Meta primeiro (grátis). 0 ads = DESCARTAR forçado com reason, sem gastar SerpAPI.
- Cache 30 dias em data/cache/ para trends e SEO. force_refresh=True no --monthly.
- Pulse quarta: reutiliza trends/csv/seo do pack anterior, só atualiza Meta. Detecta delta de variantes por advertiser.
- Landing price: HTTP primeiro (grátis), Firecrawl só se HTTP não encontra preço (1 credit). Pausa ≥6s entre calls Firecrawl.
- Degradação graciosa em todos os adapters. Adapter que falha = secção "sem dados: {razão}", run continua.

## Quota (quadro completo)

| Fonte | Limite | Controlo | Uso previsto |
|---|---|---|---|
| SerpAPI | 250/mês | hard stop 230 em data/quota.json | ~140/mês (4 domingos + watchlist) |
| Firecrawl | 1.000/mês | soft warning 800 em data/quota.json | ~120/mês |
| Meta Ad Library | ilimitado | — | — |
| Resto | ilimitado | — | — |

## Próximas fases (não iniciadas)

Fase 2 — Fontes secundárias:
- PRAW Reddit adapter
- YouTube Data API v3 adapter
- TikTok Creative Center scraper (best-effort, nunca bloqueia o run)

Fase 3 — Clustering IA:
- Comentários Reddit/YouTube → DeepSeek → clusters de dor/desejo
- Só esta fase toca em LLM

Skill no vault (paralelo):
- `ecom-research` skill: triggers "research de nicho", "vale a pena este produto", "pack de {keyword}"
- Lê packs existentes primeiro; só dispara run novo se pack >30 dias
- Handoff para `ad-machine` quando veredicto = AVANÇAR

Kill criteria: se após 2 semanas de digests diários nenhum pack alimentar o ad-machine nem gerar decisão de produto, o projeto morre.

## Decisões técnicas importantes

- Streamlit morreu. Dashboard seria 2 serviços com ROI zero para 1 user. CLI + cron + Telegram.
- Git remote é SSH (gh auth usa SSH internamente). HTTPS não funciona.
- Slug de cache é md5 hash, não slug legível — evita colisões e caracteres PT.
- _read_cache docstring diz "< 30 dias" (era "< 7 dias", corrigido).
- trends.fetch() tem timeseries_only=True (1 call, sem related) e force_refresh=True (ignora cache) para o --monthly.
- Bloco DECISÃO substitui Veredicto. Digest do Telegram usa Vender?/Onde?/Prova como 3 linhas.

## Skills sugeridas para próxima sessão

- coding-guidelines (obrigatória antes de codar)
- hermes-agent (se configurar cron ou Telegram bridge)
- handoff (se precisar de passar a outra sessão)

## Ficheiros de referência

- PLAN.md no repo — documento de arquitetura completo com schema, pesos, quota, fases
- README.md no repo — setup em 9 passos + cron entry + código do Telegram bridge
- .env.example no repo — todas as variáveis necessárias
- git log --oneline — 4 commits: scaffold → Fase 1 → correções → Delta 2 + Firecrawl
