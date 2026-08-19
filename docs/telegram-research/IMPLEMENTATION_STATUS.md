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

- Rich foi aceite pela API mas duplicou visualmente cada item no smoke; investigar o endpoint/cliente antes de ativar.
- A assinatura real de erro Rich que deve disparar fallback continua por confirmar; o classificador atual falha fechado quando não reconhece a descrição.
- Batches Rich já persistidos não mudam automaticamente para HTML, por desenho de idempotência.
- A identidade de produto continua baseada no hash da keyword e não sobrevive a renomeações; um identity store canónico fica fora desta release.
- Callbacks, long polling, Mini App, fornecedores automáticos, margens, Legal Gate executável e monitorização jurídica continuam fora do âmbito.
