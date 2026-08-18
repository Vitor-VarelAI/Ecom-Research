# 02 — Opções, compatibilidade e recomendação

Razão de existência: escolher o formato que melhora a leitura semanal sem introduzir uma aplicação maior do que o problema atual exige.

## Veredicto

Implementar **Opção B — `sendRichMessage` — com um renderer completo da Opção A como fallback configurável**. A Bot API 10.1 introduziu oficialmente Rich Messages e `sendRichMessage` em 11 de junho de 2026; a 10.2 acrescentou os blocos estruturados e media em 14 de julho de 2026.

O método atual não implementa Rich Messages, mas o transporte `requests` consegue chamá-las porque a Bot API é HTTP. Não é necessário trocar a integração outbound para obter a funcionalidade.

Fontes oficiais:

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Bot API changelog — 10.1 e 10.2](https://core.telegram.org/bots/api-changelog)
- [Rich Messages](https://core.telegram.org/bots/features#rich-messages)
- [`sendRichMessage`](https://core.telegram.org/bots/api#sendrichmessage)
- [`InputRichMessage`](https://core.telegram.org/bots/api#inputrichmessage)

## Comparação

| Critério | A — `sendMessage` HTML | B — Rich Messages | C — Mini App |
|---|---|---|---|
| Alteração no outbound | Baixa | Baixa a média | Alta |
| Leitura móvel | Boa | Melhor | Melhor para exploração |
| Títulos/secções nativos | Não; composição tipográfica | Sim | Sim |
| Tabelas | Não nativas | Sim | Sim |
| Bloco expansível | Não | Sim, `details` | Sim |
| Foto/vídeo | Métodos separados | Blocos Rich | Interface própria |
| Collage/slideshow | `sendMediaGroup` como álbum | Nativo | Interface própria |
| Pesquisa no Telegram | Boa com uma mensagem e hashtags por produto | Boa com uma mensagem e hashtags por produto | Pesquisa própria |
| Botões e callbacks | Sim | Sim | Sim, mais navegação |
| Compatibilidade antiga | Maior | Versão mínima de cliente não publicada | Depende de WebView/client |
| Estado e backend | Callback exige inbound | Callback exige inbound | Backend completo obrigatório |
| Custo operacional | Baixo | Baixo | Médio/alto |
| Adequação agora | Fallback obrigatório | Recomendado | Futuro |

## Opção A

Resolve imediatamente o problema de leitura ao separar o resumo e cada produto, usar HTML, hashtags e inline keyboard. Não oferece tabelas nem `details` nativos, pelo que detalhes exigem uma mensagem adicional ou edição da mensagem.

A opção continua obrigatória como renderer degradado. Também serve para diagnosticar se um erro pertence ao conteúdo Rich ou ao transporte.

## Opção B

A Bot API 10.2 suporta headings, listas, tabelas, divisores, `details`, fotografias, vídeos, collages e slideshows. `InputRichMessage` aceita exatamente um de `html`, `markdown` ou `blocks`; o projeto deve começar com `html`, porque é mais legível para auditoria e evita construir manualmente a árvore de `RichText`.

Limites oficiais relevantes:

- 32 768 caracteres UTF-8;
- 500 blocos;
- 16 níveis de aninhamento;
- até 50 anexos de media;
- `callback_data` entre 1 e 64 bytes.

Uma mensagem por produto ficará muito abaixo desses limites.

## Opção C

A Mini App passa a fazer sentido quando existirem histórico persistente, fornecedores comparáveis, margens, documentação e estados legais suficientes para pesquisa e filtros. Criá-la agora obrigaria a introduzir backend, autenticação, hosting, API, UI e armazenamento antes de o modelo de dados estar validado.

A primeira implementação deve conservar os identificadores necessários para a Mini App: `product_id`, `research_date`, `market` e `decision_revision`.

## Compatibilidade

A documentação oficial confirma que os clientes Telegram renderizam Rich Messages, mas não publica uma matriz de versões mínimas por plataforma. O bot também não recebe a versão da aplicação do utilizador, logo não pode escolher automaticamente o renderer pelo cliente.

A política segura é:

1. disponibilizar `rich` e `html` como modos explícitos;
2. executar um smoke test Rich no dispositivo real do único decisor;
3. manter `html` como fallback imediato perante rejeição definitiva da Bot API;
4. não fazer fallback automático perante timeout ou estado ambíguo, porque isso poderia duplicar a mensagem;
5. permitir mudar o modo operacionalmente sem recalcular o research;
6. testar Android, iOS e Desktop usados pelo operador antes de produção.

## Biblioteca e runtime

### Outbound

Manter `requests` e as garantias atuais. A introdução de um framework apenas para chamar `sendRichMessage` não traz valor suficiente para justificar reescrever o protocolo de entrega endurecido.

### Inbound

O long polling é um problema diferente. Implementar manualmente `getUpdates`, offsets, backoff, shutdown e dispatch aumenta o risco. A fase de implementação deve avaliar `python-telegram-bot` como runtime inbound isolado, mantendo o outbound existente atrás de uma interface própria. A decisão final depende de um spike curto e dos testes de reinício/idempotência.

## Política de fallback

```text
renderer Rich
  ├─ aceite → persistir message_id e modo rich
  ├─ rejeição definitiva por payload → renderer HTML, novo item de tentativa auditado
  └─ timeout/5xx/resultado ambíguo → bloquear; não enviar fallback automaticamente

renderer HTML
  ├─ aceite → persistir message_id e modo html
  ├─ rejeição de HTML → texto simples escapado
  └─ timeout/resultado ambíguo → bloquear
```

A decisão e o histórico são persistidos antes da edição da mensagem. Uma falha de edição não pode apagar nem reverter a decisão.

## Recomendação final

Usar Rich Messages para estrutura, não para decoração: um resumo, uma mensagem por produto, uma tabela curta, um `details` para dados secundários e um teclado de decisão. Fotografias só aparecem quando forem provenientes de um fornecedor validado; collages, slideshows e vídeo ficam disponíveis, mas não entram no primeiro release sem dados reais.

## Verificação

```bash
grep -c 'https://core.telegram.org' docs/telegram-research/02-options-compatibility-recommendation.md
```
