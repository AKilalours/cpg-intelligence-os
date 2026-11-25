# CPG Intelligence OS: results on one page

An AI operating layer for a consumer-goods brand's back office: it reads invoices, matches retailer payments,
investigates deductions, forecasts demand, and finds lost sales. Built solo by Akila Lourdes Miriyala Francis on
public data (Walmart M5 sales, FATURA invoices, CORD receipts). Not affiliated with any company.

**Rule behind every number:** the method is fixed and written down first, then scored once on data it never saw,
with the uncertainty shown. Where a model lost, it was not shipped, and the page says so.

| What it does | Measured on unseen data | Compared with |
|---|---|---|
| Reads invoice fields | **99.89%** of values it accepted were right (3,662 of 3,666); 69.5% of invoices needed no person | A regression gate replays every human correction on each new rule |
| Matches retailer payments to invoices | **88.2%** posted with no person, **0 wrong** (868 of 984 lines) | The rest go to a person, ranked by money at risk |
| Investigates deduction claims (agent) | Wrongly disputed **1** valid claim | The rules engine wrongly disputed **24**, same claims, 8 unseen worlds (p = 2e-6) |
| Stops automation when it starts failing (error budget) | A silent 5% fault: **30** wrong postings | **126** with no gate; a bad release: **0** against 252 |
| Watches its inputs for drift | **98%** of injected changes caught (393 of 400), 0 false alarms on cash and claims | 8 unseen worlds, exact statistical tests |
| Forecasts demand, 30,490 real series | **4.4%** lower error (WRMSSE 0.855) | Exponential smoothing 0.894; the 80% ranges held 79.5% of real weeks |
| Finds lost sales (likely empty shelves) | **50.9%** of planted lost dollars caught, flagging 0.7% of real weeks | Est. **$879K** lost of $45.1M sold over 52 real weeks |
| Reads deduction reason codes | **85.3%** right on wordings no reader trained on (682 of 800) | Hybrid: small classifier when confident, local 7B model otherwise |

**Two honest negatives, kept on purpose:**
- Predicting promotion lift: on 167 real price cuts the ML model's error (1.035) was worse than assuming no lift
  (0.986). The Promotions page shows break-even arithmetic and real evidence instead of a prediction.
- Fine-tuning a 0.5B model with LoRA: 99.8% on seen wording, 45.9% on unseen wording. Not shipped.

**What a brand gets out of it (Impact page):** a $50M brand loses $125K to $750K a year to invalid deductions at
Inmar's published rates, $250K to $1M at HighRadius's. The calculator shows the arithmetic and links the sources;
it claims no recovered dollars.

**Engineering:** Postgres with database-enforced roles (the agent cannot change a ledger value), FastAPI, Next.js,
LightGBM, local models through Ollama, a paid-model budget cap, 560+ tests in CI, a read-only public demo built
by GitHub Actions. Every design choice and its evidence: [docs/decisions.md](decisions.md).

**Next:** expected deductions from the promotion plan and forecast, so an inflated claim is flagged before it is
paid; and an agent that chooses its own next tool, scored against today's fixed agent on the same unseen claims.
