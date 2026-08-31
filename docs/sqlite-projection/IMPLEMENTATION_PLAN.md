# Implementação — projeção SQLite idempotente + explorer Datasette read-only

Estado: integrada na `main` até `bcd3d50` e verificada em 2026-08-30. Este documento regista o contrato implementado e o runbook; não é trabalho pendente.

## Âmbito desta entrega

Adicionar uma projeção SQLite descartável e idempotente dos packs JSON
classificados atuais, mais um explorer Datasette read-only. Os packs
(`data/*.json`) permanecem evidência canónica e **nunca são reescritos**:
a projeção importa-os transacionalmente, chaveados por `(run_id,
product_id, market_id)`, e não substitui a prova. Não há chamadas Apify/Telegram live,
não há rescoring/reclassificação e nenhum pack/JSON histórico muda.

## Contrato decidido

1. Novo pacote focado `pipeline/projection/` (validação, store, datasette,
   CLI) + CLI raiz separada `explore.py`; **sem** acoplamento a `run.py` ou
   `config/products.json`.
2. Só são importados packs completos classificados **atuais**:
   - `run_id`, `product_id` e `market_id` top-level não vazios, com snapshot `market` consistente;
   - `artifact_complete == true`;
   - `date` ISO válida e `score.label` num dos labels advisory
     (`AVANÇAR`/`OBSERVAR`/`DESCARTAR`/`DADOS INSUFICIENTES`);
   - `facebook_ads.matching_schema_version == MATCHING_SCHEMA_VERSION`
     (o marcador top-level aditivo, sozinho, não reivindica atualidade);
   - candidatos classificados presentes (`candidates` lista; cada entrada
     com `match.label` conhecido — lista vazia é sucesso válido).
3. Packs legados válidos (sem `matching_schema_version` ou com versão
   antiga) são **saltados read-only** e reportados com razão clara. JSON
   malformado, ou um pack que reivindica a schema atual mas viola a shape
   exigida, **falha fechado** com erro acionável — nunca é rotulado legado.
   Números projetados respeitam o intervalo integer signed 64-bit do SQLite;
   erros de shape mostram campo, posição e tipo, nunca o valor cru do pack.
4. Chave de pack `(run_id, product_id, market_id)`; fingerprint SHA-256 sobre o JSON
   canónico completo do pack (`sort_keys`, separadores compactos,
   `ensure_ascii=False`). Reimportar conteúdo idêntico é no-op; mesma
   identidade com hash diferente levanta `PackConflictError` específico e
   deixa as linhas existentes intactas.
5. Importação atómica por pack: linha do pack + todas as linhas candidato
   numa única transação SQLite, `PRAGMA foreign_keys = ON` e schema version
   fixada via `PRAGMA user_version`. A validação partilhada por import e serve
   rejeita versão, integridade, tipos/afinidades, `NOT NULL`, chaves compostas
   ou `ON DELETE CASCADE` incompatíveis e instrui o operador a reconstruir a
   base descartável. Chave de candidato inclui a posição na lista
   (`ad_archive_id` pode faltar).
6. Projeção copia valores já sanitizados verbatim (sem sanitizadores de URL,
   sem derivar score ou decisões de match). Campos JSON guardados como texto
   JSON canónico.
7. CLI: `explore.py import [--db data/ecom_research.db] [--data-dir data]`
   (descoberta determinística de `data/*.json`) e
   `explore.py serve [--db ...] [--host 127.0.0.1] [--port 8001]`
   (recusa base ausente e lança Datasette em modo imutável/read-only).
   Construção do argv testável sem lançar servidor.
8. Metadados Datasette com queries read-only úteis: últimos packs,
   candidatos needs_review e anunciantes matched. Bind localhost por
   omissão. Dependência `datasette` em `requirements.txt`.
9. `data/*.db`, `data/*.db-wal`, `data/*.db-shm` ignorados no Git.

## Comportamento sem packs classificados atuais

Se `data/` contiver apenas packs legados ou ficheiros auxiliares,
`explore.py import` cria uma projeção válida vazia, reporta todos os skips
read-only e termina com **exit 0**. Quando existirem packs com
`matching_schema_version` atual e candidatos classificados, estes são
importados normalmente.

## Estrutura

- `pipeline/projection/validation.py` — classificação current/legacy/
  not-a-pack, fingerprint canónico, falha fechada.
- `pipeline/projection/store.py` — `ProjectionStore` (schema, user_version,
  transações, idempotência, conflito, rollback) e projeção verbatim.
- `pipeline/projection/datasette.py` — metadados + `build_serve_argv`.
- `pipeline/projection/cli.py` — `import`/`serve`.
- `explore.py` — entrada raiz.

## Testes

- `tests/test_projection_validation.py` — classificação atual vs legado,
  skip de não-pack, JSON malformado/claims-current-invalid fail-closed,
  fingerprint canónico, integer signed 64-bit e razões sem valores crus.
- `tests/test_projection_store.py` — `user_version`, integridade, tipos,
  nulabilidade, chaves e ação FK, projeção verbatim, JSON canónico, chave por
  posição, no-op, conflito, rollback e classificação interna forjada.
- `tests/test_projection_cli.py` — import/report/conflito, inteiro gigante
  sem traceback, redação de valores, argv serve imutável, base ausente/schema
  errado e metadados com canned queries.

## Verificação

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline tests explore.py
git diff --check
```

Fecho de 2026-08-30: **138 testes da projeção** e **932 testes totais**
verdes; `compileall`, `pip check` e `git diff --check` limpos. Um smoke com
base temporária leu 50 JSON, importou 16 packs/973 candidatos, saltou 34
ficheiros read-only e executou uma query HTTP real pelo Datasette com
`--immutable`. O servidor local foi terminado e a base removida. Não houve
chamadas Apify/Telegram nem alteração dos packs canónicos.
