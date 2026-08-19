# 08 — Assinatura de rejeição Rich: classificador baseado em evidência

Razão de existência: registar a base de evidência e as decisões do classificador
estreito que decide quando uma rejeição definitiva de `sendRichMessage` dispara
exatamente uma tentativa HTML. Substitui a correspondência especulativa por
frases de linguagem natural por uma allowlist exata de tokens simbólicos.

## Evidência

- A Bot API expõe `error_code` inteiro e `description` legível; **não existe**
  campo estruturado oficial de erro — o classificador só pode usar `error_code`
  (ou status HTTP) + texto.
- Tokens de entidade (verificados em integrações live / open-source):
  `RICH_MESSAGE_EMAIL_INVALID`, `RICH_MESSAGE_URL_INVALID`,
  `RICH_MESSAGE_MENTION_INVALID`, `RICH_MESSAGE_HASHTAG_INVALID`,
  `RICH_MESSAGE_CASHTAG_INVALID`, `RICH_MESSAGE_BOT_COMMAND_INVALID`,
  `RICH_MESSAGE_PHONE_INVALID`, `RICH_MESSAGE_BANK_CARD_INVALID`.
- Rejeições estruturais (verificadas live):
  `RICH_MESSAGE_BLOCKS_TOO_MANY`, `RICH_MESSAGE_DEPTH_INVALID`,
  `RICH_MESSAGE_TEXT_TOO_LONG`, `RICH_MESSAGE_MEDIA_TOO_MANY`,
  `RICH_MESSAGE_TABLE_COLS_TOO_MANY`.
- Descrições completas de rejeição de capacidade (verificadas):
  `Not Found: method not found`, `Bad Request: unknown method`.
- Rejeição de formato observada em relatos do ecossistema (descrição completa):
  `Bad Request: rich message must be non-empty`.

Fontes:

- [Telegram Bot API — Making requests](https://core.telegram.org/bots/api#making-requests) — envelope oficial `ok`, `error_code` e `description`.
- [OpenClaw — política de fallback Rich/plain](https://github.com/openclaw/openclaw/blob/5e477aff/extensions/telegram/src/rich-plain-fallback.ts) e [testes de rejeições estruturais](https://github.com/openclaw/openclaw/blob/5e477aff/extensions/telegram/src/rich-plain-fallback.test.ts) — tokens de entidade e limites verificados contra Bot API 10.2.
- [OpenClaw PR #96642](https://github.com/openclaw/openclaw/pull/96642) — rejeições `RICH_MESSAGE_*_INVALID` observadas na integração.
- [Nanobot PR #4423](https://github.com/HKUDS/nanobot/pull/4423) — separação entre `method not found`/`unknown method` e erros de chat.
- [AI-Employee PR #241](https://github.com/greeves89/AI-Employee/pull/241) — ocorrência de `rich message must be non-empty`.

Os nomes simbólicos não fazem parte de um enum oficial publicado pela Bot API; são evidência operacional de integrações. Por isso, entram por allowlist exata e não por uma regex aberta.

## Classificador (`_is_rich_unsupported`)

Dispara o fallback apenas quando **ambas** as condições se verificam:

1. Rejeição definitiva 4xx consistente — `400 <= status HTTP < 500` **ou**
   `400 <= error_code < 500` no corpo JSON (a 4xx do corpo vale mesmo quando o
   HTTP é 200 com `ok=false`; e um 4xx HTTP sem `error_code` no corpo também
   vale). O classificador falha fechado em respostas inconsistentes:
   - HTTP >=500 **nunca** corresponde, mesmo que o corpo diga `error_code` 4xx
     (e o 5xx já é ambíguo a montante — `state_ambiguous`, marcador
     `in_flight` durável, nunca faz fallback);
   - um `error_code` inteiro presente no corpo que **não** seja 4xx **nunca**
     corresponde, mesmo quando o status HTTP é 4xx.
2. A `description` contém **ou**:
   - um token simbólico exato da allowlist, como token inteiro e
     case-insensitive (`[A-Za-z0-9_]+`); um near-match como
     `RICH_MESSAGE_EMAIL_INVALIDATED` **nunca** corresponde a
     `RICH_MESSAGE_EMAIL_INVALID`; **ou**
   - uma descrição completa exata normalizada — a descrição inteira,
     normalizada (case-insensitive, whitespace circundante removido), tem de
     ser igual a uma das descrições com evidência (`Not Found: method not
     found`, `Bad Request: unknown method`, `Bad Request: rich message must
     be non-empty`). Texto arbitrário que apenas *contenha* uma frase (ex.:
     `Not Found: method not found: extra`) **nunca** corresponde.

## Limites (nunca fazem fallback)

- Chat/auth/rate/config e texto 4xx desconhecido: nunca.
- Tokens near-match (ex.: `..._INVALIDATED`): nunca.
- Descrições que apenas contêm uma frase com evidência (ex.: com detalhe
  extra): nunca — a correspondência é por descrição completa exata
  normalizada, não por substring.
- HTTP >=500 ou `error_code` >=500 contendo um token reconhecido: nunca —
  um 5xx é ambíguo (o remoto pode ter aceite) e mantém o marcador `in_flight`
  durável (fail closed `state_ambiguous`).
- Respostas inconsistentes: HTTP 400 com `error_code` 500 no corpo nunca
  corresponde (o `error_code` inteiro presente e não-4xx falha fechado); HTTP
  500 com `error_code` 400 fica `state_ambiguous` pela lógica a montante.
- HTML nunca faz fallback; cada item Rich faz no máximo uma tentativa HTML.

## Desfasamento de limites Rich (32768) vs HTML (4096)

A Bot API limita `sendRichMessage` a 32768 caracteres visíveis e `sendMessage`
(texto) a 4096, ambos após o parsing de entidades. O mesmo conteúdo pode
caber no Rich e exceder o limite do HTML de fallback: uma rejeição
`RICH_MESSAGE_TEXT_TOO_LONG` cujo item já não caiba nos 4096 do HTML falha
fechado **sem** tentar a rede — o render do fragmento HTML levanta
`NotificationTooLargeError`, o item fica `rejected` com `last_error_kind =
render_failed` (report `error`/`render_failed`), `fallback_attempted` fica
`False` e o conteúdo **nunca é truncado**: o `payload_hash` durável continua a
ser o do fragmento Rich completo. A lacuna é documentada como limitação
conhecida: um item demasiado grande para o fallback não tem rota de entrega
HTML.

## Decisão

- Removidas as frases especulativas anteriores (`can't parse rich message`,
  `cannot parse rich message`, `unsupported rich message`,
  `rich message is not supported`): sem evidência live, não fazem parte do
  classificador e qualquer rejeição com esse texto volta a ser `http_error`
  sem fallback.
- Mantida a allowlist exata (frozenset de tokens), nunca uma regex
  `RICH_MESSAGE_*` ampla.
- Correspondência de frases por substring substituída por correspondência
  exata da descrição completa normalizada (frozenset de descrições):
  `Not Found: method not found`, `Bad Request: unknown method`,
  `Bad Request: rich message must be non-empty`.
- `_send_item_once`, schema v2, formato de estado, transição de fallback e
  defaults do renderer não mudaram.

## Verificação

```bash
.venv/bin/python -m pytest tests/test_delivery_v2.py -q
```

Resultado esperado: 65 passed (positivos token-a-token e por descrição
completa exata, near-match negativo, 5xx com token negativo, HTTP 400 +
`error_code` 500 negativo, HTTP 500 + `error_code` 400 ambíguo, texto que
apenas contém a frase negativo, 429/4xx desconhecido negativos, ausência da
descrição crua do estado/logs/report, `RICH_MESSAGE_TEXT_TOO_LONG` com HTML
acima dos 4096 falha fechado sem rede).
