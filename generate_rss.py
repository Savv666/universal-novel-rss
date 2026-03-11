import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# =========================================================
# CHANGE ONLY THIS LINE FOR A DIFFERENT NOVEL
# =========================================================
START_URL = "https://novelfull.net/cultivation-online-novel/chapter-1-cultivation-online.html"

STATE_FILE = "state.json"
OUTPUT_FILE = "docs/feed.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

TIMEOUT = 25
REQUEST_DELAY = 1.0

# How many NEW chapters to crawl in one workflow run
# Increase to 300, 500, or 1000 if the site is stable
BATCH_SIZE = 300

# If True, the script keeps going until there is no next chapter
# If False, it stops after BATCH_SIZE and continues next run
TRY_FULL_CRAWL_IN_ONE_RUN = False


def safe_get(url: str, retries: int = 4) -> str:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise last_err


def normalize_url(url: str) -> str:
    return url.split("#")[0].strip()


def get_novel_key(start_url: str) -> str:
    return normalize_url(start_url)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "novel_key": None,
        "novel_title": None,
        "novel_link": None,
        "visited": {},
        "next_to_crawl": None,
        "crawl_complete": False,
    }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def reset_state_for_new_novel(start_url: str):
    return {
        "novel_key": get_novel_key(start_url),
        "novel_title": None,
        "novel_link": None,
        "visited": {},
        "next_to_crawl": None,
        "crawl_complete": False,
    }


def get_soup(url: str) -> BeautifulSoup:
    html = safe_get(url)
    return BeautifulSoup(html, "html.parser")


def site_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "wuxiaworld.com" in host:
        return "wuxiaworld"
    if "novelfull.net" in host:
        return "novelfull"
    return "generic"


def is_wuxiaworld_novel_page(url: str) -> bool:
    parsed = urlparse(url)
    if "wuxiaworld.com" not in parsed.netloc.lower():
        return False
    parts = parsed.path.strip("/").split("/")
    return len(parts) == 2 and parts[0] == "novel"


def get_wuxiaworld_start_reading(url: str) -> str:
    soup = get_soup(url)
    a = soup.find("a", string=re.compile(r"START READING", re.I))
    if not a or not a.get("href"):
        raise RuntimeError("Could not find START READING link on WuxiaWorld novel page.")
    return urljoin(url, a["href"])


def find_next_link_generic(soup: BeautifulSoup, base_url: str):
    text_patterns = [
        r"^\s*Next Chapter\s*$",
        r"^\s*Next\s*$",
        r"^\s*>\s*$",
        r"^\s*Next›\s*$",
        r"^\s*Next »\s*$",
    ]

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        for pat in text_patterns:
            if re.search(pat, text, re.I):
                return urljoin(base_url, a["href"])

    for a in soup.find_all("a", href=True):
        attrs = " ".join([
            a.get("id", ""),
            " ".join(a.get("class", [])),
            a.get("title", ""),
            " ".join(a.get("rel", [])) if a.get("rel") else "",
        ])
        if re.search(r"next", attrs, re.I):
            return urljoin(base_url, a["href"])

    return None


def extract_page_data(url: str):
    soup = get_soup(url)
    kind = site_type(url)

    novel_title = None
    novel_link = None
    chapter_title = None
    next_url = None

    if kind == "novelfull":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            if href.endswith(".html") and "/chapter-" not in href.lower() and text:
                novel_title = text
                novel_link = urljoin(url, href)
                break

        h = soup.find(["h1", "h2"])
        if h:
            chapter_title = h.get_text(" ", strip=True)

        next_url = find_next_link_generic(soup, url)

    elif kind == "wuxiaworld":
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            parts = href.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "novel" and text:
                novel_title = text
                novel_link = urljoin(url, href)
                break

        h = soup.find(["h1", "h2", "h3", "h4"])
        if h:
            chapter_title = h.get_text(" ", strip=True)

        next_url = find_next_link_generic(soup, url)

    else:
        title_tag = soup.find("title")
        if title_tag:
            chapter_title = title_tag.get_text(" ", strip=True)
        next_url = find_next_link_generic(soup, url)
        novel_link = url
        novel_title = urlparse(url).netloc

    if not chapter_title:
        title_tag = soup.find("title")
        chapter_title = title_tag.get_text(" ", strip=True) if title_tag else url.rsplit("/", 1)[-1]

    if not novel_title:
        novel_title = "Novel Feed"
    if not novel_link:
        novel_link = url

    paragraphs = []
    for p in soup.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if txt:
            paragraphs.append(txt)

    if not paragraphs:
        raw = soup.get_text("\n", strip=True)
        paragraphs = [line.strip() for line in raw.splitlines() if line.strip()]

    summary = " ".join(paragraphs[:4])[:1000]

    return {
        "url": url,
        "novel_title": novel_title,
        "novel_link": novel_link,
        "chapter_title": chapter_title,
        "next_url": next_url,
        "summary": summary,
        "fetched_at": int(time.time()),
    }


def chapter_number(title: str):
    m = re.search(r"\bChapter\s+(\d+)\b", title, re.I)
    return int(m.group(1)) if m else None


def sort_key(item):
    n = chapter_number(item["chapter_title"])
    return (n if n is not None else -1, item["url"])


def prepare_start_url(start_url: str) -> str:
    start_url = normalize_url(start_url)
    if is_wuxiaworld_novel_page(start_url):
        return get_wuxiaworld_start_reading(start_url)
    return start_url


def crawl(state: dict, start_url: str):
    visited = state.get("visited", {})
    crawl_complete = state.get("crawl_complete", False)

    if crawl_complete:
        print("Crawl already complete. No new chapters to fetch.")
        return state

    current_url = state.get("next_to_crawl") or start_url
    current_url = normalize_url(current_url)

    pages_fetched_this_run = 0

    while current_url:
        if current_url in visited:
            # Already seen this page. Stop to avoid loops.
            print(f"Already visited: {current_url}")
            state["next_to_crawl"] = None
            state["crawl_complete"] = True
            break

        data = extract_page_data(current_url)

        visited[current_url] = {
            "url": data["url"],
            "novel_title": data["novel_title"],
            "novel_link": data["novel_link"],
            "chapter_title": data["chapter_title"],
            "summary": data["summary"],
            "fetched_at": data["fetched_at"],
        }

        state["novel_title"] = data["novel_title"]
        state["novel_link"] = data["novel_link"]

        next_url = normalize_url(data["next_url"]) if data["next_url"] else None
        state["next_to_crawl"] = next_url

        pages_fetched_this_run += 1
        print(f'Fetched {pages_fetched_this_run}: {data["chapter_title"]}')

        if not next_url:
            print("No next chapter found. Crawl complete.")
            state["next_to_crawl"] = None
            state["crawl_complete"] = True
            break

        if not TRY_FULL_CRAWL_IN_ONE_RUN and pages_fetched_this_run >= BATCH_SIZE:
            print(f"Stopping after batch of {BATCH_SIZE} pages. Will continue on next run.")
            break

        current_url = next_url
        time.sleep(REQUEST_DELAY)

    state["visited"] = visited
    return state


def build_feed(state: dict):
    os.makedirs("docs", exist_ok=True)

    fg = FeedGenerator()
    fg.title(f'{state.get("novel_title", "Novel Feed")} - RSS')
    fg.link(href=state.get("novel_link", START_URL), rel="alternate")
    fg.description("Auto-generated RSS feed from chapter pages.")
    fg.language("en")

    entries = list(state.get("visited", {}).values())
    entries.sort(key=sort_key, reverse=True)

    for item in entries:
        fe = fg.add_entry()
        fe.title(item["chapter_title"])
        fe.link(href=item["url"])
        fe.guid(item["url"], permalink=True)
        fe.description(item["summary"] or item["chapter_title"])
        fe.pubDate(time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(item["fetched_at"])))

    fg.rss_file(OUTPUT_FILE)
    print(f"Wrote {OUTPUT_FILE} with {len(entries)} entries.")


def main():
    start_url = prepare_start_url(START_URL)
    novel_key = get_novel_key(START_URL)

    state = load_state()

    if state.get("novel_key") != novel_key:
        print("Detected a new novel URL. Resetting saved state.")
        state = reset_state_for_new_novel(START_URL)
        state["next_to_crawl"] = start_url

    state = crawl(state, start_url)
    save_state(state)
    build_feed(state)

    if state.get("crawl_complete"):
        print("All available chapters have been collected.")
    else:
        print(f'Next run will continue from: {state.get("next_to_crawl")}')


if __name__ == "__main__":
    main()
