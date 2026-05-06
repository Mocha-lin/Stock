import http.server
import socketserver
import webbrowser
import threading
import time

PORT = 8000

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 允許 CORS，確保本地讀取更順暢 (雖然 SimpleHTTPRequestHandler 在同源下不需要，但這可增加相容性)
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

def open_browser():
    # 稍等伺服器啟動後再開啟瀏覽器
    time.sleep(1.5)
    url = f"http://localhost:{PORT}/index.html"
    print(f"正在開啟瀏覽器: {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    # 使用 Thread 在背景啟動瀏覽器
    threading.Thread(target=open_browser, daemon=True).start()
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"本地伺服器已啟動於 http://localhost:{PORT}")
        print("請不要關閉此視窗，否則網頁將無法正常運作。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n伺服器已停止。")
