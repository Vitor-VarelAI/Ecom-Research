# HANDOFF — Ecom Research

## Estado atual

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds e labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`) não foi alterada. O código de produção continua a enviar um digest plain text através de `sendMessage`; nenhuma parte do plano Telegram Rich foi implementada nesta sessão.

Foi concluída uma investigação técnica e comercial para evoluir o relatório semanal. O pacote completo está em [`docs/telegram-research/`](docs/telegram-research/), com os 22 entregáveis consolidados em [`docs/telegram-research/REPORT.md`](docs/telegram-research/REPORT.md).

## Decisão Telegram

A Bot API oficial confirma:

- Bot API 10.1, 11/06/2026: Rich Messages e `sendRichMessage`;
- Bot API 10.2, 14/07/2026: blocos estruturados, tabelas, `details`, media, collages e slideshows;
- `editMessageText` aceita `rich_message`;
- `reply_markup`, botões URL e callbacks são suportados.

A recomendação aprovada para planeamento é:

1. `sendRichMessage` como renderer principal;
2. `sendMessage` com HTML como fallback integral;
3. resumo semanal separado;
4. uma mensagem por produto;
5. long polling persistente no host atual;
6. apenas um `Telegram user_id` autorizado;
7. Mini App adiada.

O outbound pode continuar a usar `requests`. O runtime inbound deve ser decidido num spike entre `python-telegram-bot` e uma implementação mínima, sem reescrever o protocolo de entrega atual.

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
- `docs/telegram-research/REPORT.md` — índice dos 22 entregáveis.

## Próxima sessão

Não começar pela Mini App nem pela automação de fornecedores. A ordem recomendada é:

1. executar a Fase 0 do plano: fixtures de contrato, chat de teste e spike do runtime inbound;
2. implementar modelo/renderers Rich e HTML com TDD;
3. generalizar write-ahead de chunks para itens de mensagem;
4. só depois introduzir long polling e decisões;
5. fornecedores/margem e Legal Gate entram em releases separados.

Estimativa sem Mini App: 18–31 dias-agente; núcleo Telegram Rich/multi-mensagem: 5–8 dias-agente.

## Garantias existentes a preservar

- `run_id` determinístico e zero repetição de fontes pagas em reentrega;
- write-ahead antes da chamada Telegram;
- timeout/5xx/resposta ilegível mantém estado ambíguo e bloqueia reenvio;
- tokens e URLs sensíveis fora de logs e estado;
- batch tecnicamente incompleto não é entregue;
- fallback stdout completo;
- scoring permanece advisory.

## Riscos anteriores ainda pendentes

- packs legados podem reutilizar `source_url` de landing não sanitizada;
- quota pode falhar aberta se a persistência falhar;
- fetch de landing ainda precisa de defesa SSRF;
- CSV malformado pode levantar `UnicodeDecodeError`;
- valores gigantes/não finitos não têm guarda total em todos os caches;
- `_is_inactive` pode ignorar `is_active: false` em casos específicos;
- `state_ambiguous` Telegram continua sem resolver operacional automático;
- token Telegram anteriormente exposto continua a precisar de rotação pelo operador.

## Verificação desta sessão

Não foi executada a suite de produção porque só foram alterados documentos. Foi validado:

- `product-schema.example.json` como JSON;
- os quatro payloads JSON de `03b-telegram-payloads.md`;
- presença exata das 22 secções no `REPORT.md`;
- links internos dos documentos;
- ausência de alterações fora de documentação.

Antes de qualquer implementação:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline
git diff --check
```

O último baseline anterior continua registado com 561 testes verdes.

## Suggested skills

- `tdd` — implementar cada slice Rich/callback com red-green-refactor.
- `diagnose` — qualquer divergência de entrega, polling ou estado ambíguo.
- `security-review` — antes de ativar callbacks, URL/media externos ou armazenamento de decisões.
- `assess-impact` — antes de alterar `telegram.py`, `run_state.py` e o manifesto.
- `request-review` — depois de cada release executável.
