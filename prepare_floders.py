import os
import time
import csv
import requests
from lxml import etree
import concurrent.futures

header = {
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0',
}


# 获取总页数
def page_get(url):
    try:
        response = requests.get(url, headers=header)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        tree = etree.HTML(response.text)
        total_pages = tree.xpath('//div[@class="public-page tc clearfix pt10 pb20"]/em[1]/text()')
        return int(total_pages[0]) if total_pages else 1
    except Exception as e:
        print(f"Error fetching total pages from {url}: {e}")
        return 1


# 爬取单个页面的数据（菜品名称和链接）
def get_data(url):
    try:
        response = requests.get(url, headers=header)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        tree = etree.HTML(response.text)
        data = tree.xpath('//ul[@class="clearfix"]/li')
        dishes = []
        for item in data:
            name = item.xpath('./p[@class="title f16 tc"]/a/text()')
            href = item.xpath('./p[@class="title f16 tc"]/a/@href')
            if name and href:
                dish_name = name[0].strip()
                dish_link = 'https://www.food365.com.cn' + href[0]
                dishes.append({dish_name: dish_link})
        return dishes
    except Exception as e:
        print(f"Error fetching data from {url}: {e}")
        return []


# 爬取单个菜品的详情（文本和图片）
def detail_get(url, output_dir):
    try:
        response = requests.get(url, headers=header)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        tree = etree.HTML(response.text)
        content_div = tree.xpath('//div[@class="news-main f16 p15"]')[0]

        # 提取文本
        paragraphs = content_div.xpath('.//p')
        paragraph_texts = [''.join(p.xpath('.//text()')).strip() for p in paragraphs if
                           ''.join(p.xpath('.//text()')).strip()]
        text_content = " ".join(paragraph_texts)

        # 下载图片并保存路径
        img_paths = []
        img_urls = content_div.xpath('.//img/@src')
        for index, img_url in enumerate(img_urls, start=1):
            img_response = requests.get(img_url, headers=header)
            img_response.raise_for_status()
            img_path = os.path.join(output_dir, f'picture{index}.jpg')
            with open(img_path, 'wb') as img_file:
                img_file.write(img_response.content)
            img_paths.append(img_path)

        return text_content, img_paths

    except Exception as e:
        print(f"Error fetching details from {url}: {e}")
        return "", []


# 处理单个菜系的所有页面，并爬取数据
def fetch_and_process(url, category, cuisine, csv_writer):
    print(f"正在爬取 {cuisine}...")

    # 获取总页数
    total_pages = page_get(url)

    # 遍历每个页面，获取菜品数据
    for page in range(1, total_pages + 1):
        page_url = f"{url}/index_{page}.html" if page > 1 else url
        dishes = get_data(page_url)  # 这里使用 get_data 爬取每一页的数据

        for index, dish in enumerate(dishes, start=1):
            for name, link in dish.items():
                folder_path = os.path.join('./result', category, cuisine, f'菜名{index}_{name}')
                os.makedirs(folder_path, exist_ok=True)

                # 获取菜品详情：文本和图片路径
                text_content, img_paths = detail_get(link, folder_path)

                # 写入 CSV 文件，使用菜系名称作为 label
                label = cuisine
                for img_path in img_paths:
                    csv_writer.writerow([text_content, img_path, label])

            time.sleep(5)  # 控制请求频率
    print(f"{cuisine} 菜系的所有菜已被爬取完成")


# 主函数，执行所有菜系的爬取
def main():
    # 创建 CSV 文件并写入表头
    with open('train.csv', 'w', newline='', encoding='utf-8-sig') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['text', 'img_path', 'label'])  # 写入表头

        urls_cuisines = [
            ('https://www.food365.com.cn/caixi/lucai/', '菜系', '鲁菜'),
            ('https://www.food365.com.cn/caixi/chuancai/', '菜系', '川菜'),
            # 添加其他URLs和菜系对
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for url, category, cuisine in urls_cuisines:
                futures.append(executor.submit(fetch_and_process, url, category, cuisine, csv_writer))
            for future in concurrent.futures.as_completed(futures):
                future.result()  # 获取结果或异常


if __name__ == '__main__':
    main()
