# HANDOFF — Ecom Research

## Estado

O projeto continua a ser um pipeline CLI determinístico e advisory. A fórmula de score, pesos, thresholds, labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`), decisões de mercado, comandos existentes e direcção send-only do Telegram não mudaram. A qualificação dos dados de entrada ficou mais restrita para rejeitar falsos positivos, e o digest passou a apresentar `AVANÇAR` explicitamente como sinal advisory em vez de instrução de venda.

A validação diferencial inicial contra `155a3fabfe20b7f6aa02721601727214d5f28b17` confirmou que o envelope de robustez — `run_id`, timestamps, proveniência, nomes sem colisões e estado de entrega — não alterava o comportamento de negócio. As correções operacionais posteriores mudaram intencionalmente a elegibilidade da evidência, o tratamento de moeda e a redação advisory, portanto as 71 comparações exactas dessa fase inicial já não descrevem o diff completo atual. A fórmula de score permanece inalterada; o comportamento atual está coberto por 561 testes e pelos artefactos live.

### Hardening operacional final

### Review ronda 3 (achados pendentes, não bloqueantes)

A ronda 3 da revisão independente levantou achados que ficam para análise posterior, depois de observarmos os primeiros digests automáticos. Nenhum foi confirmado como bloqueante nesta sessão, mas devem ser reavaliados com evidência real:

1. Packs legados reutilizados nos modos quarta/mensal podem carregar `source_url` de landing não sanitizada (`run.py` sanitiza `ads_data`, mas não o `landing_price` reutilizado).
2. `quota.py` falha aberta se a escrita da quota falhar (ex.: disco cheio): o charge reporta sucesso sem persistir.
3. Fetches de landing aceitam qualquer hostname HTTP(S) com redirects — sem defesa SSRF (hosts privados/loopback).
4. `keyword_csv._read_text` ainda pode levantar `UnicodeDecodeError` em UTF-8-BOM malformado, violando o contrato never-raise.
5. Números gigantes/não finitos em caches e scores relacionados (`trends`, `serp_seo`) sem guarda total.
6. `_is_inactive` no Apify ignora `is_active: false` quando `end_date` é futuro/ausente.
7. Sem resolver operacional para estados Telegram `state_ambiguous` — bloqueio permanente até intervenção manual no ficheiro.

## Próxima sessão

Foram concluídas as correções de relevância comercial, correspondência exata do CSV por produto, filtragem das sugestões Trends, proveniência e sanitização dos erros HTTP, moeda e URLs de landing, packs advisory e estado write-ahead do Telegram. URLs persistidas perdem query strings potencialmente sensíveis, moedas diferentes não são combinadas e estados de entrega ambíguos bloqueiam retries automáticos para evitar duplicação.

O fluxo Telegram mantém o token fora de logs, estado persistido e exceções; `.env` está a `0600` e ignorado pelo Git. `data/digests/` passa a ser criado/apertado a `0700` e os ficheiros de estado nascem a `0600`. A rotação do token exposto ficou adiada pelo operador e continua pendente, sem bloquear a operação.

## Onde está cada fluxo

- `run.py`: parser, precedência de modos, execução full/domingo/quarta/mensal/auto, idempotência e checkpoints por keyword.
- `pipeline/run_state.py`: identidade determinística, manifestos, escritas atómicas e locks de run.
- `pipeline/provenance.py`: estados `valid`, `missing`, `stale` e `failed`, mais políticas não secretas das fontes.
- `pipeline/output/pack.py`: Markdown/JSON run-scoped, metadados de proveniência e marcador `artifact_complete`.
- `pipeline/delivery/telegram.py`: send-only, chunks de 4000 caracteres, estado persistido e resume apenas dos chunks não enviados.
- `pipeline/quota.py`: quota SerpAPI/Firecrawl com escrita atómica e transacções bloqueantes/fail-closed.
- `pipeline/adapters/`: contratos, caches, timeouts e fallbacks por fonte; payloads inválidos ou de erro não são reutilizados como cache válido.
- `pipeline/scoring/score.py`: cálculo inalterado; `SCORING_VERSION` e snapshot da configuração são apenas metadados.

O diagrama e a descrição completa da arquitectura estão em `README.md`.

## Garantias agora cobertas

- Precedência fixa: `auto > sunday > wednesday > monthly > all > keyword`; selectores ou CSV ignorados produzem avisos determinísticos.
- Um `run_id` concluído não repete fontes pagas, packs ou digests. Domingo e `--all` gravam checkpoints e repetem apenas keywords com falha técnica.
- Falha mensal do Trends preserva o último pack `OBSERVAR`, portanto a keyword não desaparece da watchlist.
- Packs de modos/keywords diferentes não colidem; formatos legados continuam a ser lidos, mas novos packs usam nomes run-scoped.
- Manifestos, packs, caches e estados Telegram malformados são rejeitados sem serem tratados como evidência válida.
- Excepções de rede e falhas técnicas são sanitizadas; tokens e URLs sensíveis não entram em packs, manifestos, digests ou logs.
- O estado Telegram distingue sucesso, parcial, falha, configuração ausente e estado corrompido; chunks confirmados localmente não são reenviados.
- Quota e runs usam locks bloqueantes e fail-closed. Não existem retries novos nem alterações aos limites de chamadas.

## Verificação

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline
.venv/bin/python run.py --help
git diff --check
```

Último resultado: **561 testes passaram**; `compileall`, CLI help e `git diff --check` passaram. Todos os testes de rede usam mocks.

Validação final:

- baseline de robustez: commit `155a3fabfe20b7f6aa02721601727214d5f28b17`, **135 testes**;
- estado atual endurecido, **561 testes**;
- fórmula de score inalterada; elegibilidade da evidência, moeda e redação advisory corrigidas intencionalmente;
- smoke live, CSV real, entrega Telegram e scheduler validados.

## Riscos e decisões pendentes

1. Um crash depois de uma chamada paga e antes do checkpoint pode repetir essa chamada; exactly-once por fonte exigiria reservas/checkpoints externos ao processo.
2. O Telegram continua sem exactly-once remoto: se a aceitação ocorrer sem confirmação local, o estado write-ahead fica ambíguo e os retries são bloqueados até resolução manual, evitando duplicação automática.
3. O lock de quota fica activo durante o pedido ao provider. Isto preserva limites duros, mas serializa processos e pode aumentar latência.
4. Um batch com excepção técnica adia o Telegram final até todas as keywords terminarem; o digest parcial fica no stdout e no manifesto.
5. Uma segunda execução intencional no mesmo dia, com os mesmos inputs, é tratada como repetição. Um eventual `--force` precisa de decisão de produto.
6. CSV, ads históricos e preços reutilizados são marcados como stale, mas não expiram decisões automaticamente; impor TTL mudaria regras de negócio.
7. `data/runs/` e `data/digests/` ainda não têm política de retenção. `data/digests/` passou a `0700` (owner-only) nesta sessão; `data/runs/` continua com o umask default.

## Próxima sessão

1. **Aguardar os digests automáticos do cron** (domingo, quarta e dia 1 às 08:00 Lisboa) e observar o comportamento real em `data/cron.log`.
2. Reavaliar os achados da ronda 3 (lista acima) com base nas falhas observadas nos digests; corrigir apenas o que se confirmar relevante.
3. Rodar o token Telegram quando o operador estiver disponível e repetir apenas o teste de configuração/entrega, sem voltar a executar fontes pagas.
4. Decidir, sem bloquear produção, se são necessários `--force`, TTLs obrigatórios e retenção dos manifestos/digests.

O CSV real, o smoke live, a entrega Telegram e o cron diário `run.py --auto --digest` já estão validados. A aprovação humana continua obrigatória para produto, mercado, sugestões e passagem para ad-machine.

## Suggested skills

- `diagnose` — para qualquer divergência observada no smoke live.
- `tdd` — para alterações futuras aos modos, adapters, idempotência ou entrega.
- `security-review` — antes de alterar persistência de credenciais, Telegram ou integrações externas.
