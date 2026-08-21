# Plano — matching produto↔anúncio v1

## Âmbito desta entrega

Esta entrega corrige a qualidade da evidência e do score. SQLite, Datasette e revisão manual ficam para a entrega seguinte, depois de os packs novos produzirem dados classificados.

## Contrato decidido

1. `apify_ads` limita a fonte, remove inativos, normaliza e deduplica todos os candidatos ativos; não decide se pertencem ao produto.
2. `pipeline/matching/product_ads.py` recebe o `Product` e candidatos canónicos, devolvendo `matched`, `needs_review` ou `rejected` com confiança categórica, reason codes ordenados e `matcher_version`.
3. Matching exige o product-head num campo comercial forte. Os restantes tokens semânticos da keyword também precisam de prova forte; presença apenas em copy ou prova parcial produz `needs_review`. Ausência do product-head produz `rejected`. Qualificadores geográficos mantêm a prova estrita existente.
4. `candidates` guarda até 100 candidatos ativos canónicos. `top_ads`, `ad_ids`, `advertisers`, `copy_examples`, `saturation` e `global_market_proven` passam a ser projeções apenas dos `matched`, para que landing, score, notificações e pulse não consumam anúncios ambíguos.
5. O pack guarda contagens separadas de raw, inativos, candidatos canónicos, duplicados, matched, needs_review e rejected. URLs persistidas em `candidates` e `top_ads` passam pela mesma sanitização.
6. A fórmula numérica de score não muda. `SCORING_VERSION` sobe porque a coorte de entrada muda, e `MATCHER_VERSION` entra no `run_id` e na proveniência.
7. Zero candidatos ativos continua `DESCARTAR`. Zero matched com candidatos `needs_review` produz `DADOS INSUFICIENTES`; zero matched com todos rejeitados produz `DESCARTAR`. Um pack legado sem `matching_schema_version` nunca é reclassificado nem re-scored sob a versão nova.

## Sequência TDD e rollback

1. Adicionar fixtures do matcher, incluindo mochila personalizada real, mochila porta-bebé, mochila escolar genérica e concentrador de oxigénio “com mochila”. Commit RED separado.
2. Implementar o módulo de matching e a projeção matched até os testes ficarem verdes. Commit GREEN.
3. Alterar o adapter para emitir candidatos ativos canónicos antes de matching e atualizar o contrato de contagens. Adicionar testes antes do código; checkpoint verde.
4. Integrar no `run.py`, tornar `top_ads`/agregados matched-only, versionar run/proveniência e tratar zero matched/legado. Adicionar testes antes do código; checkpoint verde.
5. Estender sanitização a `candidates`, com testes de userinfo/query/fragment e garantia de não mutação. Este passo entra no mesmo checkpoint funcional que começa a persistir candidatos.
6. Atualizar JSON/Markdown, README/PLAN e compatibilidade dos modos mensal/quarta. Verificar testes focados, `compileall`, `git diff --check` e a suite completa.

Cada checkpoint fica num commit Conventional Commit dentro da branch `feature/ad-matching-evidence`; o `main` e os packs existentes não são alterados. O rollback é `git revert` do checkpoint ou remoção integral da worktree/branch antes de merge.
