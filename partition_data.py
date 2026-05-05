import json
import os
import shutil

def partition_data(input_file="data.json", output_dir="details", summary_file="stocks_summary.json"):
    """
    將巨大的 data.json 拆分為摘要檔與各別股票的詳情檔。
    """
    if not os.path.exists(input_file):
        print(f"找不到 {input_file}")
        return

    print(f"正在讀取 {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        try:
            db = json.load(f)
        except Exception as e:
            print(f"讀取 JSON 失敗: {e}")
            return

    if not isinstance(db, list):
        print("資料格式錯誤：應為陣列")
        return

    # 確保輸出目錄存在
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    summary_list = []
    print(f"共有 {len(db)} 檔股票，開始分片...")

    for stock in db:
        stock_id = stock.get("id")
        if not stock_id:
            continue

        # 1. 提取詳情並儲存
        detail_path = os.path.join(output_dir, f"{stock_id}.json")
        with open(detail_path, "w", encoding="utf-8") as f:
            json.dump(stock, f, ensure_ascii=False, indent=2)

        # 2. 提取摘要資訊
        summary_item = {
            "id": stock_id,
            "name": stock.get("name"),
            "lastUpdated": stock.get("lastUpdated"),
            "basicInfo": stock.get("basicInfo"),
            "status": stock.get("status"), # 狀態標籤 (BUY/FLU 等)
            "themes": stock.get("themes", []),
            "industry_analysis": {
                "sector": stock.get("industry_analysis", {}).get("sector")
            },
            "technical_analysis": {
                "status": stock.get("technical_analysis", {}).get("status")
            }
        }
        
        # 為了牆面展示，可能需要少量的預測資訊
        if "ai_forecasts" in stock:
            summary_item["ai_forecasts"] = stock["ai_forecasts"]
        if "technical_analysis" in stock and "predictions" in stock["technical_analysis"]:
            summary_item["technical_analysis"]["predictions"] = stock["technical_analysis"]["predictions"]

        summary_list.append(summary_item)

    # 3. 儲存摘要檔
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_list, f, ensure_ascii=False, indent=2)

    print(f"分片完成！摘要檔: {summary_file}, 詳情檔目錄: {output_dir}")
    
    # 打印檔案大小比較
    orig_size = os.path.getsize(input_file) / (1024 * 1024)
    sum_size = os.path.getsize(summary_file) / (1024 * 1024)
    print(f"原始大小: {orig_size:.2f} MB")
    print(f"摘要大小: {sum_size:.2f} MB (減少 {(1 - sum_size/orig_size)*100:.1f}%)")

if __name__ == "__main__":
    partition_data()
