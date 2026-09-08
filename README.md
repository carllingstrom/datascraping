# DataScraper

Internal tool: chat with an AI to define a scrape baseline, then let Python walk the sites and export Excel.

## Flow

1. **Chat** (Ollama or Claude) → agree on goal, sites, fields, filters, login-or-not  
2. AI emits a **ScrapePlan** JSON (live-preview checked)  
3. **Python engine** paginates mechanically until empty (or `max_pages`)  
4. Results → **Excel** in `output/`

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
ollama pull llama3.2
# AI_PROVIDER=ollama in .env
```

### Claude

```
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
```

### Browser scrapes (optional)

```bash
pip install -r requirements-browser.txt
playwright install chromium
```

## Usage (CLI)

```bash
python main.py chat
python main.py chat --provider claude
python main.py run plans/tractors_klaravik_mascus.json -y
python main.py providers
```

In-chat: `/plan` · `/save` · `/run` · `/quit`

For a **comprehensive** pull: leave `max_items` null and set `max_pages` high (default **500**). The engine stops when a page returns no rows.

## Streamlit UI

### Local

```bash
streamlit run streamlit_app.py
# http://127.0.0.1:8501
```

### Streamlit Community Cloud (no Mac required)

1. Push this repo to GitHub (already: `carllingstrom/datascraping`).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Choose repo `carllingstrom/datascraping`, branch `main`, main file `streamlit_app.py`.
4. Under **Advanced settings → Secrets**, paste:

```toml
AI_PROVIDER = "claude"
ANTHROPIC_API_KEY = "sk-ant-your-key"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
MAX_PAGES_DEFAULT = "500"
ENRICH_DETAIL_LIMIT = "0"
```

5. Deploy — you’ll get a `https://….streamlit.app` URL.

Cloud hosts cannot reach Ollama on your laptop; use Claude there. Large scrapes may take several minutes on the free tier.

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
      "max_pages": 500,
      "login": null
    }
  ],
  "filters": {},
  "max_items": null
}
```

Leave selectors empty when unsure. Set `"method": "browser"` and `login` only when needed.
