# ORBIT

ORBIT is the autonomous agent runner for **Autonomy 10K**, a side-income experiment with one goal:
**$10,000 in total income**, with AI agents doing as much of the work as possible.

Every weekday morning a GitHub Action wakes ORBIT up. ORBIT reads your income log, decides which
agents to run, runs them with the Claude API, and commits everything they made back to this repo,
along with a daily report. You review the drafts and do the final step yourself.

## The agents

| Agent | What it does | Output |
|---|---|---|
| **SCOUT** | Researches Etsy digital-product ideas in your niche with web search, picks the best one, and explains why (with sources) | `outputs/scout/` |
| **FORGE** | Writes the full Etsy listing (title under 15 words, exactly 13 tags of 20 characters or fewer, description, price, photo plan) and **builds the product** if it's a spreadsheet (`.xlsx`) or printable (`.pdf`) | `outputs/forge/<date>-<product>/` |
| **PIXEL** | Writes Fiverr thumbnail-design gig copy, packages, FAQ, and gig thumbnail concepts | `outputs/pixel/` |
| **QUILL** | Writes affiliate blog posts with an FTC disclosure and `[AFFILIATE LINK]` placeholders | `outputs/quill/` |
| **PITCH** | Finds UGC-friendly brands and writes cold DM and email **drafts** (never sent) | `outputs/pitch/DRAFT-*.md` |
| **ORBIT** | The coordinator: reads `data/income.csv`, picks which agents run, writes the daily report | `reports/YYYY-MM-DD.md` |

## Hard rules

These are in every agent's prompt (`prompts/rules.md`), and the ones that can be checked are also
**enforced in code** (`orbit/rules.py`). Output that fails a check is sent back to the agent to fix.

- **Drafts only.** ORBIT never creates accounts, posts, sends emails or DMs, or buys anything. There
  is no code in this repo that can. Everything is a file for you to review.
- **Original work only.** No trademarks or brand names in products. A built-in list catches common
  slips (characters, franchises, big brands, app names).
- **No invented facts.** No made-up stats, reviews, or testimonials. SCOUT and PITCH must cite the
  URL for every fact; PITCH may only list an email it actually found on a page.
- Etsy tag counts and lengths, title word counts, Fiverr title limits, and the FTC disclosure are
  all checked in code, not just requested.

## Setup

### 1. Get an Anthropic API key

Create a key at [console.anthropic.com](https://console.anthropic.com). The API is pay-as-you-go
and billed separately from a Claude subscription.

### 2. Add it as a repository secret

1. On GitHub, open this repo and go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `ANTHROPIC_API_KEY`. Secret: paste your key. Click **Add secret**.

### 3. Put the code on your default branch

GitHub only runs scheduled workflows from the repository's default branch (usually `main`), so merge
this branch into it.

### 4. Pick your niches

Edit [`orbit.toml`](orbit.toml): what SCOUT researches, what QUILL writes about, which brands PITCH
looks for, and what PIXEL offers on Fiverr.

### 5. Do a first run

Go to **Actions → ORBIT daily run → Run workflow**. Leave "agents" blank to let ORBIT decide, or type
e.g. `scout,forge`. When it finishes, open `reports/` for the day's report.

After that it runs on its own at **7:47am Pacific (6:47am in winter), Monday to Friday**. To change
the time, edit the `cron` line in [`.github/workflows/orbit.yml`](.github/workflows/orbit.yml)
(GitHub cron times are in UTC).

## Logging income

ORBIT steers by what's actually earning, so log every payout in [`data/income.csv`](data/income.csv):

```csv
date,channel,amount,note
2026-10-14,etsy,6.50,first sale - paycheck budget sheet
2026-10-20,fiverr,25.00,thumbnail pack for a cooking channel
2026-10-28,affiliate,3.12,October commissions
2026-11-02,ugc,150.00,2 videos for a kitchen brand
```

- `date` is `YYYY-MM-DD`; `amount` is in USD (net of fees is most honest).
- Use any channel names; `etsy`, `fiverr`, `affiliate`, and `ugc` match the agents.
- Easiest way: edit the file right on GitHub (pencil icon) and commit.
- Or from a local checkout: `python -m orbit log-income etsy 6.50 "first sale"`.

Check progress any time with `python -m orbit status`, or read the top of the latest report.

## Daily report

Each `reports/YYYY-MM-DD.md` contains:

- Progress toward $10K, overall and by channel
- Which agents ORBIT picked and why
- What each agent made, with links to the files
- **Your review queue**: a checklist of what needs your eyes or hands
- Pipeline status and API spend

## Costs and budget

Each run calls ORBIT plus up to 2 agents (`max_agents_per_run` in `orbit.toml`). Spend is estimated
from token usage after every run and logged to `data/spend.csv`. Once the month's estimate reaches
`[budget] monthly_usd` (default **$15**), runs are skipped until next month and the report says so.
Lower `effort` or switch `model` in `orbit.toml` to spend less. Check your real bill in the
Anthropic Console.

## Editing the agents

Every agent's instructions live in [`prompts/`](prompts/) as plain markdown, so you can change tone,
pricing strategy, post length, or anything else without touching code.

- `prompts/rules.md` is shared by every agent. Keep the hard rules in it.
- Keep each prompt's **Reply JSON** section's field names as they are; the code reads those fields.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m orbit run                      # ORBIT decides
python -m orbit run --agents scout,forge # pick agents yourself
python -m pytest -q                      # tests (no API key needed)
```

## Layout

```
orbit.toml              settings: model, budget, niches
prompts/                agent instructions (edit freely)
orbit/
  coordinator.py        ORBIT: plan, run agents, write report
  agents.py             SCOUT, FORGE, PIXEL, QUILL, PITCH
  rules.py              hard-rule checks (tags, titles, trademarks, FTC disclosure)
  builders.py           turns FORGE specs into .xlsx / .pdf files
  llm.py                Claude API wrapper: web search, retries, spend tracking
  state.py              income log, spend log, pipeline state
data/
  income.csv            you log income here
  spend.csv             API spend per run (written by ORBIT)
  state.json            pipeline memory (written by ORBIT)
outputs/                everything the agents make
reports/                daily reports
```
