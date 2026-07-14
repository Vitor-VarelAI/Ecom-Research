# ECOM-RESEARCH — Plano de Revival — 2026-07-14

Repo original: https://github.com/Vitor-VarelAI/Ecom-Research (1 commit, 2025-07-15, esqueleto Streamlit sem lógica)

## Decisão de arquitetura

Morre a UI. O projeto renasce como **pipeline CLI no Contabo + skill no vault**, agent-native: files as interface. Output é um `research-pack.md` por keyword, consumido pelo `ad-machine` como input de campanha e entregue diariamente via Telegram.

Razões:
1. Um único user (Vitor). Dashboard Next.js + FastAPI = 2 serviços a manter com ROI zero.
2. Todo o sistema existente (skills, vault, Telegram bridge, ad-machine) fala em ficheiros. Um dashboard vive fora do circuito; um .md entra direto.
3. KPI original ainda válido: reduzir research de 3h para 15min. Um digest diário no Telegram cumpre isto sem UI.

UI só se doer, e nesse caso: página Next.js estática a ler os JSONs do VPS. Sem API entre eles. Não é Fase de nada, é hipótese futura.

## O que se mantém do original

- Conceito: keyword → multi-source → score → decisão
- Data flow dos docs (`extracted_docs/DATA_FLOW.md`)
- KPI: 1 insight útil/dia, 3h → 15min
- Keyword Planner via CSV manual (não há API grátis, continua certo)

## O que morre

- Streamlit e todo o código de `app/` e `components/` (era placeholder)
- pytrends (Google bloqueia desde 2024)
- Scraping direto do TikTok como fonte primária (anti-bot 2026)
- requirements.txt de 2025

## Fontes de dados (2026, por ordem de prioridade)

| Fonte | Via | Custo | Nota |
|---|---|---|---|
| Meta Ad Library | API oficial | Grátis | **Fonte central.** Na UE, o DSA obriga a Meta a expor TODOS os ads (comerciais incluídos) com alcance por país. Para mercado PT/ES é a fonte mais rica que existe. Requer app FB + token. |
| Google Trends | SerpAPI | 250 searches/mês grátis | Cache agressiva (1 keyword = 1 call, guardar 30 dias). Se apertar: Glimpse ou scraping com cache. |
| Keyword Planner | CSV manual | Grátis | Vitor exporta, pipeline faz parse. |
| Reddit | PRAW | Grátis (<100 QPM) | Fase 2. |
| YouTube | Data API v3 | Grátis (10k unid/dia) | Fase 2. |
| TikTok Creative Center | Scraper no VPS | Grátis, frágil | Fase 2. Aceitar que parte; nunca bloquear o pipeline por causa dele. |

## Schema do research-pack (o contrato)

Um ficheiro por keyword por run: `packs/YYYY-MM-DD--{keyword-slug}.md`
Dados brutos em paralelo: `data/YYYY-MM-DD--{keyword-slug}.json`

```markdown
# RESEARCH PACK — {keyword} — {data}

## Veredicto
Score: {0-100} | {AVANÇAR / OBSERVAR / DESCARTAR}
{1-2 linhas: porquê, em linguagem de decisão}

## Trend Signal (Google Trends via SerpAPI)
- Direção 90 dias: {subida/estável/queda} ({slope %})
- Sazonalidade: {sim/não + pico}
- Geos quentes: {top 3 países}

## Volume & CPC (Keyword Planner CSV)
- Volume mensal: {n} | CPC médio: €{n}
- Top 5 keywords relacionadas com volume

## Ads Ativos (Meta Ad Library)
- Total de ads ativos para o nicho: {n}
- Top 3 ads por longevidade (ad a correr há semanas = ad que paga):
  - {advertiser} | ativo há {n} dias | alcance UE: {n} | link
- Ângulos de copy dominantes: {2-3 bullets}
- Saturação: {baixa <10 ads / média 10-50 / alta >50}

## SEO snapshot (SerpAPI engine google, PT)
- Top 10 orgânicos: {domínio + título, quem domina}
- Ads no SERP: {n} (proxy de competição paga)
- PAA questions: {top 3, matéria-prima para conteúdo}
- Oportunidade SEO: {alta se top 10 fraco / baixa se dominado por marketplaces}

## Feedback (Fase 2 — Reddit/YouTube)
- Clusters de dor/desejo: {bullets}
- Red flags: {queixas recorrentes}

## Score breakdown
trend({peso 0.3}) + volume({0.2}) + ad_longevity({0.3}) + saturação_invertida({0.2}) = {total}
Fórmula determinística. Sem LLM no score.

## Próximo passo sugerido
{1 linha: ex. "alimentar ad-machine com este pack" ou "reavaliar em 14 dias"}
```

Regras do schema:
1. **Veredicto primeiro.** O Vitor lê 3 linhas no Telegram e decide se abre o resto.
2. Score determinístico e reproduzível. LLM só entra na Fase 3 (clustering de comentários), delegado ao DeepSeek.
3. Secções sem dados escrevem "sem dados: {razão}", nunca inventam.

## Cadência e quota (regime 80/20)

Contexto: projeto side, 2 pontos de contacto por semana. O cron corre diário no sentido técnico (verifica o dia, decide o que fazer), mas o Vitor só interage domingo e quarta.

### Quotas confirmadas (julho 2026)

| Fonte | Free tier | Binding? |
|---|---|---|
| SerpAPI | **250 searches/mês**, 50/hora | SIM — única quota que morde |
| Meta Ad Library | Grátis, rate limits Graph API standard | Não |
| YouTube Data v3 | 10.000 units/dia (search=100, comments=1) | Não |
| Reddit PRAW | 100 queries/min | Não |
| Keyword Planner | CSV manual | Não |

### Custo SerpAPI por keyword (modo Full)

Cada `data_type` do engine google_trends é 1 search separado:
1. Trends timeseries (90 dias) = 1 call
2. Related queries = 1 call
3. SEO SERP orgânico (engine google: quem ranka, ads no SERP, PAA) = 1 call

**Total: 3 calls por keyword novo.**

### Regime semanal

**Domingo — deep run (dia caro):**
- 8-10 keywords novos em modo Full = 24-30 calls SerpAPI
- Meta Ad Library para todos (grátis)
- Digest completo no Telegram ao fim do run: veredicto em 3 linhas por keyword, para planear a semana

**Quarta — pulse (dia grátis, 0 calls SerpAPI):**
- Refresh Meta Ad Library APENAS nos keywords com veredicto AVANÇAR/OBSERVAR da semana
- Detetar: ads novos a entrar no nicho, ads antigos que morreram, longevidade atualizada
- Digest curto no Telegram: "os ads mexeram?"
- Racional: Trends/SEO quase não mudam em 3 dias; ads na Meta mudam depressa e a fonte é grátis

**Dia 1 do mês — watchlist re-score:**
- Keywords em OBSERVAR: re-score com 1 call cada (timeseries só)
- Watchlist máx 20 keywords = 20 calls/mês

### Orçamento mensal

| Rubrica | Calls |
|---|---|
| 4 domingos × ~30 | 120 |
| Watchlist mensal | 20 |
| **Buffer livre** (curiosidade manual, falhas, re-runs) | **110** |
| **Total** | **250** |

Capacidade: ~35-40 nichos novos analisados a fundo/mês + 20 em observação contínua. Custo €0.

### Regras de proteção do budget (implementar no pipeline)

1. **Cache 30 dias.** Re-run da mesma keyword dentro de 30 dias = zero calls SerpAPI (Trends não muda o suficiente em 7 dias).
2. **Contador local de quota** em `data/quota.json` (calls do mês corrente, reset dia 1). Aos 230 calls, o pipeline recusa keywords novos e avisa no Telegram. Nunca deixar a SerpAPI devolver 429 às cegas.
3. **Meta como pre-filter.** Ordem de execução: Meta Ad Library PRIMEIRO (grátis). Se a keyword tem 0 ads ativos, gravar pack com veredicto DESCARTAR (razão: "sem mercado de ads ativo") e NÃO gastar as 3 calls de SerpAPI. Poupa 30-40% da quota.
4. **Watchlist refresh mensal, não semanal.**

### Lógica de dias no cron

```
cron diário →
  se domingo: deep_run(config/keywords.txt, modo=full)
  se quarta:  meta_pulse(packs com veredicto AVANÇAR|OBSERVAR, <14 dias)
  se dia 1:   watchlist_rescore(packs OBSERVAR, 1 call/kw)
  senão:      exit 0
```

## Fases

### Fase 0 — Setup (1 sessão, Vitor)
- [ ] Criar app FB + token Meta Ad Library API
- [ ] Conta SerpAPI (free tier)
- [x] Repo limpo: branch legacy, main novo
- [x] Estrutura: `/pipeline` (Python), `/packs`, `/data`, `/config/keywords.txt`

### Fase 1 — Pipeline mínimo (1 fim de semana, delegável ao DeepSeek)
- [x] Adapter Meta Ad Library (interface comum: `fetch(keyword) -> dict`)
- [ ] Adapter SerpAPI Trends com cache em disco (30 dias) + related queries
- [ ] Adapter SerpAPI SEO (engine google, SERP snapshot)
- [ ] Parser CSV Keyword Planner
- [ ] Scoring determinístico (pesos no schema acima)
- [ ] Gerador de research-pack.md a partir dos dicts
- [ ] Quota counter (data/quota.json, hard stop 230)
- [ ] Pre-filter Meta (Meta primeiro, 0 ads = DESCARTAR sem SerpAPI)
- [ ] CLI modos: --sunday, --wednesday, --monthly, --auto
- [ ] Cron diário no Contabo + push do Veredicto para o Telegram bridge
- Critério de feito: `python run.py "mochila personalizada"` produz pack completo e chega ao Telegram

### Fase 2 — Fontes secundárias (só após Fase 1 provar valor)
- [ ] PRAW Reddit adapter
- [ ] YouTube Data API adapter
- [ ] TikTok Creative Center scraper (best-effort, nunca bloqueia o run)

### Fase 3 — Clustering IA
- [ ] Comentários Reddit/YouTube → DeepSeek via delegation-gate → clusters de dor/desejo
- [ ] Só esta fase toca em LLM

## Skill no vault (paralelo à Fase 1)
- [ ] `ecom-research` skill: triggers "research de nicho", "vale a pena este produto", "pack de {keyword}"
- [ ] A skill lê os packs existentes primeiro; só dispara run novo se o pack tiver >30 dias
- [ ] Handoff direto para `ad-machine` quando veredicto = AVANÇAR

## Kill criteria

Se após **2 semanas de digests diários** nenhum pack alimentar o ad-machine nem gerar decisão de produto, o projeto morre. Custo total do falhanço: 1 fim de semana. Sem sunk cost.

## Custo estimado

- SerpAPI free tier: €0 (250 searches/mês com cache 30 dias)
- Meta Ad Library: €0
- Compute: já pago (Contabo)
- LLM: €0 na Fase 1-2; centavos na Fase 3 via DeepSeek
