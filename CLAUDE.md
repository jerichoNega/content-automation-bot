# Content Automation Pipeline

A Python CLI tool that takes a topic or brief, calls the Claude API in parallel, and produces three platform-ready content pieces — plus optional infographics — in one shot.

## What it does

**Input:** A topic or brief typed in the terminal (or passed as a CLI argument).

**Outputs (saved to `/outputs`):**

| File | Description |
|---|---|
| `{topic}_linkedin_{ts}.txt` | LinkedIn post — 150–200 words, professional but genuinely human tone |
| `{topic}_email_hook_{ts}.txt` | Email hook — 2–3 punchy opening lines |
| `{topic}_blog_outline_{ts}.txt` | Blog outline — title + 5 structured sections with one-line descriptions |
| `{topic}_linkedin_infographic_{ts}.png` | 1080×1080 LinkedIn insight card (generated when content warrants it) |
| `{topic}_blog_infographic_{ts}.png` | 1200×628 blog overview card / Open Graph image (generated when content warrants it) |

## How it works

1. Takes your topic from `stdin` or CLI args
2. Fires **3 Claude API calls in parallel** using `asyncio.gather()` — LinkedIn, email hook, and blog outline all generate simultaneously
3. Each content response optionally includes structured infographic data (the model decides whether a visual adds genuine value)
4. Infographics are rendered with **Pillow** using professional dark-mode palettes designed to not look AI-generated
5. All files are saved to `/outputs` with the topic slug + timestamp in the name
6. A clean terminal summary prints all three outputs and lists the saved files

## Model

Uses `claude-sonnet-4-0` (`claude-sonnet-4-20250514`) for a balance of quality and speed on content tasks.

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

```bash
# Interactive (prompted input)
python3 content_pipeline.py

# Or pass the topic directly
python3 content_pipeline.py "the future of remote work"
python3 content_pipeline.py "why most B2B SaaS products fail in year 2"
```

## Writing style notes

The LinkedIn prompt is engineered to avoid AI-sounding clichés ("In today's fast-paced world", "game-changer", "Let's dive in"). It asks Claude to write as a genuine human professional — first-person, honest observations, natural rhythm, short paragraphs.

The email hook is designed for specificity and punch — no generic openers, no corporate fluff.

## Infographic design

- **LinkedIn card:** 1080×1080px, dark navy palette, displays headline + stat + 3 bullet points
- **Blog card:** 1200×628px (Open Graph), three color themes (professional / energetic / minimal), displays section roadmap + "5 sections" callout
- Infographics are only created when Claude determines the topic has genuine visual data potential — topics without clear data or list structure will skip infographic generation

## File structure

```
bot/
├── content_pipeline.py   # Main script
├── requirements.txt       # anthropic, Pillow
├── CLAUDE.md              # This file
└── outputs/               # All generated files land here
    ├── *.txt
    └── *.png
```
