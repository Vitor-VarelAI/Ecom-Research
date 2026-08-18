# Relatório — evolução do research semanal no Telegram

Razão de existência: consolidar os 22 entregáveis da investigação e fixar a decisão técnica antes de qualquer implementação.

**Recomendação:** implementar `sendRichMessage` da Bot API 10.2, com uma mensagem inicial, uma mensagem por produto e fallback completo para `sendMessage` com HTML. Usar long polling para decisões de um único operador. Manter o Legal Gate progressivo: documentos podem bloquear venda sem bloquear pesquisa de fornecedor.

Nenhum código, scoring, configuração, pack ou dado de produção foi alterado.

## 1. Diagnóstico do formato atual

O digest atual concatena todos os produtos numa string. A informação está correta, mas a unidade de entrega impede pesquisa por produto, botões contextuais, edição isolada e histórico de decisões. A solução é transformar resumo e produtos em itens de entrega independentes, preservando um texto integral para stdout.

Detalhe: [`01b-current-system-diagnosis.md`](01b-current-system-diagnosis.md).

## 2. Integração Telegram existente

- Python com `requests` direto; sem framework Telegram.
- `/sendMessage` com `chat_id`, `text` e `disable_web_page_preview`.
- Sem `parse_mode`, teclado, callbacks, webhook ou polling.
- Chunks de 4000 caracteres.
- Estado write-ahead, resume, bloqueio de ambiguidade e sanitização de segredos.
- Cron `run.py --auto --digest`.

Referências: `pipeline/delivery/telegram.py:263,360-372`, `run.py:151-159,601-618` e `pipeline/output/pack.py:624-648`.

## 3. Comparação A, B e C

| Opção | Veredicto |
|---|---|
| A — HTML | Excelente fallback; não tem tabelas/details Rich |
| B — Rich Messages | Implementar agora; melhor leitura com pouca mudança no outbound |
| C — Mini App | Adiar até existir histórico, backend e dados suficientes |

Detalhe: [`02-options-compatibility-recommendation.md`](02-options-compatibility-recommendation.md).

## 4. Recomendação técnica

Usar `InputRichMessage.html` em vez de construir manualmente árvores `InputRichBlock`. Limitar cada produto a headings, uma tabela curta, um `details`, hashtags e teclado. Media só entra quando houver fornecedor e direitos validados.

## 5. Compatibilidade

Bot API 10.1 introduziu Rich Messages e `sendRichMessage` em 11/06/2026; 10.2 acrescentou blocos/media em 14/07/2026. A Telegram não publica uma matriz de versões mínimas por cliente e o bot não recebe a versão da aplicação. O modo é configurável `rich|html`, validado no dispositivo real. Timeout/5xx não pode gerar fallback automático porque pode duplicar uma mensagem aceite remotamente.

Detalhe oficial: [`01-current-system-and-bot-api.md`](01-current-system-and-bot-api.md).

## 6. Mockup da mensagem inicial

O resumo mostra 5 produtos, 2 `AVANÇAR` advisory, 3 `OBSERVAR`, 4 autorizados a prosseguir para pesquisa, 1 a exigir triagem de IP antes da publicidade e 0 aptos para venda teste. Mostra alterações legais UE/PT, mercados comerciais PT/GB/US e última atualização. O botão de relatório fica oculto porque os packs são locais.

Mockup: [`03-mockups-and-interactions.md`](03-mockups-and-interactions.md).

## 7. Uma mensagem por produto

O documento contém mockups completos para mochila, camisola, caneca, tapete e velas. Não existem imagens, URLs de prova, margens ou conformidade confirmadas. Mochila, caneca, tapete e velas avançam para pesquisa; camisola exige triagem IP antes da publicidade.

## 8. Estrutura dos botões

- máximo de dois por linha;
- URL só quando HTTPS e validado;
- páginas de pesquisa identificadas como `PESQUISAR FORNECEDOR`;
- decisão: Aprovar, Observar, Descartar, Guardar, Pedir validação legal e Pedir novo fornecedor;
- `Aprovar` refere-se apenas à próxima fase mostrada;
- callback usa token opaco inferior a 64 bytes.

## 9. Fluxo callback

Long polling persistente, uma instância, offset durável e um `TELEGRAM_AUTHORIZED_USER_ID`. Deduplicação por `update_id` e `callback_query.id`; evento append-only persistido antes de editar a mensagem. Falha de edição não reverte a decisão.

Detalhe: [`04-data-callbacks-and-persistence.md`](04-data-callbacks-and-persistence.md).

## 10. Estrutura JSON

O schema cobre todos os campos pedidos e acrescenta `validation_stage`, `image_provenance` e estado Telegram. `null` é desconhecido; renderers aplicam placeholders. O estado jurídico e o workflow progressivo são independentes.

Exemplo válido: [`product-schema.example.json`](product-schema.example.json).

## 11. Payload Bot API

Existem exemplos exatos de:

- `sendRichMessage` para resumo;
- `sendRichMessage` por produto;
- `editMessageText` com `rich_message`;
- inline keyboard com tokens opacos.

Payloads: [`03b-telegram-payloads.md`](03b-telegram-payloads.md).

## 12. Fallback `sendMessage`

O fallback usa HTML normal, `link_preview_options.is_disabled`, texto achatado e o mesmo teclado. Não tenta usar `table`, `details`, collage ou slideshow no parse mode HTML normal.

## 13. Alterações necessárias no código

- `run.py`: produzir e entregar N itens.
- `pipeline/delivery/telegram.py`: Rich/fallback, estado por mensagem, `message_id` e edição.
- `pipeline/run_state.py`: manifesto v2.
- novos `pipeline/notifications/`: modelo, renderers, botões e URL policy.
- novos `pipeline/decisions/` e `telegram_poller.py`.
- novos `pipeline/legal/`.
- schema de fornecedores/margens separado.
- novos testes sem alterar o scoring.

Plano: [`07-implementation-plan-and-tests.md`](07-implementation-plan-and-tests.md).

## 14. Análise de fornecedores

Foram encontradas páginas individuais candidatas no AliExpress para mochila, caneca, tapete e velas; continuam `CANDIDATE`, não fornecedores confirmados. BigBuy/Gelato bloquearam acesso público, CJ exige autenticação, Alibaba depende de JavaScript e Printful oferece categorias candidatas. Shipping PT/GB/US, preço, MOQ, stock, devoluções e documentos permanecem por validar.

Detalhe e URLs: [`05-suppliers-and-margins.md`](05-suppliers-and-margins.md).

## 15. Modelo de margem

O modelo separa receita líquida, IVA de venda, IVA recuperável, importação/IOSS, HS/CN, shipping, personalização, fulfilment, pagamentos, devoluções e publicidade. `CACBreakEven = ContributionBeforeAds`. CPC só permite estimar CAC quando existe taxa de conversão explícita. Cenários incompletos não produzem margem.

Não foram inventados preços nem thresholds comerciais.

## 16. Arquitetura do Legal Gate

Legal Gate UE/PT progressivo:

1. descoberta;
2. teste sem venda;
3. venda teste;
4. escala.

Uma lacuna só bloqueia a fase em que é material. EPR/packaging aplicáveis continuam obrigatórios antes da primeira venda; CE só quando a categoria o exige; Safety Gate sem resultados não aprova nem bloqueia sozinho. Camisola requer triagem IP; os outros quatro podem prosseguir para pesquisa.

Detalhe: [`06-legal-gate-and-monitoring.md`](06-legal-gate-and-monitoring.md).

## 17. Monitorização legal semanal

Source registry oficial → snapshot datado → hash/diff semântico → mapeamento por produto/país → revisão humana → novo estado → edição Telegram e alerta separado. Mudança visual, 404 ou redirecionamento é incidente de fonte, não mudança jurídica automática.

## 18. Fontes oficiais utilizadas

Telegram:

- <https://core.telegram.org/bots/api>
- <https://core.telegram.org/bots/api-changelog>
- <https://core.telegram.org/bots/features#rich-messages>

UE/Portugal:

- PPWR: <https://eur-lex.europa.eu/eli/reg/2025/40/oj>
- GPSR: <https://eur-lex.europa.eu/eli/reg/2023/988/oj>
- Market surveillance: <https://eur-lex.europa.eu/eli/reg/2019/1020/oj>
- REACH: <https://eur-lex.europa.eu/eli/reg/2006/1907/oj>
- CLP: <https://eur-lex.europa.eu/eli/reg/2024/2865/oj>
- Contacto alimentar: <https://eur-lex.europa.eu/eli/reg/2004/1935/oj>
- Cerâmica: <https://eur-lex.europa.eu/eli/dir/1984/500/oj>
- Têxteis: <https://eur-lex.europa.eu/eli/reg/2011/1007/oj>
- APA: <https://apambiente.pt/residuos/embalagens-e-residuos-de-embalagens>
- DL 152-D/2017: <https://diariodarepublica.pt/dr/legislacao-consolidada/decreto-lei/2017-114350681>
- Safety Gate: <https://ec.europa.eu/safety-gate-alerts/screen/home>
- INPI: <https://inpi.justica.gov.pt>
- EUIPO: <https://euipo.europa.eu>

## 19. Riscos e limitações

- clientes antigos sem matriz oficial de compatibilidade;
- impossibilidade de exactly-once remoto perante timeout;
- estado ambíguo atual não tem resolução operacional automática;
- poller passa a ser um serviço crítico;
- marketplaces dificultam validação pública de preços e shipping;
- imagens de fornecedor exigem direito de utilização;
- Legal Gate depende de composição e documentação ainda inexistentes;
- monitorização oficial produz falsos alertas sem diff semântico;
- âmbito legal GB/US não está coberto nesta fase;
- a investigação não constitui parecer jurídico.

## 20. Estimativa de esforço

- núcleo Telegram Rich/multi-mensagem: 5–8 dias-agente;
- decisões/long polling: 2–4;
- fornecedores/margem: 4–7;
- Legal Gate/monitorização: 5–9;
- rollout: 2–3.

Total sem Mini App: **18–31 dias-agente**, aproximadamente **12–20 dias decorridos** com paralelização.

## 21. Plano por etapas

0. contratos e spike;
1. modelo e renderers;
2. entrega multi-mensagem;
3. long polling e decisões;
4. fornecedores, margem e workflow progressivo;
5. Legal Gate e monitorização;
6. endurecimento e rollout gradual.

Cada fase tem gate próprio no documento 07. A ordem evita criar Mini App ou automação legal antes de validar o modelo básico.

## 22. Testes antes de produção

- fixtures oficiais Rich/fallback;
- escaping e limites;
- golden tests dos cinco produtos;
- write-ahead, resume e ambiguidade;
- callbacks autorizados, deduplicação e reinício;
- decisão persistida antes da edição;
- workflow progressivo e regras aplicáveis;
- fórmulas fiscais/margem por ramo;
- diff legal e impacto;
- segurança de tokens/URLs;
- regressão da suite existente;
- smoke em chat e clientes reais;
- rollback testado para HTML send-only.

## Estado final da investigação

**READY para planeamento de implementação.** A primeira implementação recomendada termina após as fases 0–3: Rich Messages, fallback, multi-mensagem e decisões. Fornecedores/margens e Legal Gate avançam em releases separados, preservando a estratégia dropshipping sem transformar qualquer lacuna documental numa rejeição prematura do produto.

## Verificação

```bash
test -f docs/telegram-research/REPORT.md && grep -c '^## [0-9]' docs/telegram-research/REPORT.md
```

Resultado esperado: 22.
