import os
import json
import subprocess
import sys
from pathlib import Path

def run_test():
    print("="*60)
    print("🚀 啟動本地流程模擬測試 (Simulating GitHub Action)")
    print("="*60)

    # 1. 檢查環境變數
    if not os.path.exists(".env"):
        print("⚠️ 警告: 找不到 .env 檔案。請確保已根據 .env.example 建立 .env 並填入 FINMIND_TOKEN。")

    # 2. 模擬 Action Step: Extract Stock IDs from summary
    print("\n[Step 1] 正在從 stocks_summary.json 提取股票 ID...")
    try:
        with open('stocks_summary.json', 'r', encoding='utf-8') as f:
            summary_data = json.load(f)
            stock_ids = [item['id'] for item in summary_data]

        # 測試時如果股票太多，可以只選前 2 檔以節省時間 (stock_ids = stock_ids[:2])
        test_stocks = stock_ids[:]
        print(f"  ✅ 提取成功。測試標的: {test_stocks} (原總數: {len(stock_ids)})")
    except Exception as e:
        print(f"  ❌ 提取失敗: {e}")
        return

    # 3. 模擬 Action Step: Run Stock Pipeline
    print(f"\n[Step 2] 正在執行 stock_pipeline_local.py 對應標的: {test_stocks}...")
    try:
        # 呼叫 python 執行腳本
        cmd_pipeline = [sys.executable, "stock_pipeline_local.py", "--stocks"] + test_stocks
        result = subprocess.run(cmd_pipeline, capture_output=False, text=True)

        if result.returncode == 0:
            print("  ✅ Pipeline 執行成功。")
        else:
            print(f"  ❌ Pipeline 執行失敗，退出碼: {result.returncode}")
            return
    except Exception as e:
        print(f"  ❌ 執行 Pipeline 時發生錯誤: {e}")
        return

    # 4. 模擬 Action Step: Update JSON from TXT
    print("\n[Step 3] 正在執行 update_json_from_txt.py 更新資料庫...")
    try:
        cmd_update = [sys.executable, "update_json_from_txt.py"]
        result = subprocess.run(cmd_update, capture_output=False, text=True)

        if result.returncode == 0:
            print("  ✅ 資料更新成功。")
        else:
            print(f"  ❌ 資料更新失敗，退出碼: {result.returncode}")
            return
    except Exception as e:
        print(f"  ❌ 執行更新腳本時發生錯誤: {e}")
        return

    print("\n" + "="*60)
    print("🎉 本地流程測試完成！")
    print("請檢查 stocks_summary.json 與 details/ 資料夾下的檔案是否已更新時間與價格。")
    print("="*60)

if __name__ == "__main__":
    run_test()
