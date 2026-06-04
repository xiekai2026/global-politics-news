from __future__ import annotations

import argparse
import concurrent.futures
import email.utils
import hashlib
import html
import http.cookies
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Global Politics News Monitor)"
CACHE_TTL = 15 * 60
NEWS_CACHE: dict[str, Any] = {"expires": 0.0, "payload": None}
TRANSLATION_CACHE: dict[str, Any] = {"expires": 0.0, "payload": None, "key": ""}
SESSIONS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
COOKIE = "news_session"

SOURCES = [
    {"id": "bbc-world", "name": "BBC World", "cat": "欧美", "region": "英国/全球", "url": "https://www.bbc.com/news/world", "feed": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"id": "guardian-world", "name": "The Guardian World", "cat": "欧美", "region": "英国/全球", "url": "https://www.theguardian.com/world", "feed": "https://www.theguardian.com/world/rss"},
    {"id": "guardian-us", "name": "The Guardian US Politics", "cat": "欧美", "region": "美国", "url": "https://www.theguardian.com/us-news/us-politics", "feed": "https://www.theguardian.com/us-news/us-politics/rss"},
    {"id": "nyt-world", "name": "The New York Times World", "cat": "欧美", "region": "美国/全球", "url": "https://www.nytimes.com/section/world", "feed": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"},
    {"id": "wsj-world", "name": "WSJ World", "cat": "欧美", "region": "美国/全球", "url": "https://www.wsj.com/world", "feed": "https://feeds.a.dj.com/rss/RSSWorldNews.xml"},
    {"id": "dw-world", "name": "DW World", "cat": "欧中东", "region": "德国/欧洲", "url": "https://www.dw.com/en", "feed": "https://rss.dw.com/rdf/rss-en-world"},
    {"id": "france24", "name": "France 24", "cat": "欧中东", "region": "法国/全球", "url": "https://www.france24.com/en/", "feed": "https://www.france24.com/en/rss"},
    {"id": "rfi", "name": "RFI International", "cat": "欧中东", "region": "法国/全球", "url": "https://www.rfi.fr/en/international/", "feed": "https://www.rfi.fr/en/international/rss"},
    {"id": "aljazeera", "name": "Al Jazeera", "cat": "欧中东", "region": "卡塔尔/全球", "url": "https://www.aljazeera.com", "feed": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"id": "nikkei", "name": "Nikkei Asia", "cat": "亚太", "region": "日本/亚洲", "url": "https://asia.nikkei.com", "feed": "https://asia.nikkei.com/rss/feed/nar"},
    {"id": "scmp-asia", "name": "SCMP Asia", "cat": "亚太", "region": "香港/亚洲", "url": "https://www.scmp.com/news/asia", "feed": "https://www.scmp.com/rss/91/feed"},
    {"id": "scmp-china", "name": "SCMP China", "cat": "涉华", "region": "香港/中国", "url": "https://www.scmp.com/news/china", "feed": "https://www.scmp.com/rss/4/feed"},
    {"id": "cna-world", "name": "CNA World", "cat": "亚太", "region": "新加坡/全球", "url": "https://www.channelnewsasia.com/world", "feed": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6311"},
    {"id": "hindu", "name": "The Hindu International", "cat": "亚太", "region": "印度/全球", "url": "https://www.thehindu.com/news/international/", "feed": "https://www.thehindu.com/news/international/feeder/default.rss"},
    {"id": "abc-au", "name": "ABC Australia", "cat": "亚太", "region": "澳大利亚/南太", "url": "https://www.abc.net.au/news/world", "feed": "https://www.abc.net.au/news/feed/51120/rss.xml"},
    {"id": "voa-cn", "name": "美国之音中文", "cat": "涉华", "region": "美国/中文", "url": "https://www.voachinese.com", "feed": "https://www.voachinese.com/api/zm_yql-vomx-tpeybti"},
    {"id": "dw-cn", "name": "德国之声中文", "cat": "涉华", "region": "德国/中文", "url": "https://www.dw.com/zh", "feed": "https://rss.dw.com/rdf/rss-chi-all"},
    {"id": "xinhua", "name": "Xinhua 新华社英文", "cat": "快讯", "region": "中国/全球", "url": "https://english.news.cn/world/", "feed": "https://www.xinhuanet.com/english/rss/worldrss.xml"},
    {"id": "tass", "name": "TASS 塔斯社", "cat": "快讯", "region": "俄罗斯/全球", "url": "https://tass.com", "feed": "https://tass.com/rss/v2.xml"},
    {"id": "un", "name": "UN News", "cat": "官方", "region": "联合国/全球", "url": "https://news.un.org/en/", "feed": "https://news.un.org/feed/subscribe/en/news/all/rss.xml"},
    {"id": "eu", "name": "EU Press Corner", "cat": "官方", "region": "欧盟", "url": "https://ec.europa.eu/commission/presscorner/home/en", "feed": "https://ec.europa.eu/commission/presscorner/api/rss?language=en"},
    {"id": "google", "name": "Google News World Politics", "cat": "聚合", "region": "全球", "url": "https://news.google.com", "feed": "https://news.google.com/rss/search?q=world%20politics%20diplomacy%20election&hl=en-US&gl=US&ceid=US:en"},
]

POLITICS = "politics president minister parliament congress election vote diplomacy sanction war ceasefire conflict military security nato united nations china taiwan ukraine russia israel gaza iran 时政 政治 外交 政府 总统 总理 国会 议会 选举 制裁 战争 停火 冲突 军事 安全 北约 联合国 欧盟 台湾 台海 南海 乌克兰 俄罗斯 以色列 加沙 伊朗".split()


def clean(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def local_now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(item: ET.Element, names: set[str]) -> str:
    for child in list(item):
        if tag_name(child.tag) in names:
            return clean("".join(child.itertext()))
    return ""


def parse_date(raw: str) -> tuple[str, float]:
    raw = clean(raw)
    parsed = None
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except Exception:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                parsed = None
    if not parsed:
        parsed = datetime.now(timezone.utc)
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(TZ)
    return local.isoformat(timespec="seconds"), local.timestamp()


def link_from(item: ET.Element) -> str:
    text = child_text(item, {"link"})
    if text:
        return text
    for child in list(item):
        if tag_name(child.tag) == "link" and child.attrib.get("href"):
            return clean(child.attrib["href"])
    guid = child_text(item, {"guid", "id"})
    return guid if guid.startswith("http") else ""


def score(title: str, summary: str, cat: str) -> int:
    text = f"{title} {summary}".lower()
    base = sum(1 for term in POLITICS if term.lower() in text)
    if cat in {"快讯", "官方"}:
        base += 2
    return min(base, 12)


def fetch_source(source: dict[str, str]) -> dict[str, Any]:
    started = time.time()
    try:
        req = urllib.request.Request(source["feed"], headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
        raw = urllib.request.urlopen(req, timeout=12).read(2_000_000)
        root = ET.fromstring(raw)
        items = [x for x in root.iter() if tag_name(x.tag) in {"item", "entry"}]
        articles = []
        today = datetime.now(TZ).date()
        for index, item in enumerate(items[:60]):
            title = child_text(item, {"title"})
            if not title:
                continue
            summary = child_text(item, {"description", "summary", "content", "encoded"})[:360]
            url = link_from(item) or source["url"]
            published, ts = parse_date(child_text(item, {"pubdate", "published", "updated", "date"}))
            article_id = hashlib.sha1((url or f"{source['id']}:{title}").encode("utf-8")).hexdigest()
            articles.append({
                "id": article_id,
                "title": title,
                "summary": summary,
                "url": url,
                "source": source["name"],
                "source_id": source["id"],
                "source_url": source["url"],
                "source_icon": "https://www.google.com/s2/favicons?sz=64&domain_url=" + urllib.parse.quote(source["url"], safe=""),
                "category": source["cat"],
                "region": source["region"],
                "published_at": published,
                "published_ts": ts - index * 0.01,
                "is_today": datetime.fromtimestamp(ts, TZ).date() == today,
                "politics_score": score(title, summary, source["cat"]),
                "tags": [source["cat"]]
            })
        return {"ok": True, "source": source["name"], "count": len(articles), "elapsed_ms": round((time.time() - started) * 1000), "articles": articles}
    except Exception as exc:
        return {"ok": False, "source": source["name"], "count": 0, "elapsed_ms": round((time.time() - started) * 1000), "error": f"{type(exc).__name__}: {exc}", "articles": []}


def news_payload(force: bool = False) -> dict[str, Any]:
    now = time.time()
    with LOCK:
        if not force and NEWS_CACHE["payload"] and NEWS_CACHE["expires"] > now:
            return NEWS_CACHE["payload"]
    articles, status = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(fetch_source, SOURCES):
            status.append({k: v for k, v in result.items() if k != "articles"})
            articles.extend(result.get("articles", []))
    seen, deduped = set(), []
    for item in sorted(articles, key=lambda x: (x["is_today"], x["politics_score"], x["published_ts"]), reverse=True):
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        deduped.append(item)
    payload = {
        "generated_at": local_now(),
        "article_count": len(deduped),
        "today_count": sum(1 for x in deduped if x["is_today"]),
        "source_count": len(SOURCES),
        "ok_source_count": sum(1 for x in status if x["ok"]),
        "failed_source_count": sum(1 for x in status if not x["ok"]),
        "status": status,
        "articles": deduped
    }
    with LOCK:
        NEWS_CACHE.update({"expires": time.time() + CACHE_TTL, "payload": payload})
    return payload


def session_from_cookie(header: str | None) -> tuple[str, dict[str, Any]]:
    cookie = http.cookies.SimpleCookie(header or "")
    token = cookie.get(COOKIE).value if cookie.get(COOKIE) else ""
    if token and token in SESSIONS:
        return token, SESSIONS[token]
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {"api_key": "", "model": "deepseek-v4-flash", "created": time.time()}
    return token, SESSIONS[token]


def deepseek(session: dict[str, Any]) -> dict[str, str]:
    return {
        "api_key": (session.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")).strip(),
        "model": (session.get("model") or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")).strip(),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    }


def call_deepseek(session: dict[str, Any], messages: list[dict[str, str]], max_tokens: int = 3000) -> dict[str, Any]:
    cfg = deepseek(session)
    if not cfg["api_key"]:
        return {"enabled": False, "error": "请先在左侧输入自己的 DeepSeek API Key。"}
    body = {"model": cfg["model"], "messages": messages, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "max_tokens": max_tokens}
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    raw = urllib.request.urlopen(req, timeout=75).read(2_000_000).decode("utf-8", "replace")
    content = json.loads(raw)["choices"][0]["message"]["content"]
    return json.loads(content)


def translate_payload(session: dict[str, Any], limit: int = 60) -> dict[str, Any]:
    news = news_payload(False)
    selected = news["articles"][:limit]
    key = hashlib.sha1(json.dumps([x["id"] for x in selected], ensure_ascii=False).encode("utf-8") + deepseek(session)["api_key"].encode()).hexdigest()
    with LOCK:
        if TRANSLATION_CACHE["payload"] and TRANSLATION_CACHE["key"] == key and TRANSLATION_CACHE["expires"] > time.time():
            return TRANSLATION_CACHE["payload"]
    items = [{"id": x["id"], "title": x["title"], "summary": x["summary"], "source": x["source"]} for x in selected]
    messages = [
        {"role": "system", "content": "你是严谨的新闻中文翻译编辑。只翻译标题和摘要为简体中文，不添加事实。输出 JSON。"},
        {"role": "user", "content": json.dumps({"output": {"translations": [{"id": "原id", "title_zh": "中文标题", "summary_zh": "中文摘要"}]}, "articles": items}, ensure_ascii=False)}
    ]
    try:
        data = call_deepseek(session, messages, 5000)
        translations = {x["id"]: {"title_zh": clean(x.get("title_zh")), "summary_zh": clean(x.get("summary_zh"))} for x in data.get("translations", []) if x.get("id")}
        payload = {"enabled": True, "ok": True, "translations": translations, "article_count": len(translations), "generated_at": local_now()}
    except Exception as exc:
        payload = {"enabled": bool(deepseek(session)["api_key"]), "ok": False, "translations": {}, "error": f"{type(exc).__name__}: {exc}"}
    with LOCK:
        TRANSLATION_CACHE.update({"expires": time.time() + CACHE_TTL, "payload": payload, "key": key})
    return payload


def briefing_payload(session: dict[str, Any]) -> dict[str, Any]:
    news = news_payload(False)
    items = [{"title": x["title"], "summary": x["summary"][:220], "source": x["source"], "published_at": x["published_at"], "url": x["url"]} for x in news["articles"][:35]]
    messages = [
        {"role": "system", "content": "你是国际时政新闻编辑。只基于给定新闻生成中文简报，不编造事实。输出 JSON。"},
        {"role": "user", "content": json.dumps({"output": {"headline": "一句话总览", "overview": ["观察"], "top_stories": [{"title": "事件", "why_it_matters": "重要性"}], "watchlist": ["继续关注"]}, "articles": items}, ensure_ascii=False)}
    ]
    try:
        data = call_deepseek(session, messages, 2500)
        return {"enabled": True, "ok": True, "briefing": data, "generated_at": local_now()}
    except Exception as exc:
        return {"enabled": bool(deepseek(session)["api_key"]), "ok": False, "error": f"{type(exc).__name__}: {exc}"}


HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>全球时政新闻实时系统</title><style>
:root{--bg:#f6f7f9;--surface:#fff;--line:#d9dee5;--ink:#17202c;--muted:#667085;--accent:#d94b64;--green:#11845b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}.top{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:auto minmax(260px,1fr) auto;gap:16px;align-items:center;background:#fff;border-bottom:1px solid var(--line);padding:16px 28px}.brand{display:flex;gap:12px;align-items:center}.mark{display:grid;place-items:center;width:44px;height:44px;border-radius:8px;background:#17202c;color:#fff;font-weight:900;font-size:24px}h1,h2,p{margin:0}h1{font-size:26px}.brand p,.meta{color:var(--muted);font-size:13px}.broadcast{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 10px;align-items:center;border:1px solid var(--line);border-left:5px solid var(--accent);border-radius:8px;padding:9px 12px;background:#fff}.broadcast b{grid-row:1/3;color:var(--accent);font-size:12px}.broadcast a{font-weight:800;font-size:17px;text-decoration:none;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.refresh{height:42px;width:42px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-size:22px}.search{padding:14px 28px;background:#fff;border-bottom:1px solid var(--line)}.search input{width:100%;height:48px;border:2px solid #303744;border-radius:999px;padding:0 20px;font-size:17px}.layout{display:grid;grid-template-columns:280px minmax(0,1fr) 340px;min-height:calc(100vh - 145px)}aside,.right{padding:18px 14px;border-right:1px solid var(--line);display:grid;gap:14px;align-content:start}.right{border-right:0;border-left:1px solid var(--line);background:#fff}.card,.article,.source{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px}.head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:10px}.key,.select{width:100%;height:38px;border:1px solid var(--line);border-radius:8px;padding:0 10px}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.btn{border:1px solid var(--line);border-radius:8px;background:#fff;min-height:36px;padding:0 12px;cursor:pointer}.btn.primary,.btn.active{background:#17202c;color:#fff}.hot{display:grid;gap:8px}.hot a{display:grid;grid-template-columns:24px 1fr;gap:4px 8px;text-decoration:none;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:9px}.hot span{display:grid;place-items:center;background:#fff0f2;color:var(--accent);border-radius:6px;font-weight:900}.hot small{grid-column:2;color:var(--muted)}.cat button{width:100%;display:flex;justify-content:space-between;margin:4px 0}.main{padding:22px 24px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}.stat{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px}.stat strong{display:block;font-size:28px;margin-top:4px}.feedHead{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:14px}.actions{display:flex;gap:8px}.list{display:grid;gap:12px}.article{display:grid;gap:10px}.ameta{display:flex;align-items:center;gap:10px}.icon{width:34px;height:34px;border-radius:8px}.grow{min-width:0;flex:1}.grow strong,.grow span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.score{border:1px solid #bfd7fb;background:#eef6ff;color:#2f72d6;border-radius:999px;padding:5px 8px;font-size:12px;font-weight:800}.title{font-size:22px;font-weight:900;line-height:1.35;text-decoration:none;color:var(--ink);overflow-wrap:anywhere}.summary{line-height:1.65;color:#3f4a5a}.original{background:#eef2f5;border-radius:8px;padding:8px 10px;color:var(--muted);font-size:12px}.tag{display:inline-block;background:#eef2f5;border:1px solid var(--line);border-radius:999px;padding:4px 8px;margin-right:6px;font-size:12px;color:var(--muted)}.source{display:grid;grid-template-columns:42px 1fr;gap:10px}.source img{width:42px;height:42px;border-radius:8px}.source p{font-size:12px;color:var(--muted);line-height:1.5}.link{color:#a91935;font-weight:800;text-decoration:none;font-size:13px}.hidden{display:none}@media(max-width:980px){.top{grid-template-columns:1fr}.layout{display:block}.right{border-left:0;border-top:1px solid var(--line)}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:540px){.main{padding:16px}.feedHead,.actions{display:grid}.stats{grid-template-columns:1fr}.title{font-size:19px}}</style></head><body>
<header class="top"><div class="brand"><div class="mark">G</div><div><h1>全球时政新闻实时系统</h1><p>公共访问 · 用户自带 DeepSeek Key</p></div></div><section class="broadcast"><b>最新最热</b><a id="broadcastTitle" href="#">等待刷新</a><span id="broadcastMeta" class="meta">实时播报</span></section><button id="refresh" class="refresh">↻</button></header>
<section class="search"><input id="q" placeholder="搜索国家、机构、议题或媒体"></section><main class="layout"><aside><section class="card"><div class="head"><h2>AI Key</h2><span id="aiStatus" class="meta">未启用</span></div><input id="apiKey" class="key" type="password" placeholder="输入你自己的 DeepSeek API Key"><div class="row"><button id="saveKey" class="btn primary">保存</button><button id="clearKey" class="btn">清除</button></div><select id="model" class="select"><option>deepseek-v4-flash</option><option>deepseek-v4-pro</option></select><p class="meta">Key 只保存在你的临时会话里，不写入文件。</p></section><section class="card"><div class="head"><h2>AI 简报</h2><button id="briefBtn" class="btn primary">生成</button></div><div id="brief">保存 Key 后生成中文简报。</div></section><section class="card"><div class="head"><h2>热点推送</h2><span id="hotCount" class="meta">0条</span></div><div id="hot" class="hot"></div></section><section class="card cat"><div class="head"><h2>分类</h2><b id="total">0</b></div><button class="btn active" data-cat="all">全部 <span></span></button><div id="cats"></div></section></aside><section class="main"><div class="stats"><div class="stat"><span class="meta">今日新闻</span><strong id="today">0</strong></div><div class="stat"><span class="meta">实时来源</span><strong id="sources">0</strong></div><div class="stat"><span class="meta">正常抓取</span><strong id="ok">0</strong></div><div class="stat"><span class="meta">暂不可用</span><strong id="fail">0</strong></div></div><div class="feedHead"><div><h2>中文时政新闻流</h2><p id="feedMeta" class="meta">正在加载</p></div><div class="actions"><button id="hotSort" class="btn active">热点优先</button><button id="timeSort" class="btn">时间优先</button><button id="statusBtn" class="btn">来源状态</button></div></div><div id="status" class="card hidden"></div><div id="articles" class="list"></div></section><aside class="right"><div class="head"><h2>网站目录</h2><span id="dirCount" class="meta">0来源</span></div><div id="directory" class="list"></div></aside></main>
<script>
const S={articles:[],sources:[],status:[],translations:{},cat:'all',sort:'hot',q:'',idx:0};const E=id=>document.getElementById(id);const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');const fmt=s=>new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(s));function show(a){const t=S.translations[a.id]||{};return{title:t.title_zh||a.title,summary:t.summary_zh||a.summary||'',translated:!!(t.title_zh||t.summary_zh)}}function hot(a){const h=Math.max(0,(Date.now()-new Date(a.published_at))/36e5);return Math.round((a.politics_score||0)*14+(a.is_today?20:0)+Math.max(0,30-h*1.4))}function sorted(list){return [...list].sort((a,b)=>S.sort==='time'?(b.published_ts-a.published_ts):(hot(b)-hot(a)||b.published_ts-a.published_ts))}function filtered(){const q=S.q.toLowerCase();return sorted(S.articles.filter(a=>(S.cat==='all'||a.category===S.cat)&&(!q||[show(a).title,show(a).summary,a.title,a.summary,a.source,a.region,a.category].join(' ').toLowerCase().includes(q))))}function render(){const list=filtered();E('feedMeta').textContent=`${S.cat==='all'?'全部来源':S.cat} · ${list.length}条匹配 · ${Object.keys(S.translations).length?'已翻译'+Object.keys(S.translations).length+'条':'未启用翻译'}`;E('articles').innerHTML=list.slice(0,180).map(a=>{const d=show(a);return `<article class="article"><div class="ameta"><img class="icon" src="${a.source_icon}"><div class="grow"><strong>${esc(a.source)}</strong><span class="meta">${esc(a.region)} · ${fmt(a.published_at)}</span></div><span class="score">热度 ${hot(a)}</span></div><a class="title" href="${esc(a.url)}" target="_blank">${esc(d.title)}</a>${d.summary?`<p class="summary">${esc(d.summary)}</p>`:''}${d.translated&&d.title!==a.title?`<p class="original">原文：${esc(a.title)}</p>`:''}<div>${(a.tags||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}</div><a class="link" href="${esc(a.url)}" target="_blank">打开原文</a></article>`}).join('');const cats={};S.articles.forEach(a=>cats[a.category]=(cats[a.category]||0)+1);E('total').textContent=S.articles.length;E('cats').innerHTML=Object.entries(cats).map(([k,v])=>`<button class="btn ${S.cat===k?'active':''}" data-cat="${esc(k)}">${esc(k)} <span>${v}</span></button>`).join('');document.querySelectorAll('[data-cat]').forEach(b=>b.onclick=()=>{S.cat=b.dataset.cat;render()});const hs=sorted(S.articles.filter(a=>a.is_today)).slice(0,8);E('hotCount').textContent=hs.length+'条';E('hot').innerHTML=hs.slice(0,6).map((a,i)=>`<a href="${esc(a.url)}" target="_blank"><span>${i+1}</span><b>${esc(show(a).title)}</b><small>${esc(a.source)} · 热度${hot(a)}</small></a>`).join('');if(hs.length){const a=hs[S.idx%hs.length];E('broadcastTitle').textContent=show(a).title;E('broadcastTitle').href=a.url;E('broadcastMeta').textContent=`${a.source} · ${fmt(a.published_at)} · 热度${hot(a)}`}}function renderDir(){E('dirCount').textContent=S.sources.length+'来源';E('directory').innerHTML=S.sources.map(s=>`<div class="source"><img src="${s.source_icon||('https://www.google.com/s2/favicons?sz=64&domain_url='+encodeURIComponent(s.url))}"><div><b>${esc(s.name)}</b><p>${esc(s.region)} · ${esc(s.cat)}</p><a class="link" href="${esc(s.url)}" target="_blank">${new URL(s.url).hostname.replace('www.','')}</a></div></div>`).join('')}async function loadNews(force=false){E('refresh').disabled=true;let r=await fetch('/api/news?limit=300'+(force?'&refresh=1':''));let d=await r.json();S.articles=d.articles;S.status=d.status;E('today').textContent=d.today_count;E('sources').textContent=d.source_count;E('ok').textContent=d.ok_source_count;E('fail').textContent=d.failed_source_count;render();E('refresh').disabled=false}async function health(){let d=await (await fetch('/api/health')).json();E('aiStatus').textContent=d.deepseek_enabled?'已启用':'未启用';if(d.deepseek_model)E('model').value=d.deepseek_model}async function saveKey(k){let d=await (await fetch('/api/deepseek-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:k,model:E('model').value})})).json();E('aiStatus').textContent=d.deepseek_enabled?'已启用':'未启用';if(d.deepseek_enabled){E('apiKey').value='';await translate(true)}else{S.translations={};render()}}async function translate(force=false){let d=await (await fetch('/api/translations?limit=80'+(force?'&refresh=1':''))).json();if(d.translations)S.translations=d.translations;render()}async function brief(){E('brief').textContent='生成中...';let d=await (await fetch('/api/briefing?refresh=1')).json();if(!d.ok){E('brief').textContent=d.error||'请先保存 Key';return}let b=d.briefing||{};E('brief').innerHTML=`<h3>${esc(b.headline||'今日简报')}</h3>${(b.overview||[]).map(x=>`<p>${esc(x)}</p>`).join('')}${(b.watchlist||[]).map(x=>`<p class="meta">继续关注：${esc(x)}</p>`).join('')}`}E('refresh').onclick=()=>loadNews(true);E('saveKey').onclick=()=>saveKey(E('apiKey').value);E('clearKey').onclick=()=>saveKey('');E('briefBtn').onclick=brief;E('hotSort').onclick=()=>{S.sort='hot';E('hotSort').classList.add('active');E('timeSort').classList.remove('active');render()};E('timeSort').onclick=()=>{S.sort='time';E('timeSort').classList.add('active');E('hotSort').classList.remove('active');render()};E('q').oninput=e=>{S.q=e.target.value;render()};E('statusBtn').onclick=()=>{E('status').classList.toggle('hidden');E('status').innerHTML=S.status.map(s=>`<p>${s.ok?'✓':'!'} ${esc(s.source)} ${s.count||0}条 ${s.error?esc(s.error):''}</p>`).join('')};setInterval(()=>{S.idx++;render()},6500);(async()=>{S.sources=await (await fetch('/api/sources')).json();renderDir();await health();await loadNews(false);})();</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload: Any, status: int = 200, cookie: str | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        token, session = session_from_cookie(self.headers.get("Cookie"))
        cookie = f"{COOKIE}={token}; Path=/; Max-Age=43200; SameSite=Lax; HttpOnly"
        if parsed.path == "/":
            raw = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/sources":
            self.send_json(SOURCES, cookie=cookie)
            return
        if parsed.path == "/api/news":
            q = urllib.parse.parse_qs(parsed.query)
            payload = dict(news_payload(q.get("refresh", ["0"])[0] in {"1", "true"}))
            limit = int(q.get("limit", ["300"])[0])
            payload["articles"] = payload["articles"][:limit]
            self.send_json(payload, cookie=cookie)
            return
        if parsed.path == "/api/translations":
            q = urllib.parse.parse_qs(parsed.query)
            self.send_json(translate_payload(session, int(q.get("limit", ["60"])[0])), cookie=cookie)
            return
        if parsed.path == "/api/briefing":
            self.send_json(briefing_payload(session), cookie=cookie)
            return
        if parsed.path == "/api/health":
            cfg = deepseek(session)
            self.send_json({"ok": True, "role": "user", "deepseek_enabled": bool(cfg["api_key"]), "deepseek_model": cfg["model"]}, cookie=cookie)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        token, session = session_from_cookie(self.headers.get("Cookie"))
        cookie = f"{COOKIE}={token}; Path=/; Max-Age=43200; SameSite=Lax; HttpOnly"
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
        except Exception:
            payload = {}
        if urllib.parse.urlsplit(self.path).path == "/api/deepseek-key":
            session["api_key"] = str(payload.get("api_key", "")).strip()
            session["model"] = str(payload.get("model", "deepseek-v4-flash")).strip() or "deepseek-v4-flash"
            self.send_json({"ok": True, "deepseek_enabled": bool(session["api_key"]), "deepseek_model": session["model"]}, cookie=cookie)
            return
        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    args = parser.parse_args()
    print(f"Global politics news running on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
