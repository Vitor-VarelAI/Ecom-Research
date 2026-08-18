# 03b — Payloads Telegram válidos

Razão de existência: substituir os mockups semânticos por exemplos alinhados com o schema oficial da Bot API 10.2. Nenhum payload foi enviado.

Fontes: [`sendRichMessage`](https://core.telegram.org/bots/api#sendrichmessage), [`InputRichMessage`](https://core.telegram.org/bots/api#inputrichmessage), [Rich HTML](https://core.telegram.org/bots/api#rich-html-style), [`editMessageText`](https://core.telegram.org/bots/api#editmessagetext), [`sendMessage`](https://core.telegram.org/bots/api#sendmessage) e [`InlineKeyboardMarkup`](https://core.telegram.org/bots/api#inlinekeyboardmarkup).

Os valores entre `<...>` são placeholders obrigatórios. Os tokens `v1:t:*` são exemplos opacos com menos de 64 bytes e só funcionam depois de persistidos no servidor.

## `sendRichMessage` — resumo

```json
{
  "chat_id": "<CHAT_ID>",
  "rich_message": {
    "html": "<h1>RESEARCH SEMANAL</h1><p>16 de agosto de 2026</p><hr/><h2>Resumo</h2><table bordered><tr><th>Estado</th><th>Total</th></tr><tr><td>AVANÇAR advisory</td><td>2</td></tr><tr><td>OBSERVAR advisory</td><td>3</td></tr><tr><td>DESCARTAR advisory</td><td>0</td></tr><tr><td>AVANÇAR PARA PESQUISA</td><td>4</td></tr><tr><td>TRIAGEM IP ANTES DE PUBLICIDADE</td><td>1</td></tr><tr><td>APTOS PARA VENDA TESTE</td><td>0</td></tr></table><details><summary>ALTERAÇÕES LEGAIS</summary><p>Âmbito: União Europeia / Portugal</p><p>Comparação semanal: PESQUISA NECESSÁRIA</p></details><p>Relatório completo: NÃO ENCONTRADO</p><p>Última atualização dos dados: 16/08/2026</p><p>#research_2026_08_16 #portugal #dropshipping</p>"
  },
  "reply_markup": {
    "inline_keyboard": [
      [
        {"text": "Mostrar AVANÇAR", "callback_data": "v1:t:SUMA1"},
        {"text": "Mostrar OBSERVAR", "callback_data": "v1:t:SUMO1"}
      ],
      [
        {"text": "Alterações legais", "callback_data": "v1:t:LEG01"}
      ]
    ]
  }
}
```

## `sendRichMessage` — produto

```json
{
  "chat_id": "<CHAT_ID>",
  "rich_message": {
    "html": "<h1>🎒 mochila personalizada</h1><p><b>Score:</b> 87/100</p><p><b>Sinal advisory:</b> AVANÇAR</p><hr/><h2>Evidência global</h2><p>Maternelle Perú · 4 variantes · coorte global</p><p>Link: NÃO ENCONTRADO</p><h2>Comercial</h2><table><tr><td>Dropshipping</td><td>FORNECEDOR EM FALTA</td></tr><tr><td>PT / GB / US</td><td>envio POR VALIDAR</td></tr><tr><td>Margem</td><td>POR VALIDAR</td></tr></table><h2>Legal Gate — UE/Portugal</h2><p><b>Estado:</b> DOCUMENTAÇÃO EM FALTA</p><p>GPSR: documentação em falta</p><p>PPWR/RERE: packaging não validado</p><p>EPR Portugal: por confirmar</p><details><summary>Ver detalhes</summary><p>Sugestões: NÃO ENCONTRADO</p><p>Dados técnicos: POR VALIDAR</p><p>Fornecedor: PESQUISA NECESSÁRIA</p><p>Documentação encontrada: NÃO ENCONTRADO</p></details><p><b>Próxima fase:</b> AVANÇAR PARA PESQUISA DE FORNECEDOR</p><p><b>Venda teste:</b> AGUARDAR DOCUMENTAÇÃO APLICÁVEL</p><p>Última atualização dos dados: 16/08/2026</p><p>#mochila_personalizada #avancar #pesquisa_fornecedor #research_2026_08_16 #portugal #dropshipping</p>"
  },
  "reply_markup": {
    "inline_keyboard": [
      [
        {"text": "Aprovar", "callback_data": "v1:t:AP001"},
        {"text": "Observar", "callback_data": "v1:t:OB001"}
      ],
      [
        {"text": "Descartar", "callback_data": "v1:t:DE001"},
        {"text": "Guardar", "callback_data": "v1:t:GU001"}
      ],
      [
        {"text": "Pedir validação legal", "callback_data": "v1:t:LE001"},
        {"text": "Pedir novo fornecedor", "callback_data": "v1:t:FO001"}
      ]
    ]
  }
}
```

Não existe `image_url` validado, portanto este payload não contém media. Um URL de imagem só entra no Rich HTML ou no campo `media` depois de validar fornecedor, origem, HTTPS, MIME e direito de utilização.

## `editMessageText` depois da decisão

```json
{
  "chat_id": "<CHAT_ID>",
  "message_id": "<MESSAGE_ID>",
  "rich_message": {
    "html": "<h1>🎒 mochila personalizada</h1><p><b>Score:</b> 87/100</p><p><b>Sinal advisory:</b> AVANÇAR</p><hr/><h2>Decisão do operador</h2><p><b>OBSERVAR</b></p><p>Por: &lt;DECISOR_AUTORIZADO&gt;</p><p>Em: &lt;UTC_TIMESTAMP&gt;</p><p><b>Próxima fase:</b> AVANÇAR PARA PESQUISA DE FORNECEDOR</p><p><b>Venda teste:</b> AGUARDAR DOCUMENTAÇÃO APLICÁVEL</p><details><summary>Legal Gate</summary><p>DOCUMENTAÇÃO EM FALTA</p></details>"
  },
  "reply_markup": {
    "inline_keyboard": [
      [
        {"text": "Ver histórico", "callback_data": "v1:t:HI001"},
        {"text": "Alterar decisão", "callback_data": "v1:t:CH001"}
      ]
    ]
  }
}
```

`rich_message` substitui `text`; não enviar ambos.

## Fallback `sendMessage` com HTML

O HTML normal da Bot API não suporta as tags estruturais Rich. Tabelas e `details` são achatados para texto.

```json
{
  "chat_id": "<CHAT_ID>",
  "text": "<b>🎒 mochila personalizada</b>\n\n<b>Score:</b> 87/100\n<b>Sinal advisory:</b> AVANÇAR\n\n<b>EVIDÊNCIA GLOBAL</b>\nMaternelle Perú · 4 variantes · coorte global\nLink: NÃO ENCONTRADO\n\n<b>COMERCIAL</b>\nDropshipping: FORNECEDOR EM FALTA\nPT / GB / US: envio POR VALIDAR\nMargem: POR VALIDAR\n\n<b>LEGAL GATE — UE/PORTUGAL</b>\nEstado: DOCUMENTAÇÃO EM FALTA\nGPSR: documentação em falta\nPPWR/RERE: packaging não validado\nEPR Portugal: por confirmar\n\n<b>DETALHES</b>\nSugestões: NÃO ENCONTRADO\nFornecedor: PESQUISA NECESSÁRIA\nDocumentação encontrada: NÃO ENCONTRADO\n\n<b>Próxima fase:</b> AVANÇAR PARA PESQUISA DE FORNECEDOR\n<b>Venda teste:</b> AGUARDAR DOCUMENTAÇÃO APLICÁVEL\nÚltima atualização dos dados: 16/08/2026\n\n#mochila_personalizada #avancar #pesquisa_fornecedor #research_2026_08_16 #portugal #dropshipping",
  "parse_mode": "HTML",
  "link_preview_options": {"is_disabled": true},
  "reply_markup": {
    "inline_keyboard": [
      [
        {"text": "Aprovar", "callback_data": "v1:t:AP001"},
        {"text": "Observar", "callback_data": "v1:t:OB001"}
      ],
      [
        {"text": "Descartar", "callback_data": "v1:t:DE001"},
        {"text": "Guardar", "callback_data": "v1:t:GU001"}
      ],
      [
        {"text": "Pedir validação legal", "callback_data": "v1:t:LE001"},
        {"text": "Pedir novo fornecedor", "callback_data": "v1:t:FO001"}
      ]
    ]
  }
}
```

## Regras de envio

- `reply_markup` fica no nível superior em `sendRichMessage`, `sendMessage` e `editMessageText`.
- Cada botão contém apenas uma ação, `callback_data` ou `url`.
- O renderer valida o comprimento UTF-8 do callback antes do envio.
- URLs inválidos eliminam o botão; placeholders nunca são enviados.
- `answerCallbackQuery` é sempre chamado depois de receber um callback.
- `Aprovar` aprova apenas a próxima fase indicada e o token fica associado a essa fase.
- Rejeição definitiva do payload Rich pode ativar fallback; timeout ou resposta ambígua bloqueiam o segundo envio para evitar duplicação.

## Verificação

Copiar cada bloco JSON para um ficheiro temporário e executar `python -m json.tool <ficheiro>` antes de o transformar em fixture de contrato.
