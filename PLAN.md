# ECOM-RESEARCH — plano operacional

## Objetivo

Manter um pipeline CLI reproduzível em que anúncios ativos globais fornecem evidência de procura. O utilizador decide produto, mercado, margem e campanha; score, sugestões e recomendações são advisory.

## Fluxo Full

1. Fazer uma chamada ao actor Apify `curious_coder~facebook-ads-library-scraper`, com uma URL global da Facebook Ad Library por keyword e `count=100`.
2. Normalizar localmente, retirar anúncios inativos e gate-1-irrelevantes (accent-folding, token product-head da keyword — primeiro token não stopword/não geográfico, equivalência exacta ou plural de um só `s` — em qualquer parte do copy/texto do anunciante) e deduplicar por ID, URL ou página+copy+data. Cada anúncio ativo, gate-1-relevante e deduplicado torna-se um candidato canónico (máx. 100).
3. Classificar cada candidato com `pipeline/matching/product_ads.py` (`matched`/`needs_review`/`rejected`). Se houver `matched`, executar Trends, SEO, CSV e preços de landing sobre os top ads matched. Se não houver candidatos (ou zero matched com todos rejeitados), forçar `DESCARTAR`, saltar SEO/landing e fazer apenas a call Related Queries cacheada para sugestões; zero matched com qualquer `needs_review` produz `DADOS INSUFICIENTES`.
4. Se a fonte Apify falhar, continuar Trends/SEO/CSV, marcar `DADOS INSUFICIENTES` e não produzir uma decisão negativa.
5. Persistir Markdown e JSON, incluindo `facebook_ads` (candidatos classificados + contagens + `matcher_version` + `matching_schema_version`) e `suggested_keywords`.

## Contrato Apify

A evidência da Ad Library é global (`country=ALL`) e não escolhe mercado. A requisição usa POST para o endpoint `run-sync-get-dataset-items`, `Authorization: Bearer APIFY_API_TOKEN`, `Content-Type: application/json`, query `clean=true&format=json`, timeout `(10,135)` e nenhum retry. O payload contém uma URL global com `active_status=active`, `ad_type=all`, `country=ALL`, `is_targeted_country=false`, `media_type=all`, `q` URL-encoded, `search_type=keyword_unordered`, e `count=100`.

O resultado do adapter tem `source`, `query`, `sample_limit`, `sample_capped`, `raw_ads_count`, `filtered_out_count`, `filtered_inactive_count`, `filtered_irrelevant_count`, `candidate_count`, `duplicates_removed`, `relevance_policy`, `matching_schema_version` e `candidates` (até 100 candidatos canónicos ativos, gate-1-relevantes e deduplicados, cada um com `match_evidence` folded commercial/geo/weak). O adapter não decide fit de produto: `pipeline/matching/product_ads.py` classifica cada candidato (`matched` / `needs_review` / `rejected`) com confiança categórica, reason codes ordenados e `matcher_version`, e deriva `total_ads` (contagem matched), `matched_count`/`needs_review_count`/`rejected_count`, `ad_ids`, `top_ads`, `advertisers`, `global_market_proven`, `copy_examples` e `saturation` **apenas dos candidatos matched**. Como a query usa `country=ALL`, `global_market_proven` descreve apenas prova global; nunca é prova de mercado local. Cada candidato/top ad tem `ad_archive_id`, advertiser, page ID, longevidade, link da Ad Library, `landing_url` (sanitizada na persistência), copy, plataformas, países e `match_evidence`. A classificação exige o product-head e todos os tokens significativos (incluindo modificadores como `personalizada`/`aromáticas`) em campos comerciais fortes e os qualificadores geográficos em campos geo-proof estritos; prova parcial é `needs_review`, ausência do head é `rejected`.

## Cadência e limites

| Ação | Comportamento |
|---|---|
| Full/domingo | Um Apify por keyword; Trends/SEO/landing só conforme o resultado; Related Queries cacheada quando zero candidatos ou zero matched (todos rejeitados) |
| Quarta | Uma chamada paga e bounded de Apify por keyword elegível; zero SerpAPI, Firecrawl ou landing; reutiliza todos os outros campos; compara IDs e advertisers (matched-only); saltada fail-closed se o pack anterior não tiver `matching_schema_version` |
| Mensal | Uma timeseries Trends forçada por keyword OBSERVAR; zero Apify, SEO, landing ou Firecrawl; rescore com os dados reutilizados; saltado fail-closed se o pack anterior não tiver `matching_schema_version` |
| Auto no dia 1 | Mensal primeiro; depois domingo ou quarta; cada ação conserva o próprio digest |

Cache de Trends, SEO e Related Queries dura 30 dias; a timeseries do Trends usa timeout de 60s e a política de proveniência expõe o mesmo valor. A quota SerpAPI mantém hard stop local em 230 chamadas. O limite Apify por execução é uma invocação por keyword e o sample cap é 100.

## Sugestões

Related Queries passa primeiro por relevância fail-closed do produto e depois por `extract_keyword_suggestions`: valores numéricos e `Breakout` são normalizados, Breakout vem primeiro, números ordenam descendentemente, e removem-se vazios, origem, duplicados, URLs e interrogativas. Cada entrada persistida tem `status: proposed`. Nunca se escreve em `config/keywords.txt` nem se executa uma sugestão automaticamente; o digest agrega no máximo três por fonte. O ficheiro permanece intacto e já não é lido em runtime — a fonte de produtos é o registo canónico em `config/products.json`.

## Decisão e mercados

O score mantém os pesos `trend 0.3 + volume 0.2 + ad_longevity 0.3 + saturação_invertida 0.2`. A recomendação é:

- `AVANÇAR`: preparar ad-machine depois de aprovação manual.
- `OBSERVAR`: manter watchlist e voltar a verificar.
- `DESCARTAR`: arquivar salvo nova evidência.
- `DADOS INSUFICIENTES`: repetir quando Apify estiver disponível.

O pack não escolhe país. A evidência da Ad Library é global (`country=ALL`), e `global_market_proven` descreve apenas essa prova global. **Sinais por mercado — decisão manual** lista PT, ES, FR e os top geos de Trends nas colunas `country`, `Trends demand`, `advertisers observed (top-ad sample)` e `ads observed (top-ad sample)`; essas contagens vêm apenas de `top_ads`, não da coorte global retida, e `—` significa desconhecido. A selecção de mercado continua manual, sem ranking automático de país, e o digest usa `Mercado: decisão manual` e `Prova global`.

## Landing e Telegram

`landing_price.py` lê apenas `top_ads[].landing_url`, rejeita URLs de Ad Library e tenta no máximo três páginas: HTTP primeiro, Firecrawl depois, sempre best-effort. A moeda é derivada da fonte (`priceCurrency` ou símbolo/código explícito) e preços de moedas diferentes nunca são combinados num range. Quarta e mensal reutilizam o preço anterior.

Cada alvo de landing é validado contra SSRF antes de qualquer request (`pipeline/adapters/ssrf_guard.py`): apenas http(s) sem userinfo, com host/porta válidos e endereços públicos unicast (`is_global`, excluindo redes privadas, loopback, link-local, reservadas, multicast e unspecified), rejeitando DNS falhado/vazio e respostas mistas público+privado. Redirects são seguidos manualmente (301/302/303/307/308, máx. 5, com deteção de loops e Location ausente/inválida); uma cadeia bloqueada ou inválida suprime o fallback Firecrawl, enquanto falha de rede ou resposta final segura sem preço continuam a cair no Firecrawl. O Firecrawl revalida o alvo antes de gastar credit. URLs com userinfo são rejeitadas em vez de limpas; `url_sanitize.py` mantém-se privacy-only. Limitações residuais aceites: DNS-rebinding TOCTOU e os redirects server-side do próprio Firecrawl.

O outbound é determinístico e HTML multi-mensagem por omissão (`TELEGRAM_RENDERER=html`, default de código e documentado em `.env.example`): `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos; `monthly` envia resumo seguido dos produtos afetados. `pipeline.notifications` constrói o batch (modelo, limites, placeholders, paridade e renderers HTML/Rich) e `pipeline.delivery.telegram.deliver_batch` entrega com estado v2 por item (`schema_version=2`): write-ahead antes de cada chamada, `message_id` só depois de `ok=true`, itens confirmados nunca reenviados e entrega ambígua fail-closed. O digest plain text integral é impresso no stdout e guardado como audit text. Rich está implementado mas desativado. O fallback Rich→HTML é baseado em evidência: um classificador estreito (4xx definitivo consistente + token simbólico exato da allowlist ou descrição completa normalizada exata) tenta no máximo uma vez, e um item que caiba nos 32768 caracteres Rich mas não nos 4096 do HTML falha fechado (`render_failed`) sem truncar. O diagnóstico da duplicação visual concluiu que não é um double-send: o estado retido prova dois itens Rich enviados uma única vez (`message_id` 17/18 consecutivos), o retry fez zero chamadas de rede, a inspeção offline encontrou um `sendRichMessage` por item, sem campo de texto irmão nem conteúdo duplicado, e as mesmas mensagens 17/18 aparecem uma única vez num segundo cliente Telegram — incompatibilidade de renderização específica do cliente, sem correção de produção justificada. `TELEGRAM_RENDERER=html` mantém-se e Rich fica desativado para a coorte de clientes atual. Não existem callbacks, botões, media, long polling, Mini App, fornecedores/margens automáticos nem Legal Gate executável. Sem credenciais ou perante qualquer erro, o pipeline nunca cai e nunca regista o token. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.

## Estado atual

O código é CLI puro, sem chamadas LLM e sem alteração automática de decisões do utilizador. O output é a interface para consumo humano e eventual aprovação posterior pelo ad-machine. O outbound Telegram é HTML multi-mensagem determinístico por omissão, com estado de entrega v2 por item (ver "Landing e Telegram"). A suite completa fica verde em cada checkpoint (as integrações de rede são mockadas na suite; os números exatos de testes mudam com o contrato e não são referenciados aqui). O parser do Keyword Planner aceita UTF-16 LE com BOM, export tab-delimitado com preâmbulo de título/data e CSV UTF-8 legado, ligando a keyword à linha de produto exacta após normalização.

O smoke live de uma keyword e uma entrega de um digest Telegram foram concluídos com sucesso; o smoke autorizado do outbound multi-mensagem no chat de teste aprovou HTML (retry com zero chamadas de rede) e bloqueou Rich por duplicação visual — diagnosticada como específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram), sem reenvio pela aplicação. O host atual usa `Europe/Berlin`; o serviço cron está ativo e habilitado (`active`/`enabled`) e existe exactamente um job. Com `TZ=Europe/Lisbon`, o job das 09:00 do host corresponde às 08:00 de Lisboa e executa `--auto --digest`:

```cron
TZ=Europe/Lisbon
0 9 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --auto --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

O cron verifica a data diariamente, mas só atua ao domingo, à quarta-feira e no dia 1 do mês. O token Telegram exposto deve ainda ser rotacionado pelo utilizador.

O identity store canónico de produtos está completo: `config/products.json` (`schema_version=1`) é o registo operado manualmente, com seis produtos (cinco ativos; `copper bottles` inativo após o smoke live) e `product_id` imutável (preservado do antigo hash de 12 hex da keyword); `config/keywords.txt` permanece intacto e já não é lido em runtime. Um `research_keyword` ativo por produto; aliases guardam keywords históricas (camisola portugal consolida camisolas portugal, camisola de portugal e camisola portugal preta). A identidade de run usa pares ordenados `(product_id, research_keyword)`: mudar a query ativa cria um run novo, enquanto o `product_id` estável preserva o histórico. Packs/manifestos novos carregam `product_id`/`product_name`/`research_keyword`; packs, manifestos e digests históricos são read-only e nunca reescritos; a migração apenas lê artefactos legados. Quarta/mensal selecionam por `product_id` com fallback read-only keyword-legada→alias, mantendo a watchlist e o baseline do pack anterior num produto renomeado. Token posicional desconhecido sai com exit 2 antes de qualquer chamada de fonte/rede; registo ausente ou inválido falha fechado. A idempotência de entrega e a segurança contra duplicação não mudaram.
