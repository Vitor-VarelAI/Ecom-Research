# 01 — Telegram Bot API: verificação técnica e matriz de capacidades

**Workstream:** Deep Worker — Telegram Bot API
**Estado:** concluído, apenas investigação. Nenhum ficheiro de código/produção alterado.
**Fontes primárias:** documentação oficial, consultadas via HTTP em 2026-08 (página atual):

- `https://core.telegram.org/bots/api` (referência completa da Bot API)
- `https://core.telegram.org/bots/api-changelog` (histórico de versões)
- `https://t.me/BotNews` / `https://t.me/BotTalk` (anúncios oficiais)

---

## 1. Veredicto central sobre `sendRichMessage`

**`sendRichMessage` EXISTE e é um método público da Telegram Bot API.** A hipótese do utilizador está **confirmada com precisão nas versões**:

| Versão | Data | O que introduziu |
|---|---|---|
| **Bot API 10.1** | 11 de junho de 2026 | Suporte a **Rich Messages**: métodos `sendRichMessage` e `sendRichMessageDraft`, classes `InputRichMessage`, `InputRichMessageContent` (usável como `InputMessageContent` em inline/guest/Web App queries), campo `rich_message` na classe `Message`, parâmetro `rich_message` em `editMessageText`, campo `blocks` em `InputRichMessage`. |
| **Bot API 10.2** | 14 de julho de 2026 | Classe `InputRichMessageMedia` e campo `media` em `InputRichMessage` (media explícito em formatação `markdown`/`html`), `InputMediaVoiceNote`, `InputRichBlockListItem` e as 21 classes `InputRichBlock*` (blocos para formato `blocks`). |

**Versão atual da API:** 10.2 (14 de julho de 2026). Fonte: topo da página oficial + changelog oficial.

Isto significa que a **Opção B (Rich Messages) é tecnicamente válida e nativa** — não é uma composição de truques. Contudo, há três ressalvas operacionais registadas abaixo (adopção cliente, upload de media, integração atual), que decidem entre B puro e B com fallback A.

---

## 2. Matriz de capacidades (confirmado contra a documentação oficial)

Legenda: ✅ nativo/suportado · 🟡 composição possível com métodos existentes · ❌ não suportado · ❓ desconhecido/por validar.

| Capacidade | Estado | Método/classe | Notas e limites |
|---|---|---|---|
| Títulos/secções (H1–H6) | ✅ | `<h1>`…`<h6>` em `html`; `#`…`######` em `markdown`; bloco `InputRichBlockSectionHeading` | Bloco "heading" com `size` 1–6 |
| Parágrafos | ✅ | `<p>` / `InputRichBlockParagraph` | |
| Listas ordenadas/não ordenadas | ✅ | `<ul>/<ol>` / `InputRichBlockList` + `InputRichBlockListItem` | Com checkboxes, `start`, `type`, `reversed`, `value` |
| Tabelas | ✅ | `<table>` / `InputRichBlockTable` | **Até 20 colunas**; `colspan`, `rowspan`, `align`, `valign`, `bordered`, `striped`, caption; células só com formatação inline |
| Divisores | ✅ | `<hr/>` / `InputRichBlockDivider` | Bloco "divider" |
| Blocos expansíveis (details) | ✅ | `<details><summary>` / `InputRichBlockDetails` | `open` para expandido por defeito; conteúdo pode ser rich (markdown dentro de details é parseado) |
| Fotografias | ✅ | `<img src="https://…">` em html/markdown; bloco `InputRichBlockPhoto` com `InputMediaPhoto`; ou `media`+`tg://photo?id=` | **Media apenas como bloco separado; só HTTP/HTTPS**; MIME decide o tipo |
| Vídeos | ✅ | `<video>` / `InputRichBlockVideo` com `InputMediaVideo` | Também via `tg://video?id=` |
| Áudio/voice notes | ✅ | `<audio>` / `InputRichBlockAudio`, `InputRichBlockVoiceNote` | `tg://audio?id=` |
| Collages | ✅ | `<tg-collage>` / `InputRichBlockCollage` | Novidade 10.2 |
| Slideshows | ✅ | `<tg-slideshow>` / `InputRichBlockSlideshow` | Novidade 10.2 |
| Media groups clássicos | ✅ (composição) | `sendMediaGroup` (método antigo) | Para bots sem suporte a Rich Messages; ver secção 6 |
| Links inline | ✅ | `<a href="https://…">`, `mailto:`, `tel:`, `tg://user?id=`, âncoras `#`, `tg-reference` | Também `[texto](url)` em markdown |
| Blocos de citação/pull quote | ✅ | `<blockquote>`, `<aside>` / `InputRichBlockBlockQuotation`, `InputRichBlockPullQuotation` | |
| Mapas | ✅ | `<tg-map>` / `InputRichBlockMap` | width+height ≤ 10000, rácio ≤ 20 |
| Matemática/LaTeX | ✅ | `<tg-math>`, `<tg-math-block>` / `RichTextMathematicalExpression` | |
| Footer | ✅ | `<footer>` / `InputRichBlockFooter` | |
| Âncoras de documento | ✅ | `<a name>` / `InputRichBlockAnchor` + `RichTextAnchorLink` | |
| Notas de rodapé | ✅ | `[^id]` em markdown; `tg-reference` em html | |
| Inline keyboards | ✅ | `reply_markup: InlineKeyboardMarkup` no `sendRichMessage` | Parâmetro documentado no próprio método |
| Botões URL | ✅ | `InlineKeyboardButton` com `url` | Mesmo mecanismo de sempre |
| Botões callback | ✅ | `InlineKeyboardButton` com `callback_data` | Mesmo mecanismo de sempre; ver secção 7 |
| Edição posterior | ✅ | `editMessageText` com `rich_message` | `text` OU `rich_message`; upload direto de ficheiros não suportado ao editar mensagens inline |
| Streaming/geração | ✅ | `sendRichMessageDraft` | Só chats privados; rascunho **efémero de 30 s**; obrigatório finalizar com `sendRichMessage`; `tg-thinking` só aqui |
| RTL | ✅ | `is_rtl` em `InputRichMessage` | |
| Spoilers, mark, sub/sup, código | ✅ | `tg-spoiler`, `mark`, `sub`, `sup`, `pre`/`code` | |
| Hashtags/cashtags/menções automáticas | ✅ | deteção automática de entidades; `skip_entity_detection` para desativar | |
| Capções em media | ✅ | `<figcaption>` + `<cite>` / `RichBlockCaption` | |

### Limites oficiais das Rich Messages (secção "Limits" da referência)

- **32 768 caracteres UTF-8** no texto da rich message (inclui texto alternativo de emoji e fonte de fórmulas);
- **500 blocos**, incluindo aninhados, itens de lista, linhas de tabela, citações e details;
- **16 níveis** de aninhamento de formatação/blocos;
- **50 anexos de media** no total;
- **20 colunas** por tabela.

---

## 3. Formato de entrada: `InputRichMessage` (exatamente UM campo obrigatório)

```
html      — String  (Rich HTML, ver lista de tags na secção "Rich HTML style")
markdown  — String  (Rich Markdown, GFM-compatível onde possível, com HTML arbitrário permitido)
blocks    — Array de InputRichBlock (21 tipos de bloco, incluindo os InputRichBlock* introduzidos na 10.2)
media     — Array de InputRichMessageMedia (opcional; referenciado por tg://photo?id=, tg://video?id=, tg://audio?id=)
is_rtl    — Boolean (opcional)
skip_entity_detection — Boolean (opcional)
```

**Media:** pode ser embutido como bloco separado com URL HTTP/HTTPS direto (`<img src="https://…">`, `<video>`, `<audio>`) ou enviado como ficheiro via `InputRichMessageMedia` (id + `InputMediaPhoto/Video/Audio/VoiceNote/Animation`) e referenciado no texto com `tg://photo?id=…`. O MIME type do URL determina o tipo de media. A legenda do media usa `title`/`figcaption`.

---

## 4. Compatibilidade com clientes (Telegram antigos)

A documentação oficial **não publica uma tabela explícita de versões mínimas de clientes** para Rich Messages. O que é oficialmente verificável:

- A API marca as capacidades como adições de servidor (10.1/10.2); os clientes recebem as mensagens com o campo `rich_message` na classe `Message` (formato `RichMessage`/`RichBlock` já recebido, mesmo que o cliente não o renderize bem).
- O Telegram documenta, historicamente, que clientes antigos degradam mensagens ricas em texto simples (mesmo comportamento de mensagens com media/entities).
- **Risco real (não inventado, mas não quantificado oficialmente):** clientes muito antigos (apps com anos de desatualização) podem renderizar a rich message como texto em bruto ou com layout quebrado. A degradação exata por versão de app é ❓ **desconhecida** — não há página oficial que a especifique.
- **Mitigação oficialmente sólida:** como `sendRichMessage` e `sendMessage` coexistem, a estratégia de fallback é manter a **Opção A (sendMessage com HTML/parse_mode + inline keyboard)** como percurso para qualquer chat cujo cliente não renderize bem rich messages, ou como modo conservador configurável.

---

## 5. Transporte HTTP puro (sem framework)

**Confirmado: a Bot API é "an HTTP-based interface".** Todos os métodos, incluindo `sendRichMessage`, são endpoints `POST https://api.telegram.org/bot<token>/<method>` com corpo `application/json` (ou `multipart/form-data` para upload de ficheiros). A integração atual do projeto usa `requests` puro contra `sendMessage` — **pode chamar `sendRichMessage` exatamente da mesma forma, sem introduzir python-telegram-bot, aiogram ou qualquer dependência.**

Consequência prática para o projeto:

- O payload JSON de `sendRichMessage` é construído à mão (tal como hoje); **zero novas dependências**.
- Ficheiros de media (fotografias de produto) podem ser (a) URLs HTTP/HTTPS embutidos como blocos media — sem upload, sem ficheiros locais; ou (b) upload via `multipart/form-data`/`tg://` com `InputRichMessageMedia`. A opção (a) é a mais simples e adequada ao fluxo atual (não há hoje armazenamento de imagens locais).
- `callback_data` (1–64 bytes) e edição por `editMessageText` funcionam pelo mesmo transporte HTTP; exigem **webhook ou long polling** para receber os updates (ver secção 7).

---

## 6. Opção A como fallback (confirmado)

Tudo o que a Opção A precisa continua a existir e é oficial:

- `sendMessage` com `parse_mode: "HTML"` (tags `<b>`, `<i>`, `<a href>`, `<code>`, `<pre>`, `<tg-spoiler>` — lista curta, **sem tabelas, sem headings, sem details**);
- `sendPhoto`/`sendVideo` com `caption` (limite de caption: 1024 caracteres);
- `sendMediaGroup` para álbuns (2–10 itens, fotos/vídeos mistos permitidos);
- `InlineKeyboardMarkup` com botões `url` e `callback_data`;
- `editMessageText`/`editMessageCaption`/`editMessageReplyMarkup`.

Limitações da Opção A (é por isso que a B é superior): sem blocos expansíveis, sem tabelas nativas, sem slideshows; "Ver detalhes" só via botão que edita a mensagem ou segunda mensagem; hashtags funcionam igual.

---

## 7. Notas para o desenho de callbacks (persistência de decisões)

- `callback_data` suporta **1–64 bytes**; deve codificar `product_id` + `research_date` + ação, ou usar um id curto mapeado no backend.
- Receber callbacks exige **webhook público (HTTPS, porta 443, certificado válido) ou long polling persistente** — a integração atual é **send-only** (sem webhook/polling), pelo que este é o único ponto que obriga a infraestrutura nova (decisão do utilizador pendente).
- Depois de um callback, o bot responde com `answerCallbackQuery` (opcional) e edita a mensagem com `editMessageText`/`editMessageReplyMarkup` para refletir a decisão. Mensagens eliminadas/callback repetido produzem erros 400 tratáveis (idempotência: o estado decide, a resposta ao clique apenas confirma).

---

## 8. Matriz de suporte por bloco — resumo para os mockups

| Elemento pedido no briefing | Estado | Como |
|---|---|---|
| Títulos e secções | ✅ nativo | `## Nome` / `<h2>` / bloco heading |
| Tabelas | ✅ nativo | markdown GFM ou `<table>`; ≤20 colunas |
| Fotografias | ✅ nativo | bloco media com URL HTTPS, ou `tg://photo` + `media` |
| Vídeos | ✅ nativo | bloco media |
| Collages/slideshows | ✅ nativo (10.2) | `<tg-collage>` / `<tg-slideshow>` |
| Divisores | ✅ nativo | `---` / `<hr/>` |
| Blocos expansíveis "Ver detalhes" | ✅ nativo | `<details><summary>Ver detalhes</summary>…</details>` |
| Links | ✅ nativo | `[texto](url)` / `<a href>` |
| Inline keyboard | ✅ nativo | `reply_markup` no `sendRichMessage` |
| Botões URL | ✅ nativo | `InlineKeyboardButton.url` |
| Botões callback | ✅ nativo | `InlineKeyboardButton.callback_data` |
| Edição posterior | ✅ nativo | `editMessageText` com `rich_message` |
| Capções | ✅ nativo | `figcaption`/`title`; captions de sendMessage: 1024 chars |

---

## 9. Conclusões e riscos

1. **A Opção B é nativa e viável hoje** (Bot API 10.1+, atual 10.2). Todos os elementos do briefing — títulos, secções, tabelas, fotos, vídeos, collages, slideshows, divisores, `<details>`, links, inline keyboards, botões URL/callback, edição — são suportados oficialmente.
2. **Recomendação técnica preliminar:** B com fallback A — `sendRichMessage` como caminho principal, `sendMessage`+HTML+inline keyboard como fallback configurável por chat/cliente. Sem novas dependências (HTTP puro).
3. **Riscos/condicionantes:**
   - ❓ Adoção/renderização em clientes antigos não é quantificada oficialmente → teste manual em apps atuais e fallback A.
   - Media embutido só aceita **HTTP/HTTPS** e MIME type correto; URLs de fornecedores que bloqueiem hotlinking podem falhar (validar antes).
   - Callbacks exigem **webhook ou long polling** — a única mudança infraestrutural real.
   - `sendRichMessageDraft` é efémero (30 s) e só privado — irrelevante para o envio semanal.
4. **Não há nenhum bloqueio técnico da API** para o plano. O gate do Planner pode passar a Opção B com fallback A, sujeito às decisões de infraestrutura (webhook/polling) e à validação de URLs de media.

## 10. URLs oficiais que sustentam cada conclusão

- Referência completa (métodos, classes, limites, markdown/html styles): `https://core.telegram.org/bots/api`
  - `sendRichMessage`: secção "sendRichMessage" na mesma página
  - `sendRichMessageDraft`: secção "sendRichMessageDraft"
  - `InputRichMessage`, `InputRichMessageMedia`, `InputRichBlock*`: secções respetivas
  - Limites: secção "Limits" (Rich messages)
  - Rich HTML style / Rich Markdown style: secções respetivas
  - `editMessageText` com `rich_message`: secção "editMessageText"
  - `Message.rich_message` / `RichMessage` / `RichBlock`: secções respetivas
- Changelog (versões e datas 10.1/10.2): `https://core.telegram.org/bots/api-changelog`
- Anúncios oficiais: `https://t.me/BotNews`, discussão `https://t.me/BotTalk`

*Nota metodológica: todas as citações acima foram extraídas diretamente do HTML das páginas oficiais em 2026-08. Nenhuma capacidade foi inferida; itens marcados ❓ não têm fonte oficial e estão sinalizados como desconhecidos.*
