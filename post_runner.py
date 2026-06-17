"""
post_runner.py — Skool poster for GitHub Actions.

Two modes:
  1. Manual run ("Run workflow"): if POST_TITLE and POST_CONTENT are set
     (from workflow_dispatch inputs), it posts exactly that, immediately.
  2. Scheduled run (cron): it looks in content.json for the entry whose
     "date" matches today (in TIMEZONE) and posts it. If none matches,
     it does nothing and exits cleanly.

All credentials come from environment variables (GitHub Secrets) — nothing
is hardcoded.
"""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from apify_client import ApifyClient

# ── Config from environment ──────────────────────────────────────────────
APIFY_TOKEN    = os.environ["APIFY_TOKEN"]
SKOOL_EMAIL    = os.environ["SKOOL_EMAIL"]
SKOOL_PASSWORD = os.environ["SKOOL_PASSWORD"]
GROUP_SLUG     = os.environ.get("GROUP_SLUG") or "test-digi-2855"
ACTOR_ID       = os.environ.get("ACTOR_ID") or "cristiantala/skool-all-in-one-api"
TIMEZONE       = os.environ.get("TIMEZONE") or "UTC"

client = ApifyClient(APIFY_TOKEN)


# ── Actor runner (mirrors your working main.py) ───────────────────────────
def run_actor(run_input: dict) -> dict:
    run = client.actor(ACTOR_ID).call(run_input=run_input)
    if run is None:
        raise RuntimeError("Actor run failed to start.")
    if run.status != "SUCCEEDED":
        raise RuntimeError(
            f"Actor run did not succeed (status={run.status}). "
            f"Run: https://console.apify.com/actors/runs/{run.id}"
        )
    items = list(client.dataset(run.default_dataset_id).iterate_items())
    if not items:
        raise RuntimeError(f"Actor returned no items. Run: {run.id}")
    result = items[0]
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError(f"Actor returned error: {result}")
    return result


def login() -> str:
    res = run_actor({
        "action": "auth:login",
        "email": SKOOL_EMAIL,
        "password": SKOOL_PASSWORD,
        "groupSlug": GROUP_SLUG,
    })
    if not res.get("success"):
        raise RuntimeError(f"Login failed: {res}")
    return res["cookies"]


def resolve_label_id(cookies: str, category_name: str) -> str | None:
    res = run_actor({
        "action": "groups:get",
        "cookies": cookies,
        "groupSlug": GROUP_SLUG,
        "params": {"slug": GROUP_SLUG},
    })
    for lbl in res.get("labels", []):
        name = lbl.get("metadata", {}).get("displayName", "")
        if name.strip().lower() == category_name.strip().lower():
            return lbl["id"]
    print(f"WARNING: category '{category_name}' not found; posting without a category.")
    return None


def create_post(cookies: str, title: str, content: str, label_id: str | None) -> dict:
    params = {"title": title, "content": content}
    if label_id:
        params["labelId"] = label_id
    return run_actor({
        "action": "posts:create",
        "cookies": cookies,
        "groupSlug": GROUP_SLUG,
        "params": params,
    })


# ── Decide what to post ────────────────────────────────────────────────────
def pick_post():
    event = os.environ.get("EVENT_NAME") or os.environ.get("GITHUB_EVENT_NAME") or ""

    # On-demand: a commit to post_now.json publishes it immediately.
    if event == "push":
        try:
            with open("post_now.json", encoding="utf-8") as f:
                item = json.load(f)
            print("Mode: on-demand (post_now.json).")
            return item["title"], item["content"], item.get("category", "Newsletter")
        except FileNotFoundError:
            print("Push event but no post_now.json; nothing to do.")
            return None

    # Manual dispatch inputs.
    title = (os.environ.get("POST_TITLE") or "").strip()
    content = (os.environ.get("POST_CONTENT") or "").strip()
    category = (os.environ.get("POST_CATEGORY") or "").strip() or "Newsletter"
    if title and content:
        print("Mode: manual (workflow_dispatch).")
        return title, content, category

    # Scheduled: today's entry from content.json.
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    print(f"Mode: scheduled. Looking for entry dated {today} ({TIMEZONE}).")
    try:
        with open("content.json", encoding="utf-8") as f:
            plan = json.load(f)
    except FileNotFoundError:
        plan = []
    for item in plan:
        if item.get("date") == today:
            return item["title"], item["content"], item.get("category", "Newsletter")
    return None


def main():
    picked = pick_post()
    if not picked:
        print("Nothing scheduled for today. Exiting cleanly.")
        return
    title, content, category = picked
    cookies = login()
    label_id = resolve_label_id(cookies, category)
    post = create_post(cookies, title, content, label_id)
    print("Created post:")
    print("  url:  ", post.get("url"))
    print("  id:   ", post.get("id"))
    print("  title:", post.get("title"))


if __name__ == "__main__":
    main()
