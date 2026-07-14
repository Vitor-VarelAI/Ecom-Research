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
| Google Trends | SerpAPI | 100 searches/mês grátis | Cache agressiva (1 keyword = 1 call, guardar 7 dias). Se apertar: Glimpse ou scraping com cache. |
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

## Fases

### Fase 0 — Setup (1 sessão, Vitor)
- [ ] Criar app FB + token Meta Ad Library API
- [ ] Conta SerpAPI (free tier)
- [ ] Repo limpo: arquivar o atual ou branch `legacy`, começar `main` novo
- [ ] Estrutura: `/pipeline` (Python), `/packs`, `/data`, `/config/keywords.txt`

### Fase 1 — Pipeline mínimo (1 fim de semana, delegável ao DeepSeek)
- [ ] Adapter Meta Ad Library (interface comum: `fetch(keyword) -> dict`)
- [ ] Adapter SerpAPI Trends com cache em disco (7 dias)
- [ ] Parser CSV Keyword Planner
- [ ] Scoring determinístico (pesos no schema acima)
- [ ] Gerador de research-pack.md a partir dos dicts
- [ ] Cron diário no Contabo + push do Veredicto para o Telegram bridge
- Critério de feito: `python pipeline/run.py "mochila personalizada"` produz pack completo e chega ao Telegram

### Fase 2 — Fontes secundárias (só após Fase 1 provar valor)
- [ ] PRAW Reddit adapter
- [ ] YouTube Data API adapter
- [ ] TikTok Creative Center scraper (best-effort, nunca bloqueia o run)

### Fase 3 — Clustering IA
- [ ] Comentários Reddit/YouTube → DeepSeek via delegation-gate → clusters de dor/desejo
- [ ] Só esta fase toca em LLM

## Skill no vault (paralelo à Fase 1)
- [ ] `ecom-research` skill: triggers "research de nicho", "vale a pena este produto", "pack de {keyword}"
- [ ] A skill lê os packs existentes primeiro; só dispara run novo se o pack tiver >7 dias
- [ ] Handoff direto para `ad-machine` quando veredicto = AVANÇAR

## Kill criteria

Se após **2 semanas de digests diários** nenhum pack alimentar o ad-machine nem gerar decisão de produto, o projeto morre. Custo total do falhanço: 1 fim de semana. Sem sunk cost.

## Custo estimado

- SerpAPI free tier: €0 (até ~3 keywords novas/dia com cache)
- Meta Ad Library: €0
- Compute: já pago (Contabo)
- LLM: €0 na Fase 1-2; centavos na Fase 3 via DeepSeek

---

# DELEGATION BRIEF — ecom-research Fase 1 — 2026-07-14

## Objetivo
Pipeline Python no Contabo que recebe uma keyword e produz um research-pack.md com trend signal, dados de ads da Meta Ad Library, volume/CPC de CSV, e score determinístico. Entrega diária do veredicto via Telegram.

## Contexto mínimo
- Repo: github.com/Vitor-VarelAI/Ecom-Research (ignorar código existente em app/ e components/, é placeholder)
- Schema do pack e pesos do score: ver secção "Schema do research-pack" neste ficheiro (PLAN.md)
- VPS: Contabo, Ubuntu 24.04, Python 3.12
- Credenciais (Meta token, SerpAPI key, Telegram bot token) chegam via .env; NUNCA hardcoded
- Telegram bridge já existe no VPS; expor apenas uma função `send_digest(text)` que o Vitor liga depois

## Tarefa
1. Criar estrutura: `/pipeline/{adapters,scoring,output}`, `/packs`, `/data`, `/config/keywords.txt`, `.env.example`
2. Adapter `adapters/meta_ads.py`: dado keyword, query à Meta Ad Library API (endpoint `ads_archive`, filtro país PT+ES+BR), devolve dict com total ads ativos, top 3 por longevidade (dias ativo, alcance UE, advertiser, link), link), lista de ad copies
3. Adapter `adapters/trends.py`: SerpAPI Google Trends, 90 dias, devolve slope %, direção, top geos. Cache em `/data/cache/` com TTL 7 dias; se cache válida, ZERO calls
4. Adapter `adapters/keyword_csv.py`: parse do export do Google Keyword Planner (formato standard PT), devolve volume, CPC, top 5 related
5. `scoring/score.py`: fórmula determinística com pesos trend 0.3, volume 0.2, ad_longevity 0.3, saturação_invertida 0.2. Normalização documentada em docstring. Output 0-100 + label (AVANÇAR ≥70, OBSERVAR 40-69, DESCARTAR <40)
6. `output/pack.py`: gera o .md exatamente no schema definido, mais o .json bruto em /data. Secções sem dados escrevem "sem dados: {razão}"
7. `run.py`: CLI (`python run.py "keyword"` e `python run.py --all` para keywords.txt). No fim de --all, chama `send_digest()` com os veredictos do dia (3 linhas por keyword)
8. Cron entry documentada no README (não instalar; o Vitor instala)
9. Testes: 1 teste por adapter com fixture JSON gravada (sem calls reais), 1 teste do scoring com valores conhecidos

Critérios de feito verificáveis:
- `python run.py "mochila personalizada"` produz `/packs/2026-MM-DD--mochila-personalizada.md` com todas as secções do schema
- `pytest` verde sem network
- `.env.example` lista todas as variáveis necessárias
- README com setup em <10 passos

## Restrições
- Escrever APENAS dentro do repo
- Zero LLM calls nesta fase; o score é matemática pura
- Zero scraping de TikTok/Reddit/YouTube nesta fase
- Rate limits: máx 1 call SerpAPI por keyword por run; respeitar cache
- Sem frameworks web (nada de FastAPI/Flask); isto é CLI + cron
- Se a Meta Ad Library API exigir campo/permissão não documentada aqui, não improvisar: registar como bloqueio

## Formato de entrega
Quando terminares, usa o skill review-pack para comprimir o resultado.
Foco do review: schema do pack respeitado à letra, lógica de cache do SerpAPI, normalização do score, e que nenhum adapter parte o run inteiro quando falha (degradação graciosa por secção).

## Se ficares bloqueado
Para e regista no review-pack em "Pendente / bloqueado".
Não improvises workarounds sem os declarar em "Desvios e anomalias".
