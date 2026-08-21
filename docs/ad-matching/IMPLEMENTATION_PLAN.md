# Plano — matching produto↔anúncio v1

## Âmbito desta entrega

Esta entrega corrige a qualidade da evidência e do score. SQLite, Datasette e revisão manual ficam para a entrega seguinte, depois de os packs novos produzirem dados classificados.

## Contrato decidido

1. `apify_ads` limita a fonte, remove inativos, normaliza e deduplica todos os candidatos ativos; não decide se pertencem ao produto.
2. `pipeline/matching/product_ads.py` recebe o `Product` (research_keyword) e candidatos canónicos, devolvendo `matched`, `needs_review` ou `rejected` com confiança categórica, reason codes ordenados e `matcher_version`.
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

---

## Decisões resolvidas (pós-planeamento) — registadas nesta entrega

O planner encontrou ambiguidades; as decisões abaixo estão resolvidas, registadas aqui e cobertas por testes (ver `tests/test_product_ads.py`, `tests/test_apify_ads.py`, `tests/test_run_modes.py`, `tests/test_secret_sanitization.py`, `tests/test_monthly_preserve.py`, `tests/test_idempotency.py`).

1. **Gate 1 continua a limitar a fonte; gate 2 sai do filtro e passa a ser a classificação do matcher.** `_is_relevant` (token product-head da keyword — `suggestions.product_head_token` + `tokens_equivalent`, equivalência exacta ou plural de um só `s` — em qualquer parte do copy + texto do anunciante) permanece no adapter como única gate de fonte; o head é exigido para o ruído só-geográfico cair e nenhum limite de cobertura (≥60% nem todos os tokens) bloqueia menções genéricas de chegarem ao matcher. `_matches_high_intent` saiu do adapter — a classificação (matched/needs_review/rejected) vive em `pipeline/matching/product_ads.py`. Inativos continuam a cair antes dos candidatos; irrelevantes gate-1 continuam a cair; **todo** anúncio ativo, gate-1-relevante e deduplicado torna-se um candidato canónico persistido. O adapter nunca decide fit de produto (testes `test_gate2_is_no_longer_a_filter_body_only_mention_stays_a_candidate` e `test_gate1_head_only_reaches_matcher_geo_only_noise_drops`).

2. **Product-head ausente da prova comercial forte => `rejected`.** O head tem de estar num campo forte (title/headline/link_description/ad creative link titles/descriptions/URL sanitizada). Menção só no corpo/copy é rejeição fraca (fixture do concentrador de oxigénio).

3. **Head forte + qualquer token significativo restante (incluindo modificadores não geográficos como `personalizada` e `aromáticas`) ausente da prova forte ou só em copy/contexto fraco => `needs_review`.** Isto retira deliberadamente a antiga isenção de modificadores: `caneca personalizada` com title `Canecas da loja` era aceite e passa a `needs_review` (testes `TestBackpackFixtures` em `tests/test_product_ads.py`: porta-bebé, mochila escolar genérica e modifier só em copy fraca). Head + todos os tokens significativos com prova forte => `matched`.

4. **Qualificadores geográficos mantêm os campos geo-proof estritos** (title/headline/ad creative link titles + path/domínio da URL sanitizada). Head forte com geografia ausente/fraca => `needs_review`, nunca matched nem rejected (testes `TestGeographicProof` em `tests/test_product_ads.py`, migrados da antiga gate 2 com semântica de candidato classificado).

5. **`total_ads` = contagem matched.** Zero candidatos canónicos => `DESCARTAR`; zero matched com qualquer `needs_review` => `DADOS INSUFICIENTES`; zero matched com todos rejeitados => `DESCARTAR` (testes `test_zero_candidates_is_descartar_with_related_queries`, `test_zero_matched_with_needs_review_is_dados_insuficientes`, `test_zero_matched_all_rejected_is_descartar`).

6. **`candidates` é capped/invariante em <=100** (defensivo no adapter, `test_sample_cap_flag_and_candidate_cap`) e carrega classificação determinística, confiança categórica (high/medium/low), reason codes ordenados, `matcher_version` e evidência canónica sanitizada suficiente para auditar a decisão (`match_evidence` folded: commercial/geo/weak — nunca payloads brutos do actor, nunca URL crua com userinfo/query/fragment; `test_match_evidence_never_includes_raw_url_query_fragment_or_userinfo`). Objetos públicos JSON-safe e determinísticos.

7. **`top_ads`, `ad_ids`, `advertisers`, `copy_examples`, `saturation`, `global_market_proven` derivam só dos candidatos matched**, preservando as shapes existentes para landing/score/notificações/pulse (testes `test_matched_only_projections_full_counts_and_top_ads_sorting` e `test_saturation_and_global_proof_follow_matched_counts` em `tests/test_product_ads.py`).

8. **`matching_schema_version=1`, `MATCHER_VERSION` e contagens completas** (raw, inativos, gate-1 irrelevantes, candidatos canónicos, duplicados, matched, needs_review, rejected) são adicionadas ao resultado e ao pack. Campos flat úteis existentes (raw_ads_count, filtered_out_count, filtered_inactive_count, filtered_irrelevant_count, duplicates_removed) mantêm-se para compatibilidade; `total_ads` tem semântica explícita nova (matched).

9. **A fórmula numérica de score fica byte-for-byte inalterada**; `SCORING_VERSION` sobe para `2.0.0` porque a coorte de entrada mudou (matched-only). `MATCHER_VERSION` participa no run identity (`compute_run_id`), no manifest e na proveniência do pack (Markdown + JSON).

10. **Mensal/quarta nunca re-score nem comparam coortes sobre um pack legado sem `matching_schema_version`**: saltam fail-closed com razão explícita antes de qualquer call paga e preservam o pack anterior (testes `test_wednesday_skips_legacy_baseline_pack_fail_closed`, `test_monthly_skips_legacy_baseline_pack_fail_closed`; packs novos re-score/pulse normalmente — coberto pelo caminho mensal estabelecido `test_monthly_has_one_forced_trends_call_and_no_apify_or_landing` com baseline novo).

11. **`sanitize_ads_data` faz deep-copy e sanitiza `landing_url` em `candidates` e `top_ads`** (o link público da Ad Library sobrevive intacto), preservando a imutabilidade do input (`test_sanitize_ads_data_deep_copies_and_sanitizes_candidates_and_top_ads`, `test_run_keyword_persists_only_sanitized_candidate_landing_urls`).
