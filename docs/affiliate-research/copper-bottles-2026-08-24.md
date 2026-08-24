# Copper bottles — pesquisa afiliada verificada (2026-08-24)

Produto: `copper bottles` (`product_id=433621eb147d`), mercado escolhido pelo operador: **US**.
Estado: **teste técnico concluído; nenhuma candidatura submetida, nenhuma oferta ativa,
gate escuro**. O operador confirmou que este catálogo serve apenas para testar o schema
e o gate; não existe intenção nem tarefa pendente de inscrição. Qualquer candidatura futura
exige um novo pedido explícito. Todas as ofertas registadas em
`config/affiliate_offers.json` têm `status: candidate`,
`affiliate_url_env: null` (sem referências a variáveis de ambiente) e o produto tem
`approved: false` — mesmo que a aprovação do produto mudasse por engano, o gate
continuaria sem emitir porque não existe nenhuma oferta `active`.

Regra de ouro aplicada: só factos oficiais re-verificados durante a implementação;
tudo o que é desconhecido ou conflituoso fica `null` no schema (nunca é omitido e
nunca é inventado um valor de um range ou conflito).

## PUR Copper (purcopper.com) — primary

- Página oficial do programa: https://purcopper.com/pur-brand-ambassador-earn-15-commission-sharing-copper-water-bottles/
- Destino verificado: https://purcopper.com/product/copper-water-bottle/ (título "COPPER WATER BOTTLE")
- Comissão: **15% por venda** — citação oficial: *"15% commission on each product sold using your unique referral link"*.
- Shipping US/CA: *"$10 Flat Rate Shipping Across North America 🇺🇸 🇨🇦"* (cabeçalho do site e página de shipping) — confirma `["US", "CA"]`.
- **Desconhecido (null):** cookie duration, payout eligibility, asset rights, traffic policy.
- Evidência: a página do programa acima; `checked_at 2026-08-24`, `valid_until 2026-11-22`.

## SAMA Homes (samahomes.ca) — primary

- Página oficial do programa: https://samahomes.ca/pages/affiliate-program
- Candidatura Shopify Collabs: https://samahomes.ca/pages/collab
- Destino verificado: https://samahomes.ca/products/pure-copper-water-bottle-jumbo-1300ml-ayurvedic ("Pure Copper Water Bottle 1300ML | Jumbo Ayurvedic Vessel")
- Comissão: **conflito/range → null**. Citação oficial: *"10% - 15% Affiliate Commission"*. O campo de comissão é de valor único; um range não pode ser representado sem inventar um número.
- Cookie: **30 dias** — *"30 Days Cookie Lifetime"*.
- Payout: *"Automated Monthly Payouts"* → registado como "Pagamentos automáticos mensais via Shopify Collabs".
- Shipping US: *"We allow influencers, wellness studios, aesthetic spas, and spiritual pujaris in India, Australia, Sweden, Norway, Denmark, Finland, Canada, and the United States"* + *"SAMA Homes fulfills all international shipping and customs clearing directly"* → `["US"]`.
- Asset rights: *"Once approved, immediately access your personalized dashboard, tracking links, custom discount codes, and premium marketing images"*.
- Traffic policy (restrição de cupões/diretórios): *"Can I post my discount code on coupon sites? Absolutely not. SAMA Homes operates a strict zero-tolerance policy against coupon aggregators, browser extensions, and discount directories."*
- **Risco de health claims (SAMA):** a página promove efeitos de saúde ("oligodynamic effect", "natural antimicrobial benefits of metals", "charged in SAMA Homes unlined copper vessels"). Nada disto foi persistido no config; nenhum claim de saúde pode ser usado em conteúdo.

## CopperWater.com — primary

- Página oficial do programa: https://copperwater.com/pages/affiliateprogram (Shopify Collabs: "Apply now / Already a member? Log in")
- Destino verificado: https://copperwater.com/products/the-copper-water-bottle ("Handmade Polished Pure Copper Ayurvedic Water Bottle")
- Comissão/cookie/payout/asset/traffic: **desconhecidos → null**. A única promessa oficial é vaga: *"Earn a percentage for each bottle sold through your advocacy"*. A disponibilidade do programa Collabs está verificada.
- Shipping: *"All orders are carefully inspected, gift wrapped and shipped from the US. Free shipping within the contiguous U.S."* → `["US"]`.
- **Risco de health claims (CopperWater):** a própria página de afiliados enquadra o programa como *"A Health Journey with Copper Water Bottles"* e o título do produto inclui "Ayurvedic". Proibido persistir ou usar claims de saúde/benefício no conteúdo.
- Evidência: a página do programa acima; datas `2026-08-24` / `2026-11-22`.

## Amazon Associates US — trust_fallback

- Fee table oficial (Standard Commission Income Rates, Table 1): https://affiliate-program.amazon.com/help/operating/commission-income-statement — **"Physical Books, Kitchen, Automotive 4.50%"**; é a taxa genérica de Kitchen, não uma taxa garantida para esta listing.
- **Blocker de ativação — categoria dependente da listing:** a listing de destino escolhida está hoje categorizada *"Sports & Outdoors › Sports & Outdoor Recreation Accessories › Sports Water Bottles"* (Best Sellers Rank #5,391 em Sports & Outdoors; #280 em Water Bottles), que pela mesma Table 1 rende **3.00%** (Sports/Outdoors), não 4.5% (Kitchen). A comissão efetiva depende da categoria viva da listing no momento da compra; por isso `commission_type`/`commission_value` ficam **null** no catálogo até a listing/categoria ser revalidada antes de qualquer ativação.
- Cookie: **1 dia** — *"during a single session, which is measured as beginning when that customer clicks through that Special Link and ending upon the first to occur of the following: (x) 24 hours elapse from that click"* (Commission Income Statement §1).
- Payout eligibility: **null** — não foi possível reconfirmar oficialmente um caminho de pagamento Fx4Cash/Portugal; nada foi assumido.
- Shipping: `["US"]` (programa US, destino amazon.com).
- Asset rights (fonte: Program Policies / License): *"You will not store or cache Product Advertising Content consisting of an image, but you may store a link to Product Advertising Content consisting of an image for up to 24 hours"* e *"link each use of the Program Content solely to the related Product detail page or other relevant page of an Amazon Site"*.
- Traffic policy (fonte: Program Policies): **Prohibited Paid Search Placement** — é proibido fazer paid search com a palavra "amazon", "kindle" ou qualquer Amazon Mark (ou variações/erros ortográficos) no anúncio; anúncios pagos são permitidos apenas para queries genéricas, desde que levem ao nosso site e nunca diretamente (ou via redirect) a uma página Amazon.
- Destino verificado (listing atual, viva em 2026-08-24): https://www.amazon.com/Kosdeg-Hammered-Copper-Water-Bottle/dp/B079ZL8FS3 — título oficial "Kosdeg Copper Water Bottle - 34 Oz Extra Large - A Hammered Ayurvedic Pure Copper Vessel For Drinking". A página de detalhe foi confirmada com o slug canónico; notar que o próprio título da listing contém health claims — re-verificar estabilidade da listing antes de ativar.
- Evidência: fee table oficial (URL acima), onde estão as taxas por categoria; o Program Policies (https://affiliate-program.amazon.com/help/operating/policies) contém o income statement, a regra da sessão de 24h e as regras de paid search/imagens.

### Disclosure exigida pela Amazon (blocker de ativação)

O Operating Agreement (§5 "Identifying Yourself as an Associate") exige divulgação clara e
proeminente da frase oficial: **"As an Amazon Associate I earn from qualifying purchases."**
A disclosure PT-PT genérica do pipeline não é suficiente para o Amazon Associates: é um
blocker de ativação registado — a oferta não pode passar a `approved`/`active` sem a
disclosure Amazon exata no destino, além da FTC disclosure aplicável (FTC Guides Concerning
the Use of Endorsements and Testimonials in Advertising, referida no próprio acordo).

### Regras Amazon de imagem/link/tráfego pago (resumo fonte-gated)

1. Imagens: não guardar/cachear (só link até 24h); não alterar o conteúdo (apenas redimensionamento proporcional).
2. Links: apontar sempre para a página de detalhe do produto (ou página relevante) do Amazon Site.
3. Paid search: proibido usar "amazon"/marcas/variações no anúncio; permitido só para queries genéricas que levem ao nosso site, nunca direto à Amazon.
4. Disclosure obrigatória (ver acima).

## Copper Utensil Online — primary

- Página oficial do programa: https://www.copperutensilonline.com/programs/affiliate
- Destino verificado: https://www.copperutensilonline.com/hammered-pure-copper-water-bottle-seamless-1000-ml-capacity.php ("Hammered Pure Copper Water Bottle – 1000ml Seamless"; verificado com headers de browser — o servidor rejeita alguns user-agents com 406 Mod_Security).
- Comissão: **conflito na mesma página → null**. Citações oficiais: *"Earn 5% commission for each product you sell"*, *"we offer you 3%!"* e *"We handle the transaction and send you 15% of the sale"* (5%/3%/15% sem critério de resolução).
- Cookie: **"lifetime" não representável → null**. *"anyone who clicks through an ad or link to us from your site is tagged as 'your contact' for life"* — o schema exige um inteiro de dias fidedigno; "para sempre" não é um valor de confiança.
- Payout/shipping: **desconhecidos → null**.
- Asset rights (fonte oficial): *"Banners - Put up banners on your website... Email Ads... HTML Ads"* e *"plug and play text and graphic banners with your unique affiliate id tagged to the banner"*.
- Traffic policy: sem restrição oficial publicada → null.
- Evidência: a página do programa acima; datas `2026-08-24` / `2026-11-22`.

## Verificações exigidas apenas se existir um novo pedido futuro

1. **Aprovação do produto** (`approved: false` hoje) e **aprovação da conta/do site** em cada programa — nenhuma candidatura foi submetida.
2. **Amazon:** disclosure oficial "As an Amazon Associate I earn from qualifying purchases." obrigatória no destino; categoria viva da listing (hoje Sports & Outdoors = 3%, não Kitchen = 4.5%); payout Portugal/Fx4Cash não reconfirmado (null); listing re-verificada.
3. **SAMA/CopperWater/Copper Utensil:** comissões desconhecidas ou conflituosas (null); preencher com fonte oficial antes de qualquer aprovação.
4. **Health claims:** SAMA e CopperWater promovem benefícios de saúde nos materiais oficiais — nenhum claim pode ser persistido nem usado em conteúdo; o schema não tem campo de claim e a disclosure PT-PT não cobre claims.

## Estado

- Teste técnico encerrado: nenhuma candidatura submetida e nenhuma ação operacional pendente.
- Nenhuma oferta `active`; gate nunca emite (produto não aprovado e zero ofertas ativas).
- `checked_at = 2026-08-24`, `valid_until = 2026-11-22` (janela de evidência de 90 dias).
- Fora de âmbito: resolução de links tracked, geração de páginas, click tracking, chamadas/scraping/inscrições em programas.
