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

## 2. What the two roles ask, line by line, and where the project stands

Both postings (Applied AI/ML Engineer, $200K to $250K, 3+ years; Senior ML/AI Engineer, $250K to $300K, 5+ years)
share the responsibilities. The Founding Staff posting names five concrete projects; they are included because
they say what Confido is building.

| Asked for | In this project | Gap |
|---|---|---|
| LLM systems turning messy financial documents into typed, validated data | Three-reader invoice cascade, confidence routing, human review, regression gate, receipts (real FATURA, CORD, DocILE) | Scale: dozens of layouts, not hundreds |
| Agentic workflows over fragmented systems, with eval harnesses and guardrails | Investigation agent; database-enforced access; 1 false dispute against the rules engine's 24 on 8 unseen worlds (p = 2e-6) | The agent follows fixed playbooks; no model-chosen tool loop yet. No backup chasing |
| Forecasting thousands of intermittent series; classical and boosted models; rolling-origin backtests; best-fit selection | 30,490 real M5 series; Croston, SBA, TSB, ETS, LightGBM; LightGBM 4.4% better than smoothing on test weeks scored once; per-series best-fit shown to overfit | No Prophet or ARIMA; no hierarchy (item to store to chain) |
| Baselining, outlier and anomaly detection, entity reconciliation, heterogeneous ingestion | Drift monitors scored on unseen worlds; cash matching; four real datasets ingested | **No anomaly detection in sales data** (Founding Staff project 4). Entity matching thin |
| Eval datasets, benchmarks, monitoring for systems without one right answer | Held-out data scored once, paired tests, error budget enforced by the database, drift alarms, CI | Strongest part of the project |
| Leakage and train/serve skew | Leakage tests, one feature function for training and serving (tested) | Covered |
| Product sense: when simpler wins | Hybrid reader over a 7B model; LoRA not shipped; simpler forecaster shipped; promotion lift not shown because it failed its test | Covered |
| Recommending optimal promotions (Founding Staff project 5) | Tested honestly: on real M5 cuts the model did not beat guessing zero, so the page shows break-even arithmetic and evidence | A causal answer needs a promotion calendar or experiments |
| Fine-tuning LLMs (Founding Staff) | LoRA on Qwen2.5-0.5B tried and rejected with numbers (45.9% on unseen wording) | Covered as an honest negative |
| Production: CI/CD, containers, cloud (MLOps) | GitHub Actions CI | **No container, no hosted deployment** |

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
