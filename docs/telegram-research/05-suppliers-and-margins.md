# 05 — Fornecedores e viabilidade financeira

Razão de existência: impedir que procura de mercado ou uma listagem de marketplace sejam confundidas com um fornecedor de dropshipping economicamente utilizável.

Verificação pública realizada em 18/08/2026, sem contas, APIs pagas ou compras. Prioridades comerciais: PT, depois GB e US. O âmbito jurídico permanece UE/Portugal.

## Resultado

Nenhum dos cinco produtos tem simultaneamente fornecedor específico, dropshipping, MOQ, preço, shipping por destino, prazo, stock, devoluções e documentação confirmados. Todos permanecem `FORNECEDOR EM FALTA` ou `DOCUMENTAÇÃO DO FORNECEDOR EM FALTA` para venda, mas isso não impede a pesquisa de fornecedores. Mochila, caneca, tapete e velas podem avançar para pesquisa; a camisola exige triagem de propriedade intelectual antes de produzir publicidade com símbolos ou nomes potencialmente protegidos.

Uma página de categoria ou pesquisa é descoberta, não fornecedor confirmado. Uma página individual acessível é apenas `CANDIDATE` enquanto as condições comerciais e documentais estiverem ausentes.

## Evidência por plataforma

| Plataforma | Evidência pública | Classificação permitida |
|---|---|---|
| AliExpress | Pesquisas públicas e quatro páginas individuais identificáveis | Itens `CANDIDATE`; preço, variante, shipping e documentos por validar |
| BigBuy | Pesquisa devolveu Cloudflare HTTP 403 | `PESQUISAR FORNECEDOR`; não comprova resultados |
| CJdropshipping | Endpoint redireciona e requer autenticação | Não utilizável como pesquisa pública validada |
| Alibaba | Pesquisa HTTP 200, conteúdo dependente de JavaScript | `PESQUISA NECESSÁRIA` |
| Printful | Catálogo e categorias públicas | Categoria POD candidata; não confirma produto/variante, preço ou envio |
| Printify | Catálogo público genérico | Descoberta apenas |
| Gelato | Página bloqueada por HTTP 403 | Não validado publicamente |

## Mochila personalizada

Candidatos públicos:

- AliExpress: <https://www.aliexpress.com/item/1005002794584863.html> — página individual identificável como mochila infantil personalizável; `CANDIDATE`.
- Pesquisa AliExpress: <https://www.aliexpress.com/w/wholesale-mochila-personalizada.html> — `PESQUISAR FORNECEDOR`.
- Printful, categoria: <https://www.printful.com/custom/bags/backpacks> — categoria POD candidata.
- BigBuy: <https://www.bigbuy.eu/en/search?q=mochila%20personalizada> — pesquisa não validável devido a 403.
- Alibaba: <https://www.alibaba.com/trade/search?SearchText=mochila%20personalizada> — pesquisa dependente de JavaScript.

Dados por validar em todos os candidatos: empresa, armazém, PT/GB/US, preço, transporte, prazo, MOQ, stock, autorização de dropshipping, fluxo de personalização, formatos, prazo adicional, unidade única, neutral packaging, devoluções, documentação e operador UE.

Estado: `FORNECEDOR EM FALTA`.

## Camisola Portugal

Pesquisas públicas:

- AliExpress: <https://www.aliexpress.com/w/wholesale-camisola-portugal.html>.
- BigBuy: <https://www.bigbuy.eu/en/search?q=camisola%20portugal>.
- Alibaba: <https://www.alibaba.com/trade/search?SearchText=camisola%20portugal>.

Não foi validada uma página individual. A procura de fornecedor deve ficar suspensa para itens com emblemas, jogadores ou associação oficial até à verificação de propriedade intelectual.

Estado: `FORNECEDOR EM FALTA`; propriedade intelectual `VERIFICAÇÃO MANUAL NECESSÁRIA`.

## Caneca personalizada

Candidatos públicos:

- AliExpress: <https://www.aliexpress.com/item/1005004421076272.html> — caneca personalizável identificável; `CANDIDATE`.
- Pesquisa AliExpress: <https://www.aliexpress.com/w/wholesale-caneca-personalizada.html>.
- Printful, categoria: <https://www.printful.com/custom/mugs/personalized> — categoria POD candidata.
- BigBuy: <https://www.bigbuy.eu/en/search?q=caneca%20personalizada>.
- Alibaba: <https://www.alibaba.com/trade/search?SearchText=caneca%20personalizada>.

Faltam material, preço, shipping por mercado, MOQ, personalização unitária e documentação de contacto alimentar.

Estado: `DOCUMENTAÇÃO DO FORNECEDOR EM FALTA`.

## Tapete yoga

Candidatos públicos:

- AliExpress: <https://www.aliexpress.com/item/1005005828306541.html> — tapete EVA identificável; `CANDIDATE`.
- Pesquisa AliExpress: <https://www.aliexpress.com/w/wholesale-tapete-de-yoga.html>.
- BigBuy: <https://www.bigbuy.eu/en/search?q=tapete%20de%20yoga>.
- Alibaba: <https://www.alibaba.com/trade/search?SearchText=tapete%20yoga>.

Faltam composição, preço, shipping PT/GB/US, MOQ, stock, prazo, política de devolução e documentação REACH/GPSR.

Estado: `DOCUMENTAÇÃO DO FORNECEDOR EM FALTA`.

## Velas aromáticas

Candidatos públicos:

- AliExpress: <https://www.aliexpress.com/item/1005005513262103.html> — vela aromática identificável; `CANDIDATE`.
- Pesquisa AliExpress: <https://www.aliexpress.com/w/wholesale-velas-aromaticas.html>.
- Printful, categoria: <https://www.printful.com/custom/candles/personalized> — categoria POD candidata.
- BigBuy: <https://www.bigbuy.eu/en/search?q=velas%20arom%C3%A1ticas>.
- Alibaba: <https://www.alibaba.com/trade/search?SearchText=velas%20arom%C3%A1ticas>.

Faltam composição da cera/fragrância, preço, shipping PT/GB/US, MOQ, stock, prazo, devoluções, classificação e documentação de segurança.

Estado: `DOCUMENTAÇÃO DO FORNECEDOR EM FALTA`.

## Registo obrigatório de fornecedor

Cada candidato deve ter os seguintes campos antes de poder passar o gate:

```json
{
  "supplier_id": null,
  "name": null,
  "company_country": null,
  "warehouse_country": null,
  "public_url": null,
  "url_kind": "DIRECT_PRODUCT | CATEGORY | SEARCH",
  "product_identity_validated": false,
  "ships_to": {"PT": null, "GB": null, "US": null},
  "product_price": null,
  "shipping_cost": {"PT": null, "GB": null, "US": null},
  "delivery_estimate": {"PT": null, "GB": null, "US": null},
  "moq": null,
  "stock": null,
  "dropshipping_allowed": null,
  "personalization": {
    "available": null,
    "file_submission_process": null,
    "accepted_formats": [],
    "extra_production_time": null,
    "single_unit": null,
    "neutral_packaging": null
  },
  "returns_policy": null,
  "compliance_documents": [],
  "eu_economic_operator": null,
  "risk": "POR VALIDAR",
  "checked_at": "2026-08-18"
}
```

## Gates progressivos

| Fase | Evidência mínima | Resultado possível |
|---|---|---|
| Pesquisa | produto não proibido e questão de IP identificada | `AVANÇAR PARA PESQUISA` |
| Teste sem venda | criativo e alegações validados; sem checkout nem encomendas | `APTO PARA TESTE SEM VENDA` |
| Venda teste | fornecedor/variante, requisitos materiais, custos e shipping confirmados | `APTO PARA VENDA TESTE` |
| Escala | margem observada, documentação consolidada, packaging/EPR e operação de devoluções | `APTO PARA ESCALA` |

Uma lacuna só bloqueia a fase em que se torna material. CE, documentação química, contacto alimentar ou validação especializada não são exigidos por defeito; aplicam-se conforme categoria, composição, utilização e claims.

## Gate para primeira venda

Um produto só pode receber `APTO PARA VENDA TESTE` quando todos estes dados aplicáveis estiverem confirmados e datados:

1. página individual e identidade do produto;
2. dropshipping permitido;
3. MOQ igual a 1 ou sem MOQ;
4. envio para o mercado escolhido;
5. preço da variante;
6. custo de transporte;
7. prazo de entrega;
8. política de devolução;
9. cálculo de margem completo;
10. ausência de bloqueio comercial evidente;
11. Legal Gate compatível com venda no âmbito analisado.

Os limiares de margem, prazo, lucro mínimo e CAC são configuração de negócio ainda não fornecida. O sistema não deve inventar 25%, `3 × CPC`, `±10%` ou valores equivalentes.

## Modelo financeiro

### Receitas e IVA

```text
RevenueNet = preço_gross / (1 + taxa_IVA_venda)
IVA_venda = preço_gross - RevenueNet
CustoProdutoNet = custo_faturado - IVA_input_recuperável
```

O modelo distingue armazém UE, importação pelo comerciante e envio direto de país terceiro. IOSS, IVA de importação e recuperabilidade são ramos condicionais; nunca se acrescenta IVA uma segunda vez sem confirmar o fluxo fiscal.

### Direitos e custos variáveis

```text
ValorAduaneiro = base confirmada segundo operação, origem, transporte e seguro
Direitos = ValorAduaneiro × taxa(HS/CN, origem, destino, data)

TaxasPagamento =
  taxa_fixa
  + preço_gross × taxa_variável
  + conversão_cambial
  + reserva_chargeback

ReservaDevoluções =
  probabilidade_devolução × custo_médio_da_devolução
```

Sem código HS/CN e origem confirmados, os direitos ficam `POR VALIDAR`.

### Margem

```text
ContributionBeforeAds =
  RevenueNet
  - CustoProdutoNet
  - personalização
  - fulfilment
  - transporte
  - direitos
  - IVAImportaçãoNãoRecuperável
  - TaxasPagamento
  - ReservaDevoluções
  - taxas_da_plataforma

CACBreakEven = ContributionBeforeAds
LucroApósPublicidade = ContributionBeforeAds - CAC_real
MargemPercentual = ContributionBeforeAds / RevenueNet
```

CPC não é CAC. Só pode existir `CAC_estimado = CPC / taxa_de_conversão` quando a taxa de conversão for observada ou explicitamente assumida.

## Cenários

| Cenário | Inputs |
|---|---|
| Conservador | Menor preço de venda confirmado e maiores custos confirmados realmente disponíveis |
| Base | Variante, cotação e preço de venda considerados representativos, todos datados |
| Otimista | Maior preço de venda e menores custos entre opções confirmadas realmente disponíveis |

Se faltar qualquer input material, o cenário mostra `INCOMPLETO`. Atualmente, os três cenários dos cinco produtos estão incompletos.

## Riscos

- Uma página acessível não prova stock, shipping ou autorização de dropshipping.
- A região do domínio ou redirecionamento não prova o destino de envio.
- O país da plataforma não prova a localização do armazém.
- MOQ da plataforma não prova MOQ da variante.
- Disponibilidade num marketplace não prova conformidade.
- Custos fiscais e aduaneiros dependem da operação e não devem ser hardcoded.

## Verificação

```bash
grep -c 'Estado:' docs/telegram-research/05-suppliers-and-margins.md
```
