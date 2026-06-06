#!/usr/bin/env python3
"""
Content Automation Pipeline
Generates LinkedIn post, email hook, and blog outline from a topic or brief.
Uses Claude API with parallel requests for speed.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic
import feedparser
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# Load .env file if present (ANTHROPIC_API_KEY, LinkedIn credentials)
load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-5"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
PORTFOLIO_REPO = "jerichoNega/portfolio"  # GitHub repo for blog publishing

# ── News feeds (same sources as MyNewsJericho) ────────────────────────────────
NEWS_FEEDS = [
    {"name": "OpenAI",        "url": "https://openai.com/news/rss.xml"},
    {"name": "Google AI",     "url": "https://blog.google/technology/ai/rss/"},
    {"name": "VentureBeat",   "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "arXiv cs.AI",   "url": "https://arxiv.org/rss/cs.AI"},
    {"name": "HackerNews AI", "url": "https://hnrss.org/frontpage?q=AI"},
]

# Font paths (macOS — falls back gracefully)
FONT_PATHS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]

# ── Font loader ───────────────────────────────────────────────────────────────

def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_PATHS:
        try:
            # .ttc files may have bold variant at index 1
            idx = 1 if bold and path.endswith(".ttc") else 0
            return ImageFont.truetype(path, size, index=idx)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ── Research ─────────────────────────────────────────────────────────────────

def research_synthesis_prompt(topic: str, raw_content: str) -> str:
    return f"""You are extracting key research facts for a content writer.

Topic: {topic}

Raw source content:
{raw_content}

Extract and return ONLY:
- 3–5 specific facts, stats, or data points (include source name in parentheses if available)
- 2–3 recent developments or news items directly relevant to this topic
- 1–2 notable debates, tensions, or opposing views in this space
- Any specific names, companies, numbers, or dates worth referencing

Format as short bullet points only. Be specific — no vague summaries. No prose paragraphs. Max 300 words."""


async def fetch_article_body(url: str) -> str:
    """Fetch and extract clean article text from a URL."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded)
        return text[:3000] if text else ""
    except Exception:
        return ""


async def gather_web_research(topic: str, client: anthropic.AsyncAnthropic) -> str | None:
    """Search the web for current info on the topic and return a research brief."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("  \033[33m  duckduckgo-search not installed — skipping research.\033[0m")
        return None

    try:
        print("  \033[2mSearching the web…\033[0m", end="", flush=True)

        with DDGS() as ddg:
            results = list(ddg.text(topic, max_results=8))

        if not results:
            print("\r  \033[33m  No results found — generating without research.\033[0m")
            return None

        print(f"\r  \033[2mFetching sources…\033[0m", end="", flush=True)

        bodies = []
        for r in results[:5]:
            url = r.get("href", "")
            if not url:
                continue
            body = await fetch_article_body(url)
            if body and len(body) > 200:
                bodies.append(f"SOURCE: {r.get('title', url)}\nURL: {url}\n\n{body}")
                if len(bodies) >= 3:
                    break

        # Fall back to DDG snippets if no full bodies
        if not bodies:
            bodies = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in results[:6]]

        raw = "\n\n---\n\n".join(bodies)

        print(f"\r  \033[2mSynthesizing…\033[0m", end="", flush=True)
        brief = await call_claude(client, research_synthesis_prompt(topic, raw), max_tokens=500)
        print(f"\r  \033[32m✓  Research ready ({len(results)} sources)\033[0m          ")
        return brief

    except Exception as e:
        print(f"\r  \033[33m  Research failed ({e}) — generating without it.\033[0m")
        return None


def fetch_news_articles(max_per_feed: int = 5) -> list[dict]:
    """Pull recent articles from all configured news feeds."""
    articles = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; content-bot/1.0)"}
    for feed in NEWS_FEEDS:
        try:
            resp = requests.get(feed["url"], headers=headers, timeout=8)
            parsed = feedparser.parse(resp.text)
            for entry in parsed.entries[:max_per_feed]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue
                articles.append({
                    "source": feed["name"],
                    "title": title,
                    "link": link,
                    "summary": entry.get("summary", "")[:300],
                })
        except Exception:
            continue
    return articles


def select_news_article(articles: list[dict]) -> dict | None:
    """Display fetched articles and let the user pick one."""
    if not articles:
        print("\033[31m  No articles fetched. Check your connection.\033[0m")
        return None

    shown = articles[:15]

    print(f"\n\033[1m\033[36m{'━' * 70}\033[0m")
    print(f"\033[1m\033[36m  Latest News — pick one to write about\033[0m")
    print(f"\033[1m\033[36m{'━' * 70}\033[0m\n")

    for i, a in enumerate(shown):
        print(f"  \033[1m[{i+1:2}]\033[0m  \033[36m{a['source']}\033[0m")
        print(f"        {a['title'][:80]}")
        print()

    try:
        choice = input(f"  \033[1mPick a number (1–{len(shown)}), or Enter to cancel:\033[0m  ").strip()
        if not choice:
            return None
        idx = int(choice) - 1
        if 0 <= idx < len(shown):
            return shown[idx]
    except (ValueError, KeyboardInterrupt):
        pass
    return None


# ── Prompts ───────────────────────────────────────────────────────────────────

def linkedin_prompt(topic: str, research: str | None = None) -> str:
    research_block = f"\n\nCURRENT RESEARCH & FACTS — use these to make the post specific and grounded. Reference real details where they fit naturally:\n{research}\n" if research else ""
    return f"""Write a LinkedIn post about: {topic}
{research_block}

Requirements:
- 150-200 words (count carefully)
- Write exactly like a thoughtful human professional would — with real feelings, genuine opinions, and natural rhythm
- BANNED phrases and words: "In today's fast-paced world", "game-changer", "Let's dive in", "It's no secret", "At the end of the day", "Thrilled to share", "leverage", "utilize", "robust", "seamless", "transformative", "delve", "harness", "synergy", "paradigm", "groundbreaking", "cutting-edge", "comprehensive", "pivotal"
- NO em dashes (—). Use commas, periods, or a new sentence instead.
- Use first-person but write as a curious observer or commentator — someone who follows this space closely, reads about it, finds it fascinating or alarming
- Do NOT assume you have a CFO, company, employees, or corporate role. Write as an individual who is aware of what's happening in the world, not as an executive or business owner
- Short punchy paragraphs — no walls of text
- One light question at the end to invite real conversation
- Maximum 2 emojis, placed naturally, not forced
- Do NOT add hashtags

After the post, on a new line, write exactly:
---INFOGRAPHIC---
Then provide a JSON object on one line:
{{"create": true or false, "headline": "one punchy insight (max 12 words)", "points": ["point one (max 6 words)", "point two (max 6 words)", "point three (max 6 words)"], "stat": "one compelling stat or fact with source if known, or empty string"}}

Set create=true only if a visual card would genuinely add value to this specific topic (data, lists, frameworks). Otherwise false."""


def email_hook_prompt(topic: str, research: str | None = None) -> str:
    research_block = f"\n\nCURRENT RESEARCH & FACTS — pull from these to make the hook specific, not generic:\n{research}\n" if research else ""
    return f"""Write an email hook for: {topic}
{research_block}

Requirements:
- Exactly 2-3 lines
- Each line is its own short, punchy sentence
- Creates immediate curiosity or a "wait, what?" reaction
- Feels human and direct — not like marketing copy
- No generic openers. Make it specific to the topic
- NO em dashes (—). Use commas or a new sentence instead.
- NO jargon: no "leverage", "utilize", "robust", "seamless", "transformative"
- Do NOT include a subject line, sign-off, or anything else

Output ONLY the 2-3 lines. Nothing else."""


def blog_post_prompt(topic: str, research: str | None = None) -> str:
    research_block = f"\n\nCURRENT RESEARCH & FACTS — weave specific details, stats, and developments from this into the article. Don't list them robotically — use them where they strengthen the argument:\n{research}\n" if research else ""
    return f"""Write a complete blog article about: {topic}
{research_block}

WRITING RULES — follow all of these exactly:
- NO em dashes (—). Use commas, periods, or a new sentence instead.
- NO jargon: no "leverage", "utilize", "robust", "seamless", "transformative", "delve", "harness", "synergy", "paradigm", "groundbreaking", "cutting-edge", "comprehensive", "pivotal", "game-changer", "revolutionize", "innovative"
- Write like a real person thinking carefully about this topic. First-person where it feels natural.
- Varied sentence length. Some short. Some longer when the idea needs room.
- No bullet-point summaries. No "in conclusion". No "in this article we will explore".
- Honest, specific, grounded. Not hype.
- Total length: 900-1200 words across all sections.

Format your response EXACTLY as follows (no extra text before or after):

---BLOG_POST---
TITLE: [specific, compelling title — not generic]
CATEGORY: [1-2 word categories, e.g. "Security · Finance"]
SUBTITLE: [one sentence that says what the article is actually about]
READ_TIME: [estimated minutes, e.g. 5]

SECTION: [section title]
[2-3 paragraphs of prose. Real sentences. No bullet lists.]

SECTION: [section title]
[2-3 paragraphs of prose]

SECTION: [section title]
[2-3 paragraphs of prose]

SECTION: [section title]
[2-3 paragraphs of prose]

SECTION: [section title]
[2-3 paragraphs of prose]

AUTHOR_NOTE: [1 sentence — what perspective you're writing from]
---END_BLOG_POST---

After ---END_BLOG_POST---, on a new line write exactly:
---INFOGRAPHIC---
Then provide a JSON object on one line:
{{"create": true or false, "title": "short visual title for the infographic", "sections": ["Section 1 name", "Section 2 name", "Section 3 name", "Section 4 name", "Section 5 name"], "color_theme": "professional or energetic or minimal"}}

Set create=true if a visual roadmap/overview card would genuinely help a reader grasp the structure."""


# ── Blog post parser ─────────────────────────────────────────────────────────

def parse_blog_post(raw: str) -> tuple[dict | None, dict | None]:
    """
    Parse the structured blog post format.
    Returns (post_data, infographic_data).
    post_data keys: title, category, subtitle, read_time, sections (list of {title, body}), author_note
    """
    # Split off infographic data first
    blog_block = raw
    infographic_data = None

    if "---INFOGRAPHIC---" in raw:
        parts = raw.split("---INFOGRAPHIC---", 1)
        blog_block = parts[0]
        try:
            ig = json.loads(parts[1].strip())
            if ig.get("create", False):
                infographic_data = ig
        except json.JSONDecodeError:
            pass

    # Extract the main blog post block
    if "---BLOG_POST---" in blog_block:
        blog_block = blog_block.split("---BLOG_POST---", 1)[1]
    if "---END_BLOG_POST---" in blog_block:
        blog_block = blog_block.split("---END_BLOG_POST---")[0]

    blog_block = blog_block.strip()
    lines = blog_block.split("\n")

    post: dict = {"title": "", "category": "", "subtitle": "", "read_time": "5", "sections": [], "author_note": ""}

    current_section_title = None
    current_section_lines: list[str] = []

    def flush_section():
        if current_section_title is not None:
            post["sections"].append({
                "title": current_section_title,
                "body": "\n".join(current_section_lines).strip(),
            })

    for line in lines:
        if line.startswith("TITLE:"):
            post["title"] = line[6:].strip()
        elif line.startswith("CATEGORY:"):
            post["category"] = line[9:].strip()
        elif line.startswith("SUBTITLE:"):
            post["subtitle"] = line[9:].strip()
        elif line.startswith("READ_TIME:"):
            post["read_time"] = line[10:].strip()
        elif line.startswith("AUTHOR_NOTE:"):
            flush_section()
            current_section_title = None
            current_section_lines = []
            post["author_note"] = line[12:].strip()
        elif line.startswith("SECTION:"):
            flush_section()
            current_section_title = line[8:].strip()
            current_section_lines = []
        elif current_section_title is not None:
            current_section_lines.append(line)

    flush_section()

    if not post["title"]:
        return None, infographic_data

    return post, infographic_data


# ── Blog HTML renderer ────────────────────────────────────────────────────────

def render_blog_html(post: dict, slug: str) -> str:
    """Generate a standalone HTML blog post matching the portfolio style."""
    title = post["title"]
    category = post["category"]
    subtitle = post["subtitle"]
    read_time = post["read_time"]
    author_note = post["author_note"]
    month_year = datetime.now().strftime("%b %Y")

    # Build section HTML
    section_html_parts = []
    for section in post["sections"]:
        paragraphs = [p.strip() for p in section["body"].split("\n\n") if p.strip()]
        para_html = "\n".join(f"    <p>{p}</p>" for p in paragraphs)
        section_html_parts.append(f"    <h2>{section['title']}</h2>\n{para_html}")

    sections_html = "\n\n".join(section_html_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Beyaricko Degu</title>
  <meta name="description" content="{subtitle}" />
  <meta name="author" content="Beyaricko Degu" />
  <meta property="og:title" content="{title} | Beyaricko Degu" />
  <meta property="og:description" content="{subtitle}" />
  <meta property="og:type" content="article" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Sora:wght@300;400;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="assets/css/style.css" />
  <style>
    .article-hero {{
      position: relative; z-index: 2;
      min-height: 52vh; display: flex; align-items: flex-end;
      padding: 10rem 4rem 5rem;
      border-bottom: 1px solid var(--border);
    }}
    .article-hero-inner {{ max-width: 900px; }}
    .article-tag {{
      font-family: 'Space Mono', monospace; font-size: .6rem;
      letter-spacing: .3em; text-transform: uppercase;
      color: var(--orange); margin-bottom: 1.75rem;
      display: flex; align-items: center; gap: .6rem;
    }}
    .article-tag::before {{ content: '> '; color: var(--cyan); }}
    .article-title {{
      font-family: 'Orbitron', monospace; font-weight: 900;
      font-size: clamp(2rem, 5vw, 3.4rem); line-height: 1.08;
      color: #fff; margin-bottom: 1.5rem; opacity: 1; animation: none;
    }}
    .article-subtitle {{
      font-family: 'Sora', sans-serif; font-size: 1.05rem;
      line-height: 1.75; color: var(--muted); max-width: 640px;
      margin-bottom: 2.5rem;
    }}
    .article-meta {{
      font-family: 'Space Mono', monospace; font-size: .62rem;
      letter-spacing: .15em; color: var(--muted);
      display: flex; align-items: center; gap: 1.25rem; flex-wrap: wrap;
    }}
    .article-meta .sep {{ color: var(--border); }}
    .back-bar {{
      position: relative; z-index: 2; padding: 1.25rem 4rem;
      border-bottom: 1px solid var(--border);
      background: rgba(4,4,10,0.6);
    }}
    .back-link {{
      font-family: 'Space Mono', monospace; font-size: .62rem;
      letter-spacing: .15em; text-transform: uppercase;
      color: var(--muted); text-decoration: none;
      display: inline-flex; align-items: center; gap: .6rem; transition: color .2s;
    }}
    .back-link::before {{ content: '←'; color: var(--orange); }}
    .back-link:hover {{ color: var(--cyan); }}
    .article-body {{
      position: relative; z-index: 2; background: var(--bg2);
      border-bottom: 1px solid var(--border);
      padding: 5rem 4rem;
    }}
    .article-content {{
      max-width: 720px; margin: 0 auto;
      font-family: 'Sora', sans-serif; font-size: 1.05rem; line-height: 1.85;
      color: var(--text);
    }}
    .article-content h2 {{
      font-family: 'Orbitron', monospace; font-weight: 700;
      font-size: 1.2rem; color: var(--cyan); margin: 3rem 0 1rem;
      letter-spacing: .04em; text-transform: uppercase;
    }}
    .article-content p {{ margin-bottom: 1.4rem; color: var(--muted); }}
    .article-author {{
      position: relative; z-index: 2; padding: 4rem;
      background: rgba(4,4,10,0.4);
    }}
    .author-card {{
      max-width: 720px; margin: 0 auto;
      border: 1px solid var(--border); border-radius: 8px;
      padding: 2rem; background: var(--bg2);
    }}
    .author-label {{
      font-family: 'Space Mono', monospace; font-size: .6rem;
      letter-spacing: .25em; text-transform: uppercase;
      color: var(--cyan); margin-bottom: .75rem;
    }}
    .author-name {{
      font-family: 'Orbitron', monospace; font-size: 1rem;
      font-weight: 700; color: #fff; margin-bottom: .5rem;
    }}
    .author-bio {{
      font-family: 'Sora', sans-serif; font-size: .9rem;
      line-height: 1.7; color: var(--muted);
    }}
    @media (max-width: 768px) {{
      .article-hero, .back-bar, .article-body, .article-author {{
        padding-left: 1.5rem; padding-right: 1.5rem;
      }}
    }}
  </style>
</head>
<body>
<div id="glow"></div>

<nav>
  <a href="index.html" class="nav-logo">BD</a>
  <ul class="nav-links">
    <li><a href="index.html#about">About</a></li>
    <li><a href="index.html#portfolio">Work</a></li>
    <li><a href="index.html#blog">Blog</a></li>
    <li><a href="index.html#contact">Contact</a></li>
    <li><a href="cv.html" target="_blank">CV</a></li>
  </ul>
  <a href="index.html#contact" class="nav-cta">Hire Me</a>
</nav>

<div class="back-bar">
  <a href="index.html#blog" class="back-link">Back to all articles</a>
</div>

<section class="article-hero">
  <div class="article-hero-inner">
    <p class="article-tag">{category}</p>
    <h1 class="article-title">{title}</h1>
    <p class="article-subtitle">{subtitle}</p>
    <div class="article-meta">
      <span>Beyaricko Degu</span>
      <span class="sep">·</span>
      <span>{month_year}</span>
      <span class="sep">·</span>
      <span>{read_time} min read</span>
    </div>
  </div>
</section>

<div class="article-body">
  <article class="article-content">
{sections_html}
  </article>
</div>

<div class="article-author">
  <div class="author-card">
    <p class="author-label">// About the author</p>
    <p class="author-name">Beyaricko Degu</p>
    <p class="author-bio">{author_note} Digital builder and AI automation practitioner finishing his Master of Content and Media Strategy at NHL Stenden, Netherlands.</p>
  </div>
</div>

<script src="assets/js/main.js"></script>
</body>
</html>"""


# ── GitHub portfolio publisher ────────────────────────────────────────────────

def _github_token() -> str | None:
    """Get the GitHub auth token via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        token = result.stdout.strip()
        return token if token else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _github_get(token: str, path: str) -> dict:
    resp = requests.get(
        f"https://api.github.com/repos/{PORTFOLIO_REPO}/contents/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _github_put(token: str, path: str, content: str, message: str, sha: str | None = None) -> bool:
    body: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    resp = requests.put(
        f"https://api.github.com/repos/{PORTFOLIO_REPO}/contents/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json=body,
        timeout=20,
    )
    return resp.status_code in (200, 201)


def publish_blog_to_portfolio(post: dict, html: str, slug: str) -> tuple[bool, str]:
    """
    Push a new blog post HTML file and add a card to index.html.
    Only adds content — never touches existing pages or cards.
    """
    token = _github_token()
    if not token:
        return False, "gh CLI not authenticated. Run: gh auth login"

    filename = f"{slug}.html"

    # ── 1. Create the blog post HTML file ────────────────────────────────────
    ok = _github_put(token, filename, html, f"blog: add {post['title']}")
    if not ok:
        return False, f"Failed to create {filename} on GitHub"

    # ── 2. Fetch current index.html ───────────────────────────────────────────
    try:
        index_data = _github_get(token, "index.html")
    except Exception as e:
        return False, f"Could not fetch index.html: {e}"

    index_sha = index_data["sha"]
    index_html = base64.b64decode(index_data["content"]).decode()

    # ── 3. Build new blog card ────────────────────────────────────────────────
    month_year = datetime.now().strftime("%b %Y")
    read_time = post.get("read_time", "5")
    excerpt = post.get("subtitle", "")[:150]
    category_html = post.get("category", "Article").replace("·", "&amp;")

    new_card = f"""
    <a href="{filename}" class="blog-card">
      <p class="blog-category">{category_html}</p>
      <p class="blog-title">{post['title']}</p>
      <p class="blog-excerpt">{excerpt}</p>
      <div class="blog-meta"><span>{month_year}</span><span>{read_time} min read</span></div>
    </a>"""

    # ── 4. Prepend card inside .blog-grid — leave all existing cards intact ──
    marker = '<div class="blog-grid reveal">'
    if marker not in index_html:
        return False, "Could not find .blog-grid in index.html — not touching the file"

    updated_index = index_html.replace(marker, marker + new_card, 1)

    # ── 5. Push updated index.html ────────────────────────────────────────────
    ok = _github_put(
        token, "index.html", updated_index,
        f"blog: add card for '{post['title']}'",
        sha=index_sha,
    )
    if not ok:
        return False, "Blog post file was created but index.html card update failed"

    return True, filename


def maybe_publish_to_portfolio(post: dict, html: str, slug: str):
    """Review gate before publishing to portfolio."""
    print(f"\n\033[1m\033[33m{'━' * 70}\033[0m")
    print(f"\033[1m\033[33m  Publish blog to portfolio?\033[0m")
    print(f"\033[1m\033[33m{'━' * 70}\033[0m")
    print(f"\033[2m  Title: {post['title']}\033[0m")
    print(f"\033[2m  File:  {slug}.html\033[0m\n")

    try:
        answer = input("  \033[1m[y] Publish now   [n] Skip\033[0m  →  ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipped.")
        return

    if answer != "y":
        print("  \033[2mSkipped — blog saved to file only.\033[0m")
        return

    print("  Publishing…")
    ok, result = publish_blog_to_portfolio(post, html, slug)

    if ok:
        print(f"\n  \033[1m\033[32m✓  Published to portfolio.\033[0m")
        print(f"  \033[2mhttps://jerichonega.github.io/portfolio/{result}\033[0m")
    else:
        print(f"\n  \033[31m✗  Publish failed: {result}\033[0m")
        print(f"  \033[2mBlog HTML saved locally — you can push manually.\033[0m")


# ── API calls (async, parallel) ───────────────────────────────────────────────

async def call_claude(client: anthropic.AsyncAnthropic, prompt: str, max_tokens: int = 1024) -> str:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def split_infographic_data(raw: str) -> tuple[str, dict | None]:
    """Split response at ---INFOGRAPHIC--- marker and parse JSON."""
    if "---INFOGRAPHIC---" not in raw:
        return raw.strip(), None

    parts = raw.split("---INFOGRAPHIC---", 1)
    content = parts[0].strip()
    json_str = parts[1].strip()

    try:
        data = json.loads(json_str)
        if not data.get("create", False):
            return content, None
        return content, data
    except json.JSONDecodeError:
        return content, None


# ── Infographic renderers ─────────────────────────────────────────────────────

# Color palettes
PALETTES = {
    "linkedin": {
        "bg": "#0A192F",
        "accent": "#64FFDA",
        "text": "#CCD6F6",
        "muted": "#8892B0",
        "card": "#112240",
    },
    "blog_professional": {
        "bg": "#1A1A2E",
        "accent": "#E94560",
        "text": "#EAEAEA",
        "muted": "#A0A0B0",
        "card": "#16213E",
    },
    "blog_energetic": {
        "bg": "#0D0D0D",
        "accent": "#FF6B35",
        "text": "#F5F5F5",
        "muted": "#999999",
        "card": "#1A1A1A",
    },
    "blog_minimal": {
        "bg": "#F8F8F8",
        "accent": "#2D2D2D",
        "text": "#1A1A1A",
        "muted": "#666666",
        "card": "#FFFFFF",
    },
}


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def draw_rounded_rect(draw: ImageDraw.Draw, xy: tuple, radius: int, fill: str):
    x1, y1, x2, y2 = xy
    r = radius
    draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
    draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
    draw.ellipse([x1, y1, x1 + 2*r, y1 + 2*r], fill=fill)
    draw.ellipse([x2 - 2*r, y1, x2, y1 + 2*r], fill=fill)
    draw.ellipse([x1, y2 - 2*r, x1 + 2*r, y2], fill=fill)
    draw.ellipse([x2 - 2*r, y2 - 2*r, x2, y2], fill=fill)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def create_linkedin_infographic(topic: str, data: dict, out_path: Path) -> Path:
    """1080x1080 LinkedIn insight card."""
    W, H = 1080, 1080
    p = PALETTES["linkedin"]

    img = Image.new("RGB", (W, H), p["bg"])
    draw = ImageDraw.Draw(img)

    # Subtle grid texture
    for x in range(0, W, 60):
        draw.line([(x, 0), (x, H)], fill="#0D2137", width=1)
    for y in range(0, H, 60):
        draw.line([(0, y), (W, y)], fill="#0D2137", width=1)

    # Accent bar top
    draw.rectangle([0, 0, W, 6], fill=p["accent"])

    # Load fonts
    f_small = load_font(22)
    f_body = load_font(30)
    f_heading = load_font(48, bold=True)
    f_big = load_font(64, bold=True)
    f_label = load_font(20)

    # Topic label
    topic_short = topic[:55] + ("…" if len(topic) > 55 else "")
    draw.text((60, 52), topic_short.upper(), font=f_small, fill=p["muted"])

    # Divider line
    draw.rectangle([60, 100, W - 60, 103], fill=p["accent"])

    # Headline
    headline = data.get("headline", "Key Insight")
    hl_lines = wrap_text(headline, f_heading, W - 120, draw)
    y = 140
    for line in hl_lines:
        draw.text((60, y), line, font=f_heading, fill=p["text"])
        y += 62

    # Stat block
    stat = data.get("stat", "")
    if stat:
        y += 20
        draw_rounded_rect(draw, (60, y, W - 60, y + 120), 16, p["card"])
        stat_lines = wrap_text(stat, f_body, W - 160, draw)
        sy = y + 24
        for line in stat_lines[:2]:
            draw.text((80, sy), line, font=f_body, fill=p["accent"])
            sy += 38
        y += 140

    y += 30

    # Bullet points
    points = data.get("points", [])
    for i, point in enumerate(points[:3]):
        draw_rounded_rect(draw, (60, y, W - 60, y + 90), 14, p["card"])
        # Numbered circle
        cx, cy = 110, y + 45
        draw.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=p["accent"])
        num_font = load_font(24, bold=True)
        draw.text((cx, cy), str(i + 1), font=num_font, fill=p["bg"], anchor="mm")
        # Point text
        draw.text((155, y + 28), point, font=f_body, fill=p["text"])
        y += 110

    # Footer
    draw.rectangle([0, H - 6, W, H], fill=p["accent"])
    footer_txt = "Generated insight card"
    draw.text((60, H - 46), footer_txt, font=f_label, fill=p["muted"])

    img.save(out_path, "PNG", quality=95)
    return out_path


def create_blog_infographic(topic: str, data: dict, out_path: Path) -> Path:
    """1200x628 blog overview card (Open Graph size)."""
    W, H = 1200, 628
    theme = data.get("color_theme", "professional")
    palette_key = f"blog_{theme}" if f"blog_{theme}" in PALETTES else "blog_professional"
    p = PALETTES[palette_key]

    img = Image.new("RGB", (W, H), p["bg"])
    draw = ImageDraw.Draw(img)

    # Side accent strip
    draw.rectangle([0, 0, 8, H], fill=p["accent"])

    # Load fonts
    f_label = load_font(18)
    f_section = load_font(24)
    f_title = load_font(42, bold=True)
    f_tag = load_font(20)

    # "Blog Outline" tag
    draw_rounded_rect(draw, (40, 36, 200, 72), 10, p["card"])
    draw.text((55, 46), "BLOG OUTLINE", font=f_label, fill=p["accent"])

    # Infographic title
    inf_title = data.get("title", topic[:50])
    title_lines = wrap_text(inf_title, f_title, W - 440, draw)
    y = 90
    for line in title_lines[:2]:
        draw.text((40, y), line, font=f_title, fill=p["text"])
        y += 54

    # Divider
    draw.rectangle([40, y + 10, 340, y + 13], fill=p["accent"])
    y += 40

    # Sections — numbered list
    sections = data.get("sections", [])
    for i, section in enumerate(sections[:5]):
        sy = y + i * 82

        # Number badge
        bx = 40
        draw.ellipse([bx, sy, bx + 46, sy + 46], fill=p["accent"])
        num_font = load_font(22, bold=True)
        draw.text((bx + 23, sy + 23), str(i + 1), font=num_font, fill=p["bg"], anchor="mm")

        # Section label
        draw.text((100, sy + 10), section, font=f_section, fill=p["text"])

        # Subtle separator
        if i < 4:
            draw.rectangle([40, sy + 58, W - 60, sy + 60], fill=p["card"])

    # Right-side decorative block
    rx = W - 320
    draw_rounded_rect(draw, (rx, 36, W - 36, H - 36), 20, p["card"])

    # "5 Sections" callout
    draw.text((rx + 60, 90), "5", font=load_font(110, bold=True), fill=p["accent"])
    draw.text((rx + 60, 210), "sections", font=load_font(28), fill=p["muted"])
    draw.rectangle([rx + 40, 260, W - 76, 263], fill=p["accent"])
    draw.text((rx + 60, 280), "to master", font=f_section, fill=p["text"])

    topic_clipped = (topic[:28] + "…") if len(topic) > 28 else topic
    tl = wrap_text(topic_clipped, f_tag, 220, draw)
    ty = 330
    for line in tl[:3]:
        draw.text((rx + 60, ty), line, font=f_tag, fill=p["muted"])
        ty += 28

    # Bottom accent bar
    draw.rectangle([0, H - 5, W, H], fill=p["accent"])

    img.save(out_path, "PNG", quality=95)
    return out_path


# ── File helpers ──────────────────────────────────────────────────────────────

def safe_filename(topic: str) -> str:
    """Convert topic to a safe filename base."""
    cleaned = re.sub(r"[^\w\s-]", "", topic).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:50].lower()


def save_txt(content: str, path: Path) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ── Terminal output ───────────────────────────────────────────────────────────

def divider(char: str = "─", width: int = 70) -> str:
    return char * width


def print_section(title: str, content: str, color_code: str = ""):
    reset = "\033[0m"
    bold = "\033[1m"
    print(f"\n{bold}{color_code}{'━' * 70}{reset}")
    print(f"{bold}{color_code}  {title}{reset}")
    print(f"{bold}{color_code}{'━' * 70}{reset}")
    wrapped = textwrap.fill(content, width=68, initial_indent="  ", subsequent_indent="  ")
    print(wrapped)


def print_files_summary(files: list[tuple[str, Path]]):
    print(f"\n\033[1m\033[32m{'━' * 70}\033[0m")
    print(f"\033[1m\033[32m  Files saved\033[0m")
    print(f"\033[1m\033[32m{'━' * 70}\033[0m")
    for label, path in files:
        size = path.stat().st_size
        size_str = f"{size:,} bytes" if size < 1024 else f"{size / 1024:.1f} KB"
        print(f"  \033[32m✓\033[0m  {label:<30}  {path.name}  ({size_str})")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(topic: str, research_brief: str | None = None, client: anthropic.AsyncAnthropic | None = None):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = safe_filename(topic)

    print(f"\n\033[1m\033[36m{'━' * 70}\033[0m")
    print(f"\033[1m\033[36m  Content Pipeline\033[0m")
    print(f"\033[1m\033[36m  Topic: {topic[:60]}\033[0m")
    print(f"\033[1m\033[36m{'━' * 70}\033[0m\n")

    ai = client or anthropic.AsyncAnthropic()

    # ── Research step ────────────────────────────────────────────────────────
    if research_brief is None:
        research_brief = await gather_web_research(topic, ai)

    if research_brief:
        print(f"\n\033[2m  Generating 3 outputs in parallel with research context…\033[0m\n")
    else:
        print(f"\n\033[2m  Generating 3 outputs in parallel…\033[0m\n")

    # ── Parallel API calls ───────────────────────────────────────────────────
    linkedin_raw, email_raw, blog_raw = await asyncio.gather(
        call_claude(ai, linkedin_prompt(topic, research_brief)),
        call_claude(ai, email_hook_prompt(topic, research_brief)),
        call_claude(ai, blog_post_prompt(topic, research_brief), max_tokens=3000),
    )

    # ── Parse responses ──────────────────────────────────────────────────────
    linkedin_text, linkedin_infographic_data = split_infographic_data(linkedin_raw)
    email_text = email_raw.strip()
    blog_post, blog_infographic_data = parse_blog_post(blog_raw)

    # Fallback text for display/saving if parsing fails
    blog_display_text = blog_raw.split("---INFOGRAPHIC---")[0].strip() if blog_post is None else (
        f"{blog_post['title']}\n\n" +
        "\n\n".join(f"{s['title']}\n{s['body']}" for s in blog_post["sections"])
    )

    # ── Save text files ──────────────────────────────────────────────────────
    linkedin_path = save_txt(
        linkedin_text,
        OUTPUTS_DIR / f"{slug}_linkedin_{timestamp}.txt",
    )
    email_path = save_txt(
        email_text,
        OUTPUTS_DIR / f"{slug}_email_hook_{timestamp}.txt",
    )
    blog_path = save_txt(
        blog_display_text,
        OUTPUTS_DIR / f"{slug}_blog_{timestamp}.txt",
    )

    saved_files: list[tuple[str, Path]] = [
        ("LinkedIn post", linkedin_path),
        ("Email hook", email_path),
        ("Blog article", blog_path),
    ]

    # ── Render blog HTML ─────────────────────────────────────────────────────
    blog_html: str | None = None
    if blog_post:
        blog_html = render_blog_html(blog_post, slug)
        blog_html_path = OUTPUTS_DIR / f"{slug}_blog_{timestamp}.html"
        blog_html_path.write_text(blog_html, encoding="utf-8")
        saved_files.append(("Blog HTML", blog_html_path))

    # ── Generate infographics ────────────────────────────────────────────────
    if linkedin_infographic_data:
        try:
            li_img_path = create_linkedin_infographic(
                topic,
                linkedin_infographic_data,
                OUTPUTS_DIR / f"{slug}_linkedin_infographic_{timestamp}.png",
            )
            saved_files.append(("LinkedIn infographic", li_img_path))
        except Exception as e:
            print(f"\033[33m  ⚠  LinkedIn infographic skipped: {e}\033[0m")

    if blog_infographic_data:
        try:
            blog_img_path = create_blog_infographic(
                topic,
                blog_infographic_data,
                OUTPUTS_DIR / f"{slug}_blog_infographic_{timestamp}.png",
            )
            saved_files.append(("Blog infographic", blog_img_path))
        except Exception as e:
            print(f"\033[33m  ⚠  Blog infographic skipped: {e}\033[0m")

    # ── Print terminal summary ───────────────────────────────────────────────
    print_section("LINKEDIN POST", linkedin_text, "\033[34m")
    print_section("EMAIL HOOK", email_text, "\033[35m")
    if blog_post:
        blog_preview = f"[{blog_post['category']}]  {blog_post['title']}\n{blog_post['subtitle']}\n\n" + \
            "\n".join(f"  {i+1}. {s['title']}" for i, s in enumerate(blog_post["sections"]))
        print_section("BLOG ARTICLE", blog_preview, "\033[33m")
    else:
        print_section("BLOG ARTICLE", blog_display_text[:600], "\033[33m")
    print_files_summary(saved_files)

    infographic_note = ""
    if linkedin_infographic_data or blog_infographic_data:
        count = sum([bool(linkedin_infographic_data), bool(blog_infographic_data)])
        infographic_note = f"  + {count} infographic(s) generated\n"

    print(f"\n\033[1m\033[36m  Done.\033[0m  All outputs saved to: \033[4m{OUTPUTS_DIR}\033[0m")
    if infographic_note:
        print(f"\033[36m{infographic_note}\033[0m")

    # ── LinkedIn review + post ───────────────────────────────────────────────
    linkedin_image_path = next(
        (p for label, p in saved_files if label == "LinkedIn infographic"), None
    )
    maybe_post_to_linkedin(linkedin_text, linkedin_image_path)

    # ── Portfolio review + publish ───────────────────────────────────────────
    if blog_post and blog_html:
        maybe_publish_to_portfolio(blog_post, blog_html, slug)


# ── LinkedIn posting ──────────────────────────────────────────────────────────

def get_linkedin_person_urn(access_token: str) -> str | None:
    """Resolve the correct author URN from LinkedIn's OpenID userinfo endpoint."""
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code == 200:
        sub = resp.json().get("sub", "").strip()
        if sub:
            return f"urn:li:person:{sub}"
    return None


def linkedin_upload_image(access_token: str, author: str, image_path: Path) -> str | None:
    """Upload an image to LinkedIn and return the asset URN, or None on failure."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # Step 1: register the upload
    register_payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": author,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    reg_resp = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=headers,
        json=register_payload,
        timeout=15,
    )
    if reg_resp.status_code != 200:
        return None

    reg_data = reg_resp.json().get("value", {})
    asset_urn = reg_data.get("asset")
    upload_mechanisms = reg_data.get("uploadMechanism", {})
    upload_url = (
        upload_mechanisms
        .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
        .get("uploadUrl")
    )
    if not asset_urn or not upload_url:
        return None

    # Step 2: upload the image binary
    with open(image_path, "rb") as f:
        image_data = f.read()

    upload_resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/png",
        },
        data=image_data,
        timeout=30,
    )
    if upload_resp.status_code not in (200, 201):
        return None

    return asset_urn


def linkedin_post(access_token: str, person_urn: str, text: str, image_path: Path | None = None) -> tuple[bool, str]:
    """Publish a post. Tries UGC Posts first, then REST Posts with several versions."""
    base_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # ── 1. Resolve the correct author URN from the API ────────────────────────
    api_urn = get_linkedin_person_urn(access_token)
    author = api_urn or person_urn

    # ── 2. Upload image if provided ───────────────────────────────────────────
    asset_urn = None
    if image_path and image_path.exists():
        print("  Uploading infographic…")
        asset_urn = linkedin_upload_image(access_token, author, image_path)
        if asset_urn:
            print(f"  \033[2mImage asset: {asset_urn}\033[0m")
        else:
            print("  \033[33m  Image upload failed — posting text only.\033[0m")

    # ── 3. UGC Posts (classic API, no version header required) ────────────────
    if asset_urn:
        share_content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "IMAGE",
            "media": [
                {
                    "status": "READY",
                    "description": {"text": ""},
                    "media": asset_urn,
                    "title": {"text": ""},
                }
            ],
        }
    else:
        share_content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "NONE",
        }

    ugc_payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    ugc_resp = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=base_headers,
        json=ugc_payload,
        timeout=15,
    )

    if ugc_resp.status_code == 201:
        post_id = ugc_resp.headers.get("x-restli-id", "") or ugc_resp.json().get("id", "")
        return True, post_id

    ugc_error = ugc_resp.text

    # ── 3. REST Posts fallback — walk through candidate versions ─────────────
    # LinkedIn keeps a rolling ~12-month window; try recent quarterly releases.
    candidate_versions = [
        "20260101", "20251001", "20250701", "20250401",
        "20250101", "20241001", "20240701", "20240401",
        "20231001", "20230701", "20230401",
    ]

    rest_headers = {**base_headers, "LinkedIn-Version": ""}
    rest_author = person_urn if person_urn.startswith("urn:li:member:") else author

    for version in candidate_versions:
        rest_headers["LinkedIn-Version"] = version
        rest_payload = {
            "author": rest_author,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        rest_resp = requests.post(
            "https://api.linkedin.com/rest/posts",
            headers=rest_headers,
            json=rest_payload,
            timeout=15,
        )

        if rest_resp.status_code == 201:
            post_id = rest_resp.headers.get("x-restli-id", "")
            return True, post_id

        # 426 = wrong version — keep trying; anything else = stop
        if rest_resp.status_code != 426:
            return False, f"UGC Posts: {ugc_error}\nREST Posts ({version}): {rest_resp.text}"

    return False, f"UGC Posts: {ugc_error}\n(REST Posts tried {len(candidate_versions)} versions — all rejected)"


def maybe_post_to_linkedin(text: str, image_path: Path | None):
    """Show review prompt and post to LinkedIn if confirmed."""
    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN", "").strip()
    person_urn = os.getenv("LINKEDIN_PERSON_URN", "").strip()

    if not access_token or not person_urn:
        print(f"\n\033[2m  LinkedIn not connected. Run \033[0m\033[1mpython3 linkedin_auth.py\033[0m\033[2m to set up posting.\033[0m")
        return

    print(f"\n\033[1m\033[36m{'━' * 70}\033[0m")
    print(f"\033[1m\033[36m  Post to LinkedIn?\033[0m")
    print(f"\033[1m\033[36m{'━' * 70}\033[0m")

    image_note = "  + infographic attached" if image_path else "  (text only — no infographic)"
    print(f"\033[2m{image_note}\033[0m\n")

    try:
        answer = input("  \033[1m[y] Post now   [n] Skip\033[0m  →  ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipped.")
        return

    if answer != "y":
        print("  \033[2mSkipped — post saved to file only.\033[0m")
        return

    print("  Resolving your LinkedIn identity…")
    api_urn = get_linkedin_person_urn(access_token)
    if api_urn:
        print(f"  \033[2mAuthor URN: {api_urn}\033[0m")
    else:
        print(f"  \033[33m  Could not resolve URN from API — using saved URN: {person_urn}\033[0m")

    print("  Posting…")
    success, result = linkedin_post(access_token, person_urn, text, image_path)

    if success:
        print(f"\n  \033[1m\033[32m✓  Posted to LinkedIn.\033[0m")
        if result:
            print(f"  \033[2mPost ID: {result}\033[0m")
    else:
        print(f"\n  \033[31m✗  Post failed:\033[0m")
        for line in result.split("\n"):
            print(f"  \033[31m   {line}\033[0m")
        print(f"  \033[2mContent saved to file — you can post manually.\033[0m")


async def run_news_mode():
    """Fetch latest news, let user pick an article, generate content about it."""
    print(f"\n\033[1m\033[36m{'━' * 70}\033[0m")
    print(f"\033[1m\033[36m  News Mode  ·  Fetching latest articles…\033[0m")
    print(f"\033[1m\033[36m{'━' * 70}\033[0m")

    articles = fetch_news_articles()
    article = select_news_article(articles)

    if not article:
        print("\n  Cancelled.")
        return

    topic = article["title"]
    print(f"\n  \033[32m✓  Selected:\033[0m {topic[:70]}")
    print(f"  \033[2m  {article['source']} — {article['link'][:60]}\033[0m\n")

    # Fetch full article body and synthesize as research brief
    ai = anthropic.AsyncAnthropic()

    print("  \033[2mFetching article content…\033[0m", end="", flush=True)
    body = await fetch_article_body(article["link"])
    raw = f"SOURCE: {article['source']}\nTITLE: {article['title']}\nURL: {article['link']}\n\n{body or article['summary']}"

    print("\r  \033[2mSynthesizing research brief…\033[0m", end="", flush=True)
    brief = await call_claude(ai, research_synthesis_prompt(topic, raw), max_tokens=500)
    print(f"\r  \033[32m✓  Research brief ready\033[0m                    ")

    await run(topic, research_brief=brief, client=ai)


def main():
    news_mode = "--news" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\033[31mError: ANTHROPIC_API_KEY environment variable is not set.\033[0m")
        sys.exit(1)

    if news_mode:
        asyncio.run(run_news_mode())
        return

    if args:
        topic = " ".join(args)
    else:
        try:
            topic = input("\033[1mEnter your topic or brief:\033[0m  ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            sys.exit(0)

    if not topic:
        print("Error: topic cannot be empty.")
        sys.exit(1)

    asyncio.run(run(topic))


if __name__ == "__main__":
    main()
