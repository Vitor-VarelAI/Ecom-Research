# Investigação das notificações Telegram

Razão de existência: concentrar o diagnóstico, evidência oficial, mockups e plano de implementação da evolução das notificações semanais, sem alterar o pipeline em produção.

## Documentos

- [`DECISIONS.md`](DECISIONS.md) — decisões confirmadas pelo operador.
- [`01-current-system-and-bot-api.md`](01-current-system-and-bot-api.md) — matriz oficial da Bot API.
- [`01b-current-system-diagnosis.md`](01b-current-system-diagnosis.md) — integração atual e blast radius.
- [`02-options-compatibility-recommendation.md`](02-options-compatibility-recommendation.md) — comparação A/B/C, compatibilidade e recomendação.
- [`03-mockups-and-interactions.md`](03-mockups-and-interactions.md) — mensagem inicial, cinco produtos e botões.
- [`03b-telegram-payloads.md`](03b-telegram-payloads.md) — payloads Rich, edição e fallback HTML.
- [`04-data-callbacks-and-persistence.md`](04-data-callbacks-and-persistence.md) — long polling, callbacks, idempotência e edição.
- [`product-schema.example.json`](product-schema.example.json) — estrutura JSON completa por produto.
- [`05-suppliers-and-margins.md`](05-suppliers-and-margins.md) — fornecedores públicos e modelo financeiro.
- [`06-legal-gate-and-monitoring.md`](06-legal-gate-and-monitoring.md) — Legal Gate UE/Portugal e monitorização semanal.
- [`07-implementation-plan-and-tests.md`](07-implementation-plan-and-tests.md) — impacto no código, esforço, etapas e testes.
- [`REPORT.md`](REPORT.md) — consolidação dos 22 entregáveis.

## Regras de evidência

- Capacidades Telegram são confirmadas apenas através de documentação oficial da Telegram.
- Conclusões jurídicas usam fontes oficiais da UE ou de Portugal como fontes primárias.
- Uma URL de pesquisa nunca é apresentada como página de produto confirmada.
- Valores desconhecidos permanecem `NÃO ENCONTRADO`, `POR VALIDAR`, `DOCUMENTAÇÃO EM FALTA` ou `PESQUISA NECESSÁRIA`.
- Nenhum exemplo é enviado para o Telegram durante esta investigação.

## Verificação

```bash
test -f docs/telegram-research/DECISIONS.md && grep -c '^-' docs/telegram-research/README.md
```
