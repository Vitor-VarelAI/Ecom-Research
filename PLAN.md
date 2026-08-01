# ECOM-RESEARCH — plano operacional

## Objetivo

Manter um pipeline CLI reproduzível em que anúncios ativos existentes provam procura. O utilizador decide produto, mercado, margem e campanha; score, sugestões e recomendações são advisory.

## Fluxo Full

1. Fazer uma chamada ao actor Apify `curious_coder~facebook-ads-library-scraper`, com uma URL global da Facebook Ad Library por keyword e `count=100`.
2. Normalizar localmente, retirar anúncios inativos/irrelevantes com accent-folding e deduplicar por ID, URL ou página+copy+data.
3. Se houver anúncios, executar Trends, SEO, CSV e preços de landing. Se não houver, forçar `DESCARTAR`, saltar SEO/landing e fazer apenas a call Related Queries cacheada para sugestões.
4. Se a fonte Apify falhar, continuar Trends/SEO/CSV, marcar `DADOS INSUFICIENTES` e não produzir uma decisão negativa.
5. Persistir Markdown e JSON, incluindo `facebook_ads` e `suggested_keywords`.

## Contrato Apify

A requisição usa POST para o endpoint `run-sync-get-dataset-items`, `Authorization: Bearer APIFY_API_TOKEN`, `Content-Type: application/json`, query `clean=true&format=json`, timeout `(10,135)` e nenhum retry. O payload contém uma URL global com `active_status=active`, `ad_type=all`, `country=ALL`, `is_targeted_country=false`, `media_type=all`, `q` URL-encoded, `search_type=keyword_unordered`, e `count=100`.

O resultado tem `source`, `query`, `sample_limit`, `sample_capped`, `raw_ads_count`, `filtered_out_count`, `duplicates_removed`, `total_ads`, `ad_ids`, `top_ads`, `advertisers`, `market_proven`, `copy_examples` e `saturation`. Cada top ad tem `ad_archive_id`, advertiser, page ID, longevidade, link da Ad Library, `landing_url`, copy, plataformas e países.

## Cadência e limites

| Ação | Comportamento |
|---|---|
| Full/domingo | Um Apify por keyword; Trends/SEO/landing só conforme o resultado; Related Queries cacheada quando zero ads |
| Quarta | Uma chamada paga e bounded de Apify por keyword elegível; zero SerpAPI, Firecrawl ou landing; reutiliza todos os outros campos; compara IDs e advertisers |
| Mensal | Uma timeseries Trends forçada por keyword OBSERVAR; zero Apify, SEO, landing ou Firecrawl; rescore com os dados reutilizados |
| Auto no dia 1 | Mensal primeiro; depois domingo ou quarta; cada ação conserva o próprio digest |

Cache de Trends, SEO e Related Queries dura 30 dias. A quota SerpAPI mantém hard stop local em 230 chamadas. O limite Apify por execução é uma invocação por keyword e o sample cap é 100.

## Sugestões

Related Queries passa por `extract_keyword_suggestions`: valores numéricos e `Breakout` são normalizados, Breakout vem primeiro, números ordenam descendentemente, e removem-se vazios, origem, duplicados, URLs e interrogativas. Cada entrada persistida tem `status: proposed`. Nunca se escreve em `config/keywords.txt` nem se executa uma sugestão automaticamente; o digest agrega no máximo três por fonte.

## Decisão e mercados

O score mantém os pesos `trend 0.3 + volume 0.2 + ad_longevity 0.3 + saturação_invertida 0.2`. A recomendação é:

- `AVANÇAR`: preparar ad-machine depois de aprovação manual.
- `OBSERVAR`: manter watchlist e voltar a verificar.
- `DESCARTAR`: arquivar salvo nova evidência.
- `DADOS INSUFICIENTES`: repetir quando Apify estiver disponível.

O pack não escolhe país. **Sinais por mercado — decisão manual** lista PT, ES, FR e os top geos de Trends nas colunas `country`, `Trends demand`, `advertisers observed` e `ads observed`. O digest usa `Mercado: decisão manual`.

## Landing e Telegram

`landing_price.py` lê apenas `top_ads[].landing_url`, rejeita URLs de Ad Library e tenta no máximo três páginas: HTTP primeiro, Firecrawl depois, sempre best-effort. Quarta e mensal reutilizam o preço anterior.

`pipeline.delivery.telegram.send` envia plain text em chunks de no máximo 4000 caracteres, sem parse mode, botões, callbacks ou webhook. Sem credenciais ou perante qualquer erro, imprime o digest completo e devolve `False`; o pipeline nunca cai e nunca regista o token.

## Estado atual

O código é CLI puro, sem chamadas LLM, sem cron instalado e sem alteração automática de decisões do utilizador. O output é a interface para consumo humano e eventual aprovação posterior pelo ad-machine. O baseline validado em 2026-08-01 tem 124 testes verdes; as integrações de rede são mockadas na suite.

## Gate de ativação

A ativação live exige, por ordem:

1. Configurar e testar a entrega send-only com `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
2. Validar um CSV real do Google Keyword Planner.
3. Fazer um único full run live e rever manualmente relevância/dedupe do Apify, custo, landing, score e sugestões.
4. Regenerar antes de produção credenciais que tenham sido expostas numa conversa.
5. Instalar o cron apenas depois desse smoke passar.

Até ao gate passar, não declarar o pipeline em produção e não executar batches pagos.
