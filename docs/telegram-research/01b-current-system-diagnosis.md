# 01b — Diagnóstico da integração Telegram atual

Razão de existência: fixar o contrato técnico existente e o blast radius da passagem de um digest de texto para um resumo e uma mensagem por produto.

## Diagnóstico

A informação atual está correta, mas a unidade de entrega está errada para utilização móvel: `run.py::_digest_text` concatena todos os produtos numa string e `telegram.deliver` trata essa string como chunks do mesmo documento. O utilizador não consegue pesquisar um produto isoladamente, usar botões contextuais, guardar decisões por produto ou atualizar um estado legal sem reenviar o digest completo.

O sistema atual é sólido na entrega. A mudança deve preservar o protocolo write-ahead, o bloqueio de reenvio ambíguo, a sanitização de segredos e a reutilização determinística de runs.

## Integração existente

- `pipeline/delivery/telegram.py:263` usa `requests.post`; não existe framework Telegram.
- `requirements.txt` contém apenas `requests`, `python-dotenv` e `pytest`.
- `pipeline/delivery/telegram.py:360-361` lê `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
- `pipeline/delivery/telegram.py:368-372` chama `/sendMessage` com `chat_id`, `text` e `disable_web_page_preview`.
- `tests/test_telegram.py:50-52` exige explicitamente a ausência de `parse_mode`, `reply_markup` e callbacks.
- Não existe webhook, `getUpdates`, processo persistente ou armazenamento de `message_id`.

## Formação e agendamento

- `run.py:601-618` cria o digest completo.
- `pipeline/output/pack.py:624-648` cria a linha de cada produto.
- A prova global usa `facebook_ads.advertisers[0]`, mas o digest mostra apenas o nome e número de variantes, sem link ou imagem.
- `run.py:700` cria o título de domingo.
- O cron executa diariamente `run.py --auto --digest`; o dispatcher só atua nos dias previstos.
- `run.py:151-159` entrega uma única string e imprime a mesma string no fallback.

## Garantias que não podem ser perdidas

1. O `run_id` é determinístico e impede repetir fontes pagas ou digests concluídos.
2. O estado é persistido antes de cada envio.
3. Cada chunk recebe `in_flight=true` antes da chamada de rede.
4. Timeout, HTTP 5xx ou resposta ambígua bloqueiam o reenvio automático.
5. Rejeições definitivas limpam `in_flight` e permitem retry.
6. Estado corrompido ou associado a outro conteúdo é rejeitado sem sobrescrita.
7. Tokens, URLs com token e `chat_id` não entram no estado nem nos logs.
8. Um batch tecnicamente incompleto não é enviado ao Telegram.

Referências principais: `pipeline/delivery/telegram.py:68-448`, `pipeline/run_state.py`, `tests/test_telegram.py` e `tests/test_telegram_state.py`.

## Campos atuais e lacunas

Os packs já guardam keyword, data, score, trends, Keyword Planner, anúncios, SEO, landing price, sugestões, proveniência, políticas de fontes e metadados do run.

Não existem ainda:

- `product_id` canónico independente da semana;
- imagens e respetiva licença/proveniência;
- fornecedores, MOQ, stock, shipping e personalização;
- custos, margens e cenários;
- Legal Gate, PPWR/RERE, EPR, GPSR, regras específicas, PI e Safety Gate;
- documentos em falta, confiança e datas de verificação;
- decisão do utilizador e histórico;
- `message_id`, revisão da mensagem ou estado de edição;
- URL pública do relatório.

## Blast radius

### Entrega

O estado atual valida chunks derivados de uma única string. A nova unidade deve ser um `delivery_item` estável — resumo ou produto — com `payload_hash`, estado write-ahead, `message_id`, método usado, revisão e resultado da última edição. Em estado ambíguo, o item não é reenviado automaticamente.

### Manifesto

O manifesto atual exige `digest.text`. A evolução deve conservar uma representação textual completa para stdout e auditoria, acrescentando um manifesto de mensagens estruturadas. O fallback continua disponível mesmo se Rich Messages falharem.

### Formatação

`generate_digest_line` não deve acumular responsabilidades de fornecedores, margem, legal e Telegram. A proposta separa o modelo de research, o renderer Rich, o renderer HTML fallback e o transporte.

### Runtime inbound

Callbacks exigem um serviço persistente separado do cron. A decisão confirmada é long polling no host existente, com uma única instância, um único `Telegram user_id` autorizado, offset durável e deduplicação por `update_id` e `callback_query.id`.

### Persistência de decisões

A identidade de negócio é `(product_id, research_date, market)`. Cada alteração cria um evento append-only com ação, ator, data, revisão anterior e origem Telegram. A projeção atual pode ser atualizada, mas o histórico não é substituído.

## Alterações futuras por área, sem implementação nesta fase

- `pipeline/delivery/telegram.py`: transporte Rich/fallback, estado por mensagem, captura de `message_id` e edição.
- `run.py`: construir resumo + mensagens de produto e retomar por item.
- `pipeline/run_state.py`: validar o novo manifesto e reutilizar escritas atómicas.
- `pipeline/output/`: renderers separados para Rich e HTML.
- novo módulo inbound: long polling, autorização, callbacks e histórico.
- novos módulos de domínio: fornecedores, margens e Legal Gate.
- `tests/`: contratos de payload, resume multi-mensagem, callbacks idempotentes, autorização e regressão das garantias atuais.

## Verificação

```bash
grep -c 'pipeline/' docs/telegram-research/01b-current-system-diagnosis.md
```
