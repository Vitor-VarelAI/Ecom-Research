# HANDOFF — Ecom Research

## Estado atual

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds e labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`) não foi alterada.

O outbound Telegram foi implementado e é agora HTML multi-mensagem determinístico por omissão: `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos (retenção, churn/sinais e deltas de anunciantes); `monthly` envia resumo seguido dos produtos afetados. O digest plain text integral continua impresso no stdout e guardado como audit text. O renderer resolve-se por `TELEGRAM_RENDERER` — documentado em `.env.example` e default de código `html`.

O estado de entrega v2 (`schema_version=2`) é durável e por item: impede reenvio de itens confirmados e falha fechado em entrega ambígua (timeout, 5xx, JSON ilegível ou resposta malformada). O renderer Rich existe, implementado com TDD, mas permanece desativado. A duplicação visual do smoke foi diagnosticada como incompatibilidade de renderização Rich específica do cliente, não um double-send da aplicação (evidência no follow-up de `docs/telegram-research/IMPLEMENTATION_STATUS.md`). A assinatura de erro que dispara o fallback Rich→HTML está completa e baseada em evidência (envelope oficial da Bot API, integrações open-source/live-verificadas e allowlists exatas — ver `docs/telegram-research/08-rich-fallback-signature.md`); Rich só é reativado para uma coorte de clientes compatível.

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

Não começar pela Mini App, por callbacks/long polling nem pela automação de fornecedores — o núcleo multi-mensagem HTML já está entregue. A ordem recomendada é:

1. **concluído** — duplicação visual Rich diagnosticada como incompatibilidade de renderização específica do cliente, sem double-send da aplicação (ver follow-up em `docs/telegram-research/IMPLEMENTATION_STATUS.md`); Rich permanece desativado para a coorte de clientes atual;
2. **concluído** — assinatura de erro Rich que dispara o fallback: classificador estreito baseado em evidência, sem chamada Telegram live neste slice — 4xx definitivo consistente **e** token simbólico exato da allowlist (13) ou descrição completa normalizada exata (3); chat/auth/rate/unknown/near-match/inconsistente/5xx continuam fail-closed; Rich→HTML tenta no máximo uma vez e itens demasiado grandes para os 4096 do HTML falham fechado (`render_failed`) sem truncar (32768 Rich vs 4096 HTML). Documentado em `docs/telegram-research/08-rich-fallback-signature.md`;
3. **concluído** — identity store canónico de produtos: `config/products.json` (`schema_version=1`) como registo operado manualmente; `product_id` imutável preservado do antigo hash de 12 hex; um `research_keyword` ativo por produto com aliases para keywords históricas (camisola portugal consolida camisolas portugal, camisola de portugal e camisola portugal preta); `config/keywords.txt` intacto e sem leitura em runtime; run identity por pares ordenados `(product_id, research_keyword)`; packs/manifestos novos com identidade canónica e históricos read-only; quarta/mensal por `product_id` com fallback legado→alias; token posicional desconhecido sai com exit 2 antes de qualquer chamada de fonte/rede; registo ausente ou inválido falha fechado;
4. **próxima prioridade** — long polling e decisões (callbacks), num spike entre `python-telegram-bot` e uma implementação mínima;
5. fornecedores/margem e Legal Gate executável entram em releases separados.

## Garantias existentes a preservar

- `run_id` determinístico e zero repetição de fontes pagas em reentrega;
- estado de entrega v2 por item (`schema_version=2`) com write-ahead antes de cada chamada Telegram;
- timeout/5xx/JSON ilegível/resposta malformada mantém o item ambíguo e bloqueia reenvio (fail-closed);
- item `sent` nunca é reenviado; v1 `success` é no-op histórico;
- tokens, chat ID, URL Bot API e descrição Telegram raw fora de logs, estado e report;
- batch tecnicamente incompleto não é entregue ao Telegram (digest deferred no stdout);
- digest plain text integral como audit text no estado v2;
- scoring permanece advisory.

## Riscos anteriores ainda pendentes

- packs legados podem reutilizar `source_url` de landing não sanitizada;
- quota pode falhar aberta se a persistência falhar;
- fetch de landing ainda precisa de defesa SSRF;
- CSV malformado pode levantar `UnicodeDecodeError`;
- valores gigantes/não finitos não têm guarda total em todos os caches;
- `_is_inactive` pode ignorar `is_active: false` em casos específicos;
- Rich foi aceite pela API; a duplicação visual foi diagnosticada como incompatibilidade de renderização específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram), sem double-send da aplicação — Rich permanece desativado para a coorte de clientes atual;
- a assinatura de erro Rich que dispara fallback está resolvida (classificador baseado em evidência); limitação conhecida: um item até 32768 caracteres Rich que já não caiba nos 4096 do HTML falha fechado como `render_failed`, sem truncar conteúdo;
- batches Rich já persistidos não mudam automaticamente para HTML, por desenho de idempotência;
- itens com entrega ambígua exigem reconciliação manual antes de qualquer reenvio;
- token Telegram anteriormente exposto continua a precisar de rotação pelo operador.

## Verificação desta sessão

Sessão atual (canonical product identity): baseline completo de **755 testes verdes** (`755 passed`), `compileall` sem erros, `run.py --help` e `git diff --check` limpos. Todos os testes mockam rede; não houve nenhuma chamada Telegram live nem a fornecedores neste slice. Token posicional desconhecido confirmado com exit 2 (`unknown product token`) antes de qualquer chamada de fonte/rede; registo ausente ou inválido falha fechado. Sem alterações a `.env`, dados, schema de estado, defaults do renderer, `config/keywords.txt` nem artefactos históricos — a migração apenas lê packs/manifestos/digests legados.

Histórico da release: o único tráfego real foi o smoke autorizado no chat de teste (na altura com **680 testes verdes**), com Rich e HTML separados por state ID e sem ler credenciais para output — Rich aceite pela API mas desativado após o diagnóstico de duplicação visual específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram). A sessão anterior (fallback-signature) fechou com **65 testes focados de entrega** e **708 testes totais** verdes, sem chamadas Telegram live.

Antes de qualquer alteração futura:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline
git diff --check
```

## Suggested skills

- `tdd` — implementar cada slice Rich/callback com red-green-refactor.
- `diagnose` — qualquer divergência de entrega, polling ou estado ambíguo.
- `security-review` — antes de ativar callbacks, URL/media externos ou armazenamento de decisões.
- `assess-impact` — antes de alterar `telegram.py`, `run_state.py` e o manifesto.
- `request-review` — depois de cada release executável.
