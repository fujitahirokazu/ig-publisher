#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IG Carousel auto-publisher (Instagram Graph API / stdlib only).
Mirrors ig_publish.py conventions. Posts image carousels (saves-driven format).

Flow (4 steps):
1) For each image: POST /{IG_USER_ID}/media (image_url, is_carousel_item=true) -> child_id
2) POST /{IG_USER_ID}/media (media_type=CAROUSEL, children=ids, caption)     -> container_id
3) GET /{container_id}?fields=status_code  until FINISHED
4) POST /{IG_USER_ID}/media_publish (creation_id=container_id)               -> media_id

Data: data/approved_carousels_ig.json (fallback: repo root). Posts carousels[] where
status == "pending" and scheduled_date <= today (JST). Run daily by the same workflow.
NOTE: IG image API requires JPEG image_url (PNG may be rejected). Host JPEGs.

Env (same as ig_publish.py): IG_ACCESS_TOKEN, IG_USER_ID(baked), GRAPH_HOST, GRAPH_VERSION, DRY_RUN.
"""
import os, json, time, sys, datetime, urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "approved_carousels_ig.json")
if not os.path.exists(DATA):
    DATA = os.path.join(ROOT, "approved_carousels_ig.json")
LOG = os.path.join(ROOT, "logs", "posted_carousels_log.json")

GRAPH_HOST = os.environ.get("GRAPH_HOST", "graph.instagram.com")
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v22.0")
IG_USER_ID = os.environ.get("IG_USER_ID", "17841480088005154").strip()
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
BASE = f"https://{GRAPH_HOST}/{GRAPH_VERSION}"

JST = datetime.timezone(datetime.timedelta(hours=9))
def today_jst(): return datetime.datetime.now(JST).strftime("%Y-%m-%d")
def now_iso(): return datetime.datetime.now(JST).isoformat(timespec="seconds")

def _post(path, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/{path}", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def _get(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/{path}?{q}", method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def _err(e):
    try: return e.read().decode("utf-8")
    except Exception: return str(e)

def child_container(image_url):
    res = _post(f"{IG_USER_ID}/media",
                {"image_url": image_url, "is_carousel_item": "true", "access_token": TOKEN})
    return res["id"]

def carousel_container(child_ids, caption):
    res = _post(f"{IG_USER_ID}/media",
                {"media_type": "CAROUSEL", "children": ",".join(child_ids),
                 "caption": caption, "access_token": TOKEN})
    return res["id"]

def wait_finished(container_id, timeout_s=600, interval_s=8):
    waited = 0
    while waited < timeout_s:
        res = _get(container_id, {"fields": "status_code,status", "access_token": TOKEN})
        sc = res.get("status_code")
        print(f"  status: {sc} ({res.get('status','')})")
        if sc == "FINISHED":
            return True
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} -> {sc}: {res}")
        time.sleep(interval_s); waited += interval_s
    raise TimeoutError(f"container {container_id} not FINISHED in {timeout_s}s")

def publish(container_id):
    res = _post(f"{IG_USER_ID}/media_publish",
                {"creation_id": container_id, "access_token": TOKEN})
    return res["id"]

def load(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default

def main():
    if not DRY_RUN and (not IG_USER_ID or not TOKEN):
        print("ERROR: IG_USER_ID / IG_ACCESS_TOKEN missing (check GitHub Secrets).")
        sys.exit(1)
    db = load(DATA, {"carousels": []})
    log = load(LOG, [])
    today = today_jst()
    due = [c for c in db.get("carousels", [])
           if c.get("status") == "pending" and c.get("scheduled_date", "9999") <= today]
    print(f"Run at {now_iso()} (JST). today={today} due={len(due)} graph={GRAPH_VERSION}")
    if not due:
        print("nothing due. exit.")
        return
    for car in due:
        cid_ = car["id"]; imgs = car.get("image_urls", [])
        print(f"- {cid_} | {len(imgs)} images | sched={car.get('scheduled_date')}")
        if DRY_RUN:
            print("  [DRY_RUN] skip (would build children + carousel + publish)")
            continue
        try:
            children = []
            for u in imgs:
                ch = child_container(u); children.append(ch)
                print(f"  child={ch}"); time.sleep(1)
            cont = carousel_container(children, car["caption"])
            print(f"  carousel_container={cont}")
            wait_finished(cont)
            media_id = publish(cont)
            car["status"] = "posted"; car["media_id"] = media_id; car["posted_at"] = now_iso()
            log.append({"id": cid_, "media_id": media_id, "posted_at": car["posted_at"]})
            print(f"  posted media_id={media_id}")
        except urllib.error.HTTPError as e:
            car["status"] = "failed"; car["fail_at"] = now_iso(); car["error"] = _err(e)[:500]
            print(f"  HTTPError: {car['error']}")
        except Exception as e:
            car["status"] = "failed"; car["fail_at"] = now_iso(); car["error"] = str(e)[:500]
            print(f"  {e}")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(db, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("done. JSON updated.")

if __name__ == "__main__":
    main()
