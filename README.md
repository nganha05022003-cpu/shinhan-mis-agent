# Shinhan MIS Agent

An agentic AI assistant for bank branch managers — ask questions in plain
Vietnamese or English about branches, loans, revenue, and non-performing
loan (NPL) ratios, and get real answers pulled live from a SQLite database,
plus charts, downloadable Excel/CSV reports, an automatic daily summary, and
what-if financial scenario projections.

Built for the AABW Buildathon (July 2026).

## What this project does

A branch manager should be able to open a chat window, type a question like
*"Which branch has the highest NPL ratio this month?"*, and get a correct,
data-backed answer in seconds — without knowing SQL or opening a BI tool.

A branch manager should also be able to open the app and immediately see
what changed since yesterday, without typing anything, and ask "what if"
questions to get a data-grounded projection rather than just a lookup.

This project implements that with a real function-calling AI agent (not a
hardcoded chatbot): the model decides which tool to call, writes its own SQL,
and the app executes it safely against a real database. A UI capability bar
(5 pill buttons, one per tool) lets a first-time user see the agent's full
range of abilities at a glance instead of scanning a flat list of sample
questions.

## Architecture

```
shinhan-mis-agent/
├── data/
│   ├── generate_data.py     # Regenerates the simulated SQLite database
│   └── shinhan_mis.db       # SQLite DB: branches, loans, monthly_revenue, npl_records
├── agent/
│   ├── agent.py             # Core agent: function-calling loop + guardrail + 5 tools + Morning Brief
│   └── test_agent.py        # Automated test suite (core Q&A, anomaly, chart, report, digest, what-if)
├── app/
│   ├── app.py                # Streamlit chat interface (bilingual VI/EN), capability bar, Morning Brief
│   └── assets/logo.png       # Brand logo (add your own PNG here)
└── docs/
    ├── masterplan.md          # Project plan and design decisions
    └── test_cases.md          # Full QA/QC test case suite for all 4 outputs
```

### The agent has 5 tools, plus 1 automatic (non-tool) feature

| Tool | What it does |
|---|---|
| `query_database` | Writes and runs a SQL SELECT query, returns rows as an answer |
| `detect_anomalies` | Flags branch/month NPL ratios that are statistical outliers (mean + 2 standard deviations — a fixed rule, not a model guess) |
| `generate_chart` | Runs a query and renders the result as a bar/line chart (PNG) |
| `generate_report` | Runs a query and exports the result as a downloadable Excel/CSV file |
| `whatif_npl_scenario` | Projects the revenue impact of a hypothetical NPL ratio increase, using a transparent formula grounded in that branch's own real yield rate — not a made-up constant |

**Morning Brief / Daily Digest** (`generate_daily_digest()`) is not a tool the
model calls — it's a plain Python/SQL function that runs automatically the
moment the Streamlit app loads, comparing the latest month to the previous
one (NPL ratio deltas, revenue deltas, largest current NPL loans). It never
calls the LLM, so it's instant, free, and always numerically exact — a
manager sees the state of their branches before typing a single question.

### What-if Scenario Analysis — how the projection works

`whatif_npl_scenario` turns the agent from a lookup tool into an advisor.
Given a branch and a hypothetical NPL ratio increase (e.g. "+2%"), it:

1. Derives `yield_rate = current_revenue / currently_performing_loan_balance`
   — the branch's own real interest income rate this month, not an assumed
   industry constant.
2. Applies the hypothetical NPL increase to work out how much additional
   loan balance would flip to NPL.
3. Assumes that newly-NPL balance stops generating interest at `yield_rate`
   — the lost revenue is that balance times the rate.

Every number in the result traces back to real `branches` / `npl_records` /
`monthly_revenue` rows, and the assumption itself is returned explicitly so
the model explains its reasoning instead of stating a number from nowhere.

### Guardrail (SQL safety)

The agent never executes arbitrary SQL blindly. `is_safe_query()` in
`agent/agent.py` uses an **allowlist** approach (define what's allowed,
reject everything else) rather than a blocklist of banned keywords:

1. Exactly one SQL statement is allowed (blocks "stacked query" attacks like
   `SELECT ...; DROP TABLE ...;`, regardless of what the second statement says).
2. The statement must start with `SELECT`.
3. Every table referenced must be one of the 4 whitelisted tables.

A secondary keyword blocklist (`DROP`, `DELETE`, `UPDATE`, etc.) is kept only
as defense-in-depth, not as the primary protection.

## AI model / mechanism

- Model: OpenAI `gpt-4o-mini` via the Chat Completions API with function calling (tools).
- Flow: user question → model decides whether to call a tool → tool executes
  against the real SQLite database → result returned to the model → model
  writes the final natural-language answer.
- The loop can call tools multiple times per question (up to 5 rounds) before
  giving a final answer, so it can combine, for example, a query + a chart in
  one turn.
- Answer language (Vietnamese or English) is driven explicitly by the
  Streamlit UI's language toggle, not guessed from the question's wording —
  this also controls chart title/axis-unit language and the Morning Brief's
  language, so switching the toggle makes every part of the app consistent.

## Running it

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=your-key-here      # PowerShell: setx OPENAI_API_KEY "your-key-here"

# Regenerate the database (optional — one is already included)
python3 data/generate_data.py

# Run the agent from the command line
python3 agent/agent.py

# Run the automated test suite
python3 agent/test_agent.py            # everything
python3 agent/test_agent.py core       # just the core Q&A tests
python3 agent/test_agent.py digest     # Morning Brief only (no API key needed)
python3 agent/test_agent.py whatif     # What-if Scenario tests

# Run the web demo
streamlit run app/app.py
```

## Testing

See `docs/test_cases.md` for the full QA/QC suite — data validation tests
(Output 1), agent integration tests including chart/anomaly/report/digest/
what-if tests (Output 2), guardrail unit + adversarial tests (Output 3), and
usability tests (Output 4).

## Notes for judges

- All financial figures are simulated data (see `data/generate_data.py`),
  not real customer data.
- The database can be regenerated deterministically (`random.seed(42)`) to
  verify the generation logic isn't hardcoded.
- The Streamlit app supports both Vietnamese and English via a language
  toggle button in the top right of the interface — this affects chat
  answers, chart labels, and the Morning Brief simultaneously.
- The capability bar (5 pill buttons above the chat box) is the fastest way
  to see everything the agent can do: click any one to reveal example
  questions for just that capability.
