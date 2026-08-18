# 04 — Dados, callbacks e persistência

Razão de existência: definir como mensagens, decisões e alterações posteriores permanecem idempotentes e auditáveis.

## Estrutura de produto

O exemplo completo está em [`product-schema.example.json`](product-schema.example.json). `null` significa desconhecido; o renderer converte esse valor para `NÃO ENCONTRADO`, `POR VALIDAR`, `DOCUMENTAÇÃO EM FALTA` ou `PESQUISA NECESSÁRIA` conforme o campo.

As dimensões não podem ser fundidas:

- `commercial_status.advisory_signal`: resultado do scoring existente;
- `validation_stage.workflow_gate`: próxima ação permitida — pesquisa, teste sem venda, venda teste ou escala;
- `dropshipping_status`: fornecedor, MOQ, shipping e margem;
- `legal_gate.status`: verdade documental da triagem UE/Portugal;
- `final_status`: decisão aplicável à fase atual;
- `user_decision`: escolha do operador, sem poder sobrepor evidência legal.

Um Legal Gate com `DOCUMENTAÇÃO EM FALTA` pode permitir `RESEARCH_ALLOWED` quando os documentos ainda não são materiais para pesquisa de fornecedor. O mesmo estado bloqueia `SALE_ALLOWED` se a documentação for obrigatória antes da primeira venda.

## Identidade

- `product_id`: identificador canónico e estável; a implementação pode iniciar com o hash já produzido por `_keyword_hash`, mas deve persistir uma identidade canónica para sobreviver a renomeações.
- `research_date`: semana do relatório.
- `market`: mercado comercial da decisão.
- chave de projeção: `(product_id, research_date, market)`.
- chave do Legal Gate: `(product_id, research_date, EU_PORTUGAL)`.

## Estado de entrega

Cada resumo ou produto é um item independente:

```json
{
  "delivery_item_id": "<opaque-id>",
  "run_id": "<existing-run-id>",
  "kind": "PRODUCT",
  "product_id": "e942e8bf7f30",
  "research_date": "2026-08-16",
  "render_mode": "RICH",
  "payload_hash": "<sha256>",
  "status": "PENDING",
  "in_flight": false,
  "chat_id": null,
  "message_id": null,
  "sent_at": null,
  "revision": 0,
  "last_error_kind": null
}
```

O protocolo atual generaliza-se de chunk para item:

1. persistir todos os itens `PENDING`;
2. marcar o próximo item `in_flight=true` e fazer fsync;
3. chamar a Bot API;
4. guardar `message_id`, método, modo e `SENT` após resposta `ok=true`;
5. perante timeout, 5xx ou resposta ilegível, conservar `in_flight` e bloquear reenvio;
6. perante rejeição definitiva, limpar `in_flight` e permitir fallback ou correção;
7. retomar pelo primeiro item ainda não confirmado.

## `callback_data`

O limite oficial é 1–64 bytes. Usar um token opaco em vez de serializar produto, semana e ação:

```text
v1:t:7K3M9Q2P
```

Mapeamento persistido:

```json
{
  "token": "7K3M9Q2P",
  "product_id": "e942e8bf7f30",
  "research_date": "2026-08-16",
  "market": "PT",
  "action": "OBSERVE",
  "message_id": null,
  "expires_at": null,
  "consumed_at": null
}
```

O token não é autorização. O poller valida sempre `from.id`, `chat.id`, mensagem, produto, semana, mercado e ação.

## Runtime de long polling

A decisão confirmada é um serviço persistente no host atual:

```text
cron de research ── envia resumo/produtos
telegram-poller ── getUpdates → valida → persiste → edita
```

Regras:

- apenas uma instância ativa, protegida por lock;
- nenhum webhook configurado simultaneamente;
- `getUpdates` com timeout longo e backoff com jitter;
- offset persistido duravelmente;
- apenas o `TELEGRAM_AUTHORIZED_USER_ID` configurado pode decidir;
- `answerCallbackQuery` é enviado rapidamente, sem prometer sucesso antes da persistência;
- segredos não entram em logs, estado ou mensagens de erro;
- updates não reconhecidos são ignorados e registados de forma sanitizada.

## Transação de decisão

1. Receber `Update`.
2. Deduplicar por `update_id` e `callback_query.id`.
3. Validar utilizador, chat e token.
4. Ler a revisão atual sob lock.
5. Se a mesma ação já for atual, responder como no-op.
6. Acrescentar evento append-only ao histórico.
7. Atualizar a projeção e incrementar `revision`.
8. Persistir antes de editar a mensagem.
9. Chamar `editMessageText` com `rich_message` ou texto HTML, e/ou `editMessageReplyMarkup`.
10. Guardar o resultado da edição.
11. Avançar o offset depois de o evento estar seguro.

Uma decisão diferente substitui apenas a projeção atual; o evento anterior permanece no histórico.

## Evento de decisão

```json
{
  "event_id": "<uuid>",
  "update_id": 0,
  "callback_query_id": "<telegram-callback-id>",
  "product_id": "e942e8bf7f30",
  "research_date": "2026-08-16",
  "market": "PT",
  "action": "OBSERVE",
  "actor": {
    "telegram_user_id": "<authorized-user-id>",
    "display_name_at_event": "<telegram-display-name>"
  },
  "previous_revision": 0,
  "new_revision": 1,
  "created_at": "<utc-timestamp>",
  "origin": "TELEGRAM_CALLBACK"
}
```

## Edição da mensagem

A mensagem mostra:

```text
DECISÃO DO OPERADOR
OBSERVAR
Por: <nome do decisor autorizado>
Em: <data/hora>
```

A edição nunca altera score, estado legal ou evidência. Se a edição falhar, a decisão continua válida e surge como `MESSAGE_UPDATE_FAILED`, permitindo reconciliação posterior pelo `message_id`.

## Alterações legais posteriores

Quando uma regra oficial alterar um produto:

1. criar um novo evento legal com fonte e data;
2. recalcular apenas o Legal Gate e decisão final;
3. manter o snapshot anterior;
4. editar a mensagem se houver `message_id` válido;
5. enviar alerta separado com países e data de aplicação;
6. não alterar a decisão humana sem novo callback.

## Mini App

A Mini App reutiliza os mesmos IDs e eventos, mas fica fora desta implementação. O bot continua responsável por notificações e decisões rápidas; pesquisa, comparação e gestão documental migram para a Mini App quando existir backend autenticado e histórico suficiente.

## Verificação

```bash
python -m json.tool docs/telegram-research/product-schema.example.json >/dev/null
```
