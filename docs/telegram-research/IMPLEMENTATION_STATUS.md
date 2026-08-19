# Estado da implementação — outbound Telegram multi-mensagem

Data: 2026-08-19

## Base e branch

- Commit inicial: `0da5e8e1542167baa0d8e46ea63611f367adee40` (`docs(telegram): documenta evolução das notificações rich`).
- Branch: `codex/telegram-rich-outbound`.
- Worktree inicial: limpo, em `main`, alinhado com o commit esperado.
- Baseline real no VPS: `561 passed in 23.55s`.
- Baseline adicional: `compileall` e `git diff --check` sem erros.

## Contrato e checkpoints

### DeepSeek V4 Pro — arquitetura read-only

O checkpoint inicial definiu o modelo mínimo, o estado por item, o cutover v1/v2, o protocolo write-ahead, a classificação de respostas e a matriz de testes. O agente não editou ficheiros.

O agente assinalou riscos P0 que foram resolvidos antes da entrega v2:

- `chat_id` não existe no estado;
- estado v1 parcial não é migrado nem reenviado automaticamente;
- fallback Rich é restrito a uma rejeição que identifique explicitamente Rich inválido/não suportado;
- o gate `delivery.status == success` continua a impedir reentrega.

A proposta inicial do agente para Wednesday/monthly foi corrigida pelo agente principal: a implementação segue o pedido do operador, com resumo e mensagens de produto nesses modos.

### DeepSeek Flash — execução por slices

1. **Modelo e renderers:** criou `pipeline/notifications/`, fixtures sintéticas e testes de identidade, ordem, escaping, placeholders, limites, payloads e paridade semântica.
2. **Entrega v2:** implementou `deliver_batch`, estado por item, write-ahead, captura de `message_id`, resume, cutover histórico e fallback Rich → HTML restrito.
3. **Integração:** ligou `run.py` aos batches multi-mensagem, adicionou snapshots seguros para reconstrução sem fontes e cobriu keyword, sunday, all, wednesday e monthly.

O agente principal inspecionou cada diff, executou os testes de cada slice, corrigiu a adaptação de `test_batch_resume.py` e manteve a responsabilidade por Git e pelos gates.

### DeepSeek V4 Pro — review final read-only

Veredicto: `READY WITH P2`, sem P0/P1.

Pontos P2 encontrados e tratados:

- removida a divergência entre o diretório default v2 e o cutover real em `data/digests/`;
- acrescentado `fsync` do diretório pai depois de `os.replace`, fechando uma janela de perda do marcador write-ahead em power loss;
- preview regenerado pelo builder de produção, em vez de usar apenas o mockup rico das fixtures;
- `TELEGRAM_RENDERER=html` documentado em `.env.example` como default seguro.

## Resultado funcional

- `keyword`: uma mensagem de produto.
- `sunday` e `all`: resumo, seguido de um item por produto.
- `wednesday`: resumo pulse, seguido de itens compactos que preservam retenção, churn/sinais e deltas de anunciantes.
- `monthly`: resumo, seguido dos produtos afetados.
- Rich e HTML derivam do mesmo conteúdo renderer-neutral.
- Não existem callbacks, `reply_markup`, imagens, media ou botão de relatório completo.
- O digest plain text integral é impresso uma vez, guardado no manifesto e guardado como audit text no estado v2.
- Runs concluídos não repetem fontes pagas nem mensagens confirmadas.

## Estado de entrega v2

Cada ficheiro contém `schema_version=2`, identidade do batch, audit text e um registo por item com `item_id`, ordem, renderer, hash do payload, estado, `in_flight`, `sent_at`, `message_id`, fallback e erro sanitizado.

Regras verificadas:

- persistência inicial e `in_flight` acontecem antes da rede;
- falha de persistência pré-rede bloqueia o envio;
- `message_id` só é guardado depois de `ok=true` com resposta válida;
- timeout, transporte, 5xx, JSON ilegível e resposta de sucesso malformada ficam ambíguos e não fazem fallback;
- item `sent` nunca é reenviado;
- falha de persistência pós-aceitação bloqueia resend;
- estado corrompido falha fechado;
- v1 `success` é no-op histórico; v1 não concluído exige resolução explícita;
- token, chat ID, URL Bot API e descrição Telegram raw não entram no estado ou report.

## Preview offline

Localização: `/tmp/ecom-telegram-rich-preview/`.

- `preview.html`: inspeção visual local de Rich, HTML fallback e audit text.
- `index.json`: ordem e identidade dos itens.
- `*.rich.json`: fragments `sendRichMessage` sem credenciais/chat.
- `*.html-fallback.json`: fragments `sendMessage` HTML sem credenciais/chat.
- `*.audit.txt` e `audit.txt`: plain text integral.

O preview foi gerado com `pipeline.notifications.build_batch`, contém um resumo e três produtos, validou nove ficheiros JSON e não chamou rede, Apify, Trends, SERP ou outra fonte paga.

## Smoke em chat de teste

O operador autorizou os dois envios abaixo para o chat de teste. Nenhum valor de credencial ou chat foi lido para output ou registado aqui.

### Rich — bloqueado para ativação

- State ID: `rich-smoke-20260819T094918Z`.
- Batch: resumo + um produto.
- Bot API aceitou ambos os itens; estado guardou `message_id` 17 e 18, `status=sent`, `in_flight=false`.
- Reexecução do mesmo state ID: zero chamadas de rede.
- Inspeção visual do operador: resumo e produto apareceram duplicados no mesmo intervalo temporal.

A aplicação não reenviou no retry, mas o resultado visual Rich não satisfaz o contrato. A causa exata pode estar no endpoint/cliente Rich e precisa de investigação separada. **Rich não fica ativado nesta release.**

### HTML — aprovado

- State ID: `html-smoke-20260819T095305Z`.
- Batch: resumo + um produto com identificador visual próprio.
- Primeira execução: exatamente duas chamadas de rede; estado guardou `message_id` 19 e 20, `status=sent`, `in_flight=false`.
- Reexecução: zero chamadas de rede.
- Inspeção visual do operador: exatamente duas mensagens, sem duplicação.

O modo operacional desta release é, por isso, HTML multi-mensagem.

## Diagnóstico follow-up — duplicação visual Rich

Data: 2026-08-19 (follow-up ao smoke; documentação apenas — sem alterações a código, testes, `.env` ou dados). Para este diagnóstico não foi enviada nenhuma mensagem live nova: a observação cross-client reutilizou as mensagens existentes 17/18.

Evidência retida e inspeção offline:

- `/tmp/ecom-telegram-smoke-state/rich-smoke-20260819T094918Z.json` (`schema_version=2`): exatamente dois itens Rich (resumo + produto), ambos `sent` uma única vez, `message_id` 17 e 18 consecutivos, `in_flight=false`; a reexecução do mesmo state ID fez zero chamadas de rede.
- Controlo HTML: `/tmp/ecom-telegram-smoke-state/html-smoke-20260819T095305Z.json` — `message_id` 19 e 20 renderizados corretamente, retry com zero chamadas de rede.
- Inspeção offline de payloads/requests: um `sendRichMessage` por item, sem campo de texto irmão no payload e sem conteúdo duplicado; os testes atuais cobrem contagem de requests e idempotência (request count e resume sem duplicação).

Observação cross-client:

- O operador visualizou as mensagens existentes 17/18 num segundo cliente Telegram: cada uma aparece exatamente uma vez nesse cliente, enquanto o cliente original mostrava cada item duplicado visualmente.

Conclusão:

- A duplicação é uma incompatibilidade de renderização Rich específica do cliente, não um double-send da aplicação; não há correção de código de produção justificada.
- A decisão operacional mantém-se `TELEGRAM_RENDERER=html`; Rich permanece desativado para a coorte de clientes atual.
- Não é reclamada uma versão específica de cliente nem internals de servidor: esses dados não foram capturados durante o diagnóstico.

## Ficheiros alterados

- `.env.example`
- `run.py`
- `pipeline/run_state.py`
- `pipeline/delivery/telegram.py`
- `pipeline/notifications/__init__.py`
- `pipeline/notifications/builder.py`
- `pipeline/notifications/limits.py`
- `pipeline/notifications/model.py`
- `pipeline/notifications/parity.py`
- `pipeline/notifications/placeholders.py`
- `pipeline/notifications/render_html.py`
- `pipeline/notifications/render_rich.py`
- `tests/fixtures/__init__.py`
- `tests/fixtures/telegram/__init__.py`
- `tests/fixtures/telegram/build_helpers.py`
- `tests/fixtures/telegram/synthetic_products.json`
- `tests/test_batch_resume.py`
- `tests/test_delivery_v2.py`
- `tests/test_idempotency.py`
- `tests/test_notifications_batch.py`
- `tests/test_notifications_html.py`
- `tests/test_notifications_model.py`
- `tests/test_notifications_parity.py`
- `tests/test_notifications_placeholders.py`
- `tests/test_notifications_rich.py`
- `tests/test_run_modes.py`
- `tests/test_run_notifications.py`
- `tests/test_run_state.py`
- `tests/test_telegram_state.py`
- `docs/telegram-research/IMPLEMENTATION_STATUS.md`

## Comandos executados pelo agente principal

Baseline:

```bash
git status --short --branch
git log -1 --format='%H %s'
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline
git diff --check
git switch -c codex/telegram-rich-outbound
```

Gates por slice e integração:

```bash
.venv/bin/python -m pytest tests/test_notifications_*.py -q
.venv/bin/python -m pytest tests/test_delivery_v2.py tests/test_telegram_state.py tests/test_telegram.py tests/test_idempotency.py -q
.venv/bin/python -m pytest tests/test_run_notifications.py tests/test_run_modes.py tests/test_idempotency.py tests/test_telegram_state.py tests/test_batch_resume.py tests/test_notifications_*.py tests/test_delivery_v2.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline
```

Preview/smoke e inspeção:

```bash
python  # build_batch + render_rich/render_html para /tmp/ecom-telegram-rich-preview/
jq '{schema_version,status,run_id,batch_id,items:[.items[]|{item_id,kind,order,renderer,status,in_flight,message_id,sent_at,fallback_attempted,last_error_kind}]}' <state-file>
```

Gates finais:

```text
680 passed in 12.32s
compileall_rc=0
run.py --help: exit 0
git diff --check: exit 0
forbidden diff (scoring/config/adapters): vazio
```

## Ativação e rollback

Configuração aprovada para esta release:

```bash
TELEGRAM_RENDERER=html
```

O default de código também é HTML. Não definir `rich` até a duplicação Rich ser reproduzida, explicada e corrigida num chat de teste.

Rollback operacional para novos batches:

1. definir `TELEGRAM_RENDERER=html` no ambiente operacional;
2. executar o mesmo comando normal do pipeline; não alterar o cron;
3. confirmar no novo estado que cada item tem `renderer=html`.

O renderer de um batch já persistido é deliberadamente durável e não muda com a variável de ambiente. Um batch Rich parcial/ambíguo exige resolução manual do estado; não apagar nem editar o ficheiro sem provar primeiro quais os `message_id` aceites, porque isso pode duplicar mensagens.

Rollback de código: fazer `git revert <commit-final-desta-branch>` depois de obter o hash na resposta final; não fazer reset, clean ou alteração de dados históricos.

## Riscos e trabalho aberto

- Rich foi aceite pela API; a duplicação visual foi diagnosticada como incompatibilidade de renderização específica do cliente (mensagens 17/18 únicas num segundo cliente Telegram), sem double-send da aplicação — Rich permanece desativado para a coorte de clientes atual.
- A assinatura real de erro Rich que deve disparar fallback continua por confirmar; o classificador atual falha fechado quando não reconhece a descrição.
- Batches Rich já persistidos não mudam automaticamente para HTML, por desenho de idempotência.
- A identidade de produto continua baseada no hash da keyword e não sobrevive a renomeações; um identity store canónico fica fora desta release.
- Callbacks, long polling, Mini App, fornecedores automáticos, margens, Legal Gate executável e monitorização jurídica continuam fora do âmbito.

## Follow-up — classificador de fallback Rich baseado em evidência (assinatura completa)

Data: 2026-08-19 (slice de reconciliação de documentação; a implementação do classificador já estava no working tree). Sem chamada Telegram live neste slice: a assinatura foi confirmada offline a partir do envelope oficial da Bot API, de evidência de integrações open-source/live-verificadas e de allowlists exatas — ver [`08-rich-fallback-signature.md`](08-rich-fallback-signature.md). O registo histórico da release (680 testes) acima permanece válido para o seu slice; este follow-up documenta a assinatura completa e a reconciliação da documentação.

### O que mudou no working tree

- `pipeline/delivery/telegram.py`: as frases especulativas anteriores (`can't parse rich message`, `cannot parse rich message`, `unsupported rich message`, `rich message is not supported`) foram removidas. O classificador `_is_rich_unsupported` passou a exigir um 4xx definitivo consistente (status HTTP **ou** `error_code` inteiro no corpo; um 4xx do corpo vale mesmo com HTTP 200 + `ok=false`, e um 4xx HTTP sem `error_code` também vale) **e** uma `description` com um dos 13 tokens simbólicos exatos (token inteiro case-insensitive, allowlist em frozenset, nunca uma regex `RICH_MESSAGE_*` ampla) ou igual a uma das 3 descrições completas normalizadas exatas (`Not Found: method not found`, `Bad Request: unknown method`, `Bad Request: rich message must be non-empty`).
- Fail-closed mantido: HTTP >=500 nunca corresponde mesmo com `error_code` 4xx no corpo (e o 5xx já é ambíguo a montante — `state_ambiguous`, marcador `in_flight` durável, nunca faz fallback); um `error_code` inteiro presente e não-4xx nunca corresponde mesmo com status HTTP 4xx; near-matches (ex.: `RICH_MESSAGE_EMAIL_INVALIDATED`), texto que apenas contém uma frase, chat/auth/rate/config, 4xx desconhecido e 429 nunca disparam fallback. A descrição Telegram raw continua fora de estado, logs e report.
- `tests/test_delivery_v2.py`: **65 testes focados** de entrega — positivo token-a-token e por descrição exata, near-match negativo, 5xx com token negativo, HTTP 400 + `error_code` 500 negativo, HTTP 500 + `error_code` 400 ambíguo, texto que apenas contém a frase negativo, 429/4xx desconhecido negativos, ausência da descrição crua do estado/logs/report, e `RICH_MESSAGE_TEXT_TOO_LONG` com HTML acima dos 4096 a falhar fechado sem rede.

### Limitação conhecida documentada (32768 Rich vs 4096 HTML)

`sendRichMessage` aceita 32768 caracteres visíveis e `sendMessage` (HTML) apenas 4096. Um item que caiba no Rich mas exceda os 4096 do HTML falha fechado **sem** tentar a rede: o render do fragmento HTML levanta `NotificationTooLargeError`, o item fica `rejected` com `last_error_kind=render_failed` (report `error`/`render_failed`), `fallback_attempted=false` e o conteúdo **nunca é truncado** — o `payload_hash` durável continua a ser o do fragmento Rich completo. Rich→HTML é tentado no máximo uma vez por item; HTML nunca faz fallback.

### Verificação deste slice

```text
65 passed (tests/test_delivery_v2.py)
708 passed (tests/ completo)
compileall_rc=0
run.py --help: exit 0
git diff --check: exit 0
```

Sem alterações a schema de estado (`schema_version=2`), defaults do renderer (`TELEGRAM_RENDERER=html`), `_send_item_once`, formato de estado, transição de fallback nem `.env`/dados. O risco "assinatura real de erro Rich continua por confirmar" do registo histórico acima está resolvido por este slice; a limitação dos 4096 do HTML fica como risco aberto conhecido. A próxima prioridade é o identity store canónico de produtos (o hash de keyword não sobrevive a renomeações); depois inbound polling/decisões; depois fornecedores/margens e Legal Gate executável em releases separados.

## Follow-up — identity store canónico de produtos (`config/products.json`)

Data: 2026-08-19 (release de reconciliação de documentação sobre a implementação já no working tree). Sem chamadas de rede, Telegram live ou fornecedores neste slice: a verificação é local. O registo histórico acima permanece válido para o seu slice; este follow-up documenta o estado completo do identity store.

### O que mudou no working tree

- `config/products.json` (novo, `schema_version=1`): registo canónico operado manualmente, com cinco produtos. `product_id` imutável, preservado do antigo hash de 12 hex da keyword: `e942e8bf7f30` mochila personalizada, `be385ea0e349` camisola portugal, `a16e2196eb4d` caneca personalizada, `ef5cd634db44` tapete yoga, `258053734570` velas aromáticas. Um `research_keyword` ativo por produto; aliases guardam keywords históricas — camisola portugal consolida camisolas portugal, camisola de portugal e camisola portugal preta. `config/keywords.txt` permanece intacto e deixou de ser lido em runtime.
- `pipeline/products.py` (novo): loader fail-closed — valida schema, chaves exatas, formato de `product_id`, duplicados e colisões de tokens normalizados; nunca escreve ficheiros e nunca vaza conteúdo nos erros.
- `run.py`: o token posicional resolve por `product_id`/nome/`research_keyword`/alias no registo; token desconhecido sai com exit 2 (`unknown product token`) antes de qualquer chamada de fonte/rede; registo ausente ou inválido é erro fatal (fail-closed), nunca um run vazio silencioso. `--all`/`--sunday` usam `registry.active_products()`.
- `pipeline/run_state.py`: `compute_run_id` passou a usar pares ordenados `(product_id, research_keyword)` em vez de keywords cruas — mudar a query ativa cria um run novo, enquanto nome/aliases sozinhos não participam na identidade; o manifesto guarda `products` e mantém `keywords` como legado.
- `pipeline/output/pack.py`: packs novos carregam `product_id`/`product_name`/`research_keyword` (aditivo; `keyword` continua a ser a query real); o segmento de 12 hex do nome do ficheiro passou a ser o `product_id` (fallback para o hash da keyword apenas em chamadores legados).
- `pipeline/notifications/builder.py`: identidade de item usa `product_id` quando presente, com fallback read-only para `stable_product_id(keyword)` apenas em resultados históricos.
- Quarta/mensal: seleção da watchlist por `product_id` (pack com `product_id` explícito ou keyword legada resolvida por alias no registo) — um produto renomeado mantém a watchlist e o baseline do pack anterior; packs órfãos (keyword legada que não resolve) são ignorados sem crash; packs, manifestos e digests históricos são read-only e nunca reescritos; a migração apenas lê artefactos legados.
- Testes: `tests/test_products.py`, `tests/test_product_watchlist_selection.py` e `tests/fixtures/products_helpers.py` (novos); adaptações nos testes de run id/CSV, idempotência, resume de batches, pack metadata, config immutability e cli resolution.

### Identidade de run e histórico

Mudar o `research_keyword` ativo de um produto cria um run novo (nova pesquisa); o `product_id` estável garante que o histórico (packs anteriores, baseline de quarta/mensal) sobrevive à renomeação. A entrega Telegram v2 (idempotência por item, duplicação-safe) não mudou; nenhum artefacto histórico é reescrito.

### Verificação deste slice

```text
755 passed (tests/ completo)
compileall_rc=0
run.py --help: exit 0
git diff --check: exit 0
```

Sem chamadas live Telegram/providers. Token posicional desconhecido confirmado com exit 2 antes de qualquer rede. Sem alterações a `.env`, dados, schema de estado nem `config/keywords.txt`.

### Riscos e trabalho aberto (atualizado)

- Resolvido: a identidade de produto deixou de depender do hash da keyword — o item "A identidade de produto continua baseada no hash da keyword e não sobrevive a renomeações" do registo histórico está fechado por esta release.
- Próxima prioridade: long polling e decisões (callbacks), num spike entre `python-telegram-bot` e uma implementação mínima; depois fornecedores/margens e Legal Gate executável em releases separados.
- Mantidos: token Telegram exposto a rotacionar pelo operador; Rich desativado para a coorte de clientes atual; limitação 4096 do HTML no fallback; itens com entrega ambígua exigem reconciliação manual.
