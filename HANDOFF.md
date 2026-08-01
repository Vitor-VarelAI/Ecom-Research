# HANDOFF — Ecom Research

## Estado

O projeto é um pipeline CLI determinístico. A fonte central de ads é a Facebook Ad Library via Apify; o transporte antigo (Meta Graph API) foi removido. O utilizador mantém todas as decisões de produto e mercado. Sugestões, score e recomendações são apenas advisory.

Este commit fecha o incremento completo que estava na árvore: migração Apify, reescrita do `run.py` (modos e contratos de custo), novo schema de pack (decisão manual de mercado, sugestões advisory, métrica `ad_cohort` no pulse de quarta), entrega Telegram send-only e a suite de testes correspondente.

## Arquitetura

```text
run.py
pipeline/
  adapters/
    apify_ads.py       # uma invocação Apify, filtros, relevância e dedupe
    trends.py          # timeseries, related queries e cache de 30 dias
    serp_seo.py        # snapshot SEO com cache
    keyword_csv.py     # parser de export manual
    landing_price.py   # landing_url: HTTP -> Firecrawl, max 3
  delivery/
    telegram.py        # send-only, chunks de 4000, fallback stdout
  output/pack.py       # Markdown + JSON facebook_ads + sugestões + ad_cohort
  suggestions.py       # propostas determinísticas com status proposed
  quota.py             # quota SerpAPI e créditos Firecrawl
  scoring/score.py     # fórmula determinística inalterada
```

## Modos

- Full/domingo: um Apify por keyword. Falha Apify continua Trends/SEO/CSV como `DADOS INSUFICIENTES` (nunca um `DESCARTAR` falso); zero ads força `DESCARTAR`, salta SEO/landing e faz uma Related Query cacheada para sugestões.
- Quarta: uma chamada Apify paga e bounded por keyword elegível (AVANÇAR/OBSERVAR, <14 dias), zero SerpAPI/Firecrawl/landing. Reutiliza Trends, CSV, SEO, landing, sugestões e score; compara ad IDs e advertisers e calcula a métrica advisory `ad_cohort`. Falha preserva o último pack bom.
- Mensal: uma timeseries Trends forçada, sem Apify/SEO/landing/Firecrawl, e rescore com o resto do pack anterior.
- Auto no dia 1: mensal primeiro, depois domingo ou quarta; cada ação envia o seu digest.

## Métrica `ad_cohort` (pulse de quarta, advisory)

`_ad_cohort(previous, current)` faz álgebra de conjuntos determinística sobre `ad_ids` e identidades de advertiser (page_id, senão nome com accent-folding) entre o pack anterior e a leitura atual. Retenções de ads e anunciantes são percentagens `None` quando o baseline não tem itens comparáveis. Concentração = quota de variantes do maior anunciante. Sinais possíveis: `durable_demand`, `high_churn`, `concentrated_market`, `insufficient_baseline`. Nunca toca em scores ou labels. No pack aparece como secção **Persistência de mercado — pulse quarta (advisory)** e no JSON como `ad_cohort`.

## Contratos de operação

`APIFY_API_TOKEN` é a única credencial usada pelo adapter Apify. Nunca expor valores do `.env`, nunca versionar esse ficheiro e nunca adicionar sugestões automaticamente a `config/keywords.txt`. O código nunca executa uma sugestão, nunca escolhe país e nunca regista tokens. Nenhum cron foi instalado.

A chave raw do pack é `facebook_ads`; a secção de mercado chama-se **Sinais por mercado — decisão manual** (colunas `country`, `Trends demand`, `advertisers observed`, `ads observed`). O JSON guarda também `suggested_keywords` (com `status: proposed`) e `ad_cohort` (só quando há pulse). Preços só aceitam `top_ads[].landing_url`, nunca um URL da Ad Library. Telegram envia plain text sem campos interativos e imprime o digest completo se não puder entregar.

## Estado local

- `APIFY_API_TOKEN`, `SERPAPI_KEY` e `FIRECRAWL_KEY` estão configuradas no `.env` ignorado pelo Git e foram autenticadas com sucesso. Os valores nunca devem aparecer em documentação ou logs.
- `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` ainda não foram fornecidos.
- Existem cinco keywords em `config/keywords.txt`, zero packs gerados após esta refatoração e nenhum CSV real do Keyword Planner validado.
- A implementação Apify foi testada unitariamente; ainda falta um smoke full live e revisão humana do primeiro pack.
- Credenciais partilhadas em conversas anteriores devem ser regeneradas antes de produção.

## Verificação

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall run.py pipeline
.venv/bin/python run.py --help
git diff --check
```

Último resultado: **135 testes passaram**, `compileall`, CLI help e `git diff --check` passaram; todos os testes de rede usam mocks.

## Próxima sessão

1. Receber `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` sem os expor em docs ou commits.
2. Validar uma mensagem Telegram send-only e o fallback stdout.
3. Confirmar ou exportar o CSV do Google Keyword Planner para `data/`.
4. Executar um único smoke full live, rever custo/relevância do Apify, inspecionar manualmente o pack (incluindo o bloco `ad_cohort` num pulse de quarta) e confirmar que o score e as sugestões estão sanos.
5. Só depois instalar o cron diário das 08:00 com `run.py --auto --digest`.

A aprovação do utilizador continua obrigatória para produto, mercado, sugestões e handoff para ad-machine.

## Suggested skills

- `tdd` — para qualquer adaptador ou modo novo (a suite atual cobre contrato, custo e normalização com mocks).
- `diagnose` — se o smoke full live revelar falhas de rede/parser do Apify ou da SerpAPI, usar o loop reproduzir → isolar → corrigir.
