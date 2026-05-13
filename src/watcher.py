import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv

# Appleの整備済Mac miniページと，通知済み商品を保存するローカルJSON．
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/mac-mini"
NOTIFIED_ITEMS_PATH = "data/notified_items.json"
TEST_FIXTURE_PATH = "tests/fixtures/macmini_test_page.html"
TEST_MODE_ENV = "WATCHER_TEST_MODE"
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; refurb-watcher/1.0)",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

# 将来の絞り込み条件（現在は未使用）
# FILTER_CHIP = "M4"
# FILTER_MIN_MEMORY_GB = 16
# FILTER_MIN_SSD_GB = 512
# FILTER_TEN_GBE = False
# FILTER_MAX_PRICE = 200000

APPLE_BASE_URL = "https://www.apple.com"
LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"
SAVED_ITEM_FIELDS = ("url", "product_id", "name", "price", "detected_at")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_page(url: str) -> str:
    # Appleのページを取得する．HTTPエラーは呼び出し元で処理する．
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def extract_from_json_script(html: str) -> list[dict] | None:
    # Appleページ内の埋め込みJSONから商品データを探す．
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict] = []
    seen_urls: set[str] = set()

    for script in soup.find_all("script", attrs={"type": "application/json"}):
        if not script.string:
            continue

        try:
            payload = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        for candidate in _walk_json(payload):
            item = _product_from_mapping(candidate)
            if not item or not is_mac_mini(item["name"]):
                continue
            if item["url"] in seen_urls:
                continue
            products.append(item)
            seen_urls.add(item["url"])

    return products or None


def extract_from_html(html: str) -> list[dict]:
    # 埋め込みJSONで取れない場合に，HTMLの商品リンク周辺から情報を拾う．
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict] = []
    seen_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue

        name = _clean_text(link.get_text(" ", strip=True))
        if not is_mac_mini(name):
            name = _find_nearby_name(link)
        if not name or not is_mac_mini(name):
            continue

        url = normalize_url(str(link["href"]))
        if not _looks_like_product_url(url) or url in seen_urls:
            continue

        container = _find_product_container(link)
        price = _find_price_text(container) or _find_price_text(link.parent) or ""

        products.append(
            {
                "name": name,
                "price": price,
                "url": url,
                "product_id": extract_product_id(url),
            }
        )
        seen_urls.add(url)

    return products


def extract_mac_mini_items(html: str) -> list[dict]:
    # JSON優先，失敗時はHTMLフォールバックでMac miniだけに正規化する．
    items = extract_from_json_script(html)
    if items is None:
        logger.info("No product data found in JSON scripts. Falling back to HTML parser.")
        items = extract_from_html(html)

    normalized_items: list[dict] = []
    seen_urls: set[str] = set()
    detected_at = datetime.now().astimezone().isoformat(timespec="seconds")

    for item in items:
        name = str(item.get("name", "")).strip()
        if not is_mac_mini(name):
            continue

        url = normalize_url(str(item.get("url", "")))
        if not url or url in seen_urls:
            continue

        normalized_items.append(
            {
                "name": name,
                "price": str(item.get("price", "")).strip(),
                "url": url,
                "product_id": str(item.get("product_id") or extract_product_id(url)),
                "detected_at": detected_at,
            }
        )
        seen_urls.add(url)

    return normalized_items


def load_notified_items(path: str) -> list[dict]:
    # 通知済みJSONが壊れていても，監視自体は止めず空リストとして扱う．
    file_path = Path(path)
    if not file_path.exists():
        logger.warning("Notified items file does not exist. Starting with an empty list.")
        return []

    try:
        with file_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read notified items file. Starting with an empty list: %s", exc)
        return []

    if not isinstance(payload, list):
        logger.warning("Notified items file is not a list. Starting with an empty list.")
        return []

    return [item for item in payload if isinstance(item, dict)]


def save_notified_items(path: str, items: list[dict]) -> None:
    # 保存する情報は公開商品情報だけに限定し，秘密情報を混ぜない．
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    sanitized_items = [
        {field: str(item.get(field, "")) for field in SAVED_ITEM_FIELDS}
        for item in items
        if isinstance(item, dict)
    ]

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(sanitized_items, file, ensure_ascii=False, indent=2)
        file.write("\n")


def find_new_items(current: list[dict], notified: list[dict]) -> list[dict]:
    # URLを重複判定キーにして，未通知の商品だけを返す．
    notified_urls = {str(item.get("url", "")) for item in notified if isinstance(item, dict)}
    return [item for item in current if str(item.get("url", "")) not in notified_urls]


def format_message(item: dict) -> str:
    # LINEで読みやすいよう，商品情報を固定フォーマットの本文にする．
    message = (
        "Apple整備済製品にMac miniが入荷しました．\n\n"
        "商品名：\n"
        f"{item.get('name', '')}\n\n"
        "価格：\n"
        f"{item.get('price', '')}\n\n"
        "URL：\n"
        f"{item.get('url', '')}\n\n"
        "検出日時：\n"
        f"{item.get('detected_at', '')}"
    )
    if item.get("is_test"):
        return "【テスト通知】これは動作確認用です．実在庫ではありません．\n\n" + message
    return message


def send_line_notification(item: dict, token: str, user_id: str) -> None:
    # tokenやuser_idはログに出さず，ステータスコードだけを記録する．
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": format_message(item),
            }
        ],
    }

    logger.info("Sending LINE notification for new item.")
    response = requests.post(
        LINE_PUSH_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    logger.info("LINE API response status: %d", response.status_code)

    if response.status_code == 401:
        logger.error("LINE API authentication failed. Status: %d", response.status_code)
    elif response.status_code == 429:
        logger.error("LINE API rate limit reached. Status: %d", response.status_code)
    elif response.status_code >= 400:
        logger.error("LINE API request failed. Status: %d", response.status_code)

    response.raise_for_status()


def main() -> None:
    # ローカルでは.env，GitHub ActionsではSecrets由来の環境変数を読む．
    load_dotenv()

    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")
    if not token or not user_id:
        logger.error("Required LINE environment variables are not set.")
        sys.exit(1)

    if is_truthy(os.getenv(TEST_MODE_ENV, "")):
        run_test_mode(token, user_id)
        return

    try:
        logger.info("Fetching Apple refurbished product page.")
        html = fetch_page(TARGET_URL)
    except requests.Timeout:
        logger.error("Apple product page request timed out.")
        sys.exit(1)
    except requests.RequestException as exc:
        logger.error("Failed to fetch Apple product page: %s", exc)
        sys.exit(1)

    try:
        current_items = extract_mac_mini_items(html)
    except Exception as exc:
        logger.error("Failed to parse Apple product page: %s", exc)
        sys.exit(1)

    logger.info("Detected Mac mini items: %d", len(current_items))
    if not current_items:
        logger.warning("No Mac mini items detected. Page structure may have changed.")
        return

    notified_items = load_notified_items(NOTIFIED_ITEMS_PATH)
    new_items = find_new_items(current_items, notified_items)
    logger.info("New Mac mini items: %d", len(new_items))
    if not new_items:
        return

    try:
        for item in new_items:
            send_line_notification(item, token, user_id)
    except requests.Timeout:
        logger.error("LINE API request timed out.")
        sys.exit(1)
    except requests.RequestException as exc:
        logger.error("Failed to send LINE notification: %s", exc)
        sys.exit(1)

    try:
        save_notified_items(NOTIFIED_ITEMS_PATH, notified_items + new_items)
    except OSError as exc:
        logger.error("Failed to save notified items: %s", exc)
        sys.exit(1)

    logger.info("Updated notified items file.")


def run_test_mode(token: str, user_id: str) -> None:
    # 実在庫がなくても，fixtureのHTML解析とLINE送信だけを検証する．
    logger.info("Running watcher in test mode.")

    try:
        html = Path(TEST_FIXTURE_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to read test fixture: %s", exc)
        sys.exit(1)

    items = extract_mac_mini_items(html)
    logger.info("Detected Mac mini items from test fixture: %d", len(items))
    if not items:
        logger.error("Test fixture did not produce any Mac mini items.")
        sys.exit(1)

    test_item = dict(items[0])
    test_item["is_test"] = True
    test_item["name"] = f"[テスト] {test_item['name']}"

    try:
        send_line_notification(test_item, token, user_id)
    except requests.Timeout:
        logger.error("LINE API request timed out.")
        sys.exit(1)
    except requests.RequestException as exc:
        logger.error("Failed to send LINE test notification: %s", exc)
        sys.exit(1)

    logger.info("Test notification sent. Notified items file was not updated.")


def is_truthy(value: str) -> bool:
    # GitHub Actionsのboolean入力は文字列として渡されるため明示的に判定する．
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_mac_mini(product_name: str) -> bool:
    # 初期実装では商品名にMac miniを含むかだけで判定する．
    return "Mac mini" in product_name


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return urljoin(APPLE_BASE_URL, url)


def extract_product_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/product/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    return parsed.path.strip("/").split("/")[-1]


def _walk_json(value: Any) -> list[dict]:
    # JSON構造が変わっても拾えるよう，辞書とリストを再帰的に探索する．
    matches: list[dict] = []
    if isinstance(value, dict):
        if _mapping_contains_mac_mini(value):
            matches.append(value)
        for child in value.values():
            matches.extend(_walk_json(child))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_walk_json(child))
    return matches


def _mapping_contains_mac_mini(mapping: dict) -> bool:
    for value in mapping.values():
        if isinstance(value, str) and is_mac_mini(value):
            return True
    return False


def _product_from_mapping(mapping: dict) -> dict | None:
    # Apple側のキー名変更に備え，複数の候補キーから商品情報を組み立てる．
    name = _first_text_value(
        mapping,
        ("name", "title", "productName", "displayName", "productTitle", "description"),
    )
    if not name:
        return None

    url = _first_text_value(
        mapping,
        ("url", "href", "link", "productUrl", "productURL", "canonicalUrl"),
    )
    if not url:
        url = _first_nested_text_value(mapping, ("url", "href"))
    if not url:
        return None

    price = _first_text_value(
        mapping,
        ("price", "priceString", "currentPrice", "amount", "displayPrice", "fullPrice"),
    )
    if not price:
        price = _first_nested_text_value(mapping, ("price", "priceString", "displayPrice"))

    product_id = _first_text_value(
        mapping,
        ("product_id", "productId", "productID", "partNumber", "id", "sku"),
    )

    normalized_url = normalize_url(url)
    return {
        "name": _clean_text(name),
        "price": _clean_text(price or ""),
        "url": normalized_url,
        "product_id": _clean_text(product_id or extract_product_id(normalized_url)),
    }


def _first_text_value(mapping: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)):
            return str(value)
    return None


def _first_nested_text_value(value: Any, keys: tuple[str, ...]) -> str | None:
    # priceやurlが入れ子にあるケースを想定して深掘りする．
    if isinstance(value, dict):
        direct = _first_text_value(value, keys)
        if direct:
            return direct
        for child in value.values():
            nested = _first_nested_text_value(child, keys)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _first_nested_text_value(child, keys)
            if nested:
                return nested
    return None


def _find_nearby_name(link: Tag) -> str:
    # リンク文字列が「詳しく見る」だけの場合，親要素の見出しから商品名を探す．
    container = _find_product_container(link)
    if container is None:
        return ""

    headings = container.find_all(["h2", "h3", "h4"])
    for heading in headings:
        text = _clean_text(heading.get_text(" ", strip=True))
        if is_mac_mini(text):
            return text

    text = _clean_text(container.get_text(" ", strip=True))
    if not is_mac_mini(text):
        return ""

    match = re.search(r"([^\u3002\uFF0E]+Mac mini[^￥]+)", text)
    return _clean_text(match.group(1)) if match else text


def _find_product_container(tag: Tag) -> Tag | None:
    # 価格や見出しを探すため，商品カードらしい親要素までさかのぼる．
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"li", "article"}:
            return parent
        class_text = " ".join(str(value) for value in parent.get("class", []))
        if "product" in class_text.lower() or "rf-" in class_text.lower():
            return parent
    return tag.parent if isinstance(tag.parent, Tag) else None


def _find_price_text(tag: Tag | None) -> str:
    if tag is None:
        return ""

    text = _clean_text(tag.get_text(" ", strip=True))
    match = re.search(r"(￥\s?[\d,]+(?:\(税込\))?)", text)
    return _clean_text(match.group(1)) if match else ""


def _looks_like_product_url(url: str) -> bool:
    return "/shop/product/" in url or "/jp/shop/product/" in url


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


if __name__ == "__main__":
    main()
