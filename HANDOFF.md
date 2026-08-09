# HANDOFF — Ecom Research

## Estado

O projeto continua a ser um pipeline CLI determinístico e advisory. A revisão de robustez foi concluída sem alterar pesos, thresholds, labels (`AVANÇAR`, `OBSERVAR`, `DESCARTAR`), decisões de mercado, comandos existentes ou direcção send-only do Telegram.

O comportamento de negócio foi comparado diferencialmente com o commit anterior `155a3fabfe20b7f6aa02721601727214d5f28b17`: 5 cenários, 2 tentativas por versão, 20 execuções isoladas e 71 comparações exactas. Scores, labels, breakdowns, evidências, sequência de adapters, decisões e digests foram iguais. As diferenças são apenas o envelope de robustez documentado em `README.md`: `run_id`, timestamps, proveniência, nomes sem colisões e estado de entrega.

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

Último resultado: **289 testes passaram**; `compileall`, CLI help e `git diff --check` passaram. Todos os testes de rede usam mocks.

Validação diferencial final:

- baseline: commit `155a3fabfe20b7f6aa02721601727214d5f28b17`, **135 testes**;
- candidato: working tree endurecido, **289 testes**;
- resultado: **PASS**, zero divergências nas 71 comparações de negócio;
- `score.compute()` e os quatro componentes têm AST idêntica antes/depois.

## Riscos e decisões pendentes

1. Um crash depois de uma chamada paga e antes do checkpoint pode repetir essa chamada; exactly-once por fonte exigiria reservas/checkpoints externos ao processo.
2. O Telegram pode aceitar um chunk e o processo falhar antes da persistência local; esse chunk pode ser duplicado no retry.
3. O lock de quota fica activo durante o pedido ao provider. Isto preserva limites duros, mas serializa processos e pode aumentar latência.
4. Um batch com excepção técnica adia o Telegram final até todas as keywords terminarem; o digest parcial fica no stdout e no manifesto.
5. Uma segunda execução intencional no mesmo dia, com os mesmos inputs, é tratada como repetição. Um eventual `--force` precisa de decisão de produto.
6. CSV, ads históricos e preços reutilizados são marcados como stale, mas não expiram decisões automaticamente; impor TTL mudaria regras de negócio.
7. `data/runs/` e `data/digests/` ainda não têm política de retenção.

## Próxima sessão

1. Fazer um smoke live controlado de uma keyword e rever manualmente custo, relevância, pack e proveniência.
2. Validar uma entrega Telegram real e o fallback stdout sem expor credenciais.
3. Confirmar o CSV real do Keyword Planner antes de activar produção.
4. Decidir se são necessários `--force`, TTLs obrigatórios e retenção dos manifestos/digests.
5. Só depois instalar ou confirmar o cron diário `run.py --auto --digest`.

A aprovação humana continua obrigatória para produto, mercado, sugestões e passagem para ad-machine.

## Suggested skills

- `diagnose` — para qualquer divergência observada no smoke live.
- `tdd` — para alterações futuras aos modos, adapters, idempotência ou entrega.
- `security-review` — antes de alterar persistência de credenciais, Telegram ou integrações externas.
