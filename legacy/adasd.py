import sqlite3

def create_tables():
    try:
        # 连接到 marriage.db 数据库
        conn = sqlite3.connect('marriage.db')
        c = conn.cursor()

        # 创建 include_keywords 表
        c.execute('''
            CREATE TABLE IF NOT EXISTS include_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL
            )
        ''')

        # 创建 exclude_keywords 表
        c.execute('''
            CREATE TABLE IF NOT EXISTS exclude_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL
            )
        ''')

        conn.commit()  # 提交更改
        conn.close()   # 关闭连接

        print("✅ 表创建成功或已存在。")

    except sqlite3.Error as e:
        print(f"❌ 数据库操作失败：{str(e)}")

# 调用函数
if __name__ == "__main__":
    create_tables()