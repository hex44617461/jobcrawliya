"""잡코리아 채용 공고를 수집해 마크다운과 캡처 이미지로 저장하는 크롤러입니다."""

import asyncio
import os
import random
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# 프로젝트 루트와 수집 결과 저장 위치를 한 곳에서 정의합니다.
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data" / "scraped"
DIR_POST = DATA_DIR / "posts"
DIR_IMG = DATA_DIR / "images"

# 잡코리아 검색 URL을 만들기 위한 기본 설정입니다.
URL_BASE = "https://www.jobkorea.co.kr"
DUTY_CODE = "1000236"
PAGE_NUM_MIN = 1
PAGE_NUM_MAX = 1

# 중지 버튼을 눌렀을 때 긴 대기 중에도 빠르게 반응하도록 취소 확인 주기를 둡니다.
CANCEL_POLL_INTERVAL = 0.4

# 취소 가능한 await 헬퍼의 반환 타입을 보존하기 위한 제네릭 타입입니다.
T = TypeVar("T")


def _log(message: str, log_fn: Optional[Callable[[str], None]] = None) -> None:
    """화면 로그 콜백이 있으면 콜백으로, 없으면 콘솔로 메시지를 보냅니다."""

    if log_fn:
        log_fn(message)
    else:
        print(message)


def _is_cancelled(cancel_event) -> bool:
    """Streamlit 중지 버튼에서 전달한 취소 이벤트가 켜졌는지 확인합니다."""

    return cancel_event is not None and cancel_event.is_set()


async def _check_cancel(cancel_event) -> None:
    """취소 요청이 있으면 async 흐름을 CancelledError로 중단합니다."""

    if _is_cancelled(cancel_event):
        raise asyncio.CancelledError("사용자 요청으로 중단됨")


async def _interruptible_sleep(seconds: float, cancel_event) -> None:
    """긴 sleep을 짧게 쪼개 중간에도 중지 버튼에 반응하게 합니다."""

    remaining = seconds
    while remaining > 0:
        await _check_cancel(cancel_event)
        step = min(CANCEL_POLL_INTERVAL, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _await_with_cancel(awaitable: Awaitable[T], cancel_event) -> T:
    """Playwright await 작업을 실행하면서 취소 요청을 주기적으로 확인합니다."""

    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            if _is_cancelled(cancel_event):
                # 취소 요청이 오면 진행 중인 Playwright 작업도 같이 취소합니다.
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
        # 바깥에서 취소된 경우에도 내부 task가 남지 않도록 정리합니다.
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        raise


def ensure_output_dirs() -> None:
    """마크다운과 이미지 저장 폴더가 없으면 생성합니다."""

    DIR_POST.mkdir(parents=True, exist_ok=True)
    DIR_IMG.mkdir(parents=True, exist_ok=True)


async def scrape_jobkorea_full(
    cancel_event=None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """잡코리아 검색 결과를 순회하며 신규 공고만 로컬 파일로 저장합니다."""

    ensure_output_dirs()
    cancelled = False

    # 기존 마크다운에 저장된 원본 링크를 읽어 중복 수집을 피합니다.
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
                    # YAML의 link 필드에서 URL을 찾아 중복 확인용 set에 넣습니다.
                    match = re.search(r'link:\s*"?(https?://[^\s"]+)"?', content)
                    if match:
                        visited_links.add(match.group(1).strip())
                except Exception as file_err:
                    _log(f"    ⚠️ 파일 읽기 실패 ({filename}): {file_err}", log_fn)

    _log(f"ℹ️ 기존 파일에서 총 {len(visited_links)}개의 링크를 확인했습니다.", log_fn)

    # Playwright는 브라우저를 실제로 띄워 동적 페이지를 렌더링한 뒤 HTML과 캡처를 가져옵니다.
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # 한국어/한국 시간대/일반 브라우저 User-Agent로 사이트 접근 환경을 맞춥니다.
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
            # 현재는 테스트와 로컬 실행 부담을 줄이기 위해 1페이지만 순회합니다.
            for page_no in range(PAGE_NUM_MIN, PAGE_NUM_MAX + 1):
                await _check_cancel(cancel_event)
                search_page = await context.new_page()
                try:
                    # 첫 페이지와 이후 페이지의 URL 파라미터 형식이 달라 조건부로 붙입니다.
                    search_url = (
                        f"{URL_BASE}/Search/?tabType=recruit&duty={DUTY_CODE}"
                        + (f"&Page_No={page_no}" if page_no > 1 else "")
                    )
                    _log(
                        f"\n=== [{page_no}/{PAGE_NUM_MAX} 페이지] 리스트 접속 시도: {search_url} ===",
                        log_fn,
                    )

                    # 검색 결과 카드가 렌더링될 때까지 기다린 뒤 HTML을 파싱합니다.
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
                        # 카드 내부의 회사명, 제목, 경력, 상세 링크를 추출합니다.
                        exp_tag = card_element.find("span", class_="flex-shrink-0 text-gray700 text-typo-c1-13")
                        corp_tag = card_element.find("span", class_="truncate text-gray700 text-typo-b2-16")
                        title_tag = card_element.find("span", class_="truncate font-semibold text-typo-b1-18 text-gray900")
                        link_tag = card_element.find("a", href=True)

                        exp = exp_tag.get_text(strip=True) if exp_tag else "경력 무관"
                        corp = corp_tag.get_text(strip=True) if corp_tag else "회사명 없음"
                        title = title_tag.get_text(strip=True) if title_tag else "제목 없음"

                        if not link_tag:
                            continue

                        # 상대 경로 링크는 잡코리아 도메인을 붙여 절대 URL로 바꿉니다.
                        raw_link = link_tag["href"]
                        job_link = raw_link if raw_link.startswith("http") else URL_BASE + raw_link

                        if job_link.strip() in visited_links:
                            _log(f"  ⏭️ [링크 중복 패스] {corp} - {title}", log_fn)
                            continue

                        _log(f"  └ [신규 공고 발견] {corp} - {title}", log_fn)
                        # 파일명에 쓸 수 없는 문자는 밑줄로 바꿔 저장 실패를 막습니다.
                        safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"

                        detail_page = await context.new_page()
                        try:
                            # 상세 페이지를 열고 본문 렌더링이 안정될 시간을 줍니다.
                            await _await_with_cancel(
                                detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000),
                                cancel_event,
                            )
                            await _interruptible_sleep(10, cancel_event)

                            img_name = f"{safe_filename}.png"
                            img_path = DIR_IMG / img_name

                            # 잡코리아 상세 페이지의 본문 영역만 우선 캡처합니다.
                            content_element = await detail_page.query_selector("div.\\[grid-area\\:content\\]")
                            await _check_cancel(cancel_event)
                            if content_element:
                                # 본문 캡처가 좌우 레이아웃에 눌리지 않도록 요소 스타일을 임시 조정합니다.
                                await content_element.evaluate("""
                                    el => {
                                        el.style.width = '100%';
                                        el.style.maxWidth = '100%';
                                        el.style.marginLeft = '0';
                                        el.style.marginRight = '0';
                                        el.style.padding = '20px';
                                    }
                                """)
                                # 우측 사이드 영역이 있으면 숨겨 본문 캡처에 섞이지 않게 합니다.
                                aside_element = await detail_page.query_selector("aside, div.\\[grid-area\\:aside\\]")
                                if aside_element:
                                    await aside_element.evaluate("el => { el.style.display = 'none'; }")
                                # 본문 영역에 맞는 폭으로 캡처한 뒤 기본 뷰포트로 되돌립니다.
                                await detail_page.set_viewport_size({"width": 1000, "height": 1024})
                                await content_element.screenshot(path=str(img_path))
                                await detail_page.set_viewport_size({"width": 1440, "height": 1024})
                            else:
                                # 본문 selector가 바뀐 경우에도 최소한 전체 페이지 캡처를 남깁니다.
                                await detail_page.screenshot(path=str(img_path), full_page=True)

                            # 화면 앱이 읽을 수 있도록 공고 메타데이터를 마크다운으로 저장합니다.
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

                            # 같은 실행 중에도 중복 상세 페이지를 다시 열지 않도록 즉시 기록합니다.
                            visited_links.add(job_link.strip())
                            _log(f"    💾 저장 완료: {safe_filename}", log_fn)
                        except asyncio.CancelledError:
                            cancelled = True
                            raise
                        except Exception as detail_err:
                            _log(f"    ❌ 상세 페이지 처리 중 에러 발생 (스킵): {detail_err}", log_fn)
                        finally:
                            # 상세 페이지 탭은 공고 하나 처리 후 바로 닫아 메모리 사용을 줄입니다.
                            await detail_page.close()

                        # 사이트에 과한 요청을 보내지 않도록 공고 사이에 무작위 대기를 둡니다.
                        await _interruptible_sleep(random.uniform(10, 15), cancel_event)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
                    # 검색 결과 페이지도 페이지 단위 처리가 끝나면 닫습니다.
                    await search_page.close()

                # 다음 검색 결과 페이지로 넘어가기 전에도 잠깐 쉬어갑니다.
                await _interruptible_sleep(random.uniform(5, 12), cancel_event)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            # 성공/취소/오류 여부와 관계없이 브라우저 프로세스를 정리합니다.
            await browser.close()
            if cancelled:
                _log("\n🛑 사용자 요청으로 수집을 중단했습니다.", log_fn)
            else:
                _log("\n🏁 모든 페이지의 공고 아카이빙 순회가 완료되었습니다!", log_fn)


if __name__ == "__main__":
    # Streamlit이 아닌 CLI에서 `python -m src.scraper`로 실행할 때의 진입점입니다.
    asyncio.run(scrape_jobkorea_full())
