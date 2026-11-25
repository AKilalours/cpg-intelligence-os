# CPG Intelligence OS and Confido: what they do, what the roles ask, what this project adds

Independent analysis, November 2026. Every fact below is from a public source linked at the end. Estimates are
labelled as estimates and show their inputs. Nothing here is Confido's internal data.

## 1. What Confido is today (public facts)

- **What it sells:** "the AI operating system that runs cash application, deductions, trade and planning in one
  place for CPG brands." Ten modules: cash application, deduction management, auto-disputes, trade promotion
  management, sales forecasting, demand planning, supply planning, sales analytics, plus foodservice (planned).
- **Scale:** 250+ brands (divisions of Unilever, Mars and Nestlé; OLIPOP, Simple Mills, Daisy, Feastables, DUDE
  Wipes), "$30B+" of retail sales planned on the platform, 5x year-on-year growth in 2025.
- **Funding:** $55M Series B led by Insight Partners, $77M raised in total. Stated uses: "agentic, zero-click
  workflows" (agents that chase backup, file disputes and re-plan against new forecasts), the planning cycle,
  foodservice, and hiring.
- **Strategy in the CEO's words:** "one system where the sales forecast, the trade spend, the deductions, and the
  supply plan all agree."
- **How it makes money:** quoted per brand on annual retail revenue and integration scope; modules bought singly
  or as a suite. So revenue grows when a brand buys more modules, renews, or grows, and when new brands sign.
- **What it claims for customers:** 99% extraction accuracy and 85% less time on cash application; 85% less time on
  deductions; 3x faster dispute resolution; Feastables recovered "close to $500,000" of invalid and duplicate
  charges; Rebel Creamery flagged "over $100,000" of duplicates; DUDE Wipes a "$300k headcount saving".
- **Production stack (from the MLOps posting):** Python, Ruby/Rails, AWS, Terraform, Kubernetes, GitHub Actions,
  Snowflake, Aurora/RDS, Redis, Kafka; model quality measured "through online evals and human-in-the-loop review,
  not just uptime."

## 2. What the two roles ask, line by line, and where the project stands (re-read 2026-11-25)

Both postings share the same five responsibilities; they differ in seniority. Applied AI/ML Engineer: 3+ years
applied ML with production systems, STEM master's, $200K to $250K. Senior ML/AI Engineer: 5+ years applied ML
(its skills box also says 6+ years of production software engineering), $250K to $300K. Both are in-office in
New York with relocation.

| Responsibility (both postings) | Evidence in this project | Score | What would close the gap |
|---|---|---|---|
| LLM systems turning messy financial documents into typed, validated data | Three-reader invoice cascade (OCR, on-premises and hosted vision models), confidence routing, human review, a regression gate that replays every correction; 99.89% of accepted values right on held-out layouts | 9/10 | More layouts (dozens today, hundreds in production) |
| Agentic workflows over fragmented systems, with eval harnesses and guardrails | Investigation agent with database-enforced roles (it cannot change a ledger value); 1 valid claim wrongly disputed against the rules engine's 24 on 8 held-out ledgers (p = 2e-6); error budget and drift alarms | 7/10 | A model-chosen tool loop and retrieval over contracts and backup (planned step 3) |
| Forecasting thousands of intermittent series; classical and boosted; rolling-origin backtests | 30,490 real M5 series; SES, Croston, SBA, TSB, LightGBM; 4 rolling origins; 4.4% lower WRMSSE than smoothing on test weeks scored once; calibrated 80% ranges (79.5%) | 9/10 | ARIMA-family and Prophet baselines (named as nice-to-have); hierarchy reconciliation |
| Baselining, outlier and anomaly detection, entity reconciliation, heterogeneous ingestion | Lost-sales detector (negative binomial, 50.9% of test stock-out value at a 0.7% flag rate); drift monitors with exact tests; cash matching; four real datasets with contracts and quarantine | 8/10 | Fuzzy entity resolution (retailer and SKU name variants) |
| Eval datasets, benchmarks and monitoring for systems without one right answer | Rules written before scoring, held-out data scored once, paired tests, CI, error budget enforced by the database | 10/10 | Strongest part |
| Required: evaluation habits, leakage and train/serve skew | Leakage tests; one feature function for training and serving (tested) | 10/10 | |
| Required: product sense, simpler wins | Smoothing kept where it wins; LoRA rejected (45.9% on unseen wording); promotion model not shipped because it lost to a no-lift baseline | 10/10 | |
| Nice to have: RAG or retrieval in production | None yet | 3/10 | Retrieval over deduction backup and promotion agreements, scored on held-out claims |
| Nice to have: startup pace | Shipped solo, with CI, a public copy, and a decision log of 120 entries | 8/10 | |

**Honest overall.** For the Applied AI/ML Engineer role the project covers every responsibility, and the two
real gaps (retrieval, a model-chosen agent loop) are already the next two planned pieces of work. For the Senior
role the project shows the skills, but no project can supply the years of production experience the posting
requires; present it as evidence of senior-level judgement and apply to both, leading with the Applied role.

## 3. What would add the most value for a Confido customer, and why

Ranked by measured or sourced customer value, and by how directly each maps to a Confido module and to the
company's stated roadmap.

1. **Anomaly detection on real sales: find lost sales.** A week an item is on the shelf, normally sells, and
   sells nothing is usually an out-of-stock or a phantom-inventory problem. On M5 this can be measured on real
   data: flag such runs, estimate the units lost from the forecaster, and score the detector on injected
   anomalies it never saw, then on the real flags by how often they match a price-and-stock pattern. Maps to the
   demand-planning module and to Founding Staff project 4.
2. **Connected plan: expected deductions before they arrive.** From the promotion calendar and the forecast,
   predict what each scan-back claim should be; a claim far above its expectation is pre-flagged. This is the
   "forecast, trade spend and deductions agree" idea made concrete. Scored on the finance worlds (generated
   claims on real M5 sales, labelled as such).
3. **A model-driven agent loop for disputes.** The model picks which tool to call next (claim, promotions, POS,
   shelf prices, earlier claims), assembles the backup packet and drafts the dispute; measured with the existing
   harness against today's fixed agent on the same unseen claims. Maps to Auto-Disputes and to "zero-click".
4. **An impact model, inside the app.** A brand enters its annual gross sales; the page multiplies by sourced
   industry ranges and by this project's measured rates, and shows dollars and hours as ranges, with every input
   adjustable and every source linked.
5. **Ship it: a container and a hosted demo,** deployed by GitHub Actions, so anyone can open it.

## 4. How the impact model works (and what it will not claim)

For a brand with annual gross sales S:

- Deductions taken = S x d, where d is 5% to 15% of gross sales (Inmar) or 10% to 20% of revenue (HighRadius).
- Invalid share of deduction value = v, 5% to 10% (HighRadius).
- Recoverable dollars at stake = S x d x v. For S = $50M: $50M x 5% x 5% = $125K at the low end, to
  $50M x 15% x 10% = $750K at the high end, per year.
- What this project's agent changes is measured, not assumed: on unseen worlds it disputed 1 valid claim against
  the rules engine's 24 (fewer wrongful disputes, so less retailer friction and fewer clawbacks), and the rules
  plus agent never approved an invalid claim the rules approved. The model will apply only measured rates.
- Analyst time: HighRadius reports about 20 deductions resolved per analyst per day; the share of work the system
  settles with no person is measured per module (for example 88.2% of payment lines posted with no person, 0 wrong).

It will not claim Confido revenue, Confido margins, or any customer's results. It shows a brand what its own
numbers would look like, as a range, with the arithmetic visible.

## 5. A note on the numbers

Industry deduction benchmarks are mostly published by vendors (HighRadius, Inmar) and disagree with each other;
that is why the model uses ranges and names the source of each one. Confido's own customer metrics are its
marketing claims; they are quoted as such, not verified.

## Sources

- Confido, "Confido Raises $55M Series B": https://www.confidotech.com/blogs/confido-raises-55m-series-b-to-scale-the-ai-operating-system-for-cpg-brands
- Confido, product and pricing summary: https://www.confidotech.com/llms.txt
- Confido, Deduction Management: https://www.confidotech.com/products/deduction-management
- Applied AI/ML Engineer posting: https://www.builtinboston.com/job/applied-ai-ml-engineer/10695861
- Senior ML/AI Engineer posting: https://builtin.com/job/senior-ml-ai-engineer/9275582
- Founding Staff ML/AI Engineer posting (Y Combinator): https://www.ycombinator.com/companies/confido/jobs/8Bspwx4-founding-staff-ml-ai-engineer
- Senior ML Ops Engineer posting (Y Combinator): https://www.ycombinator.com/companies/confido/jobs/y9OiAc1-senior-ml-ops-engineer
- HighRadius, "Deductions Metrics that Matter": https://www.highradius.com/insights/finsider/deduction-metrics-enterprises/
- Inmar, "The Hidden Cost of CPG Chargebacks": https://www.inmar.com/blog/insights/martech/hidden-cost-cpg-chargebacks-what-manufacturers-overlook
