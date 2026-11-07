import requests
from concurrent.futures import ThreadPoolExecutor
import time

# 配置
BASE_URL = "https://paper.twgss.org"
HEADERS = {"User-Agent": "MyApp/1.0"}
DELAY = 0.1  

# 单次请求
def send_request(url, method="GET", data=None):
    try:
        if method == "GET":
            response = requests.get(url, headers=HEADERS, timeout=10)
        elif method == "POST":
            response = requests.post(url, json=data, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.status_code, response.text
    except requests.exceptions.RequestException as e:
        return "Error", str(e)

# 循环请求
def loop_requests(url, method="GET", times=1000):
    for i in range(times):
        status, result = send_request(url, method)
        print(f"第 {i + 1} 次请求: 状态码 {status}, 响应 {result[:50]}")
        time.sleep(DELAY)

# 并发请求
def concurrent_requests(urls):
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(send_request, urls)
        for status, result in results:
            print(f"状态码 {status}, 响应 {result[:50]}")

# 主程序
if __name__ == "__main__":
    # 单次请求
    print("单次请求")
    print(send_request(BASE_URL))

    # 循环请求
    print("\n循环请求")
    loop_requests(BASE_URL, times=3)

    # 并发请求
    print("\n并发请求")
    urls = [BASE_URL for _ in range(1000)]
    concurrent_requests(urls)