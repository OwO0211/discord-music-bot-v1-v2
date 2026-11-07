import sqlite3

# 假設我們有一個函數來檢索正確的標題
def fetch_title_from_url(url):
    # 這是一個模擬函數，你需要用實際邏輯替換
    # 比如通過一個 API 查詢或者本地文件檢索
    return "正確標題"

# 連接到數據庫
conn = sqlite3.connect('marriage.db')
c = conn.cursor()

# 查找所有標題為“未知標題”的記錄
c.execute("SELECT id, url FROM songs WHERE title = ?", ("未知標題",))
rows = c.fetchall()

# 遍歷每條記錄，更新標題
for row in rows:
    song_id = row[0]
    url = row[1]
    
    # 通過函數獲取正確的標題
    correct_title = fetch_title_from_url(url)
    
    # 更新數據庫中的標題
    c.execute("UPDATE songs SET title = ? WHERE id = ?", (correct_title, song_id))

# 提交更改並關閉連接
conn.commit()
conn.close()