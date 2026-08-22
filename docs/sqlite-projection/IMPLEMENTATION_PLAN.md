# Plano — projeção SQLite idempotente + explorer Datasette read-only

## Âmbito desta entrega

Adicionar uma projeção SQLite descartável e idempotente dos packs JSON
classificados atuais, mais um explorer Datasette read-only. Os packs
(`data/*.json`) permanecem evidência canónica e **nunca são reescritos**:
a projeção importa-os transacionalmente, chaveados por `(run_id,
product_id)`, e não substitui a prova. Não há chamadas Apify/Telegram live,
não há rescoring/reclassificação e nenhum pack/JSON histórico muda.

## Contrato decidido

1. Novo pacote focado `pipeline/projection/` (validação, store, datasette,
   CLI) + CLI raiz separada `explore.py`; **sem** acoplamento a `run.py` ou
   `config/products.json`.
2. Só são importados packs completos classificados **atuais**:
   - `run_id` e `product_id` top-level não vazios;
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
4. Chave de pack `(run_id, product_id)`; fingerprint SHA-256 sobre o JSON
   canónico completo do pack (`sort_keys`, separadores compactos,
   `ensure_ascii=False`). Reimportar conteúdo idêntico é no-op; mesma
   identidade com hash diferente levanta `PackConflictError` específico e
   deixa as linhas existentes intactas.
5. Importação atómica por pack: linha do pack + todas as linhas candidato
   numa única transação SQLite, `PRAGMA foreign_keys = ON` e schema version
   fixada via `PRAGMA user_version`. Schema incompatível instrui o operador
   a apagar/reconstruir a base descartável. Chave de candidato inclui a
   posição na lista (`ad_archive_id` pode faltar).
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

## Expectativa zero-current-classified-packs

Nenhum pack atual em `data/` carrega `matching_schema_version`/candidatos
classificados (todos são pré-classificação ou legacy). Portanto, na data
desta entrega, `explore.py import` importa **zero** packs e salta todos
read-only — comportamento documentado e **exit 0**, não erro. A projeção
começa a encher quando o pipeline voltar a produzir packs com a schema de
matching atual.

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
  skip de não-pack, JSON malformado falha fechado, claims-current-invalid
  falha fechado, fingerprint canónico estável.
- `tests/test_projection_store.py` — schema/user_version e instrução de
  rebuild, projeção verbatim, JSON canónico, chave por posição, no-op
  idêntico, conflito com linhas intactas, rollback de inserção de candidato,
  bytes da fonte inalterados.
- `tests/test_projection_cli.py` — import/report/conflito, argv serve
  imutável, recusa de base ausente/schema errado, metadados com canned
  queries.

## Verificação

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q run.py pipeline tests explore.py
git diff --check
```

Nenhuma chamada de rede/Apify/Telegram; nenhum servidor Datasette
persistente é lançado na verificação.
