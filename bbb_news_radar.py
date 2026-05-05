"""
bbb 投資戰情室 - 消息面雷達 (中文化與總結升級版)
"""

import os
import json
import time
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from deep_translator import GoogleTranslator

# ================= 配置區 =================
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
NEWSAPI_URL = "https://newsapi.org/v2/everything"

# 初始化翻譯器 (英文 -> 繁體中文)
translator = GoogleTranslator(source='en', target='zh-TW')

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

def safe_translate(text: str) -> str:
    """安全的翻譯函數，失敗時回傳原文"""
    if not text or len(text.strip()) == 0:
        return ""
    try:
        clean_text = text.split(" - ")[0]
        # 每次翻譯重新連線，避免被 Google 鎖定
        tr = GoogleTranslator(source='en', target='zh-TW')
        result = tr.translate(clean_text)
        time.sleep(0.5) # 稍微停頓 0.5 秒，模擬真人
        return result
    except Exception as e:
        print(f"翻譯失敗: {e}")
        return text

def analyze_article(title: str, desc: str, content: str, url: str, published_at: str) -> tuple:
    # 評分系統依然使用「英文原文」進行精準運算
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

def load_old_payload():
    """載入舊的 payload 以實作翻譯快取，避免重複翻譯"""
    if os.path.exists("bbb_news_payload.json"):
        try:
            with open("bbb_news_payload.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                # 建立 id -> (title, summary) 的對照表
                return {item["id"]: (item["title"], item["summary"]) for item in data.get("items", [])}
        except Exception as e:
            print(f"讀取舊快取失敗: {e}")
    return {}

def build_payload():
    if not NEWSAPI_KEY:
        print("API Key is missing!")
        return None

    translation_cache = load_old_payload()
    items = []
    seen_ids = set()

    for theme, config in WATCHLIST.items():
        articles = fetch_news(theme, config)
        print(f"正在處理題材: {theme} (取得 {len(articles)} 則新聞)")
        
        for a in articles:
            url = a.get("url") or ""
            title_en = a.get("title") or ""
            if not url or "[Removed]" in title_en:
                continue
                
            uid = generate_id(url, title_en)
            if uid in seen_ids:
                continue
            seen_ids.add(uid)

            desc_en = a.get("description") or ""
            content_en = a.get("content") or ""
            pub_date = a.get("publishedAt")
            
            # 1. 英文算分
            sentiment, importance = analyze_article(title_en, desc_en, content_en, url, pub_date)
            
            # 2. 翻譯檢查 (快取優先)
            if uid in translation_cache:
                title_zh, desc_zh = translation_cache[uid]
            else:
                print(f"  [新消息] 正在翻譯: {title_en[:30]}...")
                title_zh = safe_translate(title_en)
                desc_zh = safe_translate(desc_en)
                time.sleep(0.5) # 只有新翻譯才停頓

            item = {
                "id": uid,
                "dataset": "market_news",
                "schema_version": "bbb_news/v1",
                "theme": theme,
                "title": title_zh,
                "summary": desc_zh,
                "original_title": title_en,
                "url": url,
                "source": (a.get("source") or {}).get("name", "Unknown"),
                "domain": get_domain(url),
                "published_at": pub_date,
                "importance": importance,
                "impact": sentiment,
                "timeframe": "short_term" if importance < 75 else "short_to_mid_term",
                "tags": config.get("tags", []),
                "related_stocks": config.get("related_stocks", []),
                "related_models": ["003", "005", "006", "007", "009", "010"],
                "reason": generate_chinese_reason(theme, importance, sentiment),
            }
            items.append(item)

    # 排序：重要性高到低，時間新到舊
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
    
    # 導出 JSON
    with open("bbb_news_payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    
    # 導出 Markdown 摘要 (強化版)
    with open("bbb_news_digest.md", "w", encoding="utf-8") as f:
        f.write(f"# 📊 bbb 投資戰情室 - 消息面摘要\n")
        f.write(f"> 更新時間：{payload['generated_at']}\n\n")
        
        # 僅顯示重要性較高的前 20 則新聞
        important_items = [i for i in payload["items"] if i["importance"] >= 60][:20]
        
        if not important_items:
            f.write("目前暫無重大消息。\n")
        else:
            for item in important_items:
                impact_emoji = {"positive": "🚀", "negative": "⚠️", "neutral": "⚖️"}.get(item["impact"], "⚖️")
                f.write(f"### {impact_emoji} {item['title']}\n")
                f.write(f"- **題材**: `{item['theme']}` | **重要性**: `{item['importance']}`\n")
                f.write(f"- **來源**: {item['source']} ({item['published_at']})\n")
                f.write(f"- **AI 判讀**: {item['reason']}\n")
                f.write(f"- **摘要**: {item['summary']}\n")
                f.write(f"- [原文連結]({item['url']})\n\n")
                f.write("---\n\n")

if __name__ == "__main__":
    data = build_payload()
    export_results(data)
