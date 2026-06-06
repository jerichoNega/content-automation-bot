# content-automation-bot

Give it a topic. Get a LinkedIn post, email hook, and full blog article — all at once.

It fires all three in parallel so you're not waiting on each one sequentially. The whole thing takes about as long as one API call.

---

## What it outputs

| File | What it is |
|---|---|
| `{topic}_linkedin_{ts}.txt` | LinkedIn post, 150–200 words, written to not sound like it came from a machine |
| `{topic}_email_hook_{ts}.txt` | 2–3 punchy opening lines for an email |
| `{topic}_blog_{ts}.txt` | Full blog article — 900–1200 words, 5 sections, real prose |
| `{topic}_blog_{ts}.html` | Same article rendered as a portfolio-ready HTML file |
| `{topic}_linkedin_infographic_{ts}.png` | 1080×1080 insight card (only if the topic actually warrants it) |
| `{topic}_blog_infographic_{ts}.png` | 1200×628 Open Graph overview card (same logic — only when it makes sense) |

Everything lands in `/outputs` with a timestamp so nothing overwrites anything.

---

## After generation

Two optional steps run at the end, both with a review gate so nothing publishes without you seeing it first:

**Post to LinkedIn** — if you've connected your account via `linkedin_auth.py`, it'll ask if you want to post right now. Includes the infographic if one was generated.

**Publish to portfolio** — if the blog parsed cleanly, it'll ask if you want to push it to GitHub. Creates the HTML file and prepends a card to `index.html` automatically. Never touches existing pages.

---

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here
```

For LinkedIn posting (optional):
```bash
python3 linkedin_auth.py
```
This walks you through OAuth and saves your credentials to `.env`.

---

## Usage

```bash
# Type the topic when prompted
python3 content_pipeline.py

# Or pass it directly
python3 content_pipeline.py "why most B2B SaaS products fail in year 2"
python3 content_pipeline.py "the real cost of context switching for developers"
```

---

## A note on the writing style

The LinkedIn prompt is specifically engineered to avoid the phrases that make AI-written content obvious. No "game-changer", no "In today's fast-paced world", no em dashes used as a crutch. It writes from the perspective of a curious individual who follows the space — not a corporate executive sharing company news.

Same principle for the email hook and blog. Honest, specific, varied sentence rhythm. No bullet-point summaries at the end of articles. No "in conclusion".

The infographics are only generated when the model judges the topic has genuine visual value — data, lists, frameworks. Topics that don't benefit from a visual card just skip it.

---

## Stack

- Python 3.11+
- `anthropic` — API calls
- `Pillow` — infographic rendering
- `requests` — LinkedIn API + GitHub API
- `asyncio` — parallel generation
