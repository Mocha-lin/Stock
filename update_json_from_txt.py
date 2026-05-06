import os
import json
import glob
from datetime import datetime

def safe_str(val, fmt="%.2f"):
    if val is None: return "0.00"
    try:
        return fmt % val
    except:
        return str(val)

def update_data():
    # 1. 定義路徑
    summary_path = 'stocks_summary.json'
    details_dir = 'details'
    # 搜尋執行結果 (排除 requirements.txt)
    txt_files = glob.glob('stock_pipeline_runs/*/final_txt_export/*.txt') + [f for f in glob.glob('*.txt') if f != 'requirements.txt']
    
    if not txt_files:
        print("未找到任何待更新的 txt 檔案。")
        return

    # 2. 讀取 stocks_summary.json
    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary_data = json.load(f)
    else:
        print(f"錯誤: 找不到 {summary_path}")
        return

    # 建立 ID 到索引的映射，加速查找
    summary_map = {item['id']: i for i, item in enumerate(summary_data)}

    updated_count = 0

    # 3. 處理每一個 txt 檔案
    for txt_path in txt_files:
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            header = report.get('analysis_header', {})
            stock_id = header.get('stock_id')
            generated_at = header.get('generated_at')
            
            if not stock_id:
                continue

            details = report.get('details', {})
            
            # --- A. 取得即時報價資訊 (Realtime) ---
            rt_report = details.get('5_即時報價 (Realtime)', {})
            quote_row = {}
            if rt_report and rt_report.get('items', {}).get('live_quote'):
                quote_row = rt_report['items']['live_quote']['rows'][0]

            price = quote_row.get('現價')
            prev_close = quote_row.get('昨收(固定)')
            change_pct = quote_row.get('漲跌(%)')

            # --- B. 格式化基礎資訊 ---
            price_str = safe_str(price)
            change = (price - prev_close) if price is not None and prev_close is not None else 0
            change_str = safe_str(change)
            change_pct_str = safe_str(change_pct) + "%"

            # --- C. 更新 stocks_summary.json ---
            if stock_id in summary_map:
                idx = summary_map[stock_id]
                summary_data[idx]['basicInfo']['price'] = price_str
                summary_data[idx]['basicInfo']['change'] = change_str
                summary_data[idx]['basicInfo']['changePercent'] = change_pct_str
                summary_data[idx]['lastUpdated'] = generated_at
            
            # --- D. 更新 details/<id>.json ---
            detail_path = os.path.join(details_dir, f"{stock_id}.json")
            if os.path.exists(detail_path):
                with open(detail_path, 'r', encoding='utf-8') as f:
                    detail_data = json.load(f)
                
                # 1. 基礎資訊
                if 'basicInfo' not in detail_data: detail_data['basicInfo'] = {}
                detail_data['basicInfo']['price'] = price_str
                detail_data['basicInfo']['change'] = change_str
                detail_data['basicInfo']['changePercent'] = change_pct_str
                detail_data['lastUpdated'] = generated_at

                if 'financials' not in detail_data: detail_data['financials'] = {}
                fin = detail_data['financials']

                # 2. 月營收 (revenue_trend)
                rev_report = details.get('1_月營收 (Monthly)', {})
                if rev_report and 'rows' in rev_report:
                    fin['revenue_trend'] = [
                        {
                            "month": r.get('ym'),
                            "revenue": safe_str(r.get('rev_m', 0)/100), # 百萬 -> 億 (除以100)
                            "mom": safe_str(r.get('mom')),
                            "yoy": safe_str(r.get('yoy'))
                        } for r in rev_report['rows']
                    ]

                # 3. 季報與財務比率
                q_items = details.get('2_季度財報 (Quarterly)', {}).get('items', {})
                
                # EPS Table (eps_table)
                prof = q_items.get('profitability_quarterly', {})
                if prof and 'rows' in prof:
                    fin['eps_table'] = [
                        {
                            "period": r.get('period'),
                            "gross_margin_pct": r.get('gross_margin_pct'),
                            "net_margin_pct": r.get('net_margin_pct'),
                            "opm_pct": r.get('opm_pct'),
                            "eps": r.get('eps'),
                            "eps_ytd": r.get('eps_ytd'),
                            "gross": r.get('gross_margin_pct'),
                            "net": r.get('net_margin_pct'),
                            "opm": r.get('opm_pct')
                        } for r in prof['rows']
                    ]

                # Cashflow Table (cashflow_table)
                cf = q_items.get('cashflow_quarterly', {})
                if cf and 'rows' in cf:
                    fin['cashflow_table'] = [
                        {
                            "period": r.get('period'),
                            "ocf_m": r.get('ocf_m'),
                            "capex_m": r.get('capex_m'),
                            "fcf_m": r.get('fcf_m'),
                            "ocf": r.get('ocf_m'),
                            "capex": r.get('capex_m'),
                            "fcf": r.get('fcf_m')
                        } for r in cf['rows']
                    ]

                # Quality Table (quality_table)
                eq = q_items.get('earnings_quality_quarterly', {})
                if eq and 'rows' in eq:
                    fin['quality_table'] = [
                        {
                            "period": r.get('period'),
                            "net_income_m": r.get('net_income_m'),
                            "ocf_m": r.get('ocf_m'),
                            "cash_ratio_pct": r.get('cash_ratio_pct'),
                            "net_income": r.get('net_income_m'),
                            "ocf": r.get('ocf_m'),
                            "ratio": r.get('cash_ratio_pct')
                        } for r in eq['rows']
                    ]

                # KPI Table (kpi_table)
                kpi = q_items.get('kpi_mix_quarterly', {})
                if kpi and 'rows' in kpi:
                    fin['kpi_table'] = [
                        {
                            "period": r.get('period'),
                            "opm_pct": r.get('opm_pct'),
                            "roe_pct": r.get('roe_pct'),
                            "roa_pct": r.get('roa_pct'),
                            "fcf_100m": r.get('fcf_100m'),
                            "net_income_100m": r.get('net_income_100m'),
                            "opm": r.get('opm_pct'),
                            "roe": r.get('roe_pct'),
                            "roa": r.get('roa_pct'),
                            "fcf_b": r.get('fcf_100m'),
                            "net_b": r.get('net_income_100m')
                        } for r in kpi['rows']
                    ]

                # Structure Table (structure_table)
                st = q_items.get('structure_stability_quarterly', {})
                if st and 'rows' in st:
                    fin['structure_table'] = [
                        {
                            "period": r.get('period'),
                            "total_assets_b": r.get('total_assets_b'),
                            "equity_b": r.get('equity_b'),
                            "debt_ratio_pct": r.get('debt_ratio_pct'),
                            "current_liab_b": r.get('current_liab_b'),
                            "doi_days": r.get('doi_days'),
                            "assets": r.get('total_assets_b'),
                            "equity": r.get('equity_b'),
                            "debt": r.get('debt_ratio_pct'),
                            "cliab": r.get('current_liab_b'),
                            "doi": r.get('doi_days')
                        } for r in st['rows']
                    ]

                # PE Table (pe_table)
                pe = q_items.get('pe_precise_quarterly', {})
                if pe and 'rows' in pe:
                    fin['pe_table'] = [
                        {
                            "period": r.get('period'),
                            "deadline": r.get('deadline'),
                            "price_basis": r.get('price_basis'),
                            "avg_close_3d": r.get('avg_close_3d'),
                            "ttm_eps": r.get('ttm_eps'),
                            "pe": r.get('pe')
                        } for r in pe['rows']
                    ]

                # 4. 股本 (capital_table)
                cap = details.get('3_最新股本 (Capital)', {}).get('items', {}).get('capital_latest', {})
                if cap and 'rows' in cap:
                    fin['capital_table'] = [
                        {
                            "report_date": r.get('report_date'),
                            "capital_value_raw": r.get('capital_value_raw'),
                            "par_value": r.get('par_value'),
                            "shares_lots": r.get('shares_lots'),
                            "method": r.get('method')
                        } for r in cap['rows']
                    ]

                # 5. 法人籌碼 (chips_table)
                chips = details.get('4_法人籌碼 (Daily)', {}).get('items', {}).get('chips_15d', {})
                if chips and 'rows' in chips:
                    fin['chips_table'] = [
                        {
                            "date": r.get('date'),
                            "foreign_lots": r.get('foreign_lots'),
                            "it_lots": r.get('it_lots'),
                            "dealer_lots": r.get('dealer_lots'),
                            "sum_lots": r.get('sum_lots'),
                            "pct_float": r.get('pct_float')
                        } for r in chips['rows']
                    ]

                # 6. 年度報酬 (returns_table)
                ret = details.get('6_含息年度報酬率 (Annual Returns)', {}).get('items', {}).get('annual_returns', {})
                if ret and 'rows' in ret:
                    fin['returns_table'] = [
                        {
                            "year": r.get('year'),
                            "return_pct": r.get('return_pct'),
                            "period": r.get('period')
                        } for r in ret['rows']
                    ]

                # 7. C-Score (c_score_data)
                cs = details.get('7_結構校正C值 (C-Score)', {}).get('items', {}).get('structure_c_score', {})
                if cs:
                    benchmark_pe = cs.get('benchmark_pe', 15.0)
                    fin['c_score_data'] = {
                        "schema_version": cs.get('schema_version', "table/v1"),
                        "dataset": cs.get('dataset', "structure_c_score"),
                        "stock_id": stock_id,
                        "ticker": cs.get('ticker', f"{stock_id}.TW"),
                        "asof": cs.get('asof', generated_at.split()[0]),
                        "c_score": cs.get('c_score'),
                        "benchmark_pe": benchmark_pe,
                        "source": cs.get('source', ["FinMind", "yahooquery"]),
                        "columns": cs.get('columns', []),
                        "rows": [
                            {
                                **r,
                                "theo_price": (r.get('eps', 0) * benchmark_pe) if r.get('eps') else 0
                            } for r in cs.get('rows', [])
                        ],
                        "notes": cs.get('notes', []),
                        "name": cs.get('name'),
                        "stock_name": cs.get('stock_name')
                    }

                # 8. K線資料 (kline_data)
                kline_report = details.get('8_K線 (Daily)', {})
                if kline_report and 'rows' in kline_report:
                    fin['kline_data'] = kline_report['rows']

                with open(detail_path, 'w', encoding='utf-8') as f:
                    json.dump(detail_data, f, ensure_ascii=False, indent=2)
            
            updated_count += 1
            print(f"成功精準同步股票 {stock_id} ({header.get('stock_name')})")

        except Exception as e:
            print(f"處理 {txt_path} 時發生錯誤: {e}")

    # 4. 寫回 stocks_summary.json
    if updated_count > 0:
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        print(f"總計精準同步了 {updated_count} 檔股票資訊。")

if __name__ == "__main__":
    update_data()
