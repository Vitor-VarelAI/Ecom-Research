# Offer + Creative Arbitrage — estado de implementação

**Estado verificado:** 2026-09-08  
**Repositório:** `/home/vitor/projects/Ecom-Research`

## Estado actual

Está implementado um engine isolado em `pipeline/arbitrage/`, separado do pipeline Telegram e do restante fluxo de pesquisa. A configuração é carregada de forma fail-closed por `config/offers.json`.

`config/offers.json` está actualmente vazio:

```json
{
  "schema_version": 1,
  "base_currency": "EUR",
  "fx_rates": [],
  "offers": []
}
```

A fundação está pronta, mas ainda não existe uma oferta real operacional. Não há shortlist de negócio para executar nem validação paga autorizada neste estado.

## Fluxo local sem rede

O fluxo é determinístico e não faz chamadas de rede:

```text
load offers
  → resolve evidence
  → gates
  → Commercial Score + 48H Score
  → readiness decision
  → shortlist
```

Os buckets da shortlist são:

- `TEST_NOW`
- `RESEARCH_FURTHER`
- `WATCHLIST`
- `REJECT`

Os estados de readiness são:

- `REJECT`
- `RESEARCH_ONLY`
- `INSUFFICIENT_DATA`
- `NEEDS_VERIFICATION`
- `TESTABLE_48H`
- `WATCHLIST`

Evidência em falta, stale ou contradita não é convertida silenciosamente em zero. A decisão permanece em pesquisa/verificação até haver prova suficiente; gates falhados e contribuição não positiva podem levar a `REJECT`.

## Budget fit e decisão de 48 horas

O `budget_fit` mede o compromisso absoluto em EUR, sem inferir tráfego ou orçamento de anúncios:

| Compromisso absoluto | Pontuação |
|---:|---:|
| `<=€20` | `100` |
| `<=€50` | `75` |
| `<=€100` | `50` |
| `<=€250` | `25` |
| `>€250` | `0` |

`TESTABLE_48H` exige `Commercial Score >= 70`, `48H Score >= 70`, contribuição por encomenda positiva e `budget_fit >= 75`. Por isso, acima de €50 a oferta não chega a `TESTABLE_48H`.

## Comandos

### Shortlist local

```bash
python run.py --offer-shortlist
```

Com o `config/offers.json` actual, o resultado verificado é:

```json
{
  "TEST_NOW": [],
  "RESEARCH_FURTHER": [],
  "WATCHLIST": [],
  "REJECT": []
}
```

### Validação explícita

```bash
python run.py --validate-offer <offer_id> --openseo-project-id <project_id>
```

Esta é a única entrada para OpenSEO/DataForSEO. Antes de qualquer HTTP, a oferta é novamente carregada, a evidência local é resolvida e a decisão tem de ser exactamente `TESTABLE_48H / TEST_NOW`. Uma oferta vazia, desconhecida ou abaixo desse threshold falha fechado e não chama o provider.

O endpoint pode ser configurado por `OPENSEO_MCP_URL` ou por `--openseo-mcp-url`; o default local é `http://127.0.0.1:3001/mcp`. O project ID pode vir de `OPENSEO_PROJECT_ID`, embora o comando explícito acima seja o caminho documentado para autorizar a operação.

## Guardrails OpenSEO/DataForSEO

A validação paga só permite:

- `get_keyword_metrics`, com uma keyword e `includeClickstreamData=false`;
- `get_serp_results`, com uma query;
- localização e idioma derivados do `market_id` suportado.

Não são usados backlinks, domain overview, rank tracking ou qualquer outra ferramenta. O cliente HTTP não faz retries automáticos. A operação é opt-in, porque a validação pode gerar custo DataForSEO.

## Ledger e idempotência

Cada validação usa um artefacto em:

```text
data/offer-validations/YYYY-MM-DD/
```

O ficheiro inclui o estado por ferramenta e um ledger de transições. Antes de cada chamada, a ferramenta fica `running`; depois de uma resposta válida fica `completed`; falhas ficam registadas sem expor detalhes sensíveis. Ao retomar uma validação, uma ferramenta já `completed` é saltada e não é repetida. O endpoint, tokens, passwords e outras chaves sensíveis são removidos do JSON persistido; nenhuma credencial é guardada.

## Ficheiros principais

- `config/offers.json` — configuração versionada das ofertas; actualmente sem ofertas.
- `pipeline/arbitrage/config.py` — loader e validação fail-closed da configuração.
- `pipeline/arbitrage/models.py` — modelos imutáveis e contrato de campos.
- `pipeline/arbitrage/evidence.py` — resolução read-only de facts, packs locais e frescura da evidência.
- `pipeline/arbitrage/gates.py` — gates comerciais, de acesso, direitos, payout/spread e readiness US.
- `pipeline/arbitrage/scoring.py` — `Commercial Score`, `48H Score`, economia e `budget_fit`.
- `pipeline/arbitrage/decision.py` — estados de readiness, buckets e precedência fail-closed.
- `pipeline/arbitrage/workflow.py` — shortlist determinística em quatro secções.
- `pipeline/arbitrage/openseo.py` — cliente MCP sem retries e com erros sanitizados.
- `pipeline/arbitrage/validation.py` — autorização pós-decisão e ledger idempotente.
- `run.py` — entradas CLI `--offer-shortlist` e `--validate-offer`.
- `.env.example` — `OPENSEO_PROJECT_ID` e `OPENSEO_MCP_URL`.
- `tests/arbitrage/` — testes focados do engine, scoring, gates, workflow, cliente e ledger.

## Verificação

Em 2026-09-08:

- O comando documentado `python run.py --offer-shortlist` devolve as quatro secções vazias; a execução verificada neste checkout usou `.venv/bin/python run.py --offer-shortlist`, porque `python` não está disponível no `PATH`.
- A verificação final fica adiada por pedido do utilizador e nenhum teste adicional será executado neste fecho.
- Não foi executada a suite completa nesta verificação.
- Nenhuma chamada OpenSEO/DataForSEO live foi executada nesta implementação.

## Próximos passos

1. Preencher a primeira oferta em `config/offers.json` com evidência real e verificável.
2. Executar `python run.py --offer-shortlist`.
3. Só se a decisão local for `TESTABLE_48H / TEST_NOW`, executar a validação explícita via DataForSEO.
