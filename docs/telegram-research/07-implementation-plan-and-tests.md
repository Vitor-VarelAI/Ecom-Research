# 07 — Plano de implementação, esforço e testes

Razão de existência: transformar a investigação aprovada numa sequência de mudanças pequenas, verificáveis e reversíveis. Este documento não implementa nenhuma delas.

## Princípios

- Scoring, pesos, thresholds e labels não mudam.
- Research, rendering, transporte, decisões e Legal Gate permanecem módulos separados.
- O estado jurídico descreve evidência; o workflow progressivo decide a próxima fase permitida.
- Rich Messages são o renderer principal; HTML é fallback integral.
- Timeout ou resultado ambíguo nunca dispara um segundo envio.
- URLs, media e documentos sem evidência são omitidos.
- A Mini App fica fora deste plano.

## Dependências

```text
Fase 0 — contratos e spike
  ├─ Fase 1 — modelo + renderers
  └─ Fase 2 — entrega multi-mensagem
       └─ Fase 3 — long polling + decisões

Fase 1 ── Fase 4 — fornecedores + margem + gate progressivo
Fase 1 ── Fase 5 — Legal Gate + monitorização

Fases 2–5 ── Fase 6 — endurecimento e rollout
```

## Fase 0 — contratos e spike

Objetivo: remover incerteza de integração antes de alterar o caminho de produção.

- Criar fixtures exatas para `sendRichMessage`, `editMessageText` e fallback `sendMessage`.
- Fazer um spike isolado de `python-telegram-bot` para long polling; comparar com `requests` puro sem reescrever o outbound.
- Confirmar renderização no Android, iOS e Desktop usados pelo operador.
- Definir JSON Schema e estado de entrega v2.
- Definir `product_id` canónico e migração explícita dos produtos atuais.
- Fixar a semântica de `Aprovar`: aprova apenas a próxima fase mostrada.

Gate: payloads aceites num chat de teste, sem dados de produção; decisão sobre biblioteca inbound registada.

Esforço: 1–2 dias-agente.

## Fase 1 — modelo e renderers

Novos módulos propostos:

```text
pipeline/notifications/model.py
pipeline/notifications/render_rich.py
pipeline/notifications/render_html.py
pipeline/notifications/buttons.py
pipeline/notifications/placeholders.py
pipeline/notifications/url_policy.py
```

Responsabilidades:

- construir `WeeklySummary` e `ProductNotification` a partir dos resultados existentes;
- mapear `null` para o placeholder correto por campo;
- escapar HTML normal e Rich HTML;
- gerar hashtags estáveis;
- limitar teclados a dois botões por linha;
- omitir URL/media inválidos;
- renderizar as quatro fases progressivas sem alterar o Legal Gate;
- manter o texto completo para stdout/auditoria.

Não acrescentar estas responsabilidades a `pack.generate_digest_line`; packs e notificações têm consumidores diferentes.

Gate: golden tests dos cinco mockups e igualdade semântica entre Rich e fallback.

Esforço: 2–3 dias-agente.

## Fase 2 — entrega multi-mensagem

Alterações:

- `pipeline/delivery/telegram.py`: introduzir `delivery_item`, métodos Rich/HTML, captura de `message_id`, edição e fallback por rejeição definitiva.
- `pipeline/run_state.py`: validar manifesto v2 e estado write-ahead por item.
- `run.py`: produzir resumo + uma mensagem por produto; retomar pelo primeiro item não confirmado.
- manter um digest textual completo para stdout e incidente operacional.

Estado por item:

```text
PENDING → IN_FLIGHT → SENT
                   ├→ DEFINITE_REJECTION → fallback/retry permitido
                   └→ AMBIGUOUS → bloqueio operacional
```

Não manter indefinidamente os schemas v1/v2 em paralelo. Fazer uma migração/cutover explícito e conservar apenas o leitor necessário para os artefactos históricos que o produto ainda utiliza.

Gate: resume sem duplicar, `message_id` só depois de `ok=true`, estado ambíguo continua fail-closed.

Esforço: 2–3 dias-agente.

## Fase 3 — long polling e decisões

Novos componentes:

```text
pipeline/decisions/model.py
pipeline/decisions/store.py
pipeline/decisions/handlers.py
telegram_poller.py
```

Operação:

- serviço persistente systemd no host atual;
- uma única instância com lock;
- `getUpdates`, offset durável e backoff;
- apenas `TELEGRAM_AUTHORIZED_USER_ID`;
- tokens callback opacos e curtos;
- eventos append-only e projeção por produto/semana/mercado;
- edição Rich ou HTML depois da persistência;
- reconciliação de edições falhadas.

Gate: reiniciar o poller entre receção e edição não perde nem duplica decisão.

Esforço: 2–4 dias-agente.

## Fase 4 — fornecedores, margem e workflow progressivo

Primeiro release: dados manuais/validados, sem automação frágil de marketplaces.

- introduzir schema de fornecedor com `DIRECT_PRODUCT`, `CATEGORY` e `SEARCH`;
- validar HTTPS, domínio, redirecionamentos e ausência de credenciais;
- guardar shipping independente para PT, GB e US;
- implementar ramos fiscais configuráveis sem hardcode de IVA/IOSS/direitos;
- marcar cenários como `INCOMPLETO` quando faltar um input material;
- separar pesquisa, teste sem venda, venda teste e escala;
- tornar limiares de margem/prazo/CAC configuração explícita do operador.

Automação de BigBuy/CJ/AliExpress/Alibaba fica numa etapa posterior e só avança com API/termos adequados; scraping público bloqueado ou autenticado não é base de produção.

Gate: nenhum candidato ou URL de pesquisa satisfaz o gate de primeira venda.

Esforço: 4–7 dias-agente para schema, cálculo e entrada manual; integrações autenticadas futuras não incluídas.

## Fase 5 — Legal Gate e monitorização

Componentes propostos:

```text
pipeline/legal/model.py
pipeline/legal/rules.py
pipeline/legal/evidence.py
pipeline/legal/monitor.py
pipeline/legal/impact.py
```

Etapa 5A — gate manual:

- regras por categoria/material/uso/mercado;
- `NÃO APLICÁVEL` com justificação;
- precedência dos estados permitidos;
- evidência oficial e documentos em falta;
- workflow progressivo por fase.

Etapa 5B — monitorização:

- source registry allowlisted;
- snapshot/hash/ETag e datas jurídicas;
- diff semântico;
- mapeamento de impacto a produtos;
- revisão humana de alterações materiais;
- alerta e edição Telegram.

Gate: mudança visual de página não altera estado; uma mudança jurídica conserva fonte, diff, estado anterior e data de aplicação.

Esforço: 5–9 dias-agente. A manutenção jurídica contínua fica fora da estimativa inicial.

## Fase 6 — endurecimento e rollout

1. Rodar suite completa sem rede.
2. Gerar mensagens dos cinco produtos em dry-run.
3. Enviar para chat de teste, não produção.
4. Validar clientes usados pelo operador.
5. Ativar Rich só para o resumo semanal.
6. Ativar produtos sem callbacks.
7. Ativar poller e um único botão de decisão.
8. Ativar teclado completo.
9. Verificar o cron seguinte e reconciliação de estado.
10. Documentar rollback para modo HTML/send-only.

Esforço: 2–3 dias-agente.

## Estimativa

| Escopo | Dias-agente |
|---|---:|
| Telegram Rich + fallback + multi-mensagem | 5–8 |
| Long polling e decisões | 2–4 |
| Fornecedores/margem com entrada validada | 4–7 |
| Legal Gate e monitorização | 5–9 |
| Endurecimento/rollout | 2–3 |
| **Total sem Mini App** | **18–31** |

Com workstreams independentes em paralelo: aproximadamente 12–20 dias decorridos. A estimativa de 7–10 dias cobre apenas o núcleo Telegram, não todo o sistema comercial/jurídico.

## Testes unitários

### Renderers

- escaping de `<`, `>`, `&` e texto fornecido externamente;
- Rich HTML com headings, table, divider e details;
- fallback sem tags Rich;
- limites de 32 768 e 4096 caracteres;
- máximo de dois botões por linha;
- callback em bytes, não caracteres;
- media e URLs inválidos omitidos;
- placeholders por tipo de ausência;
- cinco golden mockups.

### Workflow progressivo

- `DOCUMENTAÇÃO EM FALTA` permite pesquisa quando a lacuna não é material;
- o mesmo estado bloqueia venda quando o documento é obrigatório;
- `NÃO APLICÁVEL` não conta como documento em falta;
- CE não é exigido sem categoria aplicável;
- IP impede publicidade, sem afirmar marca/licença não pesquisada;
- decisão humana não muda score nem Legal Gate.

### Margem

- preço gross/net e recuperabilidade do IVA;
- IOSS e importação são ramos mutuamente consistentes;
- direitos exigem HS/CN, origem e destino;
- taxa fixa e percentual do pagamento;
- custo esperado de devoluções;
- CAC break-even igual à contribuição antes de anúncios;
- cenário incompleto perante input material ausente.

## Testes de integração

- payload HTTP exato por método;
- rejeição Rich definitiva aciona fallback;
- timeout Rich não envia fallback;
- write-ahead antes da rede;
- resume entre mensagens sem duplicação;
- manifesto guarda `message_id` e renderer;
- decisão persistida antes da edição;
- edição falhada não reverte histórico;
- autorização por `user_id` e chat;
- deduplicação de `update_id` e `callback_query.id`;
- offset sobrevive a restart;
- webhook e polling não coexistem.

## Testes de monitorização

- conteúdo jurídico igual com HTML diferente não gera alerta;
- alteração de artigo/data gera evento;
- 404/redirecionamento gera incidente de fonte;
- Safety Gate sem resultados permanece não conclusivo;
- impacto só atinge categorias/mercados correspondentes;
- estado anterior e fonte ficam preservados;
- alerta inclui país e data de aplicação.

## Segurança e regressão

- tokens e chat IDs nunca entram em logs/estado;
- callback forjado ou de utilizador não autorizado é rejeitado;
- token opaco expirado não executa ação;
- URL com esquema não HTTPS, credenciais ou host inválido é rejeitado;
- nenhuma chamada paga é repetida por reentrega;
- todos os testes existentes permanecem verdes;
- smoke real apenas com credenciais e chat de teste separados.

## Critério de produção

Produção exige suite completa, dry-run dos cinco produtos, smoke Rich/fallback, reinício real do poller, decisão repetida idempotente, reconciliação de uma edição falhada simulada e rollback testado para HTML send-only.

## Verificação

```bash
grep -c '^## Fase' docs/telegram-research/07-implementation-plan-and-tests.md
```
