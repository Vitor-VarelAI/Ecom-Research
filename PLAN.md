# ECOM-RESEARCH — plano operacional

## Objetivo

Manter um pipeline CLI reproduzível em que anúncios ativos globais fornecem evidência de procura. O utilizador decide produto, mercado, margem e campanha; score, sugestões e recomendações são advisory.

## Fluxo Full

1. Fazer uma chamada ao actor Apify `curious_coder~facebook-ads-library-scraper`, com uma URL global da Facebook Ad Library por keyword e `count=100`.
2. Normalizar localmente, retirar anúncios inativos/irrelevantes com accent-folding e relevância fail-closed do produto, e deduplicar por ID, URL ou página+copy+data.
3. Se houver anúncios, executar Trends, SEO, CSV e preços de landing. Se não houver, forçar `DESCARTAR`, saltar SEO/landing e fazer apenas a call Related Queries cacheada para sugestões.
4. Se a fonte Apify falhar, continuar Trends/SEO/CSV, marcar `DADOS INSUFICIENTES` e não produzir uma decisão negativa.
5. Persistir Markdown e JSON, incluindo `facebook_ads` e `suggested_keywords`.

## Contrato Apify

A evidência da Ad Library é global (`country=ALL`) e não escolhe mercado. A requisição usa POST para o endpoint `run-sync-get-dataset-items`, `Authorization: Bearer APIFY_API_TOKEN`, `Content-Type: application/json`, query `clean=true&format=json`, timeout `(10,135)` e nenhum retry. O payload contém uma URL global com `active_status=active`, `ad_type=all`, `country=ALL`, `is_targeted_country=false`, `media_type=all`, `q` URL-encoded, `search_type=keyword_unordered`, e `count=100`.

O resultado tem `source`, `query`, `sample_limit`, `sample_capped`, `raw_ads_count`, `filtered_out_count`, `duplicates_removed`, `total_ads`, `ad_ids`, `top_ads`, `advertisers`, `global_market_proven`, `copy_examples` e `saturation`. Como a query usa `country=ALL`, `global_market_proven` descreve apenas prova global; nunca é prova de mercado local. Cada top ad tem `ad_archive_id`, advertiser, page ID, longevidade, link da Ad Library, `landing_url`, copy, plataformas e países.

## Cadência e limites

| Ação | Comportamento |
|---|---|
| Full/domingo | Um Apify por keyword; Trends/SEO/landing só conforme o resultado; Related Queries cacheada quando zero ads |
| Quarta | Uma chamada paga e bounded de Apify por keyword elegível; zero SerpAPI, Firecrawl ou landing; reutiliza todos os outros campos; compara IDs e advertisers |
| Mensal | Uma timeseries Trends forçada por keyword OBSERVAR; zero Apify, SEO, landing ou Firecrawl; rescore com os dados reutilizados |
| Auto no dia 1 | Mensal primeiro; depois domingo ou quarta; cada ação conserva o próprio digest |

Cache de Trends, SEO e Related Queries dura 30 dias; a timeseries do Trends usa timeout de 60s e a política de proveniência expõe o mesmo valor. A quota SerpAPI mantém hard stop local em 230 chamadas. O limite Apify por execução é uma invocação por keyword e o sample cap é 100.

## Sugestões

Related Queries passa primeiro por relevância fail-closed do produto e depois por `extract_keyword_suggestions`: valores numéricos e `Breakout` são normalizados, Breakout vem primeiro, números ordenam descendentemente, e removem-se vazios, origem, duplicados, URLs e interrogativas. Cada entrada persistida tem `status: proposed`. Nunca se escreve em `config/keywords.txt` nem se executa uma sugestão automaticamente; o digest agrega no máximo três por fonte.

## Decisão e mercados

O score mantém os pesos `trend 0.3 + volume 0.2 + ad_longevity 0.3 + saturação_invertida 0.2`. A recomendação é:

- `AVANÇAR`: preparar ad-machine depois de aprovação manual.
- `OBSERVAR`: manter watchlist e voltar a verificar.
- `DESCARTAR`: arquivar salvo nova evidência.
- `DADOS INSUFICIENTES`: repetir quando Apify estiver disponível.

O pack não escolhe país. A evidência da Ad Library é global (`country=ALL`), e `global_market_proven` descreve apenas essa prova global. **Sinais por mercado — decisão manual** lista PT, ES, FR e os top geos de Trends nas colunas `country`, `Trends demand`, `advertisers observed (top-ad sample)` e `ads observed (top-ad sample)`; essas contagens vêm apenas de `top_ads`, não da coorte global retida, e `—` significa desconhecido. A selecção de mercado continua manual, sem ranking automático de país, e o digest usa `Mercado: decisão manual` e `Prova global`.

## Landing e Telegram

`landing_price.py` lê apenas `top_ads[].landing_url`, rejeita URLs de Ad Library e tenta no máximo três páginas: HTTP primeiro, Firecrawl depois, sempre best-effort. A moeda é derivada da fonte (`priceCurrency` ou símbolo/código explícito) e preços de moedas diferentes nunca são combinados num range. Quarta e mensal reutilizam o preço anterior.

O outbound é determinístico e HTML multi-mensagem por omissão (`TELEGRAM_RENDERER=html`, default de código e documentado em `.env.example`): `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos; `monthly` envia resumo seguido dos produtos afetados. `pipeline.notifications` constrói o batch (modelo, limites, placeholders, paridade e renderers HTML/Rich) e `pipeline.delivery.telegram.deliver_batch` entrega com estado v2 por item (`schema_version=2`): write-ahead antes de cada chamada, `message_id` só depois de `ok=true`, itens confirmados nunca reenviados e entrega ambígua fail-closed. O digest plain text integral é impresso no stdout e guardado como audit text. Rich está implementado mas desativado — a API aceitou as mensagens no smoke e o retry fez zero chamadas de rede, mas cada item apareceu duplicado no chat de teste. Não existem callbacks, botões, media, long polling, Mini App, fornecedores/margens automáticos nem Legal Gate executável. Sem credenciais ou perante qualquer erro, o pipeline nunca cai e nunca regista o token. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.

## Estado atual

O código é CLI puro, sem chamadas LLM e sem alteração automática de decisões do utilizador. O output é a interface para consumo humano e eventual aprovação posterior pelo ad-machine. O outbound Telegram é HTML multi-mensagem determinístico por omissão, com estado de entrega v2 por item (ver "Landing e Telegram"). O baseline validado tem **680 testes verdes**; as integrações de rede são mockadas na suite. O parser do Keyword Planner aceita UTF-16 LE com BOM, export tab-delimitado com preâmbulo de título/data e CSV UTF-8 legado, ligando a keyword à linha de produto exacta após normalização.

O smoke live de uma keyword e uma entrega de um digest Telegram foram concluídos com sucesso; o smoke autorizado do outbound multi-mensagem no chat de teste aprovou HTML (retry com zero chamadas de rede) e bloqueou Rich por duplicação visual. O host atual usa `Europe/Berlin`; o serviço cron está ativo e habilitado (`active`/`enabled`) e existe exactamente um job. Com `TZ=Europe/Lisbon`, o job das 09:00 do host corresponde às 08:00 de Lisboa e executa `--auto --digest`:

```cron
TZ=Europe/Lisbon
0 9 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --auto --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

O cron verifica a data diariamente, mas só atua ao domingo, à quarta-feira e no dia 1 do mês. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.
