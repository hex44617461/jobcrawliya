import asyncio
import os
import random
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "scraped"
DIR_POST = DATA_DIR / "posts"
DIR_IMG = DATA_DIR / "images"

URL_BASE = "https://www.jobkorea.co.kr"
DUTY_CODE = "1000236"
PAGE_NUM_MIN = 1
PAGE_NUM_MAX = 1

CANCEL_POLL_INTERVAL = 0.4

T = TypeVar("T")


def _log(message: str, log_fn: Optional[Callable[[str], None]] = None) -> None:
    if log_fn:
        log_fn(message)
    else:
        print(message)


def _is_cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


async def _check_cancel(cancel_event) -> None:
    if _is_cancelled(cancel_event):
        raise asyncio.CancelledError("사용자 요청으로 중단됨")


async def _interruptible_sleep(seconds: float, cancel_event) -> None:
    remaining = seconds
    while remaining > 0:
        await _check_cancel(cancel_event)
        step = min(CANCEL_POLL_INTERVAL, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _await_with_cancel(awaitable: Awaitable[T], cancel_event) -> T:
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            if _is_cancelled(cancel_event):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.CancelledError("사용자 요청으로 중단됨")
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=CANCEL_POLL_INTERVAL)
            except asyncio.TimeoutError:
                continue
        return task.result()
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        raise


def ensure_output_dirs() -> None:
    DIR_POST.mkdir(parents=True, exist_ok=True)
    DIR_IMG.mkdir(parents=True, exist_ok=True)


async def scrape_jobkorea_full(
    cancel_event=None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    ensure_output_dirs()
    cancelled = False

    visited_links = set()
    _log("🔍 기존 마크다운 파일의 링크를 확인 중...", log_fn)
    await _check_cancel(cancel_event)

    if DIR_POST.exists():
        for filename in os.listdir(DIR_POST):
            await _check_cancel(cancel_event)
            if filename.endswith(".md"):
                file_path = DIR_POST / filename
                try:
                    content = file_path.read_text(encoding="utf-8")
                    match = re.search(r'link:\s*"?(https?://[^\s"]+)"?', content)
                    if match:
                        visited_links.add(match.group(1).strip())
                except Exception as file_err:
                    _log(f"    ⚠️ 파일 읽기 실패 ({filename}): {file_err}", log_fn)

    _log(f"ℹ️ 기존 파일에서 총 {len(visited_links)}개의 링크를 확인했습니다.", log_fn)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1024},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )

        try:
            for page_no in range(PAGE_NUM_MIN, PAGE_NUM_MAX + 1):
                await _check_cancel(cancel_event)
                search_page = await context.new_page()
                try:
                    search_url = (
                        f"{URL_BASE}/Search/?tabType=recruit&duty={DUTY_CODE}"
                        + (f"&Page_No={page_no}" if page_no > 1 else "")
                    )
                    _log(
                        f"\n=== [{page_no}/{PAGE_NUM_MAX} 페이지] 리스트 접속 시도: {search_url} ===",
                        log_fn,
                    )

                    await _await_with_cancel(
                        search_page.goto(search_url, wait_until="domcontentloaded", timeout=10000),
                        cancel_event,
                    )
                    await _await_with_cancel(
                        search_page.wait_for_selector("div.flex.w-full.gap-5.p-7", timeout=5000),
                        cancel_event,
                    )

                    soup = BeautifulSoup(await search_page.content(), "html.parser")
                    job_cards = soup.find_all("div", class_="flex w-full gap-5 p-7")

                    if not job_cards:
                        _log("수집할 공고 카드가 없습니다. 종료합니다.", log_fn)
                        break

                    for card_element in job_cards:
                        await _check_cancel(cancel_event)
                        exp_tag = card_element.find("span", class_="flex-shrink-0 text-gray700 text-typo-c1-13")
                        corp_tag = card_element.find("span", class_="truncate text-gray700 text-typo-b2-16")
                        title_tag = card_element.find("span", class_="truncate font-semibold text-typo-b1-18 text-gray900")
                        link_tag = card_element.find("a", href=True)

                        exp = exp_tag.get_text(strip=True) if exp_tag else "경력 무관"
                        corp = corp_tag.get_text(strip=True) if corp_tag else "회사명 없음"
                        title = title_tag.get_text(strip=True) if title_tag else "제목 없음"

                        if not link_tag:
                            continue

                        raw_link = link_tag["href"]
                        job_link = raw_link if raw_link.startswith("http") else URL_BASE + raw_link

                        if job_link.strip() in visited_links:
                            _log(f"  ⏭️ [링크 중복 패스] {corp} - {title}", log_fn)
                            continue

                        _log(f"  └ [신규 공고 발견] {corp} - {title}", log_fn)
                        safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"

                        detail_page = await context.new_page()
                        try:
                            await _await_with_cancel(
                                detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000),
                                cancel_event,
                            )
                            await _interruptible_sleep(10, cancel_event)

                            img_name = f"{safe_filename}.png"
                            img_path = DIR_IMG / img_name

                            content_element = await detail_page.query_selector("div.\\[grid-area\\:content\\]")
                            await _check_cancel(cancel_event)
                            if content_element:
                                await detail_page.evaluate("""
                                    const el = document.querySelector('div.\\[grid-area\\:content\\]');
                                    if (el) {
                                        el.style.width = '100%';
                                        el.style.maxWidth = '100%';
                                        el.style.marginLeft = '0';
                                        el.style.marginRight = '0';
                                        el.style.padding = '20px';
                                    }
                                    const aside = document.querySelector('aside, .\\[grid-area\\:aside\\]');
                                    if (aside) aside.style.display = 'none';
                                """)
                                await detail_page.set_viewport_size({"width": 1000, "height": 1024})
                                await content_element.screenshot(path=str(img_path))
                                await detail_page.set_viewport_size({"width": 1440, "height": 1024})
                            else:
                                await detail_page.screenshot(path=str(img_path), full_page=True)

                            post_path = DIR_POST / f"{safe_filename}.md"
                            markdown_content = f"""---
title: \"{title}\"
company: \"{corp}\"
link: \"{job_link}\"
---

# {title}

- **회사명**: {corp}
- **경력 요건**: {exp}
- **공고 링크**: [바로가기]({job_link})

## 📄 공고 본문 캡처본

![공고 스크린샷](../images/{img_name})
"""
                            post_path.write_text(markdown_content, encoding="utf-8")

                            visited_links.add(job_link.strip())
                            _log(f"    💾 저장 완료: {safe_filename}", log_fn)
                        except asyncio.CancelledError:
                            cancelled = True
                            raise
                        except Exception as detail_err:
                            _log(f"    ❌ 상세 페이지 처리 중 에러 발생 (스킵): {detail_err}", log_fn)
                        finally:
                            await detail_page.close()

                        await _interruptible_sleep(random.uniform(10, 15), cancel_event)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
                    await search_page.close()

                await _interruptible_sleep(random.uniform(5, 12), cancel_event)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            await browser.close()
            if cancelled:
                _log("\n🛑 사용자 요청으로 수집을 중단했습니다.", log_fn)
            else:
                _log("\n🏁 모든 페이지의 공고 아카이빙 순회가 완료되었습니다!", log_fn)


if __name__ == "__main__":
    asyncio.run(scrape_jobkorea_full())
