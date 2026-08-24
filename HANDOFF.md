# HANDOFF — Ecom Research

## Estado atual

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds e labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`) não foi alterada.

A entrega de matching/evidência está integrada em `main`. A pesquisa WinningHunter e o desenho desta entrega estão em `docs/winninghunter-research/` e `docs/ad-matching/`. O primeiro slice ficou implementado: o adapter Apify preserva candidatos ativos relevantes e deduplicados; `pipeline/matching/product_ads.py` classifica cada candidato como `matched`, `needs_review` ou `rejected`; score, landings, notificações e pulse consomem apenas projeções `matched`; packs novos guardam os candidatos classificados e a proveniência `matcher_version`/`matching_schema_version`. A fórmula numérica do score não mudou, mas `SCORING_VERSION` subiu porque a coorte de entrada mudou.

O gate de fonte exige o product-head em qualquer texto do anúncio, incluindo copy fraca, para que falsos positivos reais cheguem ao matcher em vez de desaparecerem antes da classificação. Assim, mochila personalizada genuína fica `matched`, mochila genérica/porta-bebé fica `needs_review` quando falta prova forte de `personalizada`, e concentrador de oxigénio que apenas menciona mochila no corpo fica `rejected`. Zero candidatos ou todos rejeitados produz `DESCARTAR`; zero matched com candidatos ambíguos produz `DADOS INSUFICIENTES`. Quarta e mensal não comparam nem re-scorizam packs legados sem `matching_schema_version`; preservam-nos e explicam o skip. URLs persistidas em `candidates` e `top_ads` são sanitizadas sem mutar as URLs cruas usadas no fetch.

O outbound Telegram foi implementado e é agora HTML multi-mensagem determinístico por omissão: `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos (retenção, churn/sinais e deltas de anunciantes); `monthly` envia resumo seguido dos produtos afetados. O digest plain text integral continua impresso no stdout e guardado como audit text. O renderer resolve-se por `TELEGRAM_RENDERER` — documentado em `.env.example` e default de código `html`.

O estado de entrega v2 (`schema_version=2`) é durável e por item: impede reenvio de itens confirmados e falha fechado em entrega ambígua (timeout, 5xx, JSON ilegível ou resposta malformada). O renderer Rich existe, implementado com TDD, mas permanece desativado. A duplicação visual do smoke foi diagnosticada como incompatibilidade de renderização Rich específica do cliente, não um double-send da aplicação (evidência no follow-up de `docs/telegram-research/IMPLEMENTATION_STATUS.md`). A assinatura de erro que dispara o fallback Rich→HTML está completa e baseada em evidência (envelope oficial da Bot API, integrações open-source/live-verificadas e allowlists exatas — ver `docs/telegram-research/08-rich-fallback-signature.md`); Rich só é reativado para uma coorte de clientes compatível.

O slice de hardening SSRF do fetch de landings está implementado: `pipeline/adapters/ssrf_guard.py` valida cada alvo (inicial e cada hop de redirect) com `validate_target` — apenas http(s), sem userinfo, com host/porta válidos e só endereços públicos unicast (`ipaddress.is_global`, excluindo explicitamente redes privadas, loopback, link-local, reservadas, multicast e unspecified), rejeitando DNS falhado/vazio e respostas DNS mistas público+privado. `landing_price.py` segue redirects manualmente (301/302/303/307/308, máx. 5, loops/Location ausente/limite detetados) e uma cadeia bloqueada ou inválida suprime o fallback Firecrawl, porque o Firecrawl poderia seguir o mesmo redirect inseguro; falha de rede ou resposta final segura sem preço continuam a cair no Firecrawl. `_fetch_via_firecrawl` revalida o alvo antes da reserva de quota/POST, para que um alvo bloqueado nunca gaste credit nem saia do processo. URLs com userinfo são rejeitadas em vez de limpas (o `url_sanitize.py` mantém-se privacy-only e inalterado). Limitações residuais aceites e documentadas: DNS-rebinding TOCTOU (o preflight DNS não elimina rebinding) e os redirects server-side do próprio Firecrawl.

A investigação técnica e comercial está em [`docs/telegram-research/`](docs/telegram-research/), com os 22 entregáveis em [`REPORT.md`](docs/telegram-research/REPORT.md) e o registo da implementação/smoke em [`IMPLEMENTATION_STATUS.md`](docs/telegram-research/IMPLEMENTATION_STATUS.md). O identity store canónico de produtos foi implementado e está completo: `config/products.json` (`schema_version=1`) é o registo operado manualmente, com cinco produtos e `product_id` imutável (preservado do antigo hash de 12 hex da keyword); `config/keywords.txt` permanece intacto e deixou de ser lido em runtime. A identidade de run usa pares ordenados `(product_id, research_keyword)` — mudar a query ativa cria um run novo (nova pesquisa), enquanto o `product_id` estável preserva o histórico. Packs/manifestos novos carregam `product_id`/`product_name`/`research_keyword`; packs, manifestos e digests históricos são read-only e nunca reescritos; a migração apenas lê artefactos legados. Quarta/mensal selecionam por `product_id` com fallback read-only keyword-legada→alias, mantendo a watchlist e o baseline do pack anterior num produto renomeado. A idempotência de entrega e a segurança contra duplicação não mudaram.

Fora do âmbito continuam callbacks, long polling, Mini App, fornecedores/margens automáticos, Legal Gate executável e monitorização jurídica.

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

O passo funcional seguinte é a projeção SQLite idempotente dos packs classificados e o explorer Datasette read-only. SQLPage, React, API própria, revisão manual, callbacks/long polling, fornecedores/margens e Legal Gate executável continuam fora desse próximo slice. O SQLite não substitui os packs como prova: importa-os transacionalmente, e o mesmo `run_id + product_id` com conteúdo diferente deve falhar. Follow-ups não bloqueantes do matcher: identidade de pulse para ads sem `ad_archive_id`, estratégia de limite para `match_evidence`, tokenização de keywords hifenizadas e polimento do regex de URLs embutidas.

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
- scoring permanece advisory.

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

O slice de SSRF fechou com a suite completa verde (853 testes), `compileall` e `git diff --check` limpos. Novos testes: `tests/test_ssrf_guard.py` (44 casos: schemes/userinfo/control chars/portas, ranges IPv4+IPv6 não-públicos incluindo metadata IP, multicast, endereços reservados e loopback IPv4-mapped, literais públicos, DNS público/privado/misto/vazio/erro) e testes de interface pública em `tests/test_landing_price.py` com stub autouse do resolver (sem DNS real): alvo inicial privado/userinfo/DNS falhado → zero GET e zero Firecrawl; redirect relativo público funciona; redirect público→privado e público→userinfo bloqueados antes do segundo GET e nunca chegam ao Firecrawl; loops/Location ausente/>5 hops falham com segurança; alvo Firecrawl bloqueado não faz POST nem gasta quota. Os testes de privacidade existentes com userinfo foram adaptados (removido o userinfo, mantidas as queries secretas) e a rejeição dedicada de userinfo foi adicionada. A revisão GPT acrescentou regressões para whitespace de controlo em `Location`, logs sem caracteres de controlo e isolamento deep-copy da proveniência SSRF. Não houve chamadas de rede, Apify, Telegram ou DNS, nem alterações a `.env`, `data/`, `packs/` ou artefactos históricos.

Usar o ambiente do repositório principal:

```bash
/home/vitor/projects/Ecom-Research/.venv/bin/python -m pytest tests/ -q
/home/vitor/projects/Ecom-Research/.venv/bin/python -m compileall -q run.py pipeline tests
git diff --check
```

## Suggested skills

- `tdd` — implementar cada slice Rich/callback com red-green-refactor.
- `diagnose` — qualquer divergência de entrega, polling ou estado ambíguo.
- `security-review` — antes de ativar callbacks, URL/media externos ou armazenamento de decisões.
- `assess-impact` — antes de alterar `telegram.py`, `run_state.py` e o manifesto.
- `request-review` — depois de cada release executável.
