# ECOM-RESEARCH — plano operacional

## Objetivo

Manter um pipeline CLI reproduzível em que cada execução combina produto, mercado e keyword local. Ads, Trends, SERP e Keyword Planner têm de provar o mesmo mercado antes de existir uma recomendação; score, sinais e recomendações continuam advisory.

## Fluxo Full

1. Resolver um target canónico a partir de `config/products.json` + `config/markets.json`; a matriz inicial contém PT para os cinco produtos e GB apenas para velas.
2. Fazer uma chamada ao actor Apify `curious_coder~facebook-ads-library-scraper` com a keyword e o país do target (`PT` ou `GB`), `is_targeted_country=true` e `count=100`.
3. Normalizar localmente, retirar anúncios inativos e gate-1-irrelevantes, deduplicar e classificar cada candidato com `pipeline/matching/product_ads.py` (`matched`/`needs_review`/`rejected`). SEO e landing usam apenas `top_ads` matched; evidência ambígua nunca entra no score.
4. Recolher Trends, Related Queries e SERP com os parâmetros do mesmo mercado e ler apenas um export Keyword Planner cujo sidecar prove `market_id` e moeda. Qualquer scope ausente ou incompatível força `DADOS INSUFICIENTES`; zero matched inequívoco pode produzir `DESCARTAR`.
5. Persistir Markdown e JSON separados por mercado, incluindo snapshot `market`, `market_signals`, fontes scoped, candidatos classificados, `matcher_version` e `matching_schema_version`.

## Contrato Apify

A requisição usa POST para o endpoint `run-sync-get-dataset-items`, `Authorization: Bearer APIFY_API_TOKEN`, `Content-Type: application/json`, query `clean=true&format=json`, timeout `(10,135)` e nenhum retry. O payload contém uma URL com `active_status=active`, `ad_type=all`, `country=<market.ads_country>`, `is_targeted_country=true`, `media_type=all`, `q` URL-encoded, `search_type=keyword_unordered`, e `count=100`. `country=ALL` fica reservado a descoberta global fora deste scoring.

O resultado do adapter persiste `market_id` e `query_country`, além das contagens, política de relevância, versão de matching e até 100 candidatos canónicos. O adapter não decide fit de produto: `pipeline/matching/product_ads.py` classifica cada candidato (`matched` / `needs_review` / `rejected`) e deriva score inputs apenas dos candidatos matched. O gate geográfico valida que o payload corresponde ao mercado esperado antes de aceitar a evidência. Cada candidato/top ad mantém identidade, anunciante, longevidade, links sanitizados na persistência, copy, países e `match_evidence`; prova parcial é `needs_review` e ausência do product-head é `rejected`.

## Cadência e limites

| Ação | Comportamento |
|---|---|
| Full/domingo | Um Apify por target; Trends/SEO/landing conforme o resultado; Related Queries e caches separados por mercado |
| Quarta | Um Apify por target elegível; zero SerpAPI, Firecrawl ou landing; compara apenas o baseline `(product_id, market_id)` matched-only |
| Mensal | Uma timeseries Trends forçada por target OBSERVAR; zero Apify, SEO, landing ou Firecrawl; rescore apenas com baseline do mesmo mercado |
| Auto no dia 1 | Mensal primeiro; depois domingo ou quarta; cada ação conserva o próprio digest |

Cache de Trends, SEO e Related Queries dura 30 dias; a timeseries do Trends usa timeout de 60s e a política de proveniência expõe o mesmo valor. A quota SerpAPI mantém hard stop local em 230 chamadas. O limite Apify por execução é uma invocação por target e o sample cap é 100.

## Sugestões

Related Queries passa primeiro por relevância fail-closed do produto e depois por `extract_keyword_suggestions`: valores numéricos e `Breakout` são normalizados, Breakout vem primeiro, números ordenam descendentemente, e removem-se vazios, origem, duplicados, URLs e interrogativas. Cada entrada persistida tem `status: proposed`. Nunca se escreve em `config/keywords.txt` nem se executa uma sugestão automaticamente; o digest agrega no máximo três por fonte. O ficheiro permanece intacto e já não é lido em runtime — a fonte de produtos é o registo canónico em `config/products.json`.

## Decisão e mercados

O score mantém os pesos `trend 0.3 + volume 0.2 + ad_longevity 0.3 + saturação_invertida 0.2`. A recomendação é:

- `AVANÇAR <mercado>`: preparar validação comercial nesse mercado depois de aprovação manual.
- `OBSERVAR <mercado>`: manter a watchlist local e voltar a verificar.
- `DESCARTAR <mercado>`: arquivar nesse mercado salvo nova evidência.
- `DADOS INSUFICIENTES <mercado>`: corrigir ou repetir as fontes locais em falta.

O target escolhe o país antes da recolha, e o pack, o digest e o Telegram identificam explicitamente PT ou GB. Trends pode acrescentar os sinais advisory `EMERGENTE` e `DURÁVEL`, mas estes não mudam a fórmula do score. Não existe ranking automático entre países; apenas a matriz configurada é executada, e descoberta global nunca entra numa decisão local.

## Landing e Telegram

`landing_price.py` lê apenas `top_ads[].landing_url`, rejeita URLs de Ad Library e tenta no máximo três páginas: HTTP primeiro, Firecrawl depois, sempre best-effort. A moeda é derivada da fonte (`priceCurrency` ou símbolo/código explícito) e preços de moedas diferentes nunca são combinados num range. Quarta e mensal reutilizam o preço anterior.

O outbound é determinístico e HTML multi-mensagem por omissão (`TELEGRAM_RENDERER=html`, default de código e documentado em `.env.example`): `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos; `monthly` envia resumo seguido dos produtos afetados. `pipeline.notifications` constrói o batch (modelo, limites, placeholders, paridade e renderers HTML/Rich) e `pipeline.delivery.telegram.deliver_batch` entrega com estado v2 por item (`schema_version=2`): write-ahead antes de cada chamada, `message_id` só depois de `ok=true`, itens confirmados nunca reenviados e entrega ambígua fail-closed. O digest plain text integral é impresso no stdout e guardado como audit text.

Rich está implementado com tabelas `bordered striped compact` no resumo e nas métricas, mais botões URL externos para anúncio, landing, Trends, Shopping, Meta Ads Library e fornecedores; o fallback HTML preserva as ações como links normais. As ações vêm de URLs sanitizados ou pesquisas determinísticas, ficam no snapshot para replay e não introduzem callbacks, `reply_markup`, inbound nem novas chamadas a providers. O fallback Rich→HTML é baseado em evidência: um classificador estreito (4xx definitivo consistente + token simbólico exato da allowlist ou descrição completa normalizada exata) tenta no máximo uma vez, e um item que caiba nos 32768 caracteres Rich mas não nos 4096 do HTML falha fechado (`render_failed`) sem truncar. O diagnóstico da duplicação visual concluiu que não é um double-send: o estado retido prova dois itens Rich enviados uma única vez (`message_id` 17/18 consecutivos), o retry fez zero chamadas de rede, a inspeção offline encontrou um `sendRichMessage` por item, sem campo de texto irmão nem conteúdo duplicado, e as mesmas mensagens 17/18 aparecem uma única vez num segundo cliente Telegram — incompatibilidade de renderização específica do cliente, sem correção de produção justificada. `TELEGRAM_RENDERER=html` mantém-se e Rich fica desativado para a coorte de clientes atual. Continuam fora do âmbito callbacks, media, long polling, Mini App, fornecedores/margens automáticos e Legal Gate executável. Sem credenciais ou perante qualquer erro, o pipeline nunca cai e nunca regista o token. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.

## Projeção SQLite e explorer Datasette

`pipeline/projection/` + `explore.py` adicionam uma projeção SQLite descartável e idempotente dos packs classificados e um explorer Datasette read-only, sem acoplamento a `run.py` ou aos registries. Os packs (`data/*.json`) permanecem evidência canónica e nunca são reescritos; a projeção só importa packs completos classificados atuais com `run_id`, `product_id`, `market_id` e snapshot `market` coerentes. A chave é `(run_id, product_id, market_id)` + fingerprint SHA-256 do JSON canónico: reimport idêntico é no-op; identidade igual com conteúdo diferente levanta `PackConflictError` e deixa linhas intactas. Cada pack importa numa única transação com candidatos, foreign keys e `PRAGMA user_version=2`; schema incompatível falha fechado e deve ser reconstruído. A projeção copia valores já sanitizados verbatim, sem rescoring nem reclassificação.

```bash
.venv/bin/python explore.py import [--db data/ecom_research.db] [--data-dir data]
.venv/bin/python explore.py serve [--db data/ecom_research.db] [--host 127.0.0.1] [--port 8001]
```

`import` descobre `data/*.json` deterministicamente; packs legados e ficheiros não-pack são saltados read-only e reportados, JSON malformado e packs que reivindicam a schema atual com shape inválida falham fechado. `serve` valida e abre a base em `mode=ro`, depois lança Datasette com `--immutable`, queries read-only (últimos packs, needs_review, anunciantes matched) e localhost por omissão. `data/*.db`, `-wal` e `-shm` estão no `.gitignore`. Se não existirem packs atuais, uma projeção vazia com os skips reportados continua a ser sucesso (exit 0); packs com `matching_schema_version` atual são importados normalmente. O smoke de 2026-08-30 importou 16 packs/973 candidatos numa base temporária e respondeu a uma query HTTP real. Detalhes em `docs/sqlite-projection/IMPLEMENTATION_PLAN.md`.

## Estado atual

O código é CLI puro, sem chamadas LLM e sem alteração automática de decisões do utilizador. O output é a interface para consumo humano e eventual aprovação posterior pelo ad-machine. O outbound Telegram é HTML multi-mensagem determinístico por omissão, com estado de entrega v2 por item (ver "Landing e Telegram"). A suite completa fica verde em cada checkpoint (as integrações de rede são mockadas na suite; os números exatos de testes mudam com o contrato e não são referenciados aqui). O parser do Keyword Planner aceita UTF-16 LE com BOM, export tab-delimitado com preâmbulo de título/data e CSV UTF-8 legado, ligando a keyword à linha de produto exacta após normalização.

O smoke live de uma keyword e uma entrega de um digest Telegram foram concluídos com sucesso; o smoke autorizado do outbound multi-mensagem no chat de teste aprovou HTML (retry com zero chamadas de rede) e bloqueou Rich por duplicação visual — diagnosticada como específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram), sem reenvio pela aplicação. O host atual usa `Europe/Berlin`; o serviço cron está ativo e habilitado (`active`/`enabled`) e existe exactamente um job. Com `TZ=Europe/Lisbon`, o job das 09:00 do host corresponde às 08:00 de Lisboa e executa `--auto --digest`:

```cron
TZ=Europe/Lisbon
0 9 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --auto --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

O cron verifica a data diariamente, mas só atua ao domingo, à quarta-feira e no dia 1 do mês. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.

## Offer + Creative Arbitrage — slice implementado

O novo slice vive isolado em `pipeline/arbitrage/` e lê `config/offers.json` através de um loader fail-closed. O ficheiro está actualmente vazio, por isso a arquitectura e os guardrails estão implementados, mas ainda não há uma oferta real operacional.

O caminho sem rede é fixo: `load offers → resolve evidence → gates → Commercial Score + 48H Score → readiness decision → shortlist`. A shortlist tem os buckets `TEST_NOW`, `RESEARCH_FURTHER`, `WATCHLIST` e `REJECT`; a decisão expõe `REJECT`, `RESEARCH_ONLY`, `INSUFFICIENT_DATA`, `NEEDS_VERIFICATION`, `TESTABLE_48H` ou `WATCHLIST`.

O orçamento de 48 horas é absoluto em EUR: `<=€20 = 100`, `<=€50 = 75`, `<=€100 = 50`, `<=€250 = 25` e `>€250 = 0`. O threshold de `TESTABLE_48H` começa em 75, logo acima de €50 a oferta fica impedida de chegar a esse estado.

O comando de shortlist é `python run.py --offer-shortlist` e, com a configuração actual, imprime quatro secções vazias. Não existe chamada automática a OpenSEO/DataForSEO: a única entrada é `python run.py --validate-offer <offer_id> --openseo-project-id <project_id>`, depois de a decisão local ser `TESTABLE_48H / TEST_NOW`. Essa validação usa apenas `get_keyword_metrics` e `get_serp_results`, uma keyword/query, `includeClickstreamData=false`, sem backlinks, domain overview ou rank tracking.

O ledger de validação fica em `data/offer-validations/YYYY-MM-DD/`, retoma sem repetir ferramentas concluídas e não persiste credenciais. `OPENSEO_PROJECT_ID` e `OPENSEO_MCP_URL` estão no `.env.example`, com endpoint local default `http://127.0.0.1:3001/mcp`; a validação pode gerar custo DataForSEO. Em 2026-09-08 não foi executada nenhuma chamada OpenSEO/DataForSEO live.

Os próximos passos são preencher a primeira oferta com evidência real, correr a shortlist local e só depois validar via DataForSEO se o resultado for `TESTABLE_48H / TEST_NOW`.

O identity store canónico usa `config/products.json` para os cinco produtos e `config/markets.json` para PT/GB. O mesmo `product_id` atravessa mercados; a identidade operacional e histórica é `(product_id, market_id)`, enquanto `research_keyword` mantém a query local. A identidade de run inclui `(product_id, market_id, research_keyword)`, packs atuais persistem o snapshot de mercado e quarta/mensal nunca usam um pack de outro mercado como baseline. Packs e manifestos históricos permanecem read-only, mas packs sem scope não contam como evidência local. Token ou mercado desconhecido sai com exit 2 antes de qualquer chamada externa; registry ausente ou inválido falha fechado.
