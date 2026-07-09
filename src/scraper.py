import asyncio
import os
import random
import re
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "jobcrawliya" / "data" / "scraped"
DIR_POST = DATA_DIR / "posts"
DIR_IMG = DATA_DIR / "images"

URL_BASE = "https://www.jobkorea.co.kr"
DUTY_CODE = "1000236"
PAGE_NUM_MIN = 1
PAGE_NUM_MAX = 1


def ensure_output_dirs() -> None:
    DIR_POST.mkdir(parents=True, exist_ok=True)
    DIR_IMG.mkdir(parents=True, exist_ok=True)


async def scrape_jobkorea_full() -> None:
    ensure_output_dirs()

    visited_links = set()
    print("🔍 기존 마크다운 파일의 링크를 확인 중...")
    for filename in os.listdir(DIR_POST):
        if filename.endswith(".md"):
            file_path = DIR_POST / filename
            try:
                content = file_path.read_text(encoding="utf-8")
                match = re.search(r'link:\s*"?(https?://[^\s"]+)"?', content)
                if match:
                    visited_links.add(match.group(1).strip())
            except Exception as file_err:
                print(f"    ⚠️ 파일 읽기 실패 ({filename}): {file_err}")

    print(f"ℹ️ 기존 파일에서 총 {len(visited_links)}개의 링크를 확인했습니다.")

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
                search_page = await context.new_page()
                try:
                    search_url = (
                        f"{URL_BASE}/Search/?tabType=recruit&duty={DUTY_CODE}"
                        + (f"&Page_No={page_no}" if page_no > 1 else "")
                    )
                    print(f"\n=== [{page_no}/{PAGE_NUM_MAX} 페이지] 리스트 접속 시도: {search_url} ===")

                    await search_page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
                    await search_page.wait_for_selector("div.flex.w-full.gap-5.p-7", timeout=5000)

                    soup = BeautifulSoup(await search_page.content(), "html.parser")
                    job_cards = soup.find_all("div", class_="flex w-full gap-5 p-7")

                    if not job_cards:
                        print("수집할 공고 카드가 없습니다. 종료합니다.")
                        break

                    for card_element in job_cards:
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
                            print(f"  ⏭️ [링크 중복 패스] {corp} - {title}")
                            continue

                        print(f"  └ [신규 공고 발견] {corp} - {title}")
                        safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"

                        detail_page = await context.new_page()
                        try:
                            await detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000)
                            await asyncio.sleep(10)

                            img_name = f"{safe_filename}.png"
                            img_path = DIR_IMG / img_name

                            content_element = await detail_page.query_selector("div.\\[grid-area\\:content\\]")
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
                            print(f"    💾 저장 완료: {safe_filename}")
                        except Exception as detail_err:
                            print(f"    ❌ 상세 페이지 처리 중 에러 발생 (스킵): {detail_err}")
                        finally:
                            await detail_page.close()

                        await asyncio.sleep(random.uniform(10, 15))
                finally:
                    await search_page.close()

                await asyncio.sleep(random.uniform(5, 12))
        finally:
            await browser.close()
            print("\n🏁 모든 페이지의 공고 아카이빙 순회가 완료되었습니다!")


if __name__ == "__main__":
    asyncio.run(scrape_jobkorea_full())
