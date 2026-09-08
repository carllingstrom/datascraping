# DataScraper

Internal CLI tool: chat with an AI to define a scrape baseline, then let Python walk the sites and export Excel.

## Why CLI

Least to carry between machines — Python + `pip install -r requirements.txt`. No Streamlit server.

## Flow

1. **Chat** (Ollama or Claude) → agree on goal, sites, fields, filters, login-or-not  
2. AI emits a **ScrapePlan** JSON  
3. **Python engine** fetches pages (HTTP by default; Playwright only if JS/login)  
4. Results → **Excel** in `output/`

AI plans. Python scrapes. Credentials are prompted only when a plan site requires login.

## Setup

```bash
cd "V - datascraping"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Ollama (default)

```bash
# install Ollama separately, then:
ollama pull llama3.2
# AI_PROVIDER=ollama in .env
```

### Claude

Set in `.env`:

```
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
```

### Browser scrapes (optional)

Only needed for JS-heavy pages or login:

```bash
pip install -r requirements-browser.txt
playwright install chromium
```

## Usage

```bash
# interactive planner + scrape
python main.py
# or
python main.py chat
python main.py chat --provider claude
python main.py chat --provider ollama --model llama3.2

# re-run a saved plan
python main.py run plans/my_plan.json

# show provider config
python main.py providers
```

### In-chat commands

| Command | Action |
|---------|--------|
| `/plan` | Show current plan |
| `/save` | Save plan JSON under `plans/` |
| `/run`  | Execute scrape → Excel |
| `/quit` | Exit |

## Plan shape (simplified)

```json
{
  "title": "Widget prices",
  "goal": "Collect widget name, price, url",
  "fields": [
    {"name": "name", "required": true},
    {"name": "price"},
    {"name": "url", "attribute": "href"}
  ],
  "sites": [
    {
      "name": "Example",
      "start_url": "https://example.com/widgets",
      "method": "http",
      "max_pages": 3,
      "login": null
    }
  ],
  "filters": {"keywords": ["widget"], "max_price": 500},
  "max_items": 100
}
```

Leave selectors empty when unsure — the engine tries JSON-LD → CSS selectors → listing heuristics → OpenGraph meta.

Set `"method": "browser"` and a `login` object only when you need them; passwords are never stored in the plan.
