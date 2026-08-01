# ECOM-RESEARCH

CLI determinístico para transformar uma keyword num research pack. O pipeline usa anúncios ativos existentes como prova de procura; o score organiza sinais, mas não escolhe produto, país ou campanha. Qualquer sugestão é advisory e precisa de aprovação manual.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

O modo full requer `APIFY_API_TOKEN` e `SERPAPI_KEY`. O repositório não contém credenciais Telegram localmente e o `.env` está ignorado pelo Git. Não alteres `config/keywords.txt` para aceitar sugestões: elas aparecem no pack e no digest com `status: proposed`.

- `APIFY_API_TOKEN`: token do Apify para `curious_coder~facebook-ads-library-scraper`.
- `SERPAPI_KEY`: Trends e snapshot SEO.
- `FIRECRAWL_KEY`: fallback opcional para landings sem preço no HTML.
- `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`: entrega opcional, sempre send-only.

O volume e CPC vêm de um export CSV manual do Google Keyword Planner colocado em `data/`. Sem CSV, o pipeline degrada graciosamente, mas o score perde esse sinal.

## Uso

```bash
.venv/bin/python run.py "mochila personalizada"
.venv/bin/python run.py --all
.venv/bin/python run.py --sunday
.venv/bin/python run.py --wednesday
.venv/bin/python run.py --monthly
.venv/bin/python run.py --auto --digest
```

`--sunday` lê as keywords ativas sem as reescrever. `--auto` executa todas as ações que coincidirem com a data: no dia 1 executa primeiro o re-score mensal; depois executa domingo ou quarta-feira quando aplicável. Cada ação produz o seu próprio digest.

## Facebook Ad Library via Apify

Cada keyword faz uma única chamada POST, com custo do actor limitado a uma invocação e sample de 100, com timeout de conexão/leitura `(10, 135)`, `count=100` e uma URL global da Ad Library com `active_status=active`, `ad_type=all`, `country=ALL`, `is_targeted_country=false`, `media_type=all`, `search_type=keyword_unordered` e `q` URL-encoded. Não há retries.

A resposta é normalizada localmente com filtros determinísticos: status inativo é removido, texto é accent-folded, uma keyword exige o token e queries com vários tokens exigem pelo menos 60% de cobertura, com a frase exata sempre aceite. Duplicados são removidos por `ad_archive_id`, depois URL, depois `page_id + copy + date`. O pack guarda amostra, descartes, duplicados, IDs, top ads, advertisers e saturação; o limite de 100 permite que `alta` (>50) seja atingível.

Um resultado vazio é sucesso e força `DESCARTAR`, sem SEO nem landing. Ainda faz uma call Related Queries da SerpAPI, cacheada por 30 dias e contada na quota, para propor alternativas. Uma falha do Apify continua Trends, SEO e CSV, mas produz `DADOS INSUFICIENTES` e nunca um `DESCARTAR` falso.

## Custos e modos

| Modo | Apify | SerpAPI | SEO/landing | Dados usados |
|---|---:|---:|---|---|
| Full / domingo | 1 por keyword | Timeseries + related, salvo cache | Ambos, exceto falha Apify ou zero ads | Fresh |
| Quarta | 1 chamada paga/bounded por keyword elegível | 0 | 0 | Trends, CSV, SEO, landing, sugestões e score anteriores |
| Dia 1 | 0 | 1 timeseries forçada por keyword | 0 | Ads, CSV, SEO, landing e sugestões anteriores |

A quarta compara `ad_ids` e advertisers, mas se o Apify falhar relata o erro e preserva o último pack bom. O mensal é um caminho de rescore separado: não faz refresh de anúncios nem chama Firecrawl.

## Sugestões e mercados

`pipeline/suggestions.py` normaliza resultados Related Queries numéricos e `Breakout`, ordena Breakout primeiro e depois valores numéricos descendentes, remove vazios, a keyword original, duplicados, URLs e interrogativas. O JSON guarda `suggested_keywords`; o Markdown usa a secção **Sugestões de keywords — aprovação manual** e declara que nada foi adicionado a `config/keywords.txt`.

A secção **Sinais por mercado — decisão manual** lista PT, ES, FR e os top geos de Trends com `country`, `Trends demand`, `advertisers observed` e `ads observed`. Não existe ranking automático de país. O digest diz `Mercado: decisão manual`.

## Landings e Telegram

Preços usam exclusivamente `top_ads[].landing_url`; links da Ad Library são rejeitados. São tentadas no máximo três landings, primeiro por HTTP e depois por Firecrawl, com falhas isoladas e sem retries. Quarta e mensal reutilizam o resultado anterior.

Telegram é send-only: cada mensagem é plain text, no máximo 4000 caracteres, dividida em quebras de linha quando possível. O payload contém apenas `chat_id`, `text` e `disable_web_page_preview=true`. Falta de credenciais, timeout, HTTP diferente de 200, JSON inválido ou `ok != true` imprime o digest completo no stdout e não interrompe o pipeline. O token nunca é escrito em logs.

## Saída

- `packs/YYYY-MM-DD--<keyword>.md`: decisão, evidência, sinais de mercado, sugestões e próximo passo advisory.
- `data/YYYY-MM-DD--<keyword>.json`: raw pack com a chave `facebook_ads` e `suggested_keywords`.
- `data/cache/`: cache de Trends/SEO/Related Queries por 30 dias.
- `data/quota.json`: contador SerpAPI e créditos Firecrawl.

As recomendações são operacionais, não decisões automáticas: `AVANÇAR` prepara o ad-machine após aprovação manual, `OBSERVAR` mantém watchlist/recheck, `DESCARTAR` arquiva salvo nova evidência e `DADOS INSUFICIENTES` pede retry do Apify.

## Verificação

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall run.py pipeline
.venv/bin/python run.py --help
git diff --check
```

Todos os testes mockam rede; não são feitas chamadas reais durante a verificação. O último baseline é de **124 testes verdes**.

## Ativação live

O código não instala cron automaticamente. Antes de ativar:

1. Configurar e testar `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
2. Validar o CSV real do Keyword Planner.
3. Executar uma única keyword live e rever manualmente o Markdown/JSON, incluindo relevância, dedupe, custo Apify, landing e sugestões.
4. Regenerar antes de produção qualquer credencial que tenha sido partilhada numa conversa.
5. Só então instalar o cron diário das 08:00:

```cron
0 8 * * * cd /home/vitor/projects/Ecom-Research && /home/vitor/projects/Ecom-Research/.venv/bin/python run.py --auto --digest >> /home/vitor/projects/Ecom-Research/data/cron.log 2>&1
```

O cron verifica a data diariamente, mas só atua ao domingo, à quarta-feira e no dia 1 do mês.
