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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Global Chinese News Fast Translate)"
CACHE_TTL = 15 * 60
SESSION_TTL = int(float(os.environ.get("NEWS_SESSION_TTL_HOURS", "12")) * 3600)
NEWS_CACHE: dict[str, Any] = {"expires": 0.0, "payload": None}
TRANSLATION_CACHE: dict[str, Any] = {"expires": 0.0, "payload": None, "key": ""}
BRIEFING_CACHE: dict[str, Any] = {"expires": 0.0, "payload": None, "key": ""}
SESSIONS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
COOKIE = "news_session"


def google_news(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )


KEYWORD_ALIASES = {
    "台海": "Taiwan Strait Taiwan China military security",
    "台海局势": "Taiwan Strait Taiwan China military drills security",
    "台湾": "Taiwan Strait Taiwan China politics security",
    "中美": "US China China United States Trump tariff trade diplomacy",
    "中美关系": "US China China United States diplomacy tariff Taiwan",
    "特朗普": "Donald Trump Trump White House United States",
    "俄乌": "Ukraine Russia Putin Zelensky war ceasefire",
    "中东": "Middle East Israel Gaza Iran ceasefire",
}


def custom_keyword_sources(raw: str) -> list[dict[str, Any]]:
    parts = [x.strip() for x in re.split(r"[,，、;；\n]+", raw or "") if x.strip()]
    sources: list[dict[str, Any]] = []
    for keyword in list(dict.fromkeys(parts))[:5]:
        query = KEYWORD_ALIASES.get(keyword, keyword)
        source_id = hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:10]
        sources.append(
            {
                "id": f"keyword-{source_id}",
                "name": f"关键词：{keyword}",
                "authority": "自定义头条",
                "region": "精准补抓",
                "url": "https://news.google.com/search?" + urllib.parse.urlencode({"q": keyword}),
                "feed": google_news(f"{query} politics diplomacy security when:2d"),
                "weight": 13,
                "keyword": keyword,
            }
        )
    return sources


SOURCES = [
    {
        "id": "reuters",
        "name": "Reuters 路透社",
        "authority": "通讯社",
        "region": "全球",
        "url": "https://www.reuters.com/world/",
        "feed": google_news("site:reuters.com/world politics OR diplomacy OR war when:2d"),
        "weight": 12,
    },
    {
        "id": "ap",
        "name": "AP 美联社",
        "authority": "通讯社",
        "region": "美国/全球",
        "url": "https://apnews.com/hub/world-news",
        "feed": google_news("site:apnews.com politics OR election OR world when:2d"),
        "weight": 12,
    },
    {
        "id": "afp",
        "name": "AFP 法新社",
        "authority": "通讯社",
        "region": "法国/全球",
        "url": "https://www.afp.com/en",
        "feed": google_news("site:afp.com international politics OR diplomacy OR conflict when:2d"),
        "weight": 11,
    },
    {
        "id": "bbc-world",
        "name": "BBC World",
        "authority": "国际媒体",
        "region": "英国/全球",
        "url": "https://www.bbc.com/news/world",
        "feed": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "weight": 10,
    },
    {
        "id": "dw-world",
        "name": "DW 德国之声",
        "authority": "国际媒体",
        "region": "德国/欧洲",
        "url": "https://www.dw.com/en",
        "feed": "https://rss.dw.com/rdf/rss-en-world",
        "weight": 9,
    },
    {
        "id": "france24",
        "name": "France 24",
        "authority": "国际媒体",
        "region": "法国/全球",
        "url": "https://www.france24.com/en/",
        "feed": "https://www.france24.com/en/rss",
        "weight": 9,
    },
    {
        "id": "nhk",
        "name": "NHK World-Japan",
        "authority": "公共媒体",
        "region": "日本/亚太",
        "url": "https://www3.nhk.or.jp/nhkworld/en/news/",
        "feed": google_news("site:www3.nhk.or.jp/nhkworld/en/news politics OR asia OR security when:2d"),
        "weight": 9,
    },
    {
        "id": "aljazeera",
        "name": "Al Jazeera",
        "authority": "国际媒体",
        "region": "中东/全球",
        "url": "https://www.aljazeera.com",
        "feed": "https://www.aljazeera.com/xml/rss/all.xml",
        "weight": 9,
    },
    {
        "id": "guardian-world",
        "name": "The Guardian World",
        "authority": "国际媒体",
        "region": "英国/全球",
        "url": "https://www.theguardian.com/world",
        "feed": "https://www.theguardian.com/world/rss",
        "weight": 8,
    },
    {
        "id": "nyt-world",
        "name": "New York Times World",
        "authority": "国际媒体",
        "region": "美国/全球",
        "url": "https://www.nytimes.com/section/world",
        "feed": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "weight": 8,
    },
    {
        "id": "wsj-world",
        "name": "Wall Street Journal World",
        "authority": "财经媒体",
        "region": "美国/全球",
        "url": "https://www.wsj.com/world",
        "feed": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "weight": 8,
    },
    {
        "id": "nikkei",
        "name": "Nikkei Asia",
        "authority": "区域媒体",
        "region": "日本/亚洲",
        "url": "https://asia.nikkei.com",
        "feed": "https://asia.nikkei.com/rss/feed/nar",
        "weight": 8,
    },
    {
        "id": "scmp-asia",
        "name": "SCMP Asia",
        "authority": "区域媒体",
        "region": "香港/亚洲",
        "url": "https://www.scmp.com/news/asia",
        "feed": "https://www.scmp.com/rss/91/feed",
        "weight": 7,
    },
    {
        "id": "scmp-china",
        "name": "SCMP China",
        "authority": "区域媒体",
        "region": "香港/中国",
        "url": "https://www.scmp.com/news/china",
        "feed": "https://www.scmp.com/rss/4/feed",
        "weight": 7,
    },
    {
        "id": "cna-world",
        "name": "CNA 亚洲新闻台",
        "authority": "区域媒体",
        "region": "新加坡/东盟",
        "url": "https://www.channelnewsasia.com/world",
        "feed": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6311",
        "weight": 7,
    },
    {
        "id": "the-hindu",
        "name": "The Hindu International",
        "authority": "区域媒体",
        "region": "印度/南亚",
        "url": "https://www.thehindu.com/news/international/",
        "feed": "https://www.thehindu.com/news/international/feeder/default.rss",
        "weight": 7,
    },
    {
        "id": "abc-au",
        "name": "ABC Australia",
        "authority": "公共媒体",
        "region": "澳大利亚/南太",
        "url": "https://www.abc.net.au/news/world",
        "feed": "https://www.abc.net.au/news/feed/51120/rss.xml",
        "weight": 7,
    },
    {
        "id": "voa-cn",
        "name": "美国之音中文",
        "authority": "中文涉华",
        "region": "美国/中文",
        "url": "https://www.voachinese.com",
        "feed": "https://www.voachinese.com/api/zm_yql-vomx-tpeybti",
        "weight": 6,
    },
    {
        "id": "xinhua",
        "name": "Xinhua 新华社英文",
        "authority": "通讯社",
        "region": "中国/全球",
        "url": "https://english.news.cn/world/",
        "feed": "https://www.xinhuanet.com/english/rss/worldrss.xml",
        "weight": 8,
    },
    {
        "id": "tass",
        "name": "TASS 塔斯社",
        "authority": "通讯社",
        "region": "俄罗斯/全球",
        "url": "https://tass.com",
        "feed": "https://tass.com/rss/v2.xml",
        "weight": 8,
    },
    {
        "id": "un",
        "name": "UN News 联合国新闻",
        "authority": "官方机构",
        "region": "联合国/全球",
        "url": "https://news.un.org/en/",
        "feed": "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        "weight": 8,
    },
    {
        "id": "google-world",
        "name": "Google News 聚合",
        "authority": "聚合",
        "region": "全球",
        "url": "https://news.google.com",
        "feed": google_news("world politics diplomacy election conflict economy security when:1d"),
        "weight": 5,
    },
]

NAV_GROUPS = [
    {
        "title": "通讯社与国际媒体",
        "links": [
            {"name": "Reuters 路透社", "desc": "全球时政快讯", "url": "https://www.reuters.com/world/"},
            {"name": "AP 美联社", "desc": "国际突发与美国政治", "url": "https://apnews.com/hub/world-news"},
            {"name": "AFP 法新社", "desc": "欧洲、中东、非洲快讯", "url": "https://www.afp.com/en"},
            {"name": "BBC World", "desc": "英国视角世界新闻", "url": "https://www.bbc.com/news/world"},
            {"name": "The New York Times", "desc": "美国深度国际报道", "url": "https://www.nytimes.com/section/world"},
            {"name": "The Wall Street Journal", "desc": "财经、地缘政治", "url": "https://www.wsj.com/world"},
            {"name": "Financial Times", "desc": "全球经济与政策", "url": "https://www.ft.com/world"},
            {"name": "Bloomberg", "desc": "金融、能源、制裁", "url": "https://www.bloomberg.com"},
            {"name": "The Guardian", "desc": "欧美公共议题", "url": "https://www.theguardian.com/world"},
            {"name": "Politico", "desc": "美国政策与国会", "url": "https://www.politico.com"},
        ],
    },
    {
        "title": "新闻电视台",
        "links": [
            {"name": "CNN International", "desc": "美国电视新闻", "url": "https://edition.cnn.com/world"},
            {"name": "BBC News", "desc": "英国公共媒体", "url": "https://www.bbc.com/news"},
            {"name": "France 24", "desc": "法国国际电视台", "url": "https://www.france24.com/en/"},
            {"name": "DW 德国之声", "desc": "德国国际媒体", "url": "https://www.dw.com/en"},
            {"name": "NHK World-Japan", "desc": "日本公共媒体", "url": "https://www3.nhk.or.jp/nhkworld/en/news/"},
            {"name": "Al Jazeera", "desc": "中东与全球新闻", "url": "https://www.aljazeera.com"},
            {"name": "CNA 亚洲新闻台", "desc": "新加坡、东盟", "url": "https://www.channelnewsasia.com/world"},
            {"name": "ABC Australia", "desc": "澳大利亚与南太", "url": "https://www.abc.net.au/news/world"},
            {"name": "CBC News", "desc": "加拿大新闻", "url": "https://www.cbc.ca/news/world"},
            {"name": "Sky News", "desc": "英国电视新闻", "url": "https://news.sky.com/world"},
        ],
    },
    {
        "title": "各国外交与国际组织发布",
        "links": [
            {"name": "中国外交部", "desc": "例行记者会与声明", "url": "https://www.mfa.gov.cn"},
            {"name": "美国国务院", "desc": "外交声明与简报", "url": "https://www.state.gov"},
            {"name": "白宫", "desc": "总统声明与政策", "url": "https://www.whitehouse.gov"},
            {"name": "联合国新闻", "desc": "联合国官方新闻", "url": "https://news.un.org/en/"},
            {"name": "欧盟 EEAS", "desc": "欧盟外交事务", "url": "https://www.eeas.europa.eu"},
            {"name": "北约 NATO", "desc": "安全与军事声明", "url": "https://www.nato.int"},
            {"name": "英国外交部", "desc": "FCDO 新闻", "url": "https://www.gov.uk/government/organisations/foreign-commonwealth-development-office"},
            {"name": "法国外交部", "desc": "法国外交发布", "url": "https://www.diplomatie.gouv.fr/en/"},
            {"name": "德国外交部", "desc": "德国外交发布", "url": "https://www.auswaertiges-amt.de/en"},
            {"name": "日本外务省", "desc": "日本外交发布", "url": "https://www.mofa.go.jp"},
            {"name": "俄罗斯外交部", "desc": "俄罗斯外交发布", "url": "https://mid.ru/en/"},
            {"name": "印度外交部", "desc": "南亚外交发布", "url": "https://www.mea.gov.in"},
        ],
    },
    {
        "title": "中国官方与国内新闻发布",
        "links": [
            {"name": "新华网", "desc": "官方新闻与时政", "url": "https://www.news.cn"},
            {"name": "人民网", "desc": "党政新闻与评论", "url": "http://www.people.com.cn"},
            {"name": "央视新闻", "desc": "视频与国内外要闻", "url": "https://news.cctv.com"},
            {"name": "中国政府网", "desc": "国务院政策发布", "url": "https://www.gov.cn"},
            {"name": "国新办", "desc": "新闻发布会", "url": "http://www.scio.gov.cn"},
            {"name": "国防部", "desc": "军事与国防发布", "url": "http://www.mod.gov.cn"},
            {"name": "国台办", "desc": "两岸政策发布", "url": "http://www.gwytb.gov.cn"},
            {"name": "商务部", "desc": "贸易、关税、制裁", "url": "http://www.mofcom.gov.cn"},
            {"name": "国家发改委", "desc": "宏观经济政策", "url": "https://www.ndrc.gov.cn"},
            {"name": "海关总署", "desc": "贸易进出口数据", "url": "http://www.customs.gov.cn"},
            {"name": "新华社英文", "desc": "中国英文国际新闻", "url": "https://english.news.cn"},
            {"name": "CGTN", "desc": "中国英文国际传播", "url": "https://www.cgtn.com"},
        ],
    },
    {
        "title": "亚太、中东与区域媒体",
        "links": [
            {"name": "Nikkei Asia", "desc": "日本、东盟、供应链", "url": "https://asia.nikkei.com"},
            {"name": "South China Morning Post", "desc": "香港、中国周边", "url": "https://www.scmp.com"},
            {"name": "The Straits Times", "desc": "新加坡与东盟", "url": "https://www.straitstimes.com"},
            {"name": "The Hindu", "desc": "印度与南亚", "url": "https://www.thehindu.com"},
            {"name": "Yonhap 韩联社", "desc": "朝鲜半岛", "url": "https://en.yna.co.kr"},
            {"name": "Kyodo 共同社", "desc": "日本政治外交", "url": "https://english.kyodonews.net"},
            {"name": "The National", "desc": "海湾与中东", "url": "https://www.thenationalnews.com"},
            {"name": "Jerusalem Post", "desc": "以色列与安全", "url": "https://www.jpost.com"},
            {"name": "Haaretz", "desc": "以色列政治", "url": "https://www.haaretz.com"},
            {"name": "TASS 塔斯社", "desc": "俄罗斯官方叙事", "url": "https://tass.com"},
        ],
    },
    {
        "title": "聚合、数据库与研究工具",
        "links": [
            {"name": "Google News", "desc": "关键词聚合", "url": "https://news.google.com"},
            {"name": "GDELT", "desc": "全球新闻近实时数据库", "url": "https://www.gdeltproject.org"},
            {"name": "Media Cloud", "desc": "媒体来源研究", "url": "https://www.mediacloud.org"},
            {"name": "Feedly", "desc": "RSS 订阅", "url": "https://feedly.com"},
            {"name": "Inoreader", "desc": "RSS 与关键词监控", "url": "https://www.inoreader.com"},
        ],
    },
]

TOPICS = [
    {"id": "headlines", "name": "今日全球头条", "terms": []},
    {"id": "latest", "name": "最新快讯", "terms": ["breaking", "live", "latest", "urgent", "update", "快讯", "突发"]},
    {"id": "us_china", "name": "中美关系", "terms": ["china", "chinese", "beijing", "taiwan", "trump", "xi", "tariff", "trade", "united states", "u.s.", "us "]},
    {"id": "ukraine", "name": "俄乌冲突", "terms": ["ukraine", "russia", "russian", "putin", "zelensky", "zelenskyy", "kyiv", "moscow", "nato"]},
    {"id": "middle_east", "name": "中东局势", "terms": ["israel", "gaza", "hamas", "iran", "syria", "lebanon", "hezbollah", "palestinian", "qatar", "saudi"]},
    {"id": "asia_pacific", "name": "亚太动态", "terms": ["japan", "korea", "india", "pakistan", "asean", "philippines", "australia", "south china sea", "pacific"]},
    {"id": "economy", "name": "全球经济", "terms": ["economy", "trade", "tariff", "market", "oil", "energy", "sanction", "inflation", "rate", "chip", "supply chain"]},
    {"id": "security", "name": "军事安全", "terms": ["military", "defense", "security", "missile", "army", "navy", "air force", "war", "drone", "nuclear"]},
    {"id": "confirmed", "name": "多源确认新闻", "terms": []},
]

POLITICS_TERMS = """
politics president prime minister minister parliament congress senate election vote government diplomacy diplomatic
foreign policy sanction sanctions war ceasefire conflict military defense security nato united nations white house
state department china taiwan ukraine russia israel gaza iran europe asia economy tariff trade energy oil military
""".split()

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "from",
    "this",
    "will",
    "have",
    "after",
    "over",
    "into",
    "about",
    "says",
    "say",
    "new",
    "news",
    "world",
    "latest",
    "live",
    "update",
    "updates",
    "report",
    "reports",
}


def clean(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_cjk(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


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
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
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


def title_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9'-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return {t.strip("-'") for t in tokens if t not in STOPWORDS and len(t.strip("-'")) > 2}


def politics_score(title: str, summary: str, source: dict[str, Any]) -> int:
    text = f"{title} {summary}".lower()
    base = sum(1 for term in POLITICS_TERMS if term in text)
    if source["authority"] in {"通讯社", "官方机构"}:
        base += 2
    return min(base + int(source.get("weight", 5)) // 3, 16)


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def topic_matches(topic_id: str, text: str) -> bool:
    if topic_id == "us_china":
        china = has_any(text, ["china", "chinese", "beijing", "xi jinping", "taiwan", "taiwan strait", "中国", "台湾", "台海"])
        usa = has_any(text, ["united states", "u.s.", "us ", "america", "american", "white house", "washington", "trump", "biden", "tariff", "美国"])
        return (china and usa) or has_any(text, ["us-china", "u.s.-china", "china-us", "中美"])
    if topic_id == "ukraine":
        ukraine = has_any(text, ["ukraine", "ukrainian", "kyiv", "zelensky", "乌克兰"])
        russia = has_any(text, ["russia", "russian", "moscow", "putin", "俄罗斯", "普京"])
        return ukraine and russia
    if topic_id == "middle_east":
        places = has_any(text, ["israel", "gaza", "iran", "syria", "lebanon", "palestinian", "qatar", "saudi", "以色列", "加沙", "伊朗", "中东"])
        conflict = has_any(text, ["hamas", "hezbollah", "ceasefire", "war", "strike", "hostage", "missile", "diplomacy", "哈马斯", "停火"])
        return places and conflict
    if topic_id == "asia_pacific":
        region = has_any(text, ["japan", "korea", "india", "pakistan", "asean", "philippines", "australia", "south china sea", "taiwan", "pacific", "日本", "韩国", "印度", "东盟", "南海", "台海", "台湾"])
        policy = has_any(text, ["politics", "election", "military", "security", "trade", "diplomacy", "government", "war", "安全", "外交", "军事"])
        return region and policy
    return False


def topics_for(title: str, summary: str, source: dict[str, Any], is_today: bool) -> list[str]:
    text = f"{title} {summary}".lower()
    topics = ["headlines"]
    if is_today:
        topics.append("latest")
    for topic in TOPICS:
        if topic["id"] in {"headlines", "latest", "confirmed"}:
            continue
        if topic["id"] in {"us_china", "ukraine", "middle_east", "asia_pacific"}:
            if topic_matches(topic["id"], text):
                topics.append(topic["id"])
            continue
        if any(term in text for term in topic["terms"]):
            topics.append(topic["id"])
    return list(dict.fromkeys(topics))


def source_icon(url: str) -> str:
    return "https://www.google.com/s2/favicons?sz=64&domain_url=" + urllib.parse.quote(url, safe="")


def fetch_source(source: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    try:
        req = urllib.request.Request(
            source["feed"],
            headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )
        raw = urllib.request.urlopen(req, timeout=12).read(2_500_000)
        root = ET.fromstring(raw)
        items = [x for x in root.iter() if tag_name(x.tag) in {"item", "entry"}]
        articles = []
        today = datetime.now(TZ).date()
        for index, item in enumerate(items[:70]):
            title = child_text(item, {"title"})
            if not title:
                continue
            summary = child_text(item, {"description", "summary", "content", "encoded"})[:420]
            url = link_from(item) or source["url"]
            published, ts = parse_date(child_text(item, {"pubdate", "published", "updated", "date"}))
            is_today = datetime.fromtimestamp(ts, TZ).date() == today
            article_id = hashlib.sha1((url or f"{source['id']}:{title}").encode("utf-8")).hexdigest()
            topic_ids = topics_for(title, summary, source, is_today)
            articles.append(
                {
                    "id": article_id,
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "source": source["name"],
                    "source_id": source["id"],
                    "source_url": source["url"],
                    "source_icon": source_icon(source["url"]),
                    "authority": source["authority"],
                    "region": source["region"],
                    "published_at": published,
                    "published_ts": ts - index * 0.01,
                    "is_today": is_today,
                    "politics_score": politics_score(title, summary, source),
                    "topics": topic_ids,
                    "custom_keyword": source.get("keyword", ""),
                    "custom_match": 1 if source.get("keyword") else 0,
                    "tokens": list(title_tokens(title)),
                }
            )
        return {
            "ok": True,
            "source": source["name"],
            "count": len(articles),
            "elapsed_ms": round((time.time() - started) * 1000),
            "articles": articles,
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": source["name"],
            "count": 0,
            "elapsed_ms": round((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
            "articles": [],
        }


def add_confirmation(articles: list[dict[str, Any]]) -> None:
    clusters: list[dict[str, Any]] = []
    for article in articles:
        tokens = set(article.get("tokens") or [])
        best = None
        best_score = 0.0
        for cluster in clusters[-220:]:
            other = cluster["tokens"]
            if not tokens or not other:
                continue
            score = len(tokens & other) / max(1, min(len(tokens), len(other)))
            if score > best_score:
                best = cluster
                best_score = score
        if best is not None and best_score >= 0.55:
            best["items"].append(article)
            best["sources"].add(article["source_id"])
            best["tokens"] |= tokens
            article["cluster_id"] = best["id"]
        else:
            cluster = {"id": article["id"], "items": [article], "sources": {article["source_id"]}, "tokens": set(tokens)}
            clusters.append(cluster)
            article["cluster_id"] = cluster["id"]
    by_id = {cluster["id"]: cluster for cluster in clusters}
    for article in articles:
        cluster = by_id.get(article["cluster_id"])
        count = len(cluster["sources"]) if cluster else 1
        article["confirm_count"] = count
        article["confirmed"] = count > 1
        if count > 1 and "confirmed" not in article["topics"]:
            article["topics"].append("confirmed")
        article.pop("tokens", None)


def news_payload(force: bool = False, keywords: str = "") -> dict[str, Any]:
    now = time.time()
    keyword_sources = custom_keyword_sources(keywords)
    cache_key = hashlib.sha1((keywords or "").strip().encode("utf-8")).hexdigest()
    with LOCK:
        if not force and NEWS_CACHE["payload"] and NEWS_CACHE.get("key") == cache_key and NEWS_CACHE["expires"] > now:
            return NEWS_CACHE["payload"]
    articles, status = [], []
    fetch_sources = SOURCES + keyword_sources
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        for result in pool.map(fetch_source, fetch_sources):
            status.append({k: v for k, v in result.items() if k != "articles"})
            articles.extend(result.get("articles", []))
    seen, deduped = set(), []
    for item in sorted(articles, key=lambda x: (x.get("custom_match", 0), x["is_today"], x["politics_score"], x["published_ts"]), reverse=True):
        sig = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", item["title"].lower())[:120]
        key = item["url"] or sig
        if key in seen or sig in seen:
            continue
        seen.add(key)
        seen.add(sig)
        deduped.append(item)
    add_confirmation(deduped)
    deduped.sort(
        key=lambda x: (
            x.get("custom_match", 0),
            x["is_today"],
            x.get("confirm_count", 1),
            x["politics_score"],
            x["published_ts"],
        ),
        reverse=True,
    )
    payload = {
        "generated_at": local_now(),
        "article_count": len(deduped),
        "today_count": sum(1 for x in deduped if x["is_today"]),
        "source_count": len(fetch_sources),
        "ok_source_count": sum(1 for x in status if x["ok"]),
        "failed_source_count": sum(1 for x in status if not x["ok"]),
        "keywords": keywords,
        "topics": TOPICS,
        "status": status,
        "articles": deduped,
    }
    with LOCK:
        NEWS_CACHE.update({"expires": time.time() + CACHE_TTL, "payload": payload, "key": cache_key})
    return payload


def session_from_cookie(header: str | None) -> tuple[str, dict[str, Any]]:
    cookie = http.cookies.SimpleCookie(header or "")
    token = cookie.get(COOKIE).value if cookie.get(COOKIE) else ""
    now = time.time()
    with LOCK:
        expired = [k for k, v in SESSIONS.items() if v.get("expires", 0) < now]
        for key in expired:
            SESSIONS.pop(key, None)
        if token and token in SESSIONS:
            SESSIONS[token]["expires"] = now + SESSION_TTL
            return token, SESSIONS[token]
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = {
            "api_key": "",
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "created": now,
            "expires": now + SESSION_TTL,
        }
        return token, SESSIONS[token]


def deepseek(session: dict[str, Any]) -> dict[str, str]:
    return {
        "api_key": (session.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")).strip(),
        "model": (session.get("model") or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")).strip(),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
    }


def call_deepseek(session: dict[str, Any], messages: list[dict[str, str]], max_tokens: int = 3000) -> dict[str, Any]:
    cfg = deepseek(session)
    if not cfg["api_key"]:
        return {"enabled": False, "error": "请先在左侧保存你自己的 DeepSeek API Key。"}
    body = {
        "model": cfg["model"],
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    raw = urllib.request.urlopen(req, timeout=75).read(2_000_000).decode("utf-8", "replace")
    content = json.loads(raw)["choices"][0]["message"]["content"]
    return json.loads(content)


def translate_payload(session: dict[str, Any], limit: int = 90, force: bool = False, keywords: str = "") -> dict[str, Any]:
    cfg = deepseek(session)
    if not cfg["api_key"]:
        return {
            "enabled": False,
            "ok": False,
            "translations": {},
            "article_count": 0,
            "error": "请先在左侧保存你自己的 DeepSeek API Key。",
        }
    news = news_payload(False, keywords)
    selected = news["articles"][: max(1, min(limit, 240))]
    if not selected:
        return {
            "enabled": True,
            "ok": False,
            "translations": {},
            "article_count": 0,
            "error": "当前没有可翻译的新闻，请先刷新新闻。",
        }
    cache_key = hashlib.sha1(
        json.dumps([x["id"] for x in selected], ensure_ascii=False).encode("utf-8") + cfg["api_key"].encode("utf-8")
    ).hexdigest()
    with LOCK:
        if not force and TRANSLATION_CACHE["payload"] and TRANSLATION_CACHE["key"] == cache_key and TRANSLATION_CACHE["expires"] > time.time():
            return TRANSLATION_CACHE["payload"]
    items = [
        {
            "id": x["id"],
            "title": x["title"],
            "summary": x["summary"],
            "source": x["source"],
            "region": x["region"],
        }
        for x in selected
    ]
    translations = {
        x["id"]: {"title_zh": x["title"], "summary_zh": x["summary"] or x["title"]}
        for x in items
        if has_cjk((x["title"] or "") + " " + (x["summary"] or ""))
    }
    pending = [x for x in items if x["id"] not in translations]
    try:
        for start in range(0, len(pending), 20):
            batch = pending[start : start + 20]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是全球权威新闻中文快译站的翻译编辑。准确、中立、简洁地翻译新闻标题和摘要。"
                        "只基于原文，不添加未经证实的信息，不写立场判断。必须输出 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "rules": [
                                "每条输入新闻都必须按相同 id 返回一条 translations 记录。",
                                "title_zh 要是自然中文新闻标题，不要解释。",
                                "summary_zh 用一句中文概括关键人物、地点、动作和结果。",
                            ],
                            "format": {
                                "translations": [
                                    {
                                        "id": "原 id",
                                        "title_zh": "准确中文标题",
                                        "summary_zh": "一句话中文摘要，保留关键人物、地点、动作",
                                    }
                                ]
                            },
                            "articles": batch,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            data = call_deepseek(session, messages, 5000)
            if data.get("enabled") is False:
                raise RuntimeError(data.get("error") or "DeepSeek API Key 未启用")
            for item in data.get("translations", []):
                item_id = str(item.get("id") or "")
                if item_id:
                    translations[item_id] = {
                        "title_zh": clean(item.get("title_zh")),
                        "summary_zh": clean(item.get("summary_zh")),
                    }
        payload = {
            "enabled": True,
            "ok": True,
            "translations": translations,
            "article_count": len(translations),
            "total_requested": len(items),
            "generated_at": local_now(),
        }
    except Exception as exc:
        payload = {"enabled": bool(cfg["api_key"]), "ok": False, "translations": {}, "error": f"{type(exc).__name__}: {exc}"}
    with LOCK:
        TRANSLATION_CACHE.update({"expires": time.time() + CACHE_TTL, "payload": payload, "key": cache_key})
    return payload


def briefing_payload(session: dict[str, Any]) -> dict[str, Any]:
    news = news_payload(False)
    cfg = deepseek(session)
    cache_key = hashlib.sha1(
        json.dumps([x["id"] for x in news["articles"][:35]], ensure_ascii=False).encode("utf-8") + cfg["api_key"].encode("utf-8")
    ).hexdigest()
    with LOCK:
        if BRIEFING_CACHE["payload"] and BRIEFING_CACHE["key"] == cache_key and BRIEFING_CACHE["expires"] > time.time():
            return BRIEFING_CACHE["payload"]
    items = [
        {
            "title": x["title"],
            "summary": x["summary"][:260],
            "source": x["source"],
            "region": x["region"],
            "published_at": x["published_at"],
        }
        for x in news["articles"][:35]
    ]
    messages = [
        {
            "role": "system",
            "content": "你是中立的国际时政快讯编辑。只基于给定新闻生成中文简报，避免夸张和立场化表述。输出 JSON。",
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "format": {
                        "headline": "一句话总览",
                        "bullets": ["3-5 条今日重点"],
                        "watchlist": ["后续关注点"],
                    },
                    "articles": items,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        data = call_deepseek(session, messages, 2500)
        payload = {"enabled": True, "ok": True, "briefing": data, "generated_at": local_now()}
    except Exception as exc:
        payload = {"enabled": bool(cfg["api_key"]), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    with LOCK:
        BRIEFING_CACHE.update({"expires": time.time() + CACHE_TTL, "payload": payload, "key": cache_key})
    return payload


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Caesar News｜全球时政中文快译</title>
  <style>
    :root{--bg:#f6f7f9;--paper:#fff;--ink:#17202c;--soft:#667085;--line:#d8dee8;--red:#c7354d;--green:#15845b;--amber:#a96b00;--blue:#2459a6;--slate:#253044}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif}a{color:inherit}.top{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line)}.top-inner{display:grid;grid-template-columns:auto minmax(320px,1fr) auto;gap:18px;align-items:center;padding:16px 28px}.brand{display:flex;gap:12px;align-items:center}.mark{display:grid;place-items:center;width:44px;height:44px;border-radius:8px;background:var(--slate);color:#fff;font-size:24px;font-weight:900}.brand h1{font-size:25px;margin:0}.brand p,.meta{margin:0;color:var(--soft);font-size:13px}.ticker{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 10px;align-items:center;border:1px solid var(--line);border-left:5px solid var(--red);border-radius:8px;padding:10px 12px}.ticker b{grid-row:1/3;color:var(--red);font-size:12px}.ticker a{font-size:17px;font-weight:900;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.refresh{border:0;border-radius:8px;background:var(--red);color:#fff;width:42px;height:42px;font-size:22px}.searchbar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;padding:0 28px 14px}.searchbar input{height:48px;border:2px solid #303744;border-radius:999px;padding:0 22px;font-size:17px}.search-note{align-self:center;color:var(--soft);font-size:13px;white-space:nowrap}.tabs{display:flex;gap:8px;overflow:auto;padding:0 28px 14px}.tab{border:1px solid var(--line);background:#fff;border-radius:8px;min-height:36px;padding:0 12px;white-space:nowrap;cursor:pointer}.tab.active{background:var(--slate);border-color:var(--slate);color:#fff}.layout{display:grid;grid-template-columns:286px minmax(0,1fr) 330px;gap:0;min-height:calc(100vh - 156px)}.rail,.right{padding:18px 14px;display:grid;align-content:start;gap:14px}.rail{border-right:1px solid var(--line)}.right{border-left:1px solid var(--line);background:#fff}.main{padding:20px 24px}.card,.article,.source,.hero{background:var(--paper);border:1px solid var(--line);border-radius:8px}.card{padding:14px}.head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.head h2{margin:0;font-size:20px}.key,.select{width:100%;height:38px;border:1px solid var(--line);border-radius:8px;padding:0 10px}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.btn{border:1px solid var(--line);background:#fff;border-radius:8px;min-height:36px;padding:0 12px;cursor:pointer;white-space:nowrap}.btn.primary,.btn.active{background:var(--slate);border-color:var(--slate);color:#fff}.btn.danger{color:var(--red)}.status-pill{border-radius:999px;padding:5px 8px;font-size:12px;background:#eef2f5;color:var(--soft);font-weight:800}.status-pill.on{background:#e9f8f0;color:var(--green)}.hint{font-size:12px;line-height:1.55;color:var(--soft)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}.stat{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px}.stat strong{display:block;font-size:28px;margin-top:3px}.hero{display:grid;gap:10px;padding:16px;margin-bottom:14px;border-left:5px solid var(--red)}.hero h2{margin:0;font-size:24px}.hero a{text-decoration:none}.feed-head{display:flex;justify-content:space-between;align-items:end;gap:12px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:14px}.feed-head h2{margin:0}.actions{display:flex;gap:8px}.list{display:grid;gap:12px}.article{padding:15px;display:grid;gap:10px}.article-top{display:flex;align-items:center;gap:10px}.icon{width:34px;height:34px;border-radius:8px}.grow{min-width:0;flex:1}.grow strong,.grow span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.score{border:1px solid #bfd7fb;background:#eef6ff;color:var(--blue);border-radius:999px;padding:5px 8px;font-size:12px;font-weight:900}.title{font-size:22px;font-weight:900;line-height:1.35;text-decoration:none;overflow-wrap:anywhere}.title.pending{color:#333}.original{border-left:3px solid var(--line);padding-left:10px;color:#4c5564;font-size:14px;line-height:1.55}.summary{margin:0;line-height:1.7;color:#354052}.tags{display:flex;flex-wrap:wrap;gap:6px}.tag{background:#eef2f5;border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:12px;color:#556070}.tag.ai{background:#fff0f2;border-color:#f3c5ce;color:var(--red)}.tag.ok{background:#eaf8f1;border-color:#bde6ce;color:var(--green)}.tag.warn{background:#fff7e8;border-color:#f1d39a;color:var(--amber)}.links{display:flex;gap:12px}.link{color:#a91935;font-weight:900;text-decoration:none;font-size:13px}.hot-list{display:grid;gap:8px}.hot-list a{display:grid;grid-template-columns:24px 1fr;gap:4px 8px;text-decoration:none;border:1px solid var(--line);border-radius:8px;padding:9px}.hot-list span{display:grid;place-items:center;background:#fff0f2;color:var(--red);border-radius:6px;font-weight:900}.hot-list small{grid-column:2;color:var(--soft)}.source{display:grid;grid-template-columns:42px 1fr;gap:10px;padding:12px}.source img{width:42px;height:42px;border-radius:8px}.source p{font-size:12px;color:var(--soft);line-height:1.5;margin:2px 0}.principles{display:grid;gap:8px}.principles p{margin:0;font-size:13px;line-height:1.55}.hidden{display:none}.empty{padding:24px;text-align:center;color:var(--soft)}
    .ticker-copy{min-width:0}.ticker-en{display:block;color:var(--soft);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.site-strip{display:flex;gap:8px;overflow:auto;padding:0 28px 14px;background:#fff;align-items:stretch}.site-strip-label{display:grid;place-items:center;border:1px solid var(--ink);background:var(--ink);color:#fff;border-radius:8px;padding:8px 12px;white-space:nowrap;font-size:13px;font-weight:900}.site-strip a{border:1px solid var(--line);border-radius:8px;padding:8px 10px;text-decoration:none;white-space:nowrap;font-size:13px;font-weight:900}.site-strip small{display:block;color:var(--soft);font-weight:400;margin-top:2px}.ai-fold{padding:0;overflow:hidden}.ai-fold summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px;cursor:pointer}.ai-fold summary::-webkit-details-marker{display:none}.fold-title{font-size:20px;font-weight:900}.fold-body{border-top:1px solid var(--line);padding:14px;display:grid;gap:10px}.zh-label{display:inline-block;color:var(--red);font-size:12px;font-weight:900;margin-bottom:4px}.site-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.site-grid a{border:1px solid var(--line);border-radius:8px;padding:8px;text-decoration:none;font-size:13px;font-weight:900;background:#fff}.site-grid small{display:block;color:var(--soft);font-weight:400;margin-top:3px}.enline{color:#4c5564;font-size:14px;line-height:1.55}.rank-score{display:inline-block;color:var(--red);font-weight:900}.rank-time{color:var(--soft);font-size:12px}
    .layout{grid-template-columns:minmax(310px,22vw) minmax(0,1fr)}.right{display:none}.ticker{padding:16px 18px;min-height:82px}.ticker b{font-size:14px}.ticker a{font-size:24px;line-height:1.25}.ticker-en{font-size:14px;margin-top:3px}.site-strip-label{cursor:pointer;line-height:1.2}.site-strip-label small{display:block;color:#d7deeb;font-size:11px;font-weight:500;margin-top:3px}.source-hub{background:#fff;border-top:1px solid var(--line);padding:18px 28px}.source-hub .head{margin-bottom:14px}.hub-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.hub-section{border:1px solid var(--line);border-radius:8px;padding:12px;background:#fbfcfe}.hub-section h3{margin:0 0 8px;font-size:16px}.hub-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.hub-link{display:block;border:1px solid var(--line);border-radius:8px;background:#fff;padding:8px;text-decoration:none;font-size:13px;font-weight:900;min-width:0}.hub-link small{display:block;color:var(--soft);font-weight:400;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .mark{position:relative;display:grid;place-items:center;width:46px;height:46px;border-radius:10px;background:linear-gradient(145deg,#121a29,#273247);border:2px solid #c7a45a;color:#f7efe0;font-family:Georgia,"Times New Roman",serif;font-size:0;font-weight:900;box-shadow:inset 0 0 0 1px rgba(255,255,255,.14)}.mark span{font-size:18px;letter-spacing:0}.mark:after{content:"";position:absolute;inset:5px;border:1px solid rgba(199,164,90,.62);border-radius:7px}.top-inner{grid-template-columns:minmax(260px,auto) minmax(320px,1fr)}.refresh{display:none}.stats{display:none}.keyword-fold{margin-top:8px}.keyword-fold summary{display:inline-flex;align-items:center;min-height:34px;border:1px solid var(--line);border-radius:8px;background:#fff;padding:0 12px;cursor:pointer;font-size:13px;font-weight:900;color:var(--slate)}.keywordbar{display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:8px;margin-top:8px;max-width:760px}.keywordbar input{height:40px;border:1px solid var(--line);border-radius:8px;padding:0 12px;font-size:14px;background:#fff}.keywordbar .btn{min-height:40px}.assist-note{font-size:12px;color:var(--soft)}
    .source-hub{max-height:62vh;overflow:auto}
    @media(max-width:1100px){.layout{grid-template-columns:290px minmax(0,1fr)}.right{display:none}.top-inner{grid-template-columns:minmax(0,1fr)}.ticker{grid-column:1/-1}.searchbar{grid-template-columns:1fr}.search-note{display:none}.hub-grid{grid-template-columns:1fr 1fr}}
    @media(max-width:760px){body{font-size:14px}.top{position:static}.layout{display:block}.rail{border-right:0;border-bottom:1px solid var(--line);padding:12px}.main{padding:12px}.top-inner{grid-template-columns:minmax(0,1fr);padding:12px 16px;gap:10px}.brand{gap:9px}.brand h1{font-size:20px;line-height:1.2}.brand p,.meta{font-size:12px}.mark{width:38px;height:38px}.mark span{font-size:15px}.ticker{grid-column:1/-1;grid-template-columns:1fr;min-height:auto;padding:12px;gap:5px}.ticker b{grid-row:auto}.ticker a{font-size:19px;white-space:normal;overflow:visible;text-overflow:clip}.ticker-en{font-size:13px;white-space:normal;overflow:visible;text-overflow:clip}.searchbar{grid-template-columns:1fr;padding-bottom:10px}.searchbar input{height:44px;font-size:15px;padding:0 16px}.keywordbar{display:grid;grid-template-columns:1fr;max-width:none}.tabs,.site-strip{padding-bottom:10px}.site-strip a,.site-strip-label{padding:8px 9px}.source-hub{max-height:58vh;overflow:auto}.feed-head,.actions{display:grid;align-items:stretch}.feed-head h2{font-size:20px}.article{padding:12px}.article-top{align-items:flex-start}.grow strong,.grow span{white-space:normal}.score{justify-self:end}.title{font-size:19px}.summary{line-height:1.6}.hot-list a{grid-template-columns:22px 1fr}.head h2{font-size:18px}.top-inner,.searchbar,.tabs,.site-strip,.source-hub{padding-left:16px;padding-right:16px}.hub-grid,.hub-links{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <header class="top">
    <div class="top-inner">
      <div class="brand"><div class="mark" aria-label="Caesar News"><span>CA</span></div><div><h1>Caesar News｜全球时政中文快译</h1><p>汇集全球权威新闻源，实时快译成中文</p><p id="latestTime" class="meta">最新时间：等待刷新</p></div></div>
      <section class="ticker"><b>今日头条</b><div class="ticker-copy"><a id="tickerTitle" href="#" target="_blank">等待中文快译</a><span id="tickerEn" class="ticker-en">英文原文同步显示</span><span id="tickerMeta" class="meta">AI 快译 · 保留英文原文 · 多源确认</span></div></section>
      <button id="refresh" class="refresh" title="刷新" hidden>刷新</button>
    </div>
    <div class="searchbar">
      <input id="q" type="search" placeholder="精准搜索：特朗普、Trump、台海、乌克兰、Reuters、关税、军事安全">
      <span id="searchTip" class="search-note">支持中文人物名自动匹配英文报道</span>
    </div>
    <nav id="topicTabs" class="tabs" aria-label="新闻栏目"></nav>
    <section class="site-strip" aria-label="全球权威新闻媒体网址">
      <button id="navToggle" class="site-strip-label" type="button" aria-expanded="false"><span>全球权威新闻媒体网址</span><small id="navHint">点击进入完整媒体库</small></button>
      <a href="https://www.reuters.com/world/" target="_blank">Reuters<small>全球快讯</small></a>
      <a href="https://apnews.com/hub/world-news" target="_blank">AP<small>国际突发</small></a>
      <a href="https://www.bbc.com/news/world" target="_blank">BBC<small>世界新闻</small></a>
      <a href="https://www.afp.com/en" target="_blank">AFP<small>通讯社</small></a>
      <a href="https://www.dw.com/en" target="_blank">DW<small>欧洲视角</small></a>
      <a href="https://www.france24.com/en/" target="_blank">France 24<small>法国国际</small></a>
      <a href="https://www3.nhk.or.jp/nhkworld/en/news/" target="_blank">NHK<small>日本亚太</small></a>
      <a href="https://www.aljazeera.com" target="_blank">Al Jazeera<small>中东全球</small></a>
      <a href="https://www.nytimes.com/section/world" target="_blank">NYT<small>深度报道</small></a>
      <a href="https://www.wsj.com/world" target="_blank">WSJ<small>财经地缘</small></a>
      <a href="https://www.theguardian.com/world" target="_blank">Guardian<small>欧洲美国</small></a>
      <a href="https://www.bloomberg.com" target="_blank">Bloomberg<small>经济政治</small></a>
    </section>
    <section id="sourceHub" class="source-hub hidden">
      <div class="head"><div><h2>全球权威新闻媒体网址</h2><p class="meta">通讯社、新闻电视台、各国外交发布、中国官方与国内新闻发布入口</p></div><span id="hubCount" class="meta">0 个入口</span></div>
      <div id="sourceHubGroups" class="hub-grid"></div>
    </section>
  </header>

  <main class="layout">
    <aside class="rail">
      <details class="card ai-fold" id="aiFold">
        <summary><span class="fold-title">AI 翻译设置</span><span id="aiStatus" class="status-pill">未启用</span></summary>
        <div class="fold-body">
        <input id="apiKey" class="key" type="password" placeholder="输入你自己的 DeepSeek API Key">
        <div class="row"><button id="saveKey" class="btn primary">保存并快译</button><button id="clearKey" class="btn danger">清除</button></div>
        <select id="model" class="select">
          <option value="deepseek-chat">deepseek-chat</option>
          <option value="deepseek-v4-flash">deepseek-v4-flash</option>
          <option value="deepseek-v4-pro">deepseek-v4-pro</option>
        </select>
        <button id="translateBtn" class="btn primary">AI 翻译最新新闻</button>
        <p id="aiMessage" class="hint">Key 只保存在当前访问会话中，不写入代码、不写入仓库。每个访问者使用自己的 Key。</p>
        </div>
      </details>
      <section class="card">
        <div class="head"><h2>全球新闻热点热度排行榜</h2><span id="hotCount" class="meta">0 条</span></div>
        <div id="hot" class="hot-list"></div>
      </section>
      <section class="card principles">
        <div class="head"><h2>公正机制</h2></div>
        <p>1. 通讯社优先：Reuters、AP、AFP 作为基础事实层。</p>
        <p>2. 保留英文原标题，中文只做快译和摘要。</p>
        <p>3. 同一事件被多个来源报道时标记“多源确认”。</p>
      </section>
    </aside>

    <section class="main">
      <div class="stats">
        <div class="stat"><span class="meta">今日新闻</span><strong id="today">0</strong></div>
        <div class="stat"><span class="meta">权威来源</span><strong id="sources">0</strong></div>
        <div class="stat"><span class="meta">正常抓取</span><strong id="ok">0</strong></div>
        <div class="stat"><span class="meta">多源确认</span><strong id="confirmed">0</strong></div>
      </div>
      <div class="feed-head">
        <div>
          <h2 id="feedTitle">今日全球头条</h2>
          <p id="feedMeta" class="meta">正在加载权威新闻源</p>
          <details class="keyword-fold">
            <summary>设置头条关键词</summary>
            <div class="keywordbar" aria-label="今日全球头条关键词设置">
              <input id="headlineKeywords" type="text" placeholder="设置头条关键词：台海局势、中美关系、特朗普、关税">
              <button id="keywordApply" class="btn primary">设为头条关键词</button>
              <button id="keywordClear" class="btn">清除</button>
            </div>
          </details>
        </div>
        <div class="actions"><button id="translateMainBtn" class="btn primary">中文快译</button><button id="hotSort" class="btn active">相关度/热度</button><button id="timeSort" class="btn">时间优先</button></div>
      </div>
      <div id="status" class="card hidden"></div>
      <div id="articles" class="list"></div>
    </section>

  </main>

  <script>
    const S={articles:[],sources:[],nav:[],status:[],topics:[],translations:{},topic:'headlines',sort:'hot',q:'',keywords:localStorage.getItem('headlineKeywords')||'',ticker:0,aiEnabled:false};
    const E=id=>document.getElementById(id);
    const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
    const fmt=s=>new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(s));
    const queryMap={
      '特朗普':['trump','donald trump','us president'],'川普':['trump','donald trump'],'拜登':['biden','joe biden'],
      '普京':['putin','vladimir putin','russia'],'泽连斯基':['zelensky','zelenskyy','ukraine'],
      '内塔尼亚胡':['netanyahu','israel'],'马克龙':['macron','france'],'习近平':['xi jinping','china','beijing'],
      '中美':['china','united states','u.s.','us china','tariff'],'中美关系':['china','united states','u.s.','us china','tariff','taiwan'],
      '台海':['taiwan','taiwan strait','china taiwan','pla','military drills'],'台海局势':['taiwan','taiwan strait','china taiwan','pla','military drills'],
      '俄乌':['ukraine','russia','putin','zelensky'],'乌克兰':['ukraine','kyiv'],'俄罗斯':['russia','moscow'],
      '中东':['middle east','israel','gaza','iran'],'加沙':['gaza','hamas','israel'],'伊朗':['iran'],
      '关税':['tariff','trade'],'军事':['military','defense','security'],'经济':['economy','trade','market']
    };
    const phraseRules=[
      [/^live:\s*us house passes bill in support of ukraine,\s*sanctions russia$/i,'直播：美国众议院通过支持乌克兰并制裁俄罗斯的法案'],
      [/^u\.s\. house passes bill to aid ukraine and impose new sanctions on russia$/i,'美国众议院通过援助乌克兰并对俄罗斯实施新制裁的法案'],
      [/^us house of representatives passes bill on sanctions against russia,\s*assistance to ukraine$/i,'美国众议院通过对俄制裁及援助乌克兰法案'],
      [/^u\.s\. house backs russia sanctions,\s*ukraine aid,/i,'美国众议院支持对俄制裁和乌克兰援助，'],
      [/^\[big read\]\s*after xi-trump summit,\s*a jittery taiwan takes stock/i,'深度：习特会后，紧张的台湾重新评估局势'],
      [/^taiwan '?leak'? of military hosting reveals shifting china power balance$/i,'台湾军事接待信息外泄，凸显中国力量格局变化'],
      [/^taiwan's disclosure of singapore military training draws attention/i,'台湾披露新加坡军事训练引发关注'],
    ];
    const zhPairs=[['U.S. House','美国众议院'],['US House of Representatives','美国众议院'],['House of Representatives','众议院'],['U.S.','美国'],['US ','美国 '],['United States','美国'],['Xi-Trump summit','习特会'],['Singapore military training','新加坡军事训练'],['military hosting','军事接待'],['shifting China power balance','中国力量格局变化'],['draws attention','引发关注'],['takes stock','重新评估局势'],['jittery','紧张的'],['disclosure','披露'],['leak','外泄'],['Russia sanctions','对俄制裁'],['new sanctions','新制裁'],['support of','支持'],['assistance to','援助'],['aid Ukraine','援助乌克兰'],['impose','实施'],['imposes','实施'],['passes','通过'],['backs','支持'],['Bill','法案'],['bill','法案'],['House','众议院'],['Senate','参议院'],['aid','援助'],['Ukraine','乌克兰'],['Russia','俄罗斯'],['sanctions','制裁'],['Trump','特朗普'],['Xi','习近平'],['China','中国'],['Taiwan Strait','台海'],['Taiwan','台湾'],['Gaza','加沙'],['Israel','以色列'],['Iran','伊朗'],['ceasefire','停火'],['war','战争'],['military','军事'],['tariff','关税'],['trade','贸易'],['election','选举'],['government','政府'],['president','总统'],['President','总统'],['prime minister','总理'],['foreign minister','外长'],['security','安全'],['defense','国防'],['economy','经济'],['market','市场'],['oil','石油'],['energy','能源'],['NATO','北约'],['EU','欧盟'],['United Nations','联合国']];
    function hasZh(s){return /[\u4e00-\u9fff]/.test(String(s||''))}
    function browserAssist(s){let out=String(s||'');if(!out||hasZh(out))return out;for(const [re,zh] of phraseRules){if(re.test(out))return out.replace(re,zh)}for(const [en,zh] of zhPairs){out=out.replaceAll(en,zh)}out=out.replace(/\b(says|said)\b/gi,'称').replace(/\bnew\b/gi,'新').replace(/\blatest\b/gi,'最新').replace(/\blive\b/gi,'直播').replace(/\bagainst\b/gi,'针对').replace(/\bon\b/gi,'关于').replace(/\bin\b/gi,'在').replace(/\bto\b/gi,'至').replace(/\band\b/gi,'和').replace(/\s+/g,' ').trim();return out}
    function data(a){const t=S.translations[a.id]||{};if(t.title_zh||t.summary_zh)return{title:t.title_zh||a.title,summary:t.summary_zh||a.summary||'',translated:true,assisted:false};const title=browserAssist(a.title),summary=browserAssist(a.summary||'');return{title,summary,translated:false,assisted:title!==a.title||summary!==(a.summary||'')}}
    function hot(a){const h=Math.max(0,(Date.now()-new Date(a.published_at))/36e5);return Math.round((a.politics_score||0)*10+(a.confirm_count||1)*18+(a.is_today?22:0)+Math.max(0,28-h*1.2))}
    const stopWords=new Set('the a an and or of to in on for with from by at as is are was were be been latest live news update updates world global international after before over under into about says said say new more this that its their his her our your has have had will would could should can amid among during across against top major big first last today yesterday tomorrow'.split(' '));
    function eventText(a){return (a.title+' '+(a.summary||'')+' '+data(a).title).toLowerCase().replace(/\b(reuters|ap news|associated press|bbc|afp|dw|france 24|guardian|bloomberg|cnn|nyt|wsj|tass|xinhua)\b/g,' ')}
    function eventTheme(a){const t=eventText(a);if(/chagos/.test(t))return'查戈斯群岛议题';if(/philippines/.test(t)&&/earthquake|tsunami/.test(t))return'菲律宾地震海啸';if(/ukraine|russia|putin|zelensky|kyiv|moscow/.test(t)&&/sanction|aid|bill|house|congress/.test(t))return'乌克兰援助与对俄制裁';if(/ukraine|russia|putin|zelensky|kyiv|moscow/.test(t)&&/war|ceasefire|missile|drone|nato|talks/.test(t))return'俄乌战争与谈判';if(/taiwan|taiwan strait/.test(t)&&/china|beijing|military|drill|standoff|security|coast guard/.test(t))return'台海局势';if(/south china sea|china coast guard|philippines/.test(t)&&/china|beijing|military|drill|standoff|security|coast guard/.test(t))return'南海局势';if(/gaza|israel|iran|hamas|netanyahu|lebanon|hezbollah|middle east/.test(t))return'中东局势';if(/trump|white house|congress|senate|u\.s\.|us |united states|tariff/.test(t)&&/china|trade|tariff|sanction|india|russia/.test(t))return'美国政策';if(/stock|market|currency|oil|energy|economy|trade|investment|bank|shares/.test(t))return'全球经济';return''}
    function eventTokens(a){const toks=(eventText(a).match(/[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}/g)||[]).filter(x=>!stopWords.has(x)&&!/^\\d+$/.test(x));return new Set(toks.slice(0,80))}
    function simSets(a,b){if(!a.size||!b.size)return 0;let hit=0;for(const x of a)if(b.has(x))hit++;return hit/Math.max(1,Math.min(a.size,b.size))}
    function clusterNews(items){const broad=new Set(['中东局势','美国政策','全球经济']);const groups=[];for(const a of [...items].sort((x,y)=>hot(y)-hot(x)||y.published_ts-x.published_ts)){const tokens=eventTokens(a),theme=eventTheme(a);let best=null,bestScore=0;for(const g of groups){const boost=(theme&&theme===g.theme)?(broad.has(theme)?0.18:0.5):0;const score=simSets(tokens,g.tokens)+boost;if(score>bestScore){best=g;bestScore=score}}if(best&&bestScore>=.42){best.items.push(a);best.sources.add(a.source);best.tokens=new Set([...best.tokens,...tokens]);best.score=Math.max(best.score,hot(a))+Math.min(best.items.length,8)*4;best.latest=Math.max(best.latest,a.published_ts);if(hot(a)>hot(best.rep)-18&&a.published_ts>best.rep.published_ts)best.rep=a}else{groups.push({rep:a,items:[a],sources:new Set([a.source]),tokens,theme,score:hot(a),latest:a.published_ts})}}return groups.sort((a,b)=>b.score-a.score||b.latest-a.latest)}
    function groupRep(g){const a={...g.rep};a._mergedCount=g.items.length;a._mergedSources=[...g.sources].slice(0,5).join('、');a._eventTheme=g.theme;a.confirm_count=Math.max(a.confirm_count||1,g.sources.size);a.confirmed=a.confirmed||g.sources.size>1||g.items.length>1;return a}
    function expandedQuery(q){let parts=[q.toLowerCase().trim()];for(const [k,v] of Object.entries(queryMap)){if(q.includes(k))parts.push(...v)}return [...new Set(parts.flatMap(x=>x.split(/\s+/).concat(x)).filter(Boolean))]}
    function searchScore(a,q){if(!q)return 1;const d=data(a);const terms=expandedQuery(q);const title=(d.title+' '+a.title).toLowerCase();const body=(d.summary+' '+a.summary).toLowerCase();const meta=(a.source+' '+a.region+' '+a.authority+' '+(a.topics||[]).join(' ')).toLowerCase();let s=0;for(const term of terms){if(!term)continue;if(title.includes(term))s+=60;if(body.includes(term))s+=18;if(meta.includes(term))s+=22}if(title.includes(q.toLowerCase()))s+=70;return s}
    function keywordScore(a){const kw=S.keywords.trim();if(!kw)return 1;if(a.custom_match)return 120;const terms=expandedQuery(kw);const hay=(a.title+' '+(a.summary||'')+' '+data(a).title+' '+data(a).summary).toLowerCase();let s=0;for(const term of terms){if(term&&hay.includes(term))s+=50}return s}
    function topicName(){return (S.topics.find(t=>t.id===S.topic)||{}).name||'今日全球头条'}
    function filtered(){const q=S.q.trim();let list=S.articles.filter(a=>S.topic==='headlines'||(S.topic==='confirmed'?a.confirmed:(a.topics||[]).includes(S.topic)));if(S.topic==='headlines'&&S.keywords.trim())list=list.map(a=>({...a,_keyword:keywordScore(a)})).filter(a=>a._keyword>0);if(q)list=list.map(a=>({...a,_match:searchScore(a,q)})).filter(a=>a._match>0);return list.sort((a,b)=>q?((b._match||0)-(a._match||0)||b.published_ts-a.published_ts):(S.sort==='time'?b.published_ts-a.published_ts:((b._keyword||0)-(a._keyword||0)||hot(b)-hot(a)||b.published_ts-a.published_ts)))}
    function renderTabs(){E('topicTabs').innerHTML=S.topics.map(t=>`<button class="tab ${S.topic===t.id?'active':''}" data-topic="${esc(t.id)}">${esc(t.name)}</button>`).join('');document.querySelectorAll('[data-topic]').forEach(b=>b.onclick=()=>{S.topic=b.dataset.topic;render()})}
    function articleCard(a){const d=data(a);return `<article class="article"><div class="article-top"><img class="icon" src="${esc(a.source_icon)}"><div class="grow"><strong>来源：${esc(a.source)}</strong><span class="meta">时间：${fmt(a.published_at)} · 地区：${esc(a.region)} · ${esc(a.authority)}</span></div><span class="score">热度 ${hot(a)}</span></div><span class="zh-label">中文快译</span><a class="title ${d.translated?'':'pending'}" href="${esc(a.url)}" target="_blank">${esc(d.title)}</a><div class="original"><b>英文原标题：</b>${esc(a.title)}</div>${d.summary?`<p class="summary"><b>一句话中文摘要：</b>${esc(d.summary)}</p>`:''}<div class="tags"><span class="tag ${d.translated?'ai':(d.assisted?'ok':'warn')}">${d.translated?'AI精准快译':(d.assisted?'浏览器辅助快译':'待AI快译')}</span><a class="tag" href="${esc(a.url)}" target="_blank">原文链接</a>${a._mergedCount>1?`<span class="tag ok">同一事件合并 ${a._mergedCount} 条</span>`:''}${a._mergedSources?`<span class="tag">合并来源 ${esc(a._mergedSources)}</span>`:''}${a.custom_keyword?`<span class="tag ok">关键词 ${esc(a.custom_keyword)}</span>`:''}${a.confirmed?`<span class="tag ok">多源确认 ${a.confirm_count}</span>`:''}${(a.topics||[]).slice(0,3).map(x=>`<span class="tag">${esc((S.topics.find(t=>t.id===x)||{}).name||x)}</span>`).join('')}</div></article>`}
    function render(){renderTabs();const list=filtered();const grouped=clusterNews(list).slice(0,120).map(groupRep);const latest=list[0]||S.articles[0];E('feedTitle').textContent=S.q.trim()?`搜索：${S.q.trim()}`:(S.topic==='headlines'&&S.keywords.trim()?`今日全球头条：${S.keywords.trim()}`:topicName());E('feedMeta').textContent=`${list.length} 条匹配 · 合并为 ${grouped.length} 个事件 · ${latest?('最新新闻时间 '+fmt(latest.published_at)+' · '):''}${Object.keys(S.translations).length?('AI精准快译 '+Object.keys(S.translations).length+' 条'):'浏览器辅助快译 + 输入 Key 后 AI 精准快译'} · 保留英文原文`;E('articles').innerHTML=grouped.length?grouped.map(articleCard).join(''):`<div class="card empty">没有匹配新闻，换一个关键词试试，例如“台海局势”。</div>`;const hotGroups=clusterNews(S.articles).slice(0,10);E('hotCount').textContent=hotGroups.length+' 组';E('hot').innerHTML=hotGroups.map((g,i)=>{const a=groupRep(g),d=data(a);return `<a href="${esc(a.url)}" target="_blank"><span>${i+1}</span><b>${esc(d.title)}</b><small>${esc(a.source)} · ${fmt(a.published_at)} · <em class="rank-score">热度 ${hot(a)}</em>${a._mergedCount>1?' · 合并 '+a._mergedCount+' 条相似报道':''}${a.confirmed?' · 多源确认 '+a.confirm_count:''}</small></a>`}).join('');if(grouped.length){const a=grouped[S.ticker%grouped.length],d=data(a);E('tickerTitle').textContent=d.title;E('tickerTitle').href=a.url;E('tickerEn').textContent='英文原文：'+a.title;E('tickerMeta').textContent=`${a.source} · ${fmt(a.published_at)} · 热度 ${hot(a)}${a._mergedCount>1?' · 同一事件合并 '+a._mergedCount+' 条':''}${S.keywords.trim()?' · 关键词 '+S.keywords.trim():''}`}}
    function renderHub(){E('hubCount').textContent=(S.nav.reduce((n,g)=>n+g.links.length,0))+' 个入口';E('sourceHubGroups').innerHTML=S.nav.map(g=>`<section class="hub-section"><h3>${esc(g.title)}</h3><div class="hub-links">${g.links.map(x=>`<a class="hub-link" href="${esc(x.url)}" target="_blank">${esc(x.name)}<small>${esc(x.desc)}</small></a>`).join('')}</div></section>`).join('')}
    function toggleHub(){const opened=E('sourceHub').classList.toggle('hidden')===false;E('navToggle').setAttribute('aria-expanded',opened?'true':'false');E('navHint').textContent=opened?'点击收起':'点击进入完整媒体库'}
    function setTranslateButtons(text){E('translateBtn').textContent=text;if(E('translateMainBtn'))E('translateMainBtn').textContent=text}
    async function loadNews(force=false){E('refresh').disabled=true;const kw=encodeURIComponent(S.keywords.trim());const d=await (await fetch('/api/news?limit=360'+(force?'&refresh=1':'')+(kw?'&keywords='+kw:''))).json();S.articles=d.articles;S.status=d.status;S.topics=d.topics;E('today').textContent=d.today_count;E('sources').textContent=d.source_count;E('ok').textContent=d.ok_source_count;E('confirmed').textContent=d.articles.filter(a=>a.confirmed).length;const newest=[...d.articles].sort((a,b)=>b.published_ts-a.published_ts)[0];E('latestTime').textContent=newest?`最新时间：${fmt(newest.published_at)}`:`最新时间：${fmt(d.generated_at)}`;render();E('refresh').disabled=false;if(S.aiEnabled&&!Object.keys(S.translations).length)translate(false)}
    async function health(){const d=await (await fetch('/api/health')).json();S.aiEnabled=!!d.deepseek_enabled;E('aiStatus').textContent=d.deepseek_enabled?'已启用':'未启用';E('aiStatus').classList.toggle('on',!!d.deepseek_enabled);if(d.deepseek_model)E('model').value=d.deepseek_model}
    async function saveKey(k){E('aiStatus').textContent='保存中';const d=await (await fetch('/api/deepseek-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:k,model:E('model').value})})).json();S.aiEnabled=!!d.deepseek_enabled;E('apiKey').value='';E('aiStatus').textContent=d.deepseek_enabled?'已启用':'未启用';E('aiStatus').classList.toggle('on',!!d.deepseek_enabled);if(d.deepseek_enabled)await translate(true);else{S.translations={};render()}}
    async function translate(force=false){if(!S.aiEnabled){E('aiFold').open=true;E('aiMessage').textContent='当前已使用浏览器辅助快译；保存 DeepSeek API Key 后可对当前新闻做更准确的 AI 精准快译。';render();return}setTranslateButtons('快译中...');E('aiMessage').textContent='正在把当前外文新闻对应翻译成中文...';const kw=encodeURIComponent(S.keywords.trim());const d=await (await fetch('/api/translations?limit=240'+(force?'&refresh=1':'')+(kw?'&keywords='+kw:''))).json();if(!d.ok){E('aiFold').open=true;E('aiMessage').textContent=d.error||'请先保存 Key'}else{E('aiMessage').textContent=`已完成 ${d.article_count||0} 条 AI 精准快译`}if(d.translations)S.translations=d.translations;setTranslateButtons('中文快译');render()}
    E('refresh').onclick=()=>loadNews(true);E('saveKey').onclick=()=>saveKey(E('apiKey').value);E('clearKey').onclick=()=>saveKey('');E('translateBtn').onclick=()=>translate(true);E('translateMainBtn').onclick=()=>translate(true);E('navToggle').onclick=toggleHub;E('keywordApply').onclick=()=>{S.keywords=E('headlineKeywords').value.trim();localStorage.setItem('headlineKeywords',S.keywords);S.topic='headlines';S.translations={};loadNews(true)};E('keywordClear').onclick=()=>{S.keywords='';E('headlineKeywords').value='';localStorage.removeItem('headlineKeywords');S.translations={};loadNews(true)};E('hotSort').onclick=()=>{S.sort='hot';E('hotSort').classList.add('active');E('timeSort').classList.remove('active');render()};E('timeSort').onclick=()=>{S.sort='time';E('timeSort').classList.add('active');E('hotSort').classList.remove('active');render()};E('q').oninput=e=>{S.q=e.target.value;render()};setInterval(()=>{S.ticker++;render()},7000);(async()=>{E('headlineKeywords').value=S.keywords;S.sources=await (await fetch('/api/sources')).json();S.nav=(await (await fetch('/api/nav')).json()).groups;renderHub();await health();await loadNews(false)})();
  </script>
</body>
</html>"""


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
        cookie = f"{COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; SameSite=Lax; HttpOnly"
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
        if parsed.path == "/api/nav":
            self.send_json({"groups": NAV_GROUPS, "count": sum(len(group["links"]) for group in NAV_GROUPS)}, cookie=cookie)
            return
        if parsed.path == "/api/news":
            q = urllib.parse.parse_qs(parsed.query)
            payload = dict(news_payload(q.get("refresh", ["0"])[0] in {"1", "true"}, q.get("keywords", [""])[0]))
            limit = int(q.get("limit", ["360"])[0])
            payload["articles"] = payload["articles"][:limit]
            self.send_json(payload, cookie=cookie)
            return
        if parsed.path == "/api/translations":
            q = urllib.parse.parse_qs(parsed.query)
            self.send_json(
                translate_payload(
                    session,
                    int(q.get("limit", ["90"])[0]),
                    q.get("refresh", ["0"])[0] in {"1", "true"},
                    q.get("keywords", [""])[0],
                ),
                cookie=cookie,
            )
            return
        if parsed.path == "/api/briefing":
            self.send_json(briefing_payload(session), cookie=cookie)
            return
        if parsed.path == "/api/health":
            cfg = deepseek(session)
            self.send_json({"ok": True, "deepseek_enabled": bool(cfg["api_key"]), "deepseek_model": cfg["model"]}, cookie=cookie)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        token, session = session_from_cookie(self.headers.get("Cookie"))
        cookie = f"{COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; SameSite=Lax; HttpOnly"
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
        except Exception:
            payload = {}
        if urllib.parse.urlsplit(self.path).path == "/api/deepseek-key":
            session["api_key"] = str(payload.get("api_key", "")).strip()
            session["model"] = str(payload.get("model", "deepseek-chat")).strip() or "deepseek-chat"
            session["expires"] = time.time() + SESSION_TTL
            with LOCK:
                TRANSLATION_CACHE.update({"expires": 0.0, "payload": None, "key": ""})
                BRIEFING_CACHE.update({"expires": 0.0, "payload": None, "key": ""})
            self.send_json({"ok": True, "deepseek_enabled": bool(session["api_key"]), "deepseek_model": session["model"]}, cookie=cookie)
            return
        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    args = parser.parse_args()
    print(f"Global Chinese news fast translate running on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
