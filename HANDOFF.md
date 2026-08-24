# HANDOFF — Ecom Research

## Estado atual

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds e labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`) não foi alterada.

A entrega de matching/evidência está integrada em `main`. A pesquisa WinningHunter e o desenho desta entrega estão em `docs/winninghunter-research/` e `docs/ad-matching/`. O primeiro slice ficou implementado: o adapter Apify preserva candidatos ativos relevantes e deduplicados; `pipeline/matching/product_ads.py` classifica cada candidato como `matched`, `needs_review` ou `rejected`; score, landings, notificações e pulse consomem apenas projeções `matched`; packs novos guardam os candidatos classificados e a proveniência `matcher_version`/`matching_schema_version`. A fórmula numérica do score não mudou, mas `SCORING_VERSION` subiu porque a coorte de entrada mudou.

O gate de fonte exige o product-head em qualquer texto do anúncio, incluindo copy fraca, para que falsos positivos reais cheguem ao matcher em vez de desaparecerem antes da classificação. Assim, mochila personalizada genuína fica `matched`, mochila genérica/porta-bebé fica `needs_review` quando falta prova forte de `personalizada`, e concentrador de oxigénio que apenas menciona mochila no corpo fica `rejected`. Zero candidatos ou todos rejeitados produz `DESCARTAR`; zero matched com candidatos ambíguos produz `DADOS INSUFICIENTES`. Quarta e mensal não comparam nem re-scorizam packs legados sem `matching_schema_version`; preservam-nos e explicam o skip. URLs persistidas em `candidates` e `top_ads` são sanitizadas sem mutar as URLs cruas usadas no fetch.

O outbound Telegram foi implementado e é agora HTML multi-mensagem determinístico por omissão: `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos (retenção, churn/sinais e deltas de anunciantes); `monthly` envia resumo seguido dos produtos afetados. O digest plain text integral continua impresso no stdout e guardado como audit text. O renderer resolve-se por `TELEGRAM_RENDERER` — documentado em `.env.example` e default de código `html`.

O estado de entrega v2 (`schema_version=2`) é durável e por item: impede reenvio de itens confirmados e falha fechado em entrega ambígua (timeout, 5xx, JSON ilegível ou resposta malformada). O renderer Rich existe, implementado com TDD, mas permanece desativado. A duplicação visual do smoke foi diagnosticada como incompatibilidade de renderização Rich específica do cliente, não um double-send da aplicação (evidência no follow-up de `docs/telegram-research/IMPLEMENTATION_STATUS.md`). A assinatura de erro que dispara o fallback Rich→HTML está completa e baseada em evidência (envelope oficial da Bot API, integrações open-source/live-verificadas e allowlists exatas — ver `docs/telegram-research/08-rich-fallback-signature.md`); Rich só é reativado para uma coorte de clientes compatível.

O slice de hardening SSRF do fetch de landings está implementado: `pipeline/adapters/ssrf_guard.py` valida cada alvo (inicial e cada hop de redirect) com `validate_target` — apenas http(s), sem userinfo, com host/porta válidos e só endereços públicos unicast (`ipaddress.is_global`, excluindo explicitamente redes privadas, loopback, link-local, reservadas, multicast e unspecified), rejeitando DNS falhado/vazio e respostas DNS mistas público+privado. `landing_price.py` segue redirects manualmente (301/302/303/307/308, máx. 5, loops/Location ausente/limite detetados) e uma cadeia bloqueada ou inválida suprime o fallback Firecrawl, porque o Firecrawl poderia seguir o mesmo redirect inseguro; falha de rede ou resposta final segura sem preço continuam a cair no Firecrawl. `_fetch_via_firecrawl` revalida o alvo antes da reserva de quota/POST, para que um alvo bloqueado nunca gaste credit nem saia do processo. URLs com userinfo são rejeitadas em vez de limpas (o `url_sanitize.py` mantém-se privacy-only e inalterado). Limitações residuais aceites e documentadas: DNS-rebinding TOCTOU (o preflight DNS não elimina rebinding) e os redirects server-side do próprio Firecrawl.

A investigação técnica e comercial está em [`docs/telegram-research/`](docs/telegram-research/), com os 22 entregáveis em [`REPORT.md`](docs/telegram-research/REPORT.md) e o registo da implementação/smoke em [`IMPLEMENTATION_STATUS.md`](docs/telegram-research/IMPLEMENTATION_STATUS.md). O identity store canónico de produtos foi implementado e está completo: `config/products.json` (`schema_version=1`) é o registo operado manualmente, com seis produtos (cinco ativos; `copper bottles` inativo após o smoke live) e `product_id` imutável (preservado do antigo hash de 12 hex da keyword); `config/keywords.txt` permanece intacto e deixou de ser lido em runtime. A identidade de run usa pares ordenados `(product_id, research_keyword)` — mudar a query ativa cria um run novo (nova pesquisa), enquanto o `product_id` estável preserva o histórico. Packs/manifestos novos carregam `product_id`/`product_name`/`research_keyword`; packs, manifestos e digests históricos são read-only e nunca reescritos; a migração apenas lê artefactos legados. Quarta/mensal selecionam por `product_id` com fallback read-only keyword-legada→alias, mantendo a watchlist e o baseline do pack anterior num produto renomeado. A idempotência de entrega e a segurança contra duplicação não mudaram.

`copper bottles` foi adicionado como sexto produto (`product_id=433621eb147d`), teve um smoke live sem Telegram em 2026-08-24 e ficou inativo depois do teste para não entrar em `--all`/cron nem consumir chamadas pagas futuras. O run terminou com exit 0: 100 ads raw, 72 candidatos, 17 `matched`, 18 `needs_review` e 37 `rejected`; Trends e SERP ficaram válidos; duas landings públicas da House Of Ayurveda produziram preços via HTTP entre USD 74–144. O Keyword Planner não tinha match exato para a keyword, por isso o componente de volume ficou em zero. **Correção do smoke (regra fail-closed do CSV):** sob a semântica corrigida o resultado efetivo é `DADOS INSUFICIENTES` — o 39 numérico fica preservado apenas como breakdown diagnóstico e o artefacto histórico (pack/JSON de 2026-08-24) não é reescrito. Pack: `packs/2026-08-24--keyword--433621eb147d--copper-bottles--956ccacaf236.md`; JSON canónico correspondente em `data/`. Sugestão principal para eventual novo teste: `real copper water bottle` (apenas proposta; não foi executada automaticamente).

Fora do âmbito continuam callbacks, long polling, Mini App, fornecedores/margens automáticos, Legal Gate executável e monitorização jurídica.

## Slice monetização afiliada (gate genérico) — implementado

A fundação genérica de monetização afiliada está implementada. `config/affiliate_offers.json` (`schema_version: 1`) configura um produto de pesquisa (copper bottles, `433621eb147d`, mercado `US`, `approved: false`) com cinco offers `status: candidate` — PUR/SAMA/CopperWater/Copper Utensil Online `primary` e Amazon US `trust_fallback` — todas sem `affiliate_url_env` (gate escuro, mesmo com aprovação acidental do produto) e sem ofertas ativas; os factos desconhecidos/conflituosos estão como JSON `null` e a evidência oficial está em `docs/affiliate-research/copper-bottles-2026-08-24.md`. `pipeline/monetization/` (model/config/gate/render, só stdlib) valida estritamente o config com nullability por ciclo de vida: `candidate`/`applied`/`rejected` podem usar `null` nos campos opcionais; `approved`/`active` exigem todos completos como o schema ativo legado; regras transversais (comissão type/value ambos ou nenhum, `fixed`→moeda, `percentage`→moeda null, evidência+datas todos ou nenhum, shipping presentes cobrem o market, env presente com sintaxe válida) e um guarda defensivo no gate que salta qualquer offer ativa com null inesperado — nunca crasha nem emite, e o renderer/pack nunca vê null porque só offers ativas completas emitem. O config falha fechado para None (missing/invalid/empty = desativado) sem quebrar o run nem expor o config/erros. O gate puro em `run_keyword` (`_evaluate_monetization`) exige `AVANÇAR` não-incompleto, `apify_ads`/`trends`/`keyword_csv` `valid`, ads matched, volume finito >= 0, produto aprovado com market e offer ativa/não-expirada com env definido (o VALOR nunca é inspecionado para o output — só o NOME viaja como `affiliate_link_ref`); emite até duas offers (primary ativa, depois trust_fallback; papel operador-escolhido, sem ranking por comissão). Anexação aditiva só no pack: `result["monetization"]`, chave JSON `monetization` e secção Markdown `## Monetização afiliada` apenas quando presente, strings do operador escapadas, disclosure PT-PT fixa, sem campo de health/benefit claim. Nada de afiliado chega ao Telegram, digest stdout, manifests (checkpoints e `completed_with_errors` são limpos via `_without_monetization`), logs ou notification snapshots. O config não participa no `run_id`. Fora do âmbito deste slice: click tracking, geração de páginas, resolução de link refs em links clicáveis, chamadas/scraping/inscrições em programas, transições automáticas de lifecycle, inferência de mercado, health claims e notificações. Re-renderizar a monetização sem repetir a pesquisa paga é um slice futuro — o pack JSON persistido já contém a emissão, pronta a servir de input. Bloqueadores registados antes de qualquer candidate → approved/active: aprovação de produto/conta, disclosure Amazon exata ("As an Amazon Associate I earn from qualifying purchases."), categoria viva da listing Amazon (hoje Sports & Outdoors = 3%, não Kitchen = 4.5%), payout Portugal não reconfirmado e risco de health claims (SAMA/CopperWater).

## Correção fail-closed do CSV (Keyword Planner)

Na branch com ads matched, qualquer erro do CSV (ficheiro em falta ou keyword não encontrada — `não encontrado`/`não encontrada`) preserva o score numérico/breakdown mas força `label="DADOS INSUFICIENTES"`, `incomplete=true` e `reason=csv error` via `_incomplete_score`; CSV válido mantém o resultado byte-compatível. Zero candidatos e zero matched (todos rejeitados) permanecem `DESCARTAR` mesmo sem CSV; needs_review e falha Apify continuam incompletos. `_source_states` classifica as mensagens de keyword em falta como `missing` (não `failed`); restantes erros de parse continuam `failed`. O smoke copper histórico não é reescrito: o 39 numérico fica como breakdown diagnóstico e o label efetivo passa a ser `DADOS INSUFICIENTES` sob a regra corrigida.

## Decisão Telegram

A Bot API oficial confirma Rich Messages e `sendRichMessage` (10.1) e blocos estruturados, tabelas, `details` e media (10.2). A recomendação de planeamento previa `sendRichMessage` como renderer principal com HTML como fallback; o smoke autorizado inverteu a decisão operacional:

1. `sendMessage` com HTML é o renderer operacional e default (código e `.env.example`): `TELEGRAM_RENDERER=html`;
2. `sendRichMessage` está implementado (`pipeline/notifications/render_rich.py`) mas desativado — a API aceitou as mensagens e o retry fez zero chamadas de rede; o diagnóstico concluiu que a duplicação visual é uma incompatibilidade de renderização específica do cliente, não um reenvio da aplicação (Rich mantém-se desativado para a coorte de clientes atual);
3. resumo separado seguido de uma mensagem por produto nos modos agregados;
4. `keyword` envia uma única mensagem de produto;
5. long polling e decisões (callbacks) ficam fora do âmbito desta release;
6. apenas um `Telegram user_id` autorizado;
7. Mini App adiada.

O outbound continua a usar `requests`. O runtime inbound deve ser decidido num spike entre `python-telegram-bot` e uma implementação mínima, sem reescrever o protocolo de entrega atual.

## Decisões do operador

- Imagens só podem vir de fornecedor validado e com utilização permitida.
- O botão do relatório completo fica oculto enquanto os packs não tiverem URL pública validada.
- Pesquisa de fornecedores usa apenas páginas públicas, sem contas ou APIs pagas nesta fase.
- Prioridades comerciais: Portugal, Reino Unido e Estados Unidos.
- Legal Gate atual: União Europeia/Portugal; regras UK/US ficam fora do âmbito jurídico desta fase.
- Restantes Estados-Membros continuam configuráveis individualmente.

## Legal Gate progressivo

O modelo inicial foi corrigido para não rejeitar oportunidades prematuramente. Estado jurídico e fase comercial são dimensões independentes:

```text
Descoberta → Pesquisa de fornecedor → Teste sem venda → Venda teste → Escala
```

Uma lacuna documental bloqueia apenas a fase em que é material. EPR/packaging aplicáveis continuam obrigatórios antes da primeira venda; CE só é exigido quando a categoria o determina; Safety Gate sem resultados não aprova nem bloqueia sozinho.

Estado preliminar dos cinco produtos:

- mochila personalizada: avançar para pesquisa de fornecedor;
- caneca personalizada: avançar para pesquisa; documentação de contacto alimentar aplicável antes da venda;
- tapete yoga: avançar para pesquisa; composição e regras aplicáveis antes da venda;
- velas aromáticas: avançar para pesquisa; composição/classificação/avisos antes da venda;
- camisola Portugal: pesquisa jurídica permitida, mas validar propriedade intelectual antes de publicidade com símbolos ou nomes potencialmente protegidos.

## Fornecedores e margens

Não existe fornecedor confirmado para venda. Há páginas individuais `CANDIDATE` no AliExpress para mochila, caneca, tapete e velas, e páginas de categoria/pesquisa noutras plataformas. Preço, MOQ, stock, shipping PT/GB/US, prazo, devoluções e documentação continuam por validar.

O modelo financeiro separa receita líquida, IVA de venda/recuperável, importação/IOSS, HS/CN, shipping, fulfilment, pagamentos, devoluções, contribuição antes de anúncios e CAC break-even. Nenhum preço, margem ou threshold comercial foi inventado.

## Documentação criada

- `docs/telegram-research/01-current-system-and-bot-api.md` — matriz oficial da Bot API.
- `docs/telegram-research/01b-current-system-diagnosis.md` — implementação atual e blast radius.
- `docs/telegram-research/02-options-compatibility-recommendation.md` — comparação A/B/C.
- `docs/telegram-research/03-mockups-and-interactions.md` — resumo e cinco produtos.
- `docs/telegram-research/03b-telegram-payloads.md` — payloads Rich, edição e fallback.
- `docs/telegram-research/04-data-callbacks-and-persistence.md` — long polling, decisões e idempotência.
- `docs/telegram-research/product-schema.example.json` — schema completo.
- `docs/telegram-research/05-suppliers-and-margins.md` — evidência pública e fórmulas.
- `docs/telegram-research/06-legal-gate-and-monitoring.md` — gate progressivo UE/PT.
- `docs/telegram-research/07-implementation-plan-and-tests.md` — fases, esforço e testes.
- `docs/telegram-research/08-rich-fallback-signature.md` — assinatura de rejeição Rich: classificador baseado em evidência (4xx definitivo consistente + token simbólico exato da allowlist ou descrição completa normalizada exata; fail-closed; limite 32768 Rich vs 4096 HTML).
- `docs/telegram-research/REPORT.md` — índice dos 22 entregáveis.
- `docs/telegram-research/IMPLEMENTATION_STATUS.md` — registo da implementação, smoke Rich/HTML e ativação/rollback.

## Próxima sessão

Retomar diretamente em `/home/vitor/projects/Ecom-Research` na `main`; não repetir a pesquisa WinningHunter nem redesenhar o matcher. O review final dual passou sem P0/P1 nem must-fix (96/100 e 97/100), e a entrega já foi integrada. Não fazer chamadas Apify/Telegram live para iniciar o próximo slice.

O passo funcional seguinte é a projeção SQLite idempotente dos packs classificados e o explorer Datasette read-only. SQLPage, React, API própria, revisão manual, callbacks/long polling, fornecedores/margens e Legal Gate executável continuam fora desse próximo slice. O SQLite não substitui os packs como prova: importa-os transacionalmente, e o mesmo `run_id + product_id` com conteúdo diferente deve falhar. A monetização afiliada continua escura até o operador aprovar o produto copper (`approved: true`) e ativar uma offer completa/não-expirada com o respetivo env em `config/affiliate_offers.json` (schema v1, cinco candidates de pesquisa hoje); quando isso acontecer, o gate só emite em runs AVANÇAR completos. Follow-ups não bloqueantes do matcher: identidade de pulse para ads sem `ad_archive_id`, estratégia de limite para `match_evidence`, tokenização de keywords hifenizadas e polimento do regex de URLs embutidas.

A disciplina de testes é pragmática porque esta é uma ferramenta pessoal: manter regressões reais e caminhos críticos, sem matrizes de SaaS nem testes de constantes/implementação. Quando matcher, projeções matched-only, zero-match, sanitização, legado e idempotência estiverem confirmados, avançar.

## Garantias existentes a preservar

- `run_id` determinístico e zero repetição de fontes pagas em reentrega;
- estado de entrega v2 por item (`schema_version=2`) com write-ahead antes de cada chamada Telegram;
- timeout/5xx/JSON ilegível/resposta malformada mantém o item ambíguo e bloqueia reenvio (fail-closed);
- item `sent` nunca é reenviado; v1 `success` é no-op histórico;
- tokens, chat ID, URL Bot API e descrição Telegram raw fora de logs, estado e report;
- alvos de landing validados contra SSRF antes de qualquer request (inicial e cada hop de redirect); cadeia bloqueada/inválida suprime o fallback Firecrawl; alvo bloqueado nunca gasta credit Firecrawl;
- logs de landing contêm apenas a URL sanitizada + reason code seguro, nunca URL crua, userinfo, texto de exceção ou lista de IPs resolvidos;
- batch tecnicamente incompleto não é entregue ao Telegram (digest deferred no stdout);
- digest plain text integral como audit text no estado v2;
- scoring permanece advisory;
- fail-closed do CSV na branch matched: erro do CSV (ficheiro em falta / keyword não encontrada, `não encontrado`/`não encontrada`) força `DADOS INSUFICIENTES` com reason do CSV, preservando score/breakdown; `_source_states` trata keyword-em-falta como `missing`;
- monetização: config estrito fail-closed (missing/invalid/empty = desativado), gate puro com emissão determinística até duas offers, payload e secção só no pack, strings escapadas, env VALUE nunca inspecionado para output, afiliado nunca em Telegram/digest/manifests/logs/snapshots.

## Riscos anteriores ainda pendentes

- packs legados podem reutilizar `source_url` de landing não sanitizada;
- quota pode falhar aberta se a persistência falhar;
- CSV malformado pode levantar `UnicodeDecodeError`;
- valores gigantes/não finitos não têm guarda total em todos os caches;
- `_is_inactive` pode ignorar `is_active: false` em casos específicos;
- Rich foi aceite pela API; a duplicação visual foi diagnosticada como incompatibilidade de renderização específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram), sem double-send da aplicação — Rich permanece desativado para a coorte de clientes atual;
- a assinatura de erro Rich que dispara fallback está resolvida (classificador baseado em evidência); limitação conhecida: um item até 32768 caracteres Rich que já não caiba nos 4096 do HTML falha fechado como `render_failed`, sem truncar conteúdo;
- batches Rich já persistidos não mudam automaticamente para HTML, por desenho de idempotência;
- itens com entrega ambígua exigem reconciliação manual antes de qualquer reenvio;
- token Telegram anteriormente exposto continua a precisar de rotação pelo operador.

## Limitações residuais do SSRF (aceites, sem sobrestimar)

- o preflight DNS + revalidação por hop não eliminam o DNS-rebinding TOCTOU: um resolver que responda público na validação e privado no connect real contorna o guard;
- o Firecrawl segue os seus próprios redirects server-side, fora do controlo do pipeline — um alvo público pode redirecionar lá para um destino privado;

## Verificação desta sessão

O slice SSRF fechou com a suite completa verde (853 testes), `compileall` e `git diff --check` limpos. Novos testes: `tests/test_ssrf_guard.py` (44 casos: schemes/userinfo/control chars/portas, ranges IPv4+IPv6 não-públicos incluindo metadata IP, multicast, endereços reservados e loopback IPv4-mapped, literais públicos, DNS público/privado/misto/vazio/erro) e testes de interface pública em `tests/test_landing_price.py` com stub autouse do resolver (sem DNS real): alvo inicial privado/userinfo/DNS falhado → zero GET e zero Firecrawl; redirect relativo público funciona; redirect público→privado e público→userinfo bloqueados antes do segundo GET e nunca chegam ao Firecrawl; loops/Location ausente/>5 hops falham com segurança; alvo Firecrawl bloqueado não faz POST nem gasta quota. Os testes de privacidade existentes com userinfo foram adaptados (removido o userinfo, mantidas as queries secretas) e a rejeição dedicada de userinfo foi adicionada. A revisão GPT acrescentou regressões para whitespace de controlo em `Location`, logs sem caracteres de controlo e isolamento deep-copy da proveniência SSRF. Não houve chamadas de rede, Apify, Telegram ou DNS, nem alterações a `.env`, `data/`, `packs/` ou artefactos históricos.

## Verificação da sessão copper candidates (nullable lifecycle + catálogo fonte-gated)

Slice fechado na branch `feat/copper-affiliate-candidates` com a suite completa verde (**1031 testes**), `compileall`, `pip check` e `git diff --check` limpos. RED confirmado antes do fix (16 falhas): os candidatos com campos `null` eram rejeitados pelo loader estrito (config inválido) e o gate emitia offers ativas sintéticas com `null` em vez de as saltar.

Novos testes: `tests/test_monetization_config.py` (59→91: nullability por status — candidate/applied/rejected com todos os campos null carregam, approved/active exigem completude, campos presentes malformados falham, simetria de comissão, fixed exige moeda e percentage proíbe moeda, evidência+datas all-or-none, shipping null/cobertura do market, chaves exatas obrigatórias, múltiplos candidates primaries válidos, candidate null ao lado de active válido), `tests/test_monetization_gate.py` (40→50: status não-ativos com null nunca emitem, candidate null ao lado de active completo emite só o active, guarda defensiva salta active sintética com null/destination null/comissão null/shipping vazio/datas de tipo inválido sem exceção, end-to-end pelo loader real), `tests/test_monetization_copper_candidates.py` (novo, 8: catálogo comprometido carrega com schema_version 1, exatamente um produto copper não aprovado, cinco candidates com roles esperadas, zero env refs e zero ativos, gate None sob AVANÇAR elegível, só a comissão PUR inequívoca é persistida, nenhum valor de URL tracked no catálogo e nenhum health claim nos campos de prosa do operador) e `tests/test_monetization_run.py` (6→7: config só-candidates mantém o output de pesquisa idêntico ao desativado). Fixture partilhada: `candidate_offer` em `tests/fixtures/monetization_helpers.py`.

Catálogo comprometido: `config/affiliate_offers.json` — produto `433621eb147d` (copper bottles, mercado `US`, `approved: false`) com cinco offers `candidate` (PUR/SAMA/CopperWater/Copper Utensil Online `primary`; Amazon US `trust_fallback`), todas `affiliate_url_env: null`, sem ofertas ativas. Evidência oficial: `docs/affiliate-research/copper-bottles-2026-08-24.md` (15% PUR; SAMA 10–15% range→null + cookie 30 + payout/shipping/asset/traffic oficiais; CopperWater sem comissão publicada + shipping US; Amazon fee table oficial Kitchen 4.5%, mas listing atual Sports & Outdoors 3% → comissão null no catálogo; sessão 24h + regras de imagem/link/paid search + disclosure exata como blocker; Copper Utensil 5%/3%/15% conflito→null + lifetime não representável; health claims SAMA/CopperWater documentados como risco). Nenhuma candidatura submetida, nenhuma oferta ativa, nenhuma chamada de rede no run dos testes.

Usar o ambiente do repositório principal:

```bash
/home/vitor/projects/Ecom-Research/.venv/bin/python -m pytest tests/ -q
/home/vitor/projects/Ecom-Research/.venv/bin/python -m compileall -q run.py pipeline tests
/home/vitor/projects/Ecom-Research/.venv/bin/python -m pip check
git diff --check
```

## Verificação da sessão monetização + CSV fail-closed

Slice fechado na branch `feat/monetization-gate` com a suite completa verde (**980 testes**), `compileall`, `pip check` e `git diff --check` limpos. RED confirmado antes do fix: `test_positive_matched_ads_no_csv_is_incomplete_not_false_verdict`, `test_positive_matched_ads_copper_row_missing_is_incomplete` e `test_source_state_feminine_row_missing_is_missing` falhavam (label OBSERVAR em vez de DADOS INSUFICIENTES; `não encontrada` como `failed`).

Novos ficheiros de teste: `tests/test_run_csv_fail_closed.py` (11: matched+sem CSV, matched+copper `não encontrada`, CSV válido byte-compatível, zero-candidatos/all-rejected DESCARTAR com CSV em falta, needs_review/Apify incompletos, classificação `missing` masculina/feminina, restantes erros `failed`), `tests/test_monetization_config.py` (59: matriz de validação — chaves exatas, tipos, formatos/duplicados de IDs, market vs shipping_markets, URLs com query/userinfo/fragment/control chars/não-http/porta inválida, datas e `checked_at <= valid_until`, moeda obrigatória para fixed e 3 letras, valor finito positivo, cookie inteiro positivo, no máximo uma ACTIVE por role, fail-closed silencioso sem leak), `tests/test_monetization_gate.py` (40: matriz do gate — não-AVANÇAR/incompleto/fontes críticas inválidas/sem matched/volume mau/não aprovado/sem ativa/expirada/env em falta → None; primary só, fallback só, ambas por ordem; sem ranking por comissão; env VALUE nunca no output), `tests/test_monetization_pack.py` (11: ausente por omissão; secção+JSON quando elegível; fixed com moeda; escaping de strings do operador; neutralização `<script>`; env VALUE ausente de todo o conteúdo renderizado; ambas as roles) e `tests/test_monetization_run.py` (6: config em falta/malformado sem regressão, run elegível anexa monetização sem alterar score, output de pesquisa idêntico com/sem config, manifest nunca carrega afiliado — checkpoint, `completed_with_errors` e digest limpos). Fixture partilhada: `tests/fixtures/monetization_helpers.py`. A revisão GPT acrescentou regressões para injeção de links Markdown em URLs, canonicalização IPv6 com brackets, chaves JSON duplicadas e distinção entre keyword ausente e coluna CSV ausente.

Não houve chamadas de rede, Apify, Telegram ou DNS, nem alterações a `.env`, `data/`, `packs/`, caches ou artefactos históricos. O artefacto copper de 2026-08-24 não foi reescrito (a correção altera apenas a interpretação: label efetivo `DADOS INSUFICIENTES` com 39 numérico como breakdown diagnóstico).

Usar o ambiente do repositório principal:

```bash
/home/vitor/projects/Ecom-Research/.venv/bin/python -m pytest tests/ -q
/home/vitor/projects/Ecom-Research/.venv/bin/python -m compileall -q run.py pipeline tests
/home/vitor/projects/Ecom-Research/.venv/bin/python -m pip check
git diff --check
```

## Suggested skills

- `tdd` — implementar cada slice Rich/callback com red-green-refactor.
- `diagnose` — qualquer divergência de entrega, polling ou estado ambíguo.
- `security-review` — antes de ativar callbacks, URL/media externos ou armazenamento de decisões.
- `assess-impact` — antes de alterar `telegram.py`, `run_state.py` e o manifesto.
- `request-review` — depois de cada release executável.
