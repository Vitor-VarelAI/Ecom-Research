# 06 — Legal Gate UE/Portugal e monitorização semanal

Razão de existência: impedir que procura, margem ou disponibilidade num marketplace sejam transformadas em autorização de venda sem evidência jurídica e documental.

Este documento define triagem técnica, não um parecer jurídico. O âmbito atual é União Europeia/Portugal. Reino Unido e Estados Unidos são mercados comerciais prioritários, mas a respetiva legislação fica fora desta fase e não pode herdar estados UE/PT.

## Fontes oficiais

| Tema | Fonte | Facto operacional |
|---|---|---|
| PPWR | [Regulamento (UE) 2025/40](https://eur-lex.europa.eu/eli/reg/2025/40/oj) | Entrou em vigor em 11/02/2025; aplicação geral em 12/08/2026, artigo 71.º |
| GPSR | [Regulamento (UE) 2023/988](https://eur-lex.europa.eu/eli/reg/2023/988/oj) | Aplicável desde 13/12/2024 |
| Fiscalização do mercado | [Regulamento (UE) 2019/1020](https://eur-lex.europa.eu/eli/reg/2019/1020/oj) | Artigo 4.º aplica-se aos produtos abrangidos pela legislação de harmonização indicada no regulamento; não substituir automaticamente o GPSR |
| Packaging/EPR PT | [APA — Embalagens](https://apambiente.pt/residuos/embalagens-e-residuos-de-embalagens) | Identificar quem efetua a primeira colocação no mercado nacional no fluxo concreto |
| EPR nacional | [DL 152-D/2017 consolidado](https://diariodarepublica.pt/dr/legislacao-consolidada/decreto-lei/2017-114350681) | Regime português de fluxos específicos sujeito a RAP |
| Safety Gate | [Portal oficial](https://ec.europa.eu/safety-gate-alerts/screen/home) | Uma pesquisa sem resultados não prova conformidade |
| REACH | [Regulamento (CE) 1907/2006](https://eur-lex.europa.eu/eli/reg/2006/1907/oj) | Aplicabilidade depende de substância, material e utilização |
| CLP | [Regulamento (UE) 2024/2865](https://eur-lex.europa.eu/eli/reg/2024/2865/oj) | Alterações relevantes para misturas e fragrâncias; composição continua indispensável |
| Contacto alimentar | [Regulamento (CE) 1935/2004](https://eur-lex.europa.eu/eli/reg/2004/1935/oj) | Aplica-se a artigos destinados ou previsivelmente usados em contacto com alimentos |
| Cerâmica | [Diretiva 84/500/CEE](https://eur-lex.europa.eu/eli/dir/1984/500/oj) | Aplicar apenas se a caneca for cerâmica |
| Têxteis | [Regulamento (UE) 1007/2011](https://eur-lex.europa.eu/eli/reg/2011/1007/oj) | Aplicabilidade depende da composição; camisola provavelmente abrangida, mas composição está por provar |
| Marcas | [INPI](https://inpi.justica.gov.pt) e [EUIPO](https://euipo.europa.eu) | É necessário guardar pesquisas concretas; a mera suspeita não confirma marca ou licença |

A designação portuguesa RERE deve acompanhar o PPWR nas mensagens destinadas ao operador português, sem transformar o PPWR num diploma nacional. O regime EPR/RAP português continua a exigir análise separada.

## Separação obrigatória

```text
Potencial comercial    → scoring advisory existente
Dropshipping            → fornecedor, MOQ, shipping e margem
Conformidade UE         → GPSR e regras específicas
Conformidade Portugal   → EPR/RAP e regras nacionais
Decisão final           → resultado dos gates
Decisão do operador     → escolha auditável, sem substituir os gates
```

Exemplo permitido:

```text
Potencial comercial: AVANÇAR
Legal Gate atual: DOCUMENTAÇÃO EM FALTA
Fase permitida: AVANÇAR PARA PESQUISA DE FORNECEDOR
Venda teste: AGUARDAR DOCUMENTAÇÃO APLICÁVEL
```

O estado jurídico descreve a evidência; o workflow determina até onde o produto pode avançar. Não ter documentos de um fornecedor ainda desconhecido não impede procurar esse fornecedor.

## Gates progressivos

| Fase | Verificação material | Resultado |
|---|---|---|
| Descoberta | excluir proibição evidente e identificar categoria/uso | `AVANÇAR PARA PESQUISA` ou hard block |
| Teste sem venda | validar IP, alegações e criativo; sem checkout ou encomendas | `APTO PARA TESTE SEM VENDA` |
| Venda teste | cumprir requisitos aplicáveis antes da primeira disponibilização, incluindo packaging/EPR quando aplicáveis | `APTO PARA VENDA TESTE` |
| Escala | operação, monitorização, devoluções, documentação e margens consolidadas | `APTO PARA ESCALA` |

Uma lacuna só bloqueia a fase em que é material. Campos irrelevantes recebem `NÃO APLICÁVEL` com justificação; não são tratados como documentos em falta.

- CE só é exigido para categorias abrangidas por legislação específica.
- Safety Gate sem correspondências não prova conformidade, mas também não bloqueia sozinho.
- Validação especializada aplica-se a risco real ou ambiguidade material, não a todos os produtos.
- EPR e obrigações de embalagem aplicáveis têm de estar resolvidos antes da primeira venda; não são adiados automaticamente para escala.
- Produtos de alto risco continuam bloqueados por defeito até existir evidência suficiente.

## Estados do Legal Gate

O armazenamento aceita apenas:

- `CONFORME PARA VENDA`;
- `PROVAVELMENTE CONFORME`;
- `DOCUMENTAÇÃO EM FALTA`;
- `RESPONSÁVEL UE EM FALTA`;
- `EPR EM FALTA`;
- `PACKAGING NÃO VALIDADO`;
- `MARCAÇÃO CE EM FALTA`;
- `REQUER VERIFICAÇÃO ESPECIALIZADA`;
- `PROIBIDO OU NÃO RECOMENDADO`;
- `REGRA EM TRANSIÇÃO`;
- `REGRA AINDA NÃO DEFINIDA`.

Regras de precedência:

1. prova de proibição ou risco material não mitigado → `PROIBIDO OU NÃO RECOMENDADO`;
2. categoria de risco ou questão que exige profissional → `REQUER VERIFICAÇÃO ESPECIALIZADA`;
3. falta de um documento aplicável à fase atual → escolher o estado específico; caso contrário `DOCUMENTAÇÃO EM FALTA`;
4. obrigação com calendário ativo → `REGRA EM TRANSIÇÃO`;
5. ato necessário ainda não adotado → `REGRA AINDA NÃO DEFINIDA`;
6. `PROVAVELMENTE CONFORME` e `CONFORME PARA VENDA` exigem evidência atual e suficiente para os requisitos realmente aplicáveis.

A ausência de dados nunca produz estado favorável, mas também não transforma uma regra não aplicável num bloqueio.

## GPSR

O gate recolhe:

- fabricante, nome e endereço;
- importador, quando aplicável;
- pessoa responsável nos termos do artigo 16.º;
- identificação do produto, lote ou modelo;
- avaliação de risco e documentação técnica;
- avisos, instruções e idioma português;
- fotografia/identificação visual;
- reclamações, acidentes, recall e cooperação com autoridades;
- informação da oferta online exigida pelo artigo 19.º.

O artigo 16.º define a pessoa responsável. O artigo 19.º regula a informação nas ofertas à distância, incluindo os contactos da pessoa responsável quando o fabricante não está estabelecido na UE. A inexistência desses campos no projeto significa `DOCUMENTAÇÃO EM FALTA`; não demonstra que o operador não existe.

## PPWR/RERE

Por produto e fornecedor, guardar separadamente:

- embalagem primária, secundária e de transporte;
- materiais e substâncias;
- reciclabilidade, reutilização e conteúdo reciclado;
- minimização e excesso de embalagem;
- rotulagem e informação ao consumidor;
- documentação e declaração aplicável;
- datas de aplicação e transições.

A reciclabilidade deve usar o artigo 6.º e atos associados: grau C, em regra, desde 01/01/2030; graus A/B desde 01/01/2038. Não atribuir essas datas ao artigo 67.º.

## EPR/RAP

O sistema determina, para cada fluxo comercial:

- quem disponibiliza a embalagem ou produto embalado pela primeira vez;
- país onde a embalagem se torna resíduo;
- vendedor, importador, marketplace e fornecedor envolvidos;
- registo, sistema individual/integrado e representante autorizado;
- quantidades, materiais, reporte e prestações financeiras;
- número de registo e evidência da entidade gestora.

Não assumir que o fornecedor chinês, marketplace ou dropshipper é sempre o único produtor. A qualificação depende das definições do artigo 3.º, n.º 15, do PPWR, do artigo 45.º e do fluxo concreto.

Portugal é o primeiro módulo. Outros Estados-Membros são configurações independentes; cumprir em Portugal não transfere conformidade EPR para outro país.

## Matriz preliminar dos cinco produtos

| Produto | Legal Gate atual | Próxima fase permitida | Bloqueio antes da venda |
|---|---|---|---|
| Mochila personalizada | `DOCUMENTAÇÃO EM FALTA` | `AVANÇAR PARA PESQUISA` | fabricante, materiais, uso/público, GPSR e packaging/EPR aplicáveis |
| Camisola Portugal | `REQUER VERIFICAÇÃO ESPECIALIZADA` | pesquisa jurídica/IP; publicidade com símbolos ou nomes fica suspensa | triagem INPI/EUIPO/titulares, composição/etiqueta, GPSR e packaging/EPR |
| Caneca personalizada | `DOCUMENTAÇÃO EM FALTA` | `AVANÇAR PARA PESQUISA` | material, contacto alimentar aplicável, GPSR e packaging/EPR |
| Tapete yoga | `DOCUMENTAÇÃO EM FALTA` | `AVANÇAR PARA PESQUISA` | composição, claims, REACH/GPSR e packaging/EPR aplicáveis |
| Velas aromáticas | `DOCUMENTAÇÃO EM FALTA` | `AVANÇAR PARA PESQUISA` | composição/classificação, avisos, GPSR e packaging/EPR aplicáveis |

Safety Gate permanece não conclusivo para todos até existir uma pesquisa guardada. Mesmo sem correspondências, o estado não se torna favorável.

## Propriedade intelectual

Para `camisola Portugal`, `Eusébio` e `Pantera Negra Portugal`, o estado atual é `VERIFICAÇÃO MANUAL NECESSÁRIA`. Não foi executada uma pesquisa de marcas específica capaz de sustentar `LICENÇA NECESSÁRIA`, `MARCA REGISTADA DETETADA` ou `POSSÍVEL CONTRAFAÇÃO`.

Cada pesquisa futura guarda termo, classe, território, titular, estado, URL oficial, data e evidência visual. Um produto existente num marketplace não demonstra autorização.

## Registo de evidência

```json
{
  "source_id": "<stable-id>",
  "instrument": "Regulamento (UE) 2023/988",
  "article": "19",
  "obligation": "informação da oferta à distância",
  "product_scope": ["<category>"],
  "country": "PT",
  "authority": "União Europeia",
  "official_url": "https://eur-lex.europa.eu/eli/reg/2023/988/oj",
  "published_at": null,
  "entered_into_force_at": null,
  "applies_from": "2024-12-13",
  "transition_end_at": null,
  "legal_state": "IN_FORCE",
  "checked_at": "<utc-timestamp>",
  "content_hash": "<sha256>",
  "confidence": "HIGH",
  "evidence_excerpt": "<exact-passage>",
  "missing_documents": []
}
```

## Monitorização legal semanal

### Fonte

Manter um registo allowlisted de EUR-Lex, Comissão Europeia, Safety Gate, APA, Diário da República, INPI, EUIPO e autoridades nacionais configuradas. Cada entrada define jurisdição, instrumentos, categorias e método de extração.

### Snapshot

Guardar URL final, status HTTP, ETag/Last-Modified, instante, hash, versão consolidada, datas jurídicas e excerto relevante. Um 404 ou redirecionamento é incidente de fonte, não alteração legal automática.

### Diff

Detetar:

- novo diploma ou alteração consolidada;
- mudança de artigo, data de aplicação ou transição;
- orientação/FAQ oficial revista;
- mudança EPR, registo ou categoria por país;
- nova restrição, proibição ou recall;
- alteração que mapeie para atributos de produtos guardados.

Mudança de HTML, navegação ou cookies não conta como mudança legal sem revisão do conteúdo normativo.

### Impacto

1. criar evento de alteração com fonte e diff;
2. localizar produtos por categoria, material, uso, público e país;
3. propor novo Legal Gate automático com confiança;
4. exigir revisão humana para estados favoráveis ou bloqueios materiais;
5. preservar estado anterior e legislação causadora;
6. editar a mensagem Telegram quando existir `message_id`;
7. enviar alerta separado com países e data de aplicação.

### Bloco semanal

```text
ALTERAÇÕES LEGAIS

Instrumento: <nome/número>
Alteração: <resumo factual>
Produtos afetados: <IDs>
Países: <lista>
Aplica-se a partir de: <data>
Estado anterior: <estado>
Estado proposto: <estado>
Fonte oficial: botão validado
Última verificação: <data>
```

## Riscos e limitações

- Esta triagem não substitui validação jurídica especializada.
- A composição e cadeia do fornecedor ainda não existem no projeto; a maioria dos gates fica bloqueada.
- Atos delegados e orientações podem completar obrigações do PPWR.
- EPR é nacional e aumenta o custo de manutenção por mercado.
- Portais oficiais mudam; hashes precisam de revisão semântica para evitar falsos alertas.
- Safety Gate tem risco de falsos negativos por termos, filtros e cobertura.
- Uma edição Telegram não substitui o histórico jurídico armazenado.

## Verificação

```bash
grep -c 'https://' docs/telegram-research/06-legal-gate-and-monitoring.md
```
