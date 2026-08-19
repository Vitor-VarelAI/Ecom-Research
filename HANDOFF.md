# HANDOFF — Ecom Research

## Estado atual

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds e labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`) não foi alterada.

O outbound Telegram foi implementado e é agora HTML multi-mensagem determinístico por omissão: `keyword` envia uma mensagem de produto; `sunday`/`all` enviam resumo seguido de uma mensagem por produto; `wednesday` envia resumo pulse seguido de itens compactos (retenção, churn/sinais e deltas de anunciantes); `monthly` envia resumo seguido dos produtos afetados. O digest plain text integral continua impresso no stdout e guardado como audit text. O renderer resolve-se por `TELEGRAM_RENDERER` — documentado em `.env.example` e default de código `html`.

O estado de entrega v2 (`schema_version=2`) é durável e por item: impede reenvio de itens confirmados e falha fechado em entrega ambígua (timeout, 5xx, JSON ilegível ou resposta malformada). O renderer Rich existe, implementado com TDD, mas permanece desativado: no smoke autorizado, a API aceitou as mensagens e o retry fez zero chamadas de rede, mas a inspeção visual mostrou cada item duplicado no chat de teste. Rich só é reativado depois de a duplicação ser reproduzida, explicada e corrigida.

A investigação técnica e comercial está em [`docs/telegram-research/`](docs/telegram-research/), com os 22 entregáveis em [`REPORT.md`](docs/telegram-research/REPORT.md) e o registo da implementação/smoke em [`IMPLEMENTATION_STATUS.md`](docs/telegram-research/IMPLEMENTATION_STATUS.md). Fora do âmbito continuam callbacks, long polling, Mini App, fornecedores/margens automáticos, Legal Gate executável e monitorização jurídica.

## Decisão Telegram

A Bot API oficial confirma Rich Messages e `sendRichMessage` (10.1) e blocos estruturados, tabelas, `details` e media (10.2). A recomendação de planeamento previa `sendRichMessage` como renderer principal com HTML como fallback; o smoke autorizado inverteu a decisão operacional:

1. `sendMessage` com HTML é o renderer operacional e default (código e `.env.example`): `TELEGRAM_RENDERER=html`;
2. `sendRichMessage` está implementado (`pipeline/notifications/render_rich.py`) mas desativado — a API aceitou as mensagens e o retry fez zero chamadas de rede, mas cada item apareceu duplicado no chat de teste; a causa exata (endpoint/cliente Rich) continua por investigar;
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
- `docs/telegram-research/REPORT.md` — índice dos 22 entregáveis.
- `docs/telegram-research/IMPLEMENTATION_STATUS.md` — registo da implementação, smoke Rich/HTML e ativação/rollback.

## Próxima sessão

Não começar pela Mini App, por callbacks/long polling nem pela automação de fornecedores — o núcleo multi-mensagem HTML já está entregue. A ordem recomendada é:

1. reproduzir e explicar a duplicação visual Rich num chat de teste antes de qualquer reativação (Rich permanece desativado);
2. confirmar a assinatura real de erro Rich que deve disparar fallback — o classificador atual falha fechado quando não reconhece a descrição;
3. identity store canónico de produtos (o hash de keyword não sobrevive a renomeações) — fora desta release;
4. só depois introduzir long polling e decisões, num spike entre `python-telegram-bot` e uma implementação mínima;
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
- Rich foi aceite pela API mas duplicou visualmente cada item no smoke; investigar o endpoint/cliente antes de ativar;
- a assinatura real de erro Rich que deve disparar fallback continua por confirmar (classificador fail-closed);
- batches Rich já persistidos não mudam automaticamente para HTML, por desenho de idempotência;
- itens com entrega ambígua exigem reconciliação manual antes de qualquer reenvio;
- token Telegram anteriormente exposto continua a precisar de rotação pelo operador.

## Verificação desta sessão

Baseline completo da release: **680 testes verdes** (`680 passed`), `compileall` sem erros e `git diff --check` limpo. Todos os testes mockam rede; o único tráfego real foi o smoke autorizado no chat de teste, com Rich e HTML separados por state ID e sem ler credenciais para output.

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
