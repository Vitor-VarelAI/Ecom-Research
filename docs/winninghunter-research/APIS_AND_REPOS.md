# APIs e repositórios reutilizáveis

Pesquisa realizada em **20/08/2026**, cruzando o estado real do Ecom-Research com documentação oficial e repositórios públicos.

## Veredito

Não encontrei um clone open source maduro do WinningHunter que já traga, em conjunto, biblioteca de anúncios, Shopify/TikTok data, tracking histórico, scoring e um dashboard pronto para produção. Os repositórios específicos de ad tracking encontrados são pequenos ou incompletos, portanto não devem tornar-se a fundação do projeto.

O dashboard, porém, não precisa de começar do zero:

1. persistir os dados do pipeline numa base SQLite;
2. usar **Datasette** para lançar imediatamente o explorer read-only;
3. acrescentar **SQLPage** quando forem necessárias páginas próprias, gráficos e formulários de revisão;
4. só passar para **React-admin** ou **Refine** se o cockpit provar valor e precisar de uma interface totalmente personalizada.

Isto aproveita o backend Python atual, evita criar já uma aplicação React e mantém aberta a evolução para um produto mais polido.

> **Nota de custo:** só adicionar uma API paga quando um spike controlado provar que resolve uma lacuna concreta, melhora materialmente a decisão e compensa o custo. O projeto deve continuar funcional sem cada enriquecimento opcional.

## Dashboard: o que reutilizar

| Repositório | O que já entrega | Encaixe no Ecom-Research | Decisão |
|---|---|---|---|
| [simonw/datasette](https://github.com/simonw/datasette) | Explorer e API automática sobre SQLite, pesquisa, filtros, ordenação e detalhe de registos | Excelente para `Ads`, `Products`, `Brands` e observações históricas em modo read-only | **Usar no primeiro slice** |
| [sqlpage/SQLPage](https://github.com/sqlpage/SQLPage) | UI construída com SQL, tabelas, cards, charts, forms, CRUD e suporte nativo a SQLite | Permite criar `Radar`, revisão de matches e decisões sem escrever frontend React | **Usar quando entrarem ações manuais** |
| [marmelab/react-admin](https://github.com/marmelab/react-admin) | Datagrid, pesquisa, filtros, saved queries, relações, auth e formulários sobre REST/GraphQL | Bom para um cockpit polido, mas exige criar API e introduzir TypeScript/React | **P1, não P0** |
| [refinedev/refine](https://github.com/refinedev/refine) | Framework headless para dashboards/CRUD com auth, routing e vários UI kits | Mais liberdade visual do que React-admin, com o mesmo custo de uma segunda stack | **Alternativa P1** |
| [appsmithorg/appsmith](https://github.com/appsmithorg/appsmith) | Builder visual de dashboards e internal tools ligado a APIs e bases de dados | Constrói rápido, mas adiciona uma plataforma self-hosted grande para um projeto ainda pequeno | **Não usar agora** |
| [coleifer/sqlite-web](https://github.com/coleifer/sqlite-web) | Browser web simples para SQLite | Útil para debug administrativo, mas não é um cockpit de produto | **Ferramenta de desenvolvimento** |
| [ste-fen/AdTracker](https://github.com/ste-fen/AdTracker) | Python + Streamlit para consultar Meta, TikTok e Google, exportar JSON e gerar screenshots | Tem ideias reutilizáveis, mas usa Google Sheets, tem pouca adoção pública e não possui o modelo histórico do Ecom-Research | **Consultar, não adotar inteiro** |

### Escolha recomendada

O primeiro dashboard deve ser **SQLite + Datasette**. Ele resolve rapidamente a consulta, filtros, drill-down e API sem criar uma aplicação web nova. Quando for necessário confirmar/rejeitar matches, guardar notas ou executar decisões, adicionar SQLPage sobre a mesma base é mais simples do que abandonar tudo por React.

Datasette e SQLPage podem coexistir:

```text
pipeline Python
    -> data/ecom_research.db
        -> Datasette: exploração read-only e API
        -> SQLPage: Radar, formulários e decisões
```

## Repositórios específicos de anúncios

| Repositório | Capacidade | Risco | Decisão |
|---|---|---|---|
| [soxoj/AdsLibrary](https://github.com/soxoj/AdsLibrary) | Interface Python normalizada para Meta, TikTok, Google, LinkedIn, Snapchat e X, além de MCP | É um fork pequeno e o repositório não declara licença; vários adapters dependem de endpoints não oficiais | **Usar apenas como referência técnica até existir licença e smoke próprio** |
| [facebookresearch/Ad-Library-API-Script-Repository](https://github.com/facebookresearch/Ad-Library-API-Script-Repository) | Scripts da Meta para consultar a Ad Library API | Exemplos estreitos e atividade de código limitada; não é dashboard nem SDK completo | **Reutilizar padrões de consulta oficiais** |
| [ste-fen/AdTracker](https://github.com/ste-fen/AdTracker) | Meta/TikTok/Google, Streamlit, screenshots e Cloud Run | Produto muito diferente: Sheets como estado, pouca maturidade pública e sem scoring/proveniência equivalente | **Possível spike isolado, não merge integral** |

Não encontrei um repositório público suficientemente maduro para substituir o Ecom-Research ou fornecer um clone funcional do WinningHunter. Existe código que acelera adapters e UI, mas o modelo de dados e o workflow de decisão continuam a ser responsabilidade deste projeto.

## APIs prioritárias

### P0 — aproveitar melhor o que já existe

| API | O que acrescenta | Repo/SDK existente | Recomendação |
|---|---|---|---|
| [Apify API](https://docs.apify.com/api/client/python/) | Execução e leitura de Actors para Meta e futuras fontes | [apify/apify-client-python](https://github.com/apify/apify-client-python), SDK oficial | Manter o provider atual e adotar o SDK quando simplificar paginação, datasets ou monitorização; não trocar apenas por trocar |
| [Meta Ad Library API](https://www.facebook.com/ads/library/api/) | Fonte oficial complementar, especialmente para anúncios comerciais entregues na UE e campos de transparência | [facebookresearch/Ad-Library-API-Script-Repository](https://github.com/facebookresearch/Ad-Library-API-Script-Repository) | Fazer spike para PT/ES/FR/DE e usar como cross-check; não substitui a cobertura global atual |
| [Google Ads API — KeywordPlanIdeaService](https://developers.google.com/google-ads/api/docs/keyword-planning/generate-keyword-ideas) | Automatiza volume, CPC e ideias que hoje entram por CSV manual | [googleads/google-ads-python](https://github.com/googleads/google-ads-python), cliente oficial | **Melhor API nova para o core**, desde que exista conta e developer token aprovados |
| [PageSpeed Insights API](https://developers.google.com/speed/docs/insights/v5/get-started) + [CrUX API](https://developer.chrome.com/docs/crux/api/) | Qualidade técnica da landing: performance e experiência real quando houver dados | Clientes HTTP/Google genéricos; não precisa de repo próprio | Adicionar como evidência advisory, sem alterar o score inicial |

### P1 — enriquecer marcas, lojas e procura

| API | O que acrescenta | Repo/SDK existente | Recomendação |
|---|---|---|---|
| [WinningHunter API](https://app.winninghunter.com/docs/api) | Meta, TikTok, Pinterest, Brands, Shopify e Trends através de uma única API | Não encontrei SDK oficial público; integração REST é pequena | **Melhor atalho pago**, começando por Brands + Shopify + Meta enrichment e impondo orçamento de créditos |
| [Wappalyzer API](https://www.wappalyzer.com/api/) ou [BuiltWith API](https://api.builtwith.com/) | Tecnologias, plataforma ecommerce, analytics e apps detetadas numa loja | Integração HTTP simples | Escolher apenas um provider depois de comparar custo e cobertura em 20 lojas reais |
| [Similarweb API](https://developers.similarweb.com/) ou [Semrush API](https://developer.semrush.com/api/) | Traffic, canais e sinais competitivos estimados | APIs oficiais; não é necessário construir crawler | Usar apenas em brands/stores selecionadas e marcar tudo como `estimated` |
| [DataForSEO API](https://docs.dataforseo.com/) | SERP, Google Shopping/Merchant, keywords e dados competitivos num provider único | [dataforseo/PythonClient](https://github.com/dataforseo/PythonClient), cliente oficial | Avaliar como alternativa ao SerpAPI, não adicionar em paralelo sem comparação de custo e qualidade |

### P2 — novas bibliotecas e fornecedores

| API | O que acrescenta | Repo/SDK existente | Recomendação |
|---|---|---|---|
| [TikTok Commercial Content API](https://developers.tiktok.com/products/commercial-content-api/) | Pesquisa de anunciantes e anúncios, com dados de transparência | O `soxoj/AdsLibrary` tem adapter não oficial/licenciado; não encontrei SDK oficial Python maduro | Adiar: acesso aprovado e finalidade de research são o bloqueio principal |
| [Google Ads Transparency Center BigQuery dataset](https://console.cloud.google.com/marketplace/product/bigquery-public-data/google-ads-transparency-center) | Criativos e anunciantes no ecossistema Google | O `ste-fen/AdTracker` já contém uma integração de referência | Fazer spike depois de existir entidade `brand`, porque a pesquisa funciona melhor por anunciante |
| [CJdropshipping API](https://developers.cjdropshipping.com/) | Produto, sourcing, stock, variantes, custos e logística | API oficial; integração própria estreita | Usar apenas no workflow de fornecedor e margem, nunca como prova de procura |

## APIs que parecem úteis, mas não resolvem o problema

- **Shopify Admin API e Storefront API:** exigem acesso/autorização da loja e não funcionam como explorer de concorrentes. Para descobrir lojas externas, usar WinningHunter, BuiltWith/Wappalyzer e um provider de traffic.
- **Pinterest API oficial:** é orientada à gestão de contas e campanhas autorizadas, não a uma biblioteca ampla de anúncios concorrentes. Para competitor research, a via realista é um provider licenciado.
- **Mais um scraper Meta:** o projeto já possui Apify e o problema principal é persistência e qualidade do match. Trocar o scraper antes de guardar e rever toda a evidência não melhora a decisão.
- **Metabase/Superset/Grafana:** servem bem analytics agregada, mas o primeiro cockpit precisa de detalhe de anúncios, media, revisão de matches e decisões; Datasette/SQLPage encaixam melhor e são mais leves.

## Ordem de implementação recomendada

1. **SQLite canónico:** criar `products`, `ads`, `observations`, `product_ad_matches`, `brands`, `stores` e `decisions`, importando os JSON existentes.
2. **Datasette read-only:** entregar imediatamente pesquisa, filtros, detalhe e API sobre todos os anúncios persistidos.
3. **Meta oficial e Google Keyword Planner:** testar uma nova fonte de ads oficial para a UE e eliminar gradualmente o CSV manual de volume/CPC.
4. **SQLPage:** adicionar `Radar`, correção de matches, watchlist, notas e decisões sobre a mesma base.
5. **WinningHunter spike:** medir cobertura, latência, campos úteis e custo real em 5 produtos antes de criar dependência permanente.
6. **UI React apenas se necessária:** React-admin ou Refine entram quando a experiência de Datasette/SQLPage limitar o uso real, não antes.

## Decisão prática

O melhor reaproveitamento não é importar um clone desconhecido do WinningHunter. É combinar:

```text
Ecom-Research atual
+ SQLite
+ Datasette
+ SQLPage
+ SDK oficial Google Ads
+ providers licenciados opcionais
```

Isto evita começar o dashboard do zero, preserva o scoring/proveniência já construídos e permite substituir cada fonte sem reescrever o cockpit.
