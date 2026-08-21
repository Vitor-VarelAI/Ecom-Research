# WinningHunter → Ecom-Research

Pesquisa realizada em **20/08/2026** sobre o site público, a documentação oficial da API/MCP, a página pública de pricing e o estado real deste repositório.

Pesquisa complementar de APIs e repositórios reutilizáveis: [`APIS_AND_REPOS.md`](APIS_AND_REPOS.md).

## Veredito

O WinningHunter não é apenas um research pack melhor apresentado. É a combinação de três sistemas:

1. uma biblioteca pesquisável de anúncios, lojas, marcas, produtos e criativos;
2. um tracker histórico que liga anúncios, anunciantes, lojas, vendas/traffic estimados e tendências;
3. uma interface de exploração com filtros, presets e análises AI.

O Ecom-Research já tem um núcleo que o WinningHunter não substitui: scoring determinístico, proveniência, idempotência, coortes de anúncios, pesquisa incremental, outputs auditáveis, Telegram e uma direção mais séria para fornecedor, margem e Legal Gate. O caminho certo não é clonar a plataforma inteira. É transformar este pipeline num **cockpit privado de decisão** e usar uma fonte licenciada para os dados que exigem uma biblioteca massiva.

A prioridade é:

> **qualidade da evidência → base histórica consultável → dashboard → tracker de concorrentes → inteligência de criativos → expansão de fontes.**

TikTok Shop, geração de anúncios e uma biblioteca própria de milhões de lojas devem vir depois. Sem esta ordem, o projeto ganha uma interface parecida com o WinningHunter, mas continua a tomar decisões em cima de amostras pequenas e correspondências fracas.

> **Nota de custo:** só adicionar uma API paga quando um spike controlado provar que resolve uma lacuna concreta, melhora materialmente a decisão e compensa o custo. O core deve continuar funcional sem cada enriquecimento opcional.

## Âmbito e limitações da pesquisa

Foram auditados:

- [landing enviada pelo utilizador](https://app.winninghunter.com/i/henrikwold?dub_id=wWCqZnkDYogYeNAG);
- [site público do WinningHunter](https://winninghunter.com/);
- [documentação oficial da API](https://app.winninghunter.com/docs/api);
- [referência oficial das ferramentas MCP](https://app.winninghunter.com/docs/mcp-tools-reference);
- guias públicos de Meta, TikTok, Shopify, Trends, Brands e Magic AI;
- [roadmap/feedback público](https://winninghunter.featurebase.app/);
- código, packs, dados e documentação atuais do Ecom-Research.

Não foi criada uma conta nem auditado conteúdo privado dentro do dashboard autenticado. Não foram realizadas chamadas pagas à API do WinningHunter. As afirmações sobre escala, precisão, receita, tráfego e ad spend são claims do fornecedor ou métricas estimadas, salvo quando a fonte diz explicitamente que o dado vem de uma plataforma oficial.

## O que o WinningHunter realmente oferece

### 1. Descoberta e análise de anúncios

O produto oferece pesquisa de anúncios Meta, TikTok e Pinterest. Na Meta, a documentação expõe filtros por:

- keyword, anunciantes e texto excluído;
- países, língua, plataforma, CTA, nicho e formato;
- data de criação e janela em que o anúncio foi observado;
- dias ativo, reach, ad spend estimado e ad score;
- número de duplicados/adsets;
- Shopify/ecommerce-only;
- ordenação por newest, reach, ad spend, ad score, duração e duplicados.

O detalhe de um anúncio pode incluir creative, landing page, duração, reach, estimativas de spend, transcript e histórico de rank. O valor real não é a existência de uma lista de ads; é conseguir explorar a lista inteira, ordenar e voltar à evidência.

### 2. Brand tracker e inteligência competitiva

O WinningHunter trata a marca/anunciante como uma entidade durável. A API e os guias expõem:

- descoberta de marcas;
- perfil detalhado da marca;
- anúncios atuais da marca;
- produtos e lojas relacionados;
- comparação entre marcas;
- análise de estratégia, hooks, personas, desejos, emoções, USPs, landing pages e formatos;
- tracking de um número limitado de marcas, conforme o plano.

Isto permite responder a perguntas que o Ecom-Research ainda não responde diretamente:

- Que concorrentes estão a escalar?
- Que criativos sobrevivem mais tempo?
- Que páginas lançaram muitos duplicados nos últimos dias?
- Que novos produtos ou landings apareceram?
- Que ângulos se repetem entre os vencedores?

### 3. Shopify Store Explorer e Store Tracker

O explorer permite filtrar lojas por:

- vendas mensais e unidades mensais estimadas;
- número de produtos;
- país, língua, moeda e vertical;
- traffic estimado;
- tecnologia e apps;
- data de criação do domínio;
- ordenação por ranking, vendas, unidades, traffic e data.

O detalhe da loja inclui produtos, ads, métricas históricas e stores semelhantes. O site público diz que combina fontes oficiais, parceiros como Similarweb/Semrush e tecnologia própria para produzir estimativas. Portanto, receita, traffic e ad spend não devem ser armazenados como factos exatos; precisam de `source`, `method`, `confidence`, `observed_at` e uma indicação visual de **estimativa**.

### 4. Product discovery, Trends e TikTok Shop

O WinningHunter oferece:

- lista curada de produtos em tendência;
- produtos e categorias do TikTok Shop;
- lojas TikTok;
- creators, vídeos e colaborações afiliadas;
- métricas como vendas, revenue, crescimento, views, likes, engagement e comissões;
- pesquisa por keyword, região, categoria, janela temporal e ordenação.

Esta área é útil, mas depende de uma camada de dados própria ou licenciada. Não deve ser a primeira feature construída internamente.

### 5. Magic AI e reverse search

Magic AI aceita texto ou imagem e procura anúncios/produtos visual ou semanticamente semelhantes. O caso de uso principal é:

1. encontrar um anúncio ou imagem;
2. descobrir quem vende o mesmo produto;
3. medir saturação por país;
4. encontrar variações do criativo;
5. ligar o produto a lojas e anúncios.

O feedback público do próprio WinningHunter pede filtros de qualidade adicionais nesta pesquisa, incluindo rank, reach e longevidade. Isso confirma que similaridade sem filtros de performance produz resultados visualmente relacionados, mas pouco úteis para decisão.

### 6. API, MCP e workflows compostos

A API oficial oferece superfícies para Meta, TikTok, Pinterest, Trends, Brands e Shopify. O MCP acrescenta ferramentas de alto nível, incluindo:

- `scan_ad`;
- `analyze_creative`;
- `generate_creative`;
- `daily_radar`;
- `scan_brand`;
- `creative_inspiration_pack`;
- `competitor_landscape`;
- `compare_brands`;
- `find_winning_products`.

Estas ferramentas mostram um padrão importante para o Ecom-Research: o utilizador não quer apenas endpoints ou JSON. Quer uma resposta composta, por exemplo “dá-me hoje cinco produtos com procura crescente, concorrência controlada, criativos persistentes e lojas verificáveis”.

## O que o Ecom-Research já tem

| Capacidade atual | Evidência no repositório | Valor |
|---|---|---|
| Pesquisa Meta global | `pipeline/adapters/apify_ads.py` | Normalização, relevância, dedupe, longevidade, anunciantes e saturação |
| Procura e tendência | `pipeline/adapters/trends.py`, Keyword Planner CSV | Direção, sazonalidade, geos, queries, volume e CPC |
| SEO e landing | `pipeline/adapters/serp_seo.py`, `landing_price.py` | Competição orgânica/paga e preço observado |
| Decisão determinística | `pipeline/scoring/score.py` | Score auditável sem LLM |
| Histórico incremental | `run.py` | Domingo, quarta, mensal, idempotência e checkpoints |
| Persistência de mercado | `run.py::_ad_cohort` | Retenção/churn de ads e anunciantes, concentração e sinais |
| Produto canónico | `config/products.json`, `pipeline/products.py` | Identidade estável, aliases e watchlist |
| Proveniência | `pipeline/provenance.py`, `source_states/source_policies` | Fonte, freshness, fallback e política por evidência |
| Telegram | `pipeline/notifications/`, `pipeline/delivery/telegram.py` | Digest multi-mensagem e entrega idempotente |
| Fornecedor, margem e Legal Gate | `docs/telegram-research/05-*`, `06-*` | Direção de produto mais profunda do que um ad-spy normal |

O projeto é hoje um **motor de research agendado**. O WinningHunter é um **produto de exploração interativa**. A maior diferença não está inicialmente no score; está no volume de entidades persistidas, na capacidade de pesquisa e na ligação entre produto, ad, brand, store e creative.

## Lacunas observadas no estado atual

### A amostra útil desaparece depois da normalização

O adapter pode receber até 100 anúncios, mas o pack persiste apenas três `top_ads`, cinco copies, cinco advertisers e uma lista de IDs. O utilizador não consegue:

- filtrar todos os ads retidos;
- rever correspondências duvidosas;
- procurar por país/plataforma/CTA;
- comparar criativos;
- abrir o histórico de um anúncio;
- perceber por que motivo um anúncio entrou no score.

Antes de construir um dashboard, o pipeline deve persistir todos os anúncios normalizados que sustentam o resultado, com uma versão compacta e sanitizada.

### A correspondência por keyword ainda aceita falsos positivos

O pack real de `mochila personalizada` de 19/08/2026 inclui anúncios de mochila porta-bebé, um concentrador de oxigénio “com mochila” e apenas uma correspondência claramente personalizada. O score ficou `AVANÇAR` porque longevidade e baixa saturação pesaram sobre uma amostra semanticamente misturada.

Esta é a correção mais importante do projeto. Um UI bonito apenas torna o falso positivo mais convincente.

O matching precisa de distinguir:

- produto principal;
- acessório ou atributo incidental;
- categoria homónima;
- intenção B2B versus consumidor;
- produto personalizado versus texto que diz “atendimento personalizado”;
- idioma e mercado;
- confiança da correspondência.

### O advertiser ainda não é uma entidade

O projeto guarda agregados de advertisers dentro de cada pack, mas não possui um registo canónico de brand/store com:

- identidade e aliases;
- domínios e landings;
- histórico de ads;
- produtos associados;
- países e plataformas;
- primeira/última observação;
- eventos de crescimento, churn e concentração;
- decisão do operador.

Sem esta entidade, o Wednesday pulse mede a coorte do produto, mas não constrói inteligência competitiva acumulada.

### O projeto guarda copy, mas não inteligência criativa

O pack mostra texto bruto. Falta extrair e persistir, sempre com excerto de prova:

- hook;
- problema/desejo;
- persona;
- promessa;
- mecanismo;
- USP;
- oferta;
- CTA;
- emoção;
- objeção tratada;
- formato;
- estilo visual;
- transcript;
- claims de risco;
- fingerprint visual para agrupar duplicados.

O LLM pode ajudar nesta camada, mas nunca deve alterar automaticamente o score. Deve produzir análise advisory com `evidence_excerpt` e confiança.

### Falta uma interface de trabalho

Markdown e Telegram são bons outputs, mas não substituem:

- pesquisa;
- filtros;
- comparação lado a lado;
- histórico;
- watchlists;
- decisão e notas;
- revisão de falsos positivos;
- drill-down de produto → brand → ad → landing.

## Matriz de comparação e prioridade

| Capacidade | WinningHunter | Ecom-Research atual | Decisão |
|---|---|---|---|
| Dashboard pesquisável | Forte | Não existe | **Construir P0** |
| Todos os ads retidos | Biblioteca completa | Top 3 + IDs | **Construir P0** |
| Qualidade product↔ad | AI/reverse search + filtros | Heurística de keyword | **Corrigir P0** |
| Filtros e presets | Muito forte | Não existe | **Construir P0** |
| Histórico de products | Parcial via trends/shops | Packs por data | Consolidar P0 |
| Brand tracker | Forte | Agregados por pack | **Construir P1** |
| Creative intelligence | Forte | Copy bruto | **Construir P1** |
| Decisões auditáveis | Básico no spy-tool | Desenhado no projeto, inbound pendente | **Construir P1** |
| AI/MCP workflows | Forte | CLI apenas | **Construir P1** |
| Shopify explorer | Forte, dados estimados | SEO/landing apenas | Integrar P2 |
| Revenue/traffic estimates | Forte, mas estimado | Não existe | Integrar P2 com confiança |
| Similar stores | Existe | Não existe | Integrar P2 |
| Reverse image search | Existe | Não existe | Construir/integrar P2 |
| TikTok Ads | Existe | Não existe | Integrar P2 |
| Pinterest Ads | Existe | Não existe | Integrar P2/P3 |
| TikTok Shop | Forte | Não existe | Integrar P3 |
| AI creative generation | Existe | Não existe | Adiar P3 |
| Supplier/margin/legal | Não é o núcleo público | Investigado, ainda não executável | **Diferenciador P1/P2** |

## Produto recomendado: Ecom Research Cockpit

O dashboard deve ser construído em cima do motor atual, não em substituição dele.

### `/radar`

Mostra oportunidades novas e mudanças relevantes:

- score e confiança;
- procura e direção;
- ads novos/removidos;
- retenção;
- novos advertisers;
- aumento de variantes;
- landing ou preço alterado;
- risco de correspondência;
- estado fornecedor/legal;
- próximo passo permitido.

### `/products/:product_id`

Página canónica do produto:

- timeline de runs;
- score e breakdown;
- Trends/volume/SEO;
- anúncios associados;
- brands/stores;
- criativos e ângulos;
- mercados;
- fornecedor/margem/legal;
- decisão e histórico.

### `/ads`

Explorer com filtros:

- produto e confiança do match;
- advertiser;
- país e plataforma;
- dias ativo;
- primeira/última observação;
- novo, retido ou removido;
- número de variantes;
- tipo de criativo;
- hook, persona, desejo, USP e CTA;
- landing/domain;
- risco de claim;
- guardado/ignorado.

### `/brands`

Watchlist de concorrentes:

- ads ativos;
- novos ads em 7/30 dias;
- retenção;
- longevidade;
- burst de duplicados;
- produtos associados;
- países/plataformas;
- landings;
- eventos recentes;
- notas e estado.

### `/decisions`

Fila operacional:

- aprovar próxima fase;
- observar;
- descartar;
- corrigir associação;
- pedir pesquisa de fornecedor;
- pedir validação legal;
- guardar concorrente;
- registar motivo e evidência.

## Modelo de dados mínimo

Começar com SQLite. O volume atual é pequeno e a prioridade é provar o workflow; Postgres só é necessário quando existir ingestão ampla e concorrência real.

Entidades mínimas:

```text
products
brands
stores
ads
creative_assets
landing_snapshots
observations
product_ad_matches
creative_analyses
decisions
source_evidence
```

Regras:

- todo número é `observed`, `estimated` ou `derived`;
- estimativas guardam método, fornecedor e confiança;
- toda análise AI guarda evidência de origem;
- ads e assets têm identidade estável;
- cada observação é temporal e nunca sobrescreve silenciosamente o histórico;
- correções manuais de match ficam persistidas;
- score continua determinístico e separado da análise AI.

## Estratégia de dados

### Reutilizar o que já existe

- Apify/Meta para o pipeline base;
- SerpAPI e Keyword Planner;
- landing fetch;
- run IDs, source states, quotas e idempotência;
- product registry;
- Telegram outbound.

### Integrar em vez de reconstruir

Usar a API oficial do WinningHunter, ou outro fornecedor licenciado equivalente, para:

- Meta enriquecida;
- TikTok/Pinterest;
- Shopify stores/products;
- Trends curadas;
- Brands;
- TikTok Shop.

O adapter deve ficar opcional. O Ecom-Research continua funcional com as fontes atuais e enriquece quando existe API:

```text
pipeline/adapters/winninghunter.py
  search_meta_ads(...)
  get_brand(...)
  search_shopify_stores(...)
  get_trending_products(...)
  search_tiktok_ads(...)
```

Não fazer scraping do dashboard autenticado nem copiar código, assets ou datasets proprietários. Reproduzir o workflow e a capacidade com fontes licenciadas.

### Construir internamente

Vale a pena construir:

- identidade e histórico;
- filtros;
- matching produto↔ad;
- creative intelligence;
- decisões;
- supplier/margin/legal;
- dashboard;
- ferramentas MCP específicas do negócio.

Estes elementos são o produto do utilizador e não dependem de possuir uma biblioteca global.

## Roadmap recomendado

### P0 — tornar a evidência consultável e confiável

1. Persistir todos os ads normalizados, não apenas top 3.
2. Adicionar `match_status`, `match_confidence`, `match_reasons` e revisão manual.
3. Corrigir os falsos positivos mais comuns com fixtures reais.
4. Criar SQLite e importar o histórico dos packs.
5. Criar dashboard read-only com Radar, Products e Ads.
6. Adicionar filtros e presets guardados.

**Gate:** o utilizador consegue abrir qualquer score e ver toda a evidência que o produziu, corrigindo associações erradas sem editar JSON.

### P1 — tracker e inteligência

1. Criar Brand/Store canónicos.
2. Produzir eventos: novo advertiser, burst de ads, churn, landing nova, preço alterado.
3. Extrair hook, angle, desire, persona, USP, CTA, format e claims com evidência.
4. Criar watchlist e histórico de decisões.
5. Implementar callbacks/decisões Telegram ou a fila `/decisions`.
6. Expor ferramentas MCP próprias: `daily_radar`, `scan_product`, `scan_brand`, `creative_pack`.
7. Tornar fornecedor/margem/legal um workflow executável, não apenas documentação.

**Gate:** uma revisão semanal é feita dentro do cockpit e termina com decisões auditáveis.

### P2 — expansão licenciada

1. Spike da API do WinningHunter com orçamento de créditos definido.
2. Meta enriquecida, Shopify e Brands primeiro.
3. TikTok/Pinterest depois.
4. Revenue, traffic e ad spend sempre marcados como estimativas.
5. Reverse image/visual clustering.
6. Alertas de concorrentes e stores.

**Gate:** a fonte licenciada melhora materialmente cobertura/tempo ou é removida; o core não fica preso ao fornecedor.

### P3 — escala e geração

1. TikTok Shop.
2. Creator/affiliate intelligence.
3. AI creative generation a partir de insights aprovados.
4. Importação para execução/campanhas, apenas se o projeto realmente passar de research para operação.

**Gate:** geração só entra depois de existir um histórico suficiente para provar quais hooks, formatos e ofertas funcionam para os produtos observados.

## Primeiro slice que eu implementaria

**Research Cockpit v0: Ads Evidence Explorer**

Escopo:

- SQLite;
- ingestão dos JSON atuais;
- persistência compacta de todos os ads normalizados futuros;
- páginas `Radar`, `Products`, `Ads`;
- filtros por produto, advertiser, dias ativo, estado da coorte e confiança;
- detalhe do ad com copy, landing, plataformas, países, observações e motivo do match;
- ação manual `confirmar`, `rejeitar` ou `reclassificar`;
- score atual apenas para leitura;
- sem TikTok, Shopify, AI generation, fornecedores automáticos ou alteração do scoring.

É o slice certo porque resolve simultaneamente a maior fraqueza observada — falsos positivos — e cria a fundação necessária para todas as features parecidas com WinningHunter.

## O que não construir agora

- uma biblioteca própria de milhões de ads/stores;
- revenue ou ad spend apresentados como factos;
- TikTok Shop antes do cockpit;
- geração de criativos antes da inteligência de criativos;
- um frontend pixel-perfect copiado do WinningHunter;
- autenticação, billing e equipas;
- ranking automático de país;
- decisão automática de lançar produto;
- scraping do dashboard autenticado;
- abstrações multi-provider antes de existir um segundo provider real.

## Estimativa relativa

| Bloco | Com API licenciada | Sem API licenciada |
|---|---:|---:|
| P0 Cockpit e qualidade | 1–2 semanas | 1–2 semanas |
| P1 Tracker/intelligence | 2–3 semanas | 2–4 semanas |
| P2 Meta/Shopify/TikTok/Pinterest | 2–4 semanas | 3–6+ meses |
| P3 TikTok Shop/generation | 2–4 semanas | vários meses adicionais |

Reconstruir o produto de dados do WinningHunter é uma empresa separada. Construir o cockpit e o motor de decisão privado é um projeto realista e aproveita o investimento já feito neste repositório.

## Fontes oficiais consultadas

- [WinningHunter — homepage](https://winninghunter.com/)
- [WinningHunter — landing Henrik Wold](https://app.winninghunter.com/i/henrikwold?dub_id=wWCqZnkDYogYeNAG)
- [WinningHunter — API Documentation](https://app.winninghunter.com/docs/api)
- [WinningHunter — MCP Tools Reference](https://app.winninghunter.com/docs/mcp-tools-reference)
- [WinningHunter — Meta Ad Library](https://app.winninghunter.com/docs/meta-ad-library)
- [WinningHunter — TikTok Ad Library](https://app.winninghunter.com/docs/tiktok-ad-library)
- [WinningHunter — TikTok Shop](https://app.winninghunter.com/docs/tiktok-shop)
- [WinningHunter — Shopify Store Explorer](https://app.winninghunter.com/docs/shopify-store-explorer)
- [WinningHunter — Brands & Brand Tracker](https://app.winninghunter.com/docs/brands)
- [WinningHunter — Trends](https://app.winninghunter.com/docs/trends)
- [WinningHunter — Magic AI](https://app.winninghunter.com/docs/magic-ai)
- [WinningHunter — public feedback/roadmap](https://winninghunter.featurebase.app/)
