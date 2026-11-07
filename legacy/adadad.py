import sqlite3

def create_or_update_keyword_song_mapping_table():
    try:
        # 连接数据库
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        # 创建表（如果不存在）
        c.execute('''
            CREATE TABLE IF NOT EXISTS keyword_song_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                song_url TEXT NOT NULL,
                play_count INTEGER DEFAULT 0,
                last_played INTEGER DEFAULT 0
            )
        ''')
        conn.commit()

        # 检查是否已存在 last_played 字段
        c.execute("PRAGMA table_info(keyword_song_mapping);")
        columns = [col[1] for col in c.fetchall()]

        if 'last_played' not in columns:
            # 如果字段不存在，则添加
            c.execute('ALTER TABLE keyword_song_mapping ADD COLUMN last_played INTEGER DEFAULT 0')
            conn.commit()
            print("✅ 字段 last_played 已成功添加。")

        conn.close()
        print("✅ 表 keyword_song_mapping 已成功创建或更新。")

    except sqlite3.Error as e:
        print(f"❌ 数据库操作失败：{str(e)}")

# 调用函数
if __name__ == "__main__":
    create_or_update_keyword_song_mapping_table()