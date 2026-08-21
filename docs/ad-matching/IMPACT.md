# Impacto — matching produto↔anúncio e evidência completa

## Alvo

Separar o matching de produto do adapter `pipeline/adapters/apify_ads.py`, persistir todos os candidatos ativos canónicos e fazer score apenas sobre anúncios classificados como `matched`.

## Dependentes

- `run.py`: duas chamadas a `apify_ads.fetch`, três chamadas a `score.compute` e três fronteiras de sanitização.
- `pipeline/adapters/landing_price.py`: consome `top_ads` para até três fetches de landing.
- `pipeline/output/pack.py`: renderiza `total_ads`, `top_ads`, anunciantes, saturação e sinais por mercado.
- `pipeline/notifications/builder.py`: expõe `total_ads`, saturação e anunciantes no outbound.
- `run.py` pulse mensal/quarta: reutiliza packs históricos, compara `ad_ids`/anunciantes e recalcula scores.
- `pipeline/run_state.py`: o `run_id` inclui `scoring_version`; a identidade deve incluir também a versão do matcher.
- 19 ficheiros de teste referenciam o contrato de ads, score, packs, sanitização ou modos incrementais.

## Cobertura existente

- `tests/test_apify_ads.py`: normalização, atividade, relevância, deduplicação, geografia e payload do actor.
- `tests/test_scoring.py`: fórmula e componentes.
- `tests/test_run_modes.py`: branches full/zero/failure, landing e sanitização.
- `tests/test_pack.py`, `tests/test_pack_metadata.py`: JSON/Markdown e proveniência.
- `tests/test_secret_sanitization.py`: URLs e segredos.
- `tests/test_monthly_preserve.py`, `tests/test_product_watchlist_selection.py`: histórico e preservação.

## Lacunas

- Não existem fixtures dos falsos positivos reais de `mochila personalizada`.
- O adapter descarta anúncios antes de criar candidatos auditáveis.
- Os detalhes completos dos candidatos não são persistidos.
- A sanitização cobre apenas `top_ads`.
- Packs legados sem classificação podem ser reinterpretados por um score novo.

## Risco: alto

O contrato é partilhado por execução completa, pulse semanal, re-score mensal, packs, notificações e 19 módulos de teste. Uma alteração parcial pode persistir URLs cruas, contaminar o score ou reinterpretar história incompatível.

## Ação recomendada

Implementar numa worktree isolada, por TDD e commits pequenos. Normalizar todos os anúncios ativos antes do matching; classificar todos os candidatos canónicos; manter `top_ads` como projeção exclusiva dos `matched`; sanitizar `candidates` e `top_ads` na mesma mudança; impedir re-score de packs legados; verificar a suite completa em cada checkpoint relevante.

## Estado

Este documento é a análise de impacto original (pré-entrega). A implementação está completa na worktree `feature/ad-matching-evidence` (ainda não merged); o contrato atual e as decisões resolvidas estão registados em `docs/ad-matching/IMPLEMENTATION_PLAN.md` (secção "Decisões resolvidas"). SQLite, Datasette e revisão manual ficam para a entrega seguinte.
