"""
bbb 投資戰情室 - 消息面雷達 (Optimized Version)
"""

import os
import json
import time
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

# ================= 配置區 =================
# 這裡會自動去抓你剛才設定在 GitHub Secrets 的金鑰
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
NEWSAPI_URL = "https://newsapi.org/v2/everything"

WATCHLIST = {
    "AI伺服器": {
        "query": '("AI server" OR "data center capex" OR "Blackwell" OR "GB200" OR "Rubin")',
        "related_stocks":["台達電", "廣達", "緯創", "緯穎", "英業達", "AES-KY"],
        "tags":["AI", "伺服器", "資料中心"]
    },
    "半導體先進製程": {
        "query": '("TSMC" OR "CoWoS" OR "advanced packaging" OR "3nm" OR "2nm")',
        "related_stocks":["台積電", "日月光", "精測", "萬潤", "弘塑", "汎銓"],
        "tags":["半導體", "CoWoS", "先進製程"]
    },
    "光通訊與矽光子": {
        "query": '("CPO" OR "silicon photonics" OR "optical transceiver" OR "800G" OR "1.6T")',
        "related_stocks":["光聖", "聯亞", "IET-KY", "上詮", "華星光", "智邦"],
        "tags": ["光通訊", "CPO", "矽光子"]
    },
    "HBM記憶體": {
        "query": '("HBM" OR "SK hynix" OR "Micron" OR "Samsung memory" OR "high bandwidth memory")',
        "related_stocks":["南亞科", "華邦電", "群聯", "創見"],
        "tags":["HBM", "記憶體"]
    },
    "總體風險": {
        "query": '("Fed rate cut" OR "inflation" OR "tariff" OR "oil prices" OR "OPEC" OR "geopolitical risk")',
        "related_stocks":["大盤", "0050", "006208", "00878", "00981A"],
        "tags":["總體", "利率", "油價", "地緣政治"]
    },
}

TRUSTED_SOURCES = {
    "reuters.com": 20, "bloomberg.com": 20, "wsj.com": 18, 
    "ft.com": 18, "cnbc.com": 15, "nikkei.com": 15,
    "semianalysis.com": 20, "digitimes.com": 18,
    "techcrunch.com": 10, "theverge.com": 8, "tomshardware.com": 12
}

KEYWORDS = {
    "bullish":[
        "beat expectations", "record", "breakthrough", "surge", "growth", 
        "upgrade", "partnership", "capacity expansion", "price increase", 
        "strong demand", "raised guidance", "win", "approval"
    ],
    "bearish":[
        "miss expectations", "delay", "investigation", "tariff", "shortage", 
        "decline", "downgrade", "scandal", "bankruptcy", "cut guidance", 
        "weak demand", "lawsuit", "sanction", "layoff", "risk"
    ],
    "structural":[
        "capex", "roadmap", "architecture", "supply chain", "capacity", 
        "yield", "advanced packaging", "data center", "infrastructure", 
        "foundry", "deployment", "mass production"
    ],
    "financial":[
        "revenue", "earnings", "eps", "margin", "guidance", "order", 
        "shipment", "pricing", "profit", "billion"
    ]
}

def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def generate_id(url: str, title: str) -> str:
    raw = f"{url}_{title}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

def analyze_article(title: str, desc: str, content: str, url: str, published_at: str) -> tuple:
    text = f"{title} {desc} {content}".lower()
    score = 40  
    domain = get_domain(url)
    score += TRUSTED_SOURCES.get(domain, 5)

    bull_count = sum(1 for w in KEYWORDS["bullish"] if w in text)
    bear_count = sum(1 for w in KEYWORDS["bearish"] if w in text)
    struct_count = sum(1 for w in KEYWORDS["structural"] if w in text)
    fin_count = sum(1 for w in KEYWORDS["financial"] if w in text)

    sentiment = "neutral"
    if bull_count > bear_count:
        sentiment = "positive"
        score += min(15, bull_count * 5)
    elif bear_count > bull_count:
        sentiment = "negative"
        score += min(15, bear_count * 5)

    score += min(10, struct_count * 5)
    score += min(10, fin_count * 5)

    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if age_hours <= 24:
                score += 15
            elif age_hours <= 72:
                score += 10
            elif age_hours <= 168:
                score += 5
        except Exception:
            pass

    return sentiment, min(100, max(0, score))

def generate_chinese_reason(theme: str, importance: int, sentiment: str) -> str:
    direction = {"positive": "偏多", "negative": "偏空", "neutral": "中性"}.get(sentiment, "中性")
    if importance >= 85:
        level = "【重大事件】可能顯著影響產業評價、EPS預期或資金流向"
    elif importance >= 70:
        level = "【重要動態】具備參考價值，建議列入觀測清單"
    elif importance >= 50:
        level = "【一般資訊】可作為題材背景知識的補充"
    else:
        level = "【雜訊】權重過低，暫不作為交易決策依據"
    return f"屬「{theme}」題材之{direction}消息。{level}。"

def fetch_news(theme: str, config: dict, days_back: int = 7) -> list:
    date_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    params = {
        "apiKey": NEWSAPI_KEY,
        "q": config["query"],
        "language": "en",
        "sortBy": "relevancy", 
        "from": date_from,
        "pageSize": 15,
    }
    response = requests.get(NEWSAPI_URL, params=params, timeout=15)
    if response.status_code != 200:
        return[]
    return response.json().get("articles",[])

def build_payload():
    if not NEWSAPI_KEY:
        print("API Key is missing!")
        return None

    items =[]
    seen_ids = set()

    for theme, config in WATCHLIST.items():
        articles = fetch_news(theme, config)
        for a in articles:
            url = a.get("url") or ""
            title = a.get("title") or ""
            if not url or "[Removed]" in title:
                continue
                
            uid = generate_id(url, title)
            if uid in seen_ids:
                continue
            seen_ids.add(uid)

            desc = a.get("description") or ""
            content = a.get("content") or ""
            pub_date = a.get("publishedAt")
            
            sentiment, importance = analyze_article(title, desc, content, url, pub_date)

            item = {
                "id": uid,
                "dataset": "market_news",
                "schema_version": "bbb_news/v1",
                "theme": theme,
                "title": title,
                "summary": desc,
                "url": url,
                "source": (a.get("source") or {}).get("name", "Unknown"),
                "domain": get_domain(url),
                "published_at": pub_date,
                "importance": importance,
                "impact": sentiment,
                "timeframe": "short_term" if importance < 75 else "short_to_mid_term",
                "tags": config.get("tags", []),
                "related_stocks": config.get("related_stocks",[]),
                "related_models":["003", "005", "006", "007", "009", "010"],
                "reason": generate_chinese_reason(theme, importance, sentiment),
            }
            items.append(item)
        time.sleep(1)

    items.sort(key=lambda x: (x["importance"], x.get("published_at") or ""), reverse=True)

    return {
        "schema_version": "bbb_news_payload/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "NewsAPI Optimized Radar",
        "note": "AI伺服器/半導體/CPO/HBM/總體風險 國際題材掃描完成。",
        "total_count": len(items),
        "items": items
    }

def export_results(payload):
    if not payload: return
    with open("bbb_news_payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open("bbb_news_digest.md", "w", encoding="utf-8") as f:
        f.write(f"# 📊 bbb 投資戰情室 - 消息面摘要\n更新時間：{payload['generated_at']}")

if __name__ == "__main__":
    data = build_payload()
    export_results(data)
