import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://www.tss-tv.co.jp/yumekirari/archive/"
OUTPUT_FILE = "yumekirari.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(url):
    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            link = (
                item.findtext("link", "")
                .strip()
            )

            guid = (
                item.findtext("guid", "")
                .strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
            }

    except Exception as e:
        print(f"既存RSS読込失敗: {e}")

    return items


def extract_date_from_url(url):
    path = urlparse(url).path

    # 例:
    # /yumekirari/archive/20260721.html
    match = re.search(
        r"/archive/"
        r"(20\d{2})"
        r"(\d{2})"
        r"(\d{2})"
        r"\.html",
        path,
    )

    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            12,
            0,
            0,
            tzinfo=JST,
        )

    except ValueError:
        return None


def extract_number(title):
    match = re.search(
        r"#\s*(\d+)",
        title,
    )

    if not match:
        return None

    return int(match.group(1))


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    articles = {}

    for a in soup.find_all(
        "a",
        href=True,
    ):
        raw_text = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        # #378亀本晴渚★神楽
        # のような放送回だけを対象
        match = re.search(
            r"#\s*(\d+)\s*(.+)",
            raw_text,
        )

        if not match:
            continue

        number = int(
            match.group(1)
        )

        title_body = clean_text(
            match.group(2)
        )

        if not title_body:
            continue

        title = (
            f"#{number}{title_body}"
        )

        link = canonicalize_url(
            urljoin(
                SOURCE_URL,
                a["href"],
            )
        )

        parsed = urlparse(link)

        if parsed.netloc not in (
            "www.tss-tv.co.jp",
            "tss-tv.co.jp",
        ):
            continue

        # 個別放送回ページだけを対象
        if not re.search(
            r"/yumekirari/archive/"
            r"20\d{6}\.html$",
            parsed.path,
        ):
            continue

        published = extract_date_from_url(
            link
        )

        if published is None:
            continue

        articles[link] = {
            "title": title,
            "link": link,
            "date": published,
            "number": number,
        }

    result = list(
        articles.values()
    )

    # 放送回番号の大きいものを優先
    result.sort(
        key=lambda x: (
            x["number"],
            x["date"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(existing_items)

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": (
                f"夢キラリ "
                f"放送回 #{article['number']}"
            ),
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
        }

    def get_number(item):
        match = re.search(
            r"#\s*(\d+)",
            item["title"],
        )

        if match:
            return int(
                match.group(1)
            )

        return 0

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=lambda x: (
            get_number(x),
            get_pubdate(x),
        ),
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = "夢キラリ 過去の放送回"

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "TSSテレビ新広島「夢キラリ」"
        "過去の放送回"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "夢キラリのアーカイブページを"
            "取得できませんでした。"
        )

    print(
        f"HTML文字数: {len(html)}"
    )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"放送回取得件数: "
        f"{len(articles)}"
    )

    print()
    print("=== 取得した放送回 ===")

    for i, article in enumerate(
        articles,
        1,
    ):
        print()
        print(
            f"{i:2d}. "
            f"{article['date'].strftime('%Y-%m-%d')} "
            f"{article['title']}"
        )

        print(
            f"    {article['link']}"
        )

    if len(articles) < 3:
        if existing_items:
            print()
            print(
                "取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"放送回取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規放送回: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()