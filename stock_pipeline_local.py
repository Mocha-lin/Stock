import os
import json
import time
import math
import zipfile
import shutil
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta, time as dtime
from dateutil.relativedelta import relativedelta
from yahooquery import Ticker
import requests_cache
import pytz
import argparse
from FinMind.data import DataLoader
from dotenv import load_dotenv

# 加載 .env 環境變數
load_dotenv()

# ==========================================
# ⚙️ 1. 系統全域參數設定區
# ==========================================
parser = argparse.ArgumentParser(description="量化分析流自動化腳本 (嚴格合併修復版)")
parser.add_argument('--stocks', nargs='+', default=["1815", "2330", "2455", "2327", "3037", "3017", "4958"], help='輸入股票代號或名稱')
parser.add_argument('--token', type=str, default=os.getenv("FINMIND_TOKEN", ""), help='FinMind API Token')
parser.add_argument('--run_id', type=str, default="", help='自訂執行批號')
args = parser.parse_args()

FINMIND_TOKEN = args.token
INPUT_STOCKS = args.stocks

tw_tz = pytz.timezone('Asia/Taipei')
now = datetime.now(tw_tz)
RUN_ID = args.run_id if args.run_id else now.strftime("%Y%m%d_%H%M")
BASE_DIR = Path(f"./stock_pipeline_runs/{RUN_ID}")
EXPORT_DIR = BASE_DIR / "final_txt_export"

SLEEP_SEC = 0.2
LOOKBACK_DAYS_CHIP = 45
SHOW_DAYS_CHIP = 15
MAX_QUARTERS_PE = 8
PE_WARN = 25
DEBT_RATIO_WARN = 60.0
DOI_WARN = 120.0
REVENUE_WEIGHT_PREMIUM = 1.1
REVENUE_WEIGHT_DISCOUNT = 0.9

requests_cache.install_cache('stock_cache', expire_after=timedelta(days=1))
dl = DataLoader()
if FINMIND_TOKEN: dl.login_by_token(api_token=FINMIND_TOKEN)

# ==========================================
# 🛠️ 2. 核心輔助函式與智慧校正
# ==========================================
def get_last_trading_day(target_date: date) -> date:
    check_date = target_date - timedelta(days=1)
    while check_date.weekday() >= 5: check_date -= timedelta(days=1)
    return check_date

today = now.date()
if now.time() < dtime(17, 0):
    asof_date = get_last_trading_day(today)
else:
    asof_date = today if today.weekday() < 5 else get_last_trading_day(today)
asof_str = asof_date.strftime("%Y-%m-%d")

# 建立 FinMind 對照表
try:
    df_info = dl.taiwan_stock_info()
    TW_MAP, SUFFIX_MAP, NAME_TO_ID_MAP = {}, {}, {}
    for _, row in df_info.iterrows():
        sid = str(row['stock_id'])
        sname = str(row['stock_name']).replace('*', '').strip()
        mtype = str(row.get('type', '')).lower()
        TW_MAP[sid] = sname
        SUFFIX_MAP[sid] = '.TW' if mtype != 'tpex' else '.TWO'
        NAME_TO_ID_MAP[sname] = sid
        NAME_TO_ID_MAP[sname.upper()] = sid
except:
    TW_MAP, SUFFIX_MAP, NAME_TO_ID_MAP = {}, {}, {}

def auto_correct_symbols(symbols):
    corrected = []
    for sym in symbols:
        sym_clean = str(sym).strip().upper()
        sid = NAME_TO_ID_MAP.get(sym_clean, sym_clean.split('.')[0])
        if sid in SUFFIX_MAP:
            corrected.append(f"{sid}{SUFFIX_MAP[sid]}")
        else:
            corrected.append(f"{sym_clean}.TW" if "." not in sym_clean else sym_clean)
    return list(dict.fromkeys(corrected))

def get_zh_name(sid): return TW_MAP.get(sid, sid).replace('*', '').strip()

def force_tz_naive(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    df = df.copy()
    if isinstance(df.index, pd.MultiIndex): df = df.reset_index()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], utc=True, errors='coerce').dt.tz_localize(None)
        df = df.set_index("date")
    elif isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    return df.sort_index()

def clean_nans(obj):
    if isinstance(obj, float): return None if math.isnan(obj) or math.isinf(obj) else obj
    elif isinstance(obj, dict): return {k: clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [clean_nans(i) for i in obj]
    return obj

# 🌟 致命 BUG 修復：加入讀取與合併邏輯，避免同一個 Bucket 被覆蓋洗掉
def save_payload_and_manifest(bucket_name, dataset_name, frequency, source_list, tables_dict, wrap_tableset=True):
    bucket_dir = BASE_DIR / bucket_name
    bucket_dir.mkdir(parents=True, exist_ok=True)
    payload_file = bucket_dir / f"{bucket_name}_payload.json"

    # 1) 如果檔案已經存在，讀取它來「合併」，不要建立全新的
    if payload_file.exists():
        try:
            with open(payload_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except:
            payload = None
    else:
        payload = None

    if not isinstance(payload, dict):
        payload = {
            "schema_version": "bundle/v1", "run_id": RUN_ID, "asof": asof_str,
            "frequency": frequency, "market": "TW", "source": [], "tables": {}
        }

    # 2) 更新資料來源清單
    src_set = set(payload.get("source", []))
    src_set.update(source_list)
    payload["source"] = sorted(list(src_set))

    # 3) 合併新的 table 資料
    for sid, tjson in tables_dict.items():
        if wrap_tableset:
            if sid not in payload["tables"] or payload["tables"][sid].get("schema_version") != "tableset/v1":
                # 保留該股票舊有的 items
                old_items = payload["tables"].get(sid, {}).get("items", {}) if payload["tables"].get(sid, {}).get("schema_version") == "tableset/v1" else {}
                payload["tables"][sid] = {"schema_version": "tableset/v1", "items": old_items}
            payload["tables"][sid]["items"][dataset_name] = tjson
        else:
            payload["tables"][sid] = tjson

    with open(payload_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def get_season_str(dt):
    return f"{dt.year}Q{(dt.month-1)//3 + 1}"

def safe_num(df, col):
    return pd.to_numeric(df.get(col, pd.NA), errors="coerce")

# ==========================================
# 📊 3. 資料處理模組
# ==========================================

def process_monthly_revenue(symbols):
    print("\n[模組 1] 處理月營收...")
    tables = {}
    start_dt = (asof_date - relativedelta(years=4)).strftime('%Y-%m-%d')
    for sym in symbols:
        sid = sym.split('.')[0]
        ticker = sym
        sname = get_zh_name(sid)

        empty_tmpl = {
            "schema_version": "table/v1", "dataset": "monthly_revenue",
            "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "M", "source": ["FinMind"],
            "fetch_range": {"start": start_dt, "end": asof_str},
            "units": {"rev_m": "TWD million", "yoy": "%", "mom": "%", "cum_yoy": "%"},
            "columns": [
                {"key": "ym", "label": "年/月", "type": "string"}, {"key": "rev_m", "label": "月營收(百萬)", "type": "number"},
                {"key": "yoy", "label": "YoY%", "type": "number"}, {"key": "mom", "label": "MoM%", "type": "number"},
                {"key": "cum_yoy", "label": "累計YoY%", "type": "number"}
            ],
            "rows": [], "notes": ["顯示近24個月", "YoY=12期差分", "累計YoY=年度累計營收做12期差分"],
            "name": sname, "stock_name": sname
        }

        try:
            df = dl.taiwan_stock_month_revenue(stock_id=sid, start_date=start_dt, end_date=asof_str)
            if df is None or df.empty:
                tables[sid] = empty_tmpl
                continue
            df['revenue'] = df['revenue'].astype(float)
            df['DateObj'] = pd.to_datetime(df['date'])
            df = df.sort_values('DateObj')
            df['月營收(百萬)'] = df['revenue'] / 1_000_000
            df['MoM%'] = df['revenue'].pct_change(periods=1) * 100
            df['YoY%'] = df['revenue'].pct_change(periods=12) * 100
            df['CumSum'] = df.groupby('revenue_year')['revenue'].cumsum()
            df['累計YoY%'] = df['CumSum'].pct_change(periods=12) * 100

            df_display = df.tail(24).sort_values('DateObj', ascending=False)
            rows = [{"ym": f"{r.revenue_year}/{r.revenue_month}", "rev_m": r['月營收(百萬)'], "yoy": r['YoY%'], "mom": r['MoM%'], "cum_yoy": r['累計YoY%']} for _, r in df_display.iterrows()]

            filled_tmpl = dict(empty_tmpl)
            filled_tmpl["rows"] = clean_nans(rows)
            tables[sid] = filled_tmpl
            print(f"  ✅ {sid} 月營收完成")
        except Exception as e:
            tables[sid] = empty_tmpl
            print(f"  ❌ {sid} 失敗: {e}")
        time.sleep(SLEEP_SEC)
    save_payload_and_manifest("monthly", "monthly_revenue", "M", ["FinMind"], tables, wrap_tableset=False)

def process_quarterly_data(symbols):
    print("\n[模組 2] 處理季報六大指標(含現金流、結構、KPI)...")
    buckets = {
        "profitability_quarterly": {}, "cashflow_quarterly": {},
        "earnings_quality_quarterly": {}, "kpi_mix_quarterly": {},
        "structure_stability_quarterly": {}, "pe_precise_quarterly": {}
    }

    # 🎯 關鍵修正 1：將季報基準拉長至 4 年，確保 TTM EPS 有足夠的基期算滿 8 季
    start_dt = (asof_date - relativedelta(years=4)).strftime('%Y-%m-%d')

    for sym in symbols:
        sid = sym.split('.')[0]
        ticker = sym
        sname = get_zh_name(sid)

        # --- 1. FinMind 財報 (Profitability) ---
        prof_tmpl = {
            "schema_version": "table/v1", "dataset": "profitability_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["FinMind"],
            "fetch_range": {"start": start_dt, "end": asof_str},
            "units": {"gross_margin_pct": "%", "net_margin_pct": "%", "eps": "TWD", "eps_ytd": "TWD", "opm_pct": "%"},
            "columns": [
                {"key": "period", "label": "日期", "type": "string"}, {"key": "gross_margin_pct", "label": "毛利(%)", "type": "number"},
                {"key": "net_margin_pct", "label": "淨利(%)", "type": "number"}, {"key": "eps", "label": "EPS", "type": "number"},
                {"key": "eps_ytd", "label": "累計EPS", "type": "number"}, {"key": "opm_pct", "label": "OPM(%)", "type": "number"}
            ],
            "rows": [], "notes": ["顯示最近8季", "毛利(%)=GrossProfit/Revenue*100（Revenue=0 → null）", "淨利(%)=NetIncome/Revenue*100（NetIncome 欄位使用多候選匹配；Revenue=0 → null）", "OPM(%)=OperatingIncome/Revenue*100（缺欄位或 Revenue=0 → null）", "累計EPS=同一年內 EPS 累加"],
            "name": sname, "stock_name": sname
        }

        df_pivot_fm = pd.DataFrame() # 保留原始含 4 年資料的完整 Dataframe 給 PE 估值使用
        try:
            df = dl.taiwan_stock_financial_statement(stock_id=sid, start_date=start_dt, end_date=asof_str)
            if df is not None and not df.empty:
                df["type"] = df["type"].astype(str).str.strip()
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                df_pivot = df.pivot_table(index="date", columns="type", values="value", aggfunc="first")
                df_pivot.index = pd.to_datetime(df_pivot.index)
                df_pivot = df_pivot.sort_index()

                df_pivot_fm = df_pivot.copy() # 備份原始長度的資料表

                rev_col = "Revenue" if "Revenue" in df_pivot.columns else "OperatingRevenue" if "OperatingRevenue" in df_pivot.columns else None
                if rev_col and "EPS" in df_pivot.columns:
                    df_pivot["Year"] = df_pivot.index.year
                    df_pivot["Quarter"] = df_pivot.index.month.map(lambda m: (m - 1) // 3 + 1)
                    df_pivot["period"] = df_pivot["Year"].astype(str) + "Q" + df_pivot["Quarter"].astype(str)

                    safe_rev = df_pivot[rev_col].where((df_pivot[rev_col].notna()) & (df_pivot[rev_col] != 0), pd.NA)
                    df_pivot["gross_margin_pct"] = (df_pivot.get("GrossProfit", 0) / safe_rev) * 100
                    ni_candidates = ["NetIncomeAttributableToOwnersOfTheParent", "NetIncomeLossAttributableToOwnersOfTheParent", "ProfitAfterIncomeTax", "NetIncome", "PreTaxIncome"]
                    ni_col = next((c for c in ni_candidates if c in df_pivot.columns), None)
                    df_pivot["net_margin_pct"] = (df_pivot[ni_col] / safe_rev * 100) if ni_col else pd.NA
                    df_pivot["opm_pct"] = (df_pivot.get("OperatingIncome", 0) / safe_rev * 100)
                    df_pivot["eps"] = df_pivot["EPS"]
                    df_pivot["eps_ytd"] = df_pivot.groupby("Year")["EPS"].cumsum()

                    # 對於獲利報表，我們僅裁切展示最新的 8 季
                    df_fin = df_pivot.drop_duplicates(subset=["period"], keep="last").tail(8).sort_index(ascending=False)
                    rows = df_fin[["period", "gross_margin_pct", "net_margin_pct", "eps", "eps_ytd", "opm_pct"]].to_dict('records')
                    prof_tmpl["rows"] = clean_nans(rows)
        except: pass
        buckets["profitability_quarterly"][sid] = prof_tmpl

        # --- 2. YahooQuery 財報 (Cashflow, EQ, KPI, Structure, PE) ---
        cf_tmpl = {"schema_version": "table/v1", "dataset": "cashflow_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["yahooquery"], "units": {"ocf_m": "TWD million", "capex_m": "TWD million", "fcf_m": "TWD million"}, "columns": [{"key": "period", "label": "日期", "type": "string"}, {"key": "ocf_m", "label": "營運現金流(百萬)", "type": "number"}, {"key": "capex_m", "label": "資本支出(百萬)", "type": "number"}, {"key": "fcf_m", "label": "自由現金流(百萬)", "type": "number"}], "rows": [], "notes": ["顯示最近5季（以 asOfDate 轉換 YYYYQn）", "優先 periodType=3M（單季）避免累計干擾", "CAPEX 通常為負值（支出），屬正常現象", "若 Yahoo 缺最新一季（例如只到Q2），本表會自動改顯示『最新可得的5季』"], "flags": {"missing_latest_quarter_possible": True}, "name": sname, "stock_name": sname}
        eq_tmpl = {"schema_version": "table/v1", "dataset": "earnings_quality_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["yahooquery"], "units": {"net_income_m": "TWD million", "ocf_m": "TWD million", "cash_ratio_pct": "%"}, "columns": [{"key": "period", "label": "日期", "type": "string"}, {"key": "net_income_m", "label": "稅後淨利(百萬)", "type": "number"}, {"key": "ocf_m", "label": "營運現金流(百萬)", "type": "number"}, {"key": "cash_ratio_pct", "label": "獲利含金量(%)", "type": "number"}], "rows": [], "notes": ["顯示最近5季（以 asOfDate 轉換為 YYYYQn）", "獲利含金量(%) = 營運現金流 / 稅後淨利 × 100；淨利為 0 或 NaN 時回傳 null"], "name": sname, "stock_name": sname}
        kpi_tmpl = {"schema_version": "table/v1", "dataset": "kpi_mix_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["yahooquery"], "units": {"opm_pct": "%", "roe_pct": "%", "roa_pct": "%", "fcf_100m": "TWD 100 million", "net_income_100m": "TWD 100 million"}, "columns": [{"key": "period", "label": "日期", "type": "string"}, {"key": "opm_pct", "label": "OPM(%)", "type": "number"}, {"key": "roe_pct", "label": "ROE(%)", "type": "number"}, {"key": "roa_pct", "label": "ROA(%)", "type": "number"}, {"key": "fcf_100m", "label": "FCF(億)", "type": "number"}, {"key": "net_income_100m", "label": "淨利(億)", "type": "number"}], "rows": [], "notes": ["顯示最近5季（以 asOfDate 轉換為 YYYYQn）", "優先 periodType=3M（單季）避免累計干擾", "OPM=OperatingIncome/TotalRevenue；ROE=NetIncome/StockholdersEquity；ROA=NetIncome/TotalAssets（分母為0則為 null）", "FCF、淨利 單位：億（原始值 / 100,000,000）"], "name": sname, "stock_name": sname}
        str_tmpl = {"schema_version": "table/v1", "dataset": "structure_stability_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["yahooquery"], "units": {"total_assets_b": "TWD hundred-million", "equity_b": "TWD hundred-million", "debt_ratio_pct": "%", "current_liab_b": "TWD hundred-million", "doi_days": "days"}, "columns": [{"key": "period", "label": "日期", "type": "string"}, {"key": "total_assets_b", "label": "總資產(億)", "type": "number"}, {"key": "equity_b", "label": "股東權益(億)", "type": "number"}, {"key": "debt_ratio_pct", "label": "負債比(%)", "type": "number"}, {"key": "current_liab_b", "label": "流動負債(億)", "type": "number"}, {"key": "doi_days", "label": "DoI(日)", "type": "number"}], "rows": [], "notes": ["顯示最近5季（以 asOfDate 轉換為 YYYYQn）", "嚴格使用 periodType=3M（單季）避免累計干擾", "負債比(%) = TotalLiabilitiesNetMinorityInterest / TotalAssets × 100", "DoI(日) = Inventory / CostOfRevenue × 91.25（單季天數）；CostOfRevenue=0/缺值 → null"], "name": sname, "stock_name": sname}
        pe_tmpl = {"schema_version": "table/v1", "dataset": "pe_precise_quarterly", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Q", "source": ["FinMind", "yahooquery"], "fetch_range": {"start": start_dt, "end": asof_str}, "units": {"avg_close_3d": "TWD", "ttm_eps": "TWD", "pe": "x"}, "columns": [{"key": "period", "label": "日期", "type": "string"}, {"key": "deadline", "label": "公告截止日", "type": "string"}, {"key": "price_basis", "label": "價格基準", "type": "string"}, {"key": "avg_close_3d", "label": "公告後3日均價", "type": "number"}, {"key": "ttm_eps", "label": "TTM EPS(加總)", "type": "number"}, {"key": "pe", "label": "PE", "type": "number"}], "rows": [], "notes": ["EPS 來源：FinMind 財報單季 EPS", "TTM EPS(加總)=近4季 EPS 加總", "股價：公告截止日後前3個交易日收盤均價", "若公告截止日尚未到(asof之前)：改用 asof 往前最近3個交易日均價暫代"], "name": sname, "stock_name": sname}

        try:
            yq = Ticker(ticker, asynchronous=False)
            df_all = yq.all_financial_data(frequency="q")
            if isinstance(df_all, pd.DataFrame) and not df_all.empty:
                df_all = df_all.reset_index()
                if "periodType" in df_all.columns: df_all = df_all[df_all["periodType"] == "3M"].copy()
                df_all["asOfDate"] = pd.to_datetime(df_all["asOfDate"], errors="coerce")
                df_all = df_all.dropna(subset=["asOfDate"]).sort_values("asOfDate", ascending=False)
                df_all["period"] = df_all["asOfDate"].dt.year.astype(str) + "Q" + (((df_all["asOfDate"].dt.month - 1)//3) + 1).astype(str)
                df_all = df_all.drop_duplicates(subset=["period"], keep="first").head(5)

                df_all["ocf_m"] = safe_num(df_all, "OperatingCashFlow") / 1_000_000
                df_all["capex_m"] = safe_num(df_all, "CapitalExpenditure") / 1_000_000
                df_all["fcf_m"] = safe_num(df_all, "FreeCashFlow") / 1_000_000
                df_all["net_income_m"] = safe_num(df_all, "NetIncome") / 1_000_000
                df_all["cash_ratio_pct"] = np.where(df_all["net_income_m"].notna() & (df_all["net_income_m"]!=0), (df_all["ocf_m"]/df_all["net_income_m"])*100, np.nan)

                rev = safe_num(df_all, "TotalRevenue").replace(0, pd.NA)
                eq = safe_num(df_all, "StockholdersEquity").replace(0, pd.NA)
                ta = safe_num(df_all, "TotalAssets").replace(0, pd.NA)
                ni = safe_num(df_all, "NetIncome")
                opi = safe_num(df_all, "OperatingIncome")

                df_all["opm_pct"] = (opi / rev) * 100
                df_all["roe_pct"] = (ni / eq) * 100
                df_all["roa_pct"] = (ni / ta) * 100
                df_all["fcf_100m"] = safe_num(df_all, "FreeCashFlow") / 100_000_000
                df_all["net_income_100m"] = ni / 100_000_000

                df_all["total_assets_b"] = ta / 100_000_000
                df_all["equity_b"] = eq / 100_000_000
                df_all["current_liab_b"] = safe_num(df_all, "CurrentLiabilities") / 100_000_000
                total_liab = safe_num(df_all, "TotalLiabilitiesNetMinorityInterest")
                df_all["debt_ratio_pct"] = (total_liab / ta) * 100
                cogs = safe_num(df_all, "CostOfRevenue").replace(0, pd.NA)
                df_all["doi_days"] = (safe_num(df_all, "Inventory") / cogs) * 91.25

                cf_tmpl["rows"] = clean_nans(df_all[["period", "ocf_m", "capex_m", "fcf_m"]].to_dict('records'))
                eq_tmpl["rows"] = clean_nans(df_all[["period", "net_income_m", "ocf_m", "cash_ratio_pct"]].to_dict('records'))
                kpi_tmpl["rows"] = clean_nans(df_all[["period", "opm_pct", "roe_pct", "roa_pct", "fcf_100m", "net_income_100m"]].to_dict('records'))
                str_tmpl["rows"] = clean_nans(df_all[["period", "total_assets_b", "equity_b", "debt_ratio_pct", "current_liab_b", "doi_days"]].to_dict('records'))

                # 🎯 關鍵修正 2：PE Precise 計算直接取用 4 年期的原始 df_pivot_fm，保證不會遺漏舊季度
                def get_dl(q):
                    y, qn = int(q[:4]), q[-2:]
                    return f"{y}-05-15" if qn=="Q1" else f"{y}-08-14" if qn=="Q2" else f"{y}-11-14" if qn=="Q3" else f"{y+1}-03-31"

                pe_rows = []
                if not df_pivot_fm.empty and "EPS" in df_pivot_fm.columns:
                    df_pivot_fm["Year"] = df_pivot_fm.index.year
                    df_pivot_fm["Quarter"] = df_pivot_fm.index.month.map(lambda m: (m - 1) // 3 + 1)
                    df_pivot_fm["period"] = df_pivot_fm["Year"].astype(str) + "Q" + df_pivot_fm["Quarter"].astype(str)

                    eps_df = df_pivot_fm.drop_duplicates(subset=["period"], keep="last").sort_values("period", ascending=True).reset_index(drop=True)
                    for i in range(len(eps_df)-3):
                        q_str = eps_df.loc[i+3, "period"]
                        ttm_eps = float(eps_df.loc[i:i+3, "EPS"].sum())
                        dd = get_dl(q_str)

                        if pd.to_datetime(dd) <= pd.to_datetime(asof_str):
                            end_d = (pd.to_datetime(dd) + timedelta(days=14)).strftime("%Y-%m-%d")
                            px = force_tz_naive(yq.history(start=dd, end=end_d))
                            px = px.dropna(subset=["close"]).head(3) if isinstance(px, pd.DataFrame) and not px.empty else pd.DataFrame()
                            basis = "公告後3日"
                        else:
                            start_d = (pd.to_datetime(asof_str) - timedelta(days=30)).strftime("%Y-%m-%d")
                            px = force_tz_naive(yq.history(start=start_d, end=(pd.to_datetime(asof_str)+timedelta(days=1)).strftime("%Y-%m-%d")))
                            px = px.dropna(subset=["close"]).tail(3) if isinstance(px, pd.DataFrame) and not px.empty else pd.DataFrame()
                            basis = "asof往前3日(暫代)"

                        avg_p = float(px["close"].mean()) if not px.empty and "close" in px.columns else None
                        pe = (avg_p / ttm_eps) if avg_p and ttm_eps > 0 else None

                        # 即便 PE 為 null，依然無條件寫入該行資訊！
                        pe_rows.append({"period": q_str, "deadline": dd, "price_basis": basis, "avg_close_3d": avg_p, "ttm_eps": ttm_eps, "pe": pe})

                # 最終只截取最新的 8 季進行輸出
                pe_tmpl["rows"] = clean_nans(pe_rows[::-1][:8])

        except Exception as e:
            print(f"  ⚠️ {sid} YahooQuery資料異常: {e}")

        buckets["profitability_quarterly"][sid] = prof_tmpl
        buckets["cashflow_quarterly"][sid] = cf_tmpl
        buckets["earnings_quality_quarterly"][sid] = eq_tmpl
        buckets["kpi_mix_quarterly"][sid] = kpi_tmpl
        buckets["structure_stability_quarterly"][sid] = str_tmpl
        buckets["pe_precise_quarterly"][sid] = pe_tmpl

        print(f"  ✅ {sid} 季報六大表處理完成")
        time.sleep(SLEEP_SEC)

    for bk_name, t_dict in buckets.items():
        save_payload_and_manifest("quarterly", bk_name, "Q", ["FinMind", "yahooquery"], t_dict, wrap_tableset=True)

def process_capital_and_chips(symbols):
    print("\n[模組 3] 處理股本與法人籌碼...")
    cap_tables, chip_tables = {}, {}
    start_dt = (asof_date - relativedelta(days=LOOKBACK_DAYS_CHIP)).strftime('%Y-%m-%d')

    for sym in symbols:
        sid = sym.split('.')[0]
        ticker = sym
        sname = get_zh_name(sid)
        shares_lots = 1e12

        cap_tmpl = {"schema_version": "table/v1", "dataset": "capital_latest", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "IRREGULAR", "source": ["FinMind"], "units": {"capital_value_raw": "raw", "par_value": "TWD", "shares_lots": "lots(1,000 shares)"}, "columns": [{"key": "report_date", "label": "報表日期", "type": "string"}, {"key": "capital_value_raw", "label": "CapitalStock原始值", "type": "number"}, {"key": "par_value", "label": "面額(推定)", "type": "number"}, {"key": "shares_lots", "label": "總發行張數(推定)", "type": "number"}, {"key": "method", "label": "換算方法", "type": "string"}], "rows": [], "notes": ["CapitalStock 口徑可能為股數或金額；本流程用啟發式推定。", "若你確認 FinMind 對你所有標的都是固定口徑，可將推定邏輯改為固定計算。"], "name": sname, "stock_name": sname}
        chip_tmpl = {"schema_version": "table/v1", "dataset": "chips_15d", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "D", "source": ["FinMind"], "fetch_range": {"start": start_dt, "end": asof_str}, "units": {"foreign_lots": "lots", "it_lots": "lots", "dealer_lots": "lots", "sum_lots": "lots", "pct_float": "%"}, "columns": [{"key": "date", "label": "日期", "type": "string"}, {"key": "foreign_lots", "label": "外資(張)", "type": "number"}, {"key": "it_lots", "label": "投信(張)", "type": "number"}, {"key": "dealer_lots", "label": "自營商(張)", "type": "number"}, {"key": "sum_lots", "label": "三大法人合計(張)", "type": "number"}, {"key": "pct_float", "label": "佔股本(%)", "type": "number"}], "rows": [], "notes": [f"顯示最近 {SHOW_DAYS_CHIP} 日（以基準日 <= {asof_str} 截切）", "外資/投信/自營商：買賣超張數（buy-sell）/1000", "自營商(張)=Dealer_self + Dealer_Hedging", "佔股本(%) = 三大法人合計(張) / 總發行張數 * 100"], "name": sname, "stock_name": sname}

        try:
            df_bs = dl.get_data(dataset="TaiwanStockBalanceSheet", data_id=sid, start_date="2024-01-01")
            if df_bs is not None and not df_bs.empty:
                df_bs["type"] = df_bs["type"].astype(str).str.strip()
                df_cap = df_bs[df_bs["type"] == "CapitalStock"].dropna(subset=["value"]).sort_values("date")
                if not df_cap.empty:
                    raw_val = float(df_cap.iloc[-1]["value"])
                    shares_lots = int(round(raw_val / 1000)) if (raw_val/1000 >= 1000 and abs((raw_val/1000)-round(raw_val/1000)) < 1e-3) else int(round(raw_val/10/1000))
                    cap_tmpl["rows"] = [{"report_date": str(df_cap.iloc[-1]["date"])[:10], "capital_value_raw": raw_val, "par_value": 10, "shares_lots": shares_lots, "method": "股數口徑：CapitalStock/1000" if shares_lots*1000==raw_val else "金額口徑"}]
        except: pass
        cap_tables[sid] = cap_tmpl

        try:
            df_inst = dl.get_data(dataset="TaiwanStockInstitutionalInvestorsBuySell", data_id=sid, start_date=start_dt, end_date=asof_str)
            if df_inst is not None and not df_inst.empty:
                for alt in ["trading_date", "trade_date", "datetime", "data_date"]:
                    if alt in df_inst.columns: df_inst = df_inst.rename(columns={alt: "date"})
                df_inst["diff_lots"] = (pd.to_numeric(df_inst["buy"], errors="coerce") - pd.to_numeric(df_inst["sell"], errors="coerce")) / 1000
                df_p = df_inst.pivot_table(index="date", columns="name", values="diff_lots", aggfunc="sum").fillna(0)
                df_p["foreign_lots"] = df_p.get("Foreign_Investor", 0)
                df_p["it_lots"] = df_p.get("Investment_Trust", 0)
                df_p["dealer_lots"] = df_p.get("Dealer_self", 0) + df_p.get("Dealer_Hedging", 0)
                df_p["sum_lots"] = df_p["foreign_lots"] + df_p["it_lots"] + df_p["dealer_lots"]
                df_p["pct_float"] = (df_p["sum_lots"] / shares_lots) * 100

                df_p.index = pd.to_datetime(df_p.index)
                df_show = df_p[df_p.index <= pd.to_datetime(asof_str)].tail(SHOW_DAYS_CHIP).sort_index(ascending=False).reset_index()
                df_show["date"] = df_show["date"].dt.strftime("%Y-%m-%d")
                rows = df_show[["date", "foreign_lots", "it_lots", "dealer_lots", "sum_lots", "pct_float"]].to_dict('records')
                chip_tmpl["rows"] = clean_nans(rows)
        except: pass
        chip_tables[sid] = chip_tmpl

        print(f"  ✅ {sid} 股本籌碼處理完成")
        time.sleep(SLEEP_SEC)

    save_payload_and_manifest("capital", "capital_latest", "IRREGULAR", ["FinMind"], cap_tables, wrap_tableset=True)
    save_payload_and_manifest("daily", "chips_15d", "D", ["FinMind"], chip_tables, wrap_tableset=True)

def process_realtime_quotes(symbols):
    print("\n[模組 4] 抓取即時報價...")
    tables = {}
    yq = Ticker(symbols, asynchronous=True)
    price_map = yq.price
    for sym in symbols:
        sid = sym.split('.')[0]
        ticker = sym
        sname = get_zh_name(sid)
        data = price_map.get(sym, {})

        rt_tmpl = {
            "schema_version": "table/v1", "dataset": "live_quote", "stock_id": sid, "ticker": ticker, "name_zh": sname, "asof": now.strftime("%Y-%m-%d %H:%M:%S"), "frequency": "RT", "source": ["yahooquery"],
            "rows": [], "notes": ["昨收(固定)=regularMarketPreviousClose（不受試撮污染）", "試撮價=preMarketPrice（若無則空）", "漲跌(%)、試撮漲跌(%)：皆以昨收(固定)自行計算"], "name": sname, "stock_name": sname
        }

        if isinstance(data, dict) and data:
            prev = data.get("regularMarketPreviousClose")
            now_p = data.get("regularMarketPrice")
            pct = ((now_p/prev)-1)*100 if prev and now_p else None
            row = {
                "日期時間": now.strftime("%Y-%m-%d %H:%M:%S"), "股票": sid, "名稱": sname,
                "市場狀態": data.get("marketState"), "昨收(固定)": prev, "現價": now_p, "漲跌(%)": pct,
                "試撮價": data.get("preMarketPrice"), "試撮漲跌(%)": None,
                "開盤": data.get("regularMarketOpen"), "最高": data.get("regularMarketDayHigh"),
                "最低": data.get("regularMarketDayLow"), "成交量(股)": data.get("regularMarketVolume")
            }
            rt_tmpl["rows"] = [clean_nans(row)]
            print(f"  ✅ {sid} 報價完成")
        else:
            print(f"  ⚠️ {sid} 無即時報價")

        tables[sid] = rt_tmpl
    save_payload_and_manifest("realtime", "live_quote", "RT", ["yahooquery"], tables, wrap_tableset=True)

def process_returns_and_cscore(symbols):
    print("\n[模組 5] 處理年度報酬與 C-Score (獨立拉長基期)...")
    ret_tables, c_tables = {}, {}

    # 🎯 關鍵修正 3：C-Score 獨立提取 5 年財報，不受前面 8 季的限制
    start_5y = (asof_date - relativedelta(years=5)).strftime('%Y-%m-%d')

    for sym in symbols:
        sid = sym.split('.')[0]
        ticker = sym
        sname = get_zh_name(sid)

        # --- 年度報酬 ---
        ret_tmpl = {"schema_version": "table/v1", "dataset": "annual_returns", "stock_id": sid, "ticker": ticker, "asof": asof_str, "frequency": "Y", "source": ["yahooquery"], "units": {"return_pct": "%"}, "columns": [{"key": "year", "label": "年度", "type": "number"}, {"key": "return_pct", "label": "含息報酬率(%)", "type": "number"}, {"key": "period", "label": "計算區間", "type": "string"}], "rows": [], "name": sname, "stock_name": sname}
        try:
            hist = force_tz_naive(Ticker(sym).history(period="10y", interval="1d"))
            annual_rets = []
            if not hist.empty and "adjclose" in hist.columns:
                hist = hist.dropna(subset=["adjclose"])
                for i in range(5):
                    yr = asof_date.year - i
                    start_dt, end_dt = pd.Timestamp(f"{yr}-01-01"), min(pd.Timestamp(asof_date), pd.Timestamp(f"{yr}-12-31"))
                    try:
                        idx_s, idx_e = hist.index.searchsorted(start_dt), hist.index.searchsorted(end_dt, side='right') - 1
                        if idx_s < len(hist) and idx_e >= idx_s:
                            ret = (hist["adjclose"].iloc[idx_e] / hist["adjclose"].iloc[idx_s] - 1) * 100
                            annual_rets.append({"year": yr, "return_pct": ret, "period": f"{hist.index[idx_s].strftime('%m/%d')}~{hist.index[idx_e].strftime('%m/%d')}"})
                    except: pass
                ret_tmpl["rows"] = clean_nans(annual_rets)
        except: pass
        ret_tables[sid] = ret_tmpl

        # --- C-Score 獨立 5 年回推 ---
        c_tmpl = {"schema_version": "table/v1", "dataset": "structure_c_score", "stock_id": sid, "ticker": ticker, "asof": asof_str, "c_score": 1.0, "benchmark_pe": 15.0, "source": ["FinMind", "yahooquery"], "columns": [{"key": "period", "label": "季度"}, {"key": "avg_price", "label": "季均價"}, {"key": "eps", "label": "EPS"}, {"key": "revenue_q", "label": "季營收(億)"}, {"key": "pe_ratio", "label": "當季PE"}, {"key": "rev_growth", "label": "營收成長%"}, {"key": "eps_growth", "label": "EPS成長%"}, {"key": "rt_weight", "label": "Rt係數"}, {"key": "deviation", "label": "偏離度"}], "rows": [], "notes": ["C值 = 平均(偏離度 * Rt係數)", "基準PE = 過去5年PE中位數", "偏離度 = 季均價 / (EPS * 基準PE)", "Rt係數：營收成長>EPS成長給予溢價，反之折價"], "name": sname, "stock_name": sname}

        try:
            # 1. 抓取完整的 5 年 EPS
            df_fin = dl.taiwan_stock_financial_statement(stock_id=sid, start_date=start_5y, end_date=asof_str)
            if df_fin is not None and not df_fin.empty:
                df_eps = df_fin[df_fin["type"] == "EPS"].copy()
                df_eps["value"] = pd.to_numeric(df_eps["value"], errors="coerce")
                df_eps["date"] = pd.to_datetime(df_eps["date"])
                df_eps["季度"] = df_eps["date"].apply(get_season_str)
                df_eps = df_eps.sort_values("date").drop_duplicates(subset=["季度"], keep="last").set_index("季度")[["value"]]
                df_eps.columns = ["EPS"]

                # 2. 抓取完整的 5 年營收
                df_rev = dl.taiwan_stock_month_revenue(stock_id=sid, start_date=start_5y, end_date=asof_str)
                df_rev_q = pd.DataFrame()
                if df_rev is not None and not df_rev.empty:
                    df_rev["revenue"] = pd.to_numeric(df_rev["revenue"], errors="coerce")
                    df_rev["date"] = pd.to_datetime(df_rev["date"])
                    df_rev["季度"] = df_rev["date"].apply(get_season_str)
                    df_rev_q = df_rev.groupby("季度")["revenue"].sum() / 100_000_000

                # 3. 抓取完整的 5 年季均價
                df_price_q = pd.DataFrame()
                try:
                    df_price = force_tz_naive(Ticker(sym).history(period="5y", interval="1d"))
                    if not df_price.empty and "close" in df_price.columns:
                        df_price = df_price.reset_index()
                        df_price["date"] = pd.to_datetime(df_price["date"])
                        df_price["季度"] = df_price["date"].apply(get_season_str)
                        df_price_q = df_price.groupby("季度")["close"].mean()
                except: pass

                # 🎯 關鍵修正 4：將 Inner Join (預設 merge) 改為 Left Join 以 EPS 為主體
                # 這樣即使 2024Q3/Q4 報價或營收稍微落後，EPS 也不會被剔除！
                df_main = df_eps.copy()
                if not df_rev_q.empty:
                    df_main = df_main.join(df_rev_q.rename("季營收(億)"), how="left")
                else:
                    df_main["季營收(億)"] = np.nan

                if not df_price_q.empty:
                    df_main = df_main.join(df_price_q.rename("季均價"), how="left")
                else:
                    df_main["季均價"] = np.nan

                df_main = df_main.sort_index()

                if len(df_main) >= 1:
                    df_main["營收成長%"] = df_main["季營收(億)"].pct_change(4) * 100
                    df_main["EPS成長%"] = df_main["EPS"].pct_change(4) * 100
                    df_main["當季PE"] = df_main["季均價"] / df_main["EPS"]

                    valid_pe = df_main[(df_main["EPS"] > 0) & (df_main["當季PE"] < 200) & (df_main["當季PE"] > 0)]["當季PE"]
                    bench_pe = valid_pe.median() if not valid_pe.empty else 15.0

                    def calc_rt(row):
                        if pd.isna(row.get("營收成長%")) or pd.isna(row.get("EPS成長%")): return 1.0
                        if row["營收成長%"] > row["EPS成長%"]: return REVENUE_WEIGHT_PREMIUM
                        if row["營收成長%"] < row["EPS成長%"]: return REVENUE_WEIGHT_DISCOUNT
                        return 1.0

                    df_main["Rt係數"] = df_main.apply(calc_rt, axis=1)
                    df_main["理論價"] = df_main["EPS"] * bench_pe
                    df_main["偏離度"] = df_main["季均價"] / df_main["理論價"]

                    # --- 嚴格還原 Colab 的 C-Score 濾網邏輯 ---
                    mask_valid = (df_main["季營收(億)"].pct_change(4) > -0.2) & (df_main["EPS"] > 0)
                    df_calc = df_main[mask_valid].copy()

                    if df_calc.empty:
                        c_val = 1.0
                    else:
                        df_calc["加權偏離"] = df_calc["偏離度"] * df_calc["Rt係數"]
                        c_val = df_calc["加權偏離"].mean()

                    rows = []
                    for _, r in df_main.sort_index(ascending=False).reset_index().iterrows():
                        rows.append({"period": r["季度"], "avg_price": r.get("季均價"), "eps": r.get("EPS"), "revenue_q": r.get("季營收(億)"), "pe_ratio": r.get("當季PE"), "rev_growth": r.get("營收成長%"), "eps_growth": r.get("EPS成長%"), "rt_weight": r.get("Rt係數"), "deviation": r.get("偏離度")})

                    c_tmpl["c_score"] = float(c_val)
                    c_tmpl["benchmark_pe"] = float(bench_pe)
                    c_tmpl["rows"] = clean_nans(rows)
        except Exception as e: print(f"    C-Score error: {e}")
        c_tables[sid] = c_tmpl

        print(f"  ✅ {sid} 報酬與C-Score完成")
        time.sleep(SLEEP_SEC)

    save_payload_and_manifest("returns", "annual_returns", "Y", ["yahooquery"], ret_tables, wrap_tableset=True)
    save_payload_and_manifest("structure_c", "structure_c_score", "Q", ["FinMind", "yahooquery"], c_tables, wrap_tableset=True)

def process_klines(symbols):
    print("\n[模組 6] 產生 K 線特徵...")
    k_dir = Path("./kline_out") / RUN_ID
    for sym in symbols:
        sid = sym.split('.')[0]
        try:
            df = force_tz_naive(Ticker(sym).history(period="800d", interval="1d"))
            if not df.empty and "close" in df.columns:
                df["MA5"] = df["close"].rolling(5).mean()
                df["MA20"] = df["close"].rolling(20).mean()
                df["MA60"] = df["close"].rolling(60).mean()
                mid = df["close"].rolling(20).mean()
                std = df["close"].rolling(20).std(ddof=0)
                df["bb_mid"] = mid
                df["bb_up"] = mid + 2*std
                df["bb_dn"] = mid - 2*std
                ema_f = df["close"].ewm(span=12, adjust=False).mean()
                ema_s = df["close"].ewm(span=26, adjust=False).mean()
                df["DIF"] = ema_f - ema_s
                df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
                df["HIST"] = (df["DIF"] - df["DEA"]) * 2
                df["volume_lot"] = df["volume"] / 1000

                out_dir = k_dir / sid
                out_dir.mkdir(parents=True, exist_ok=True)
                df_k = df.tail(120).reset_index() # 確保 date 成為 column
                df_k.to_csv(out_dir / "daily.csv", index=False)
                print(f"  ✅ {sid} K線計算完成")
        except Exception as e:
            print(f"  ❌ {sid} K線失敗: {e}")
        time.sleep(SLEEP_SEC)

# ==========================================
# 📦 4. 終極打包模組 (完美匹配 Colab 結構)
# ==========================================
def package_final_export(symbols):
    print("\n[最終模組] 整合 TXT 匯出...")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    SOURCE_MAPPING = {
        "monthly": "1_月營收 (Monthly)", "quarterly": "2_季度財報 (Quarterly)",
        "capital": "3_最新股本 (Capital)", "daily": "4_法人籌碼 (Daily)",
        "realtime": "5_即時報價 (Realtime)", "returns": "6_含息年度報酬率 (Annual Returns)",
        "structure_c": "7_結構校正C值 (C-Score)"
    }

    payloads = {}
    for bucket in SOURCE_MAPPING.keys():
        p_file = BASE_DIR / bucket / f"{bucket}_payload.json"
        if p_file.exists():
            with open(p_file, "r", encoding="utf-8") as f: payloads[bucket] = json.load(f)

    for sym in symbols:
        sid = sym.split('.')[0]
        s_name = get_zh_name(sid)

        merged = {
            "analysis_header": {
                "stock_id": sid, "stock_name": s_name, "name": s_name,
                "full_symbol": sym, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S")
            },
            "details": {},
            "ai_context_support": {"annual_report": None, "official_website": "N/A"}
        }

        try:
            prof = Ticker(sym).asset_profile.get(sym, {})
            if isinstance(prof, dict): merged["ai_context_support"]["official_website"] = prof.get("website", "N/A")
        except: pass

        for bucket, label in SOURCE_MAPPING.items():
            bucket_data = payloads.get(bucket, {})
            stock_data = bucket_data.get("tables", {}).get(sid)
            if stock_data:
                if stock_data.get("schema_version") == "table/v1":
                    stock_data["name"] = stock_data["stock_name"] = s_name
                elif stock_data.get("schema_version") == "tableset/v1":
                    for item_key in stock_data.get("items", {}):
                        stock_data["items"][item_key]["name"] = stock_data["items"][item_key]["stock_name"] = s_name
                merged["details"][label] = stock_data
            else:
                merged["details"][label] = None

        # K線補入
        k_file = Path("./kline_out") / RUN_ID / sid / "daily.csv"
        if k_file.exists():
            df_k = pd.read_csv(k_file)
            merged["details"]["8_K線 (Daily)"] = {"dataset": "kline_daily", "name": s_name, "rows": clean_nans(df_k.where(pd.notnull(df_k), None).to_dict('records'))}
        else:
            merged["details"]["8_K線 (Daily)"] = None

        ts = f"{now.year - 1911}{now.strftime('%m%d')}_{now.strftime('%H%M')}"
        with open(EXPORT_DIR / f"{s_name}_{ts}.txt", "w", encoding="utf-8") as f:
            json.dump(clean_nans(merged), f, ensure_ascii=False, indent=2)

    print(f"🎉 任務全數完成！TXT 檔案位置: {EXPORT_DIR}")


# ==========================================
# 🎯 5. 主程式進入點
# ==========================================
if __name__ == "__main__":
    MY_STOCK_LIST = auto_correct_symbols(INPUT_STOCKS)
    print("="*60)
    print(f"🚀 量化分析流啟動 (基準日: {asof_str})")
    print(f"📂 執行批號: {RUN_ID}")
    print(f"📌 智慧校正執行清單: {MY_STOCK_LIST}")
    print("="*60)

    process_monthly_revenue(MY_STOCK_LIST)
    process_quarterly_data(MY_STOCK_LIST)
    process_capital_and_chips(MY_STOCK_LIST)
    process_realtime_quotes(MY_STOCK_LIST)
    process_returns_and_cscore(MY_STOCK_LIST)
    process_klines(MY_STOCK_LIST)
    package_final_export(MY_STOCK_LIST)