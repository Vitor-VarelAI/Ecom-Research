# Telegram research — decisões confirmadas

Razão de existência: fixar as decisões do operador que limitam a investigação e evitam propostas incompatíveis com o contexto real.

- **Callbacks:** usar um serviço persistente de long polling no host atual; não exigir webhook público.
- **Autorização:** apenas um decisor, identificado por `Telegram user_id` configurado.
- **Imagens:** usar apenas imagens de um fornecedor validado e com utilização permitida; sem imagem enquanto isso não existir.
- **Relatório completo:** ocultar o botão enquanto os packs locais não tiverem uma URL pública validada.
- **Pesquisa de fornecedores:** usar apenas páginas públicas, sem contas, APIs autenticadas ou serviços pagos.
- **Mercados comerciais prioritários:** Portugal, Reino Unido e Estados Unidos.
- **Âmbito jurídico desta fase:** manter o Legal Gate limitado à União Europeia e Portugal. Reino Unido e Estados Unidos ficam fora da investigação jurídica atual.
- **Outros países da UE:** manter configuração individual, mas sem os tratar como mercados iniciais.
- **Legal Gate progressivo:** aplicar requisitos por fase e risco. Falta documental não bloqueia descoberta ou pesquisa de fornecedor quando o documento ainda não é material; venda e escala continuam dependentes da evidência aplicável.
- **Bloqueio duro:** reservar para proibição, risco material, propriedade intelectual antes de publicidade, categoria de alto risco ou ausência de documento obrigatório para a fase de venda.

## Limites

- Não alterar código, scoring, configuração, packs ou dados de produção nesta fase.
- Não enviar mensagens ao bot nem executar fontes pagas.
- Não assumir que uma funcionalidade Telegram, fornecedor ou conclusão jurídica existe sem evidência oficial.

## Verificação

```bash
grep -c '^-' docs/telegram-research/DECISIONS.md
```

Resultado esperado: pelo menos 11 regras e limites explícitos.
