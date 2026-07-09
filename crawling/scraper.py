"""크롤링 모듈

이 파일은 잡코리아의 채용 공고를 순회하여
- 기존 마크다운 파일의 YAML에서 이미 저장된 링크를 확인하고
- 중복되지 않는 신규 공고만 상세 페이지에서 본문 영역을 캡처하여
  이미지(.png)와 마크다운(.md)을 저장합니다.

주요 동작 흐름:
1. 설정 및 폴더 생성
2. 기존 포스트(.md)에서 링크 수집(중복 방지용)
3. Playwright로 검색 결과 페이지 순회
4. 각 공고 카드에서 상세 페이지로 이동하여 본문 캡처
5. 캡처 이미지와 마크다운 파일로 저장
"""

import os
import re
import random
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


async def scrape_jobkorea_full():
    """잡코리아 채용 공고를 수집하는 비동기 스크래퍼.

    함수는 로컬 폴더 `jobcrawliya/post`와 `jobcrawliya/img`에 결과를 저장합니다.
    기존 마크다운에 기록된 `link:` 값을 읽어와 중복 수집을 피합니다.
    """

    # 기본 상수/설정
    URL_BASE = "https://www.jobkorea.co.kr"
    DUTY_CODE = "1000236"  # 데이터엔지니어링 직무 코드

    # 테스트용으로 1페이지만 수집합니다(전체 수집 시 page_num_max를 늘리세요)
    page_num_min = 1
    page_num_max = 1

    # 저장 디렉터리 경로 (프로젝트 내 데이터 디렉터리 구조)
    # 컨벤션: 최상위에 `data/scraped/`를 두고, 내부에 `posts/`와 `images/`로 구분합니다.
    # 예: data/scraped/posts, data/scraped/images
    DIR_BASE = os.path.join("data", "scraped")
    DIR_POST = os.path.join(DIR_BASE, "posts")
    DIR_IMG = os.path.join(DIR_BASE, "images")

    # 출력 디렉터리 존재 보장
    for path in [DIR_POST, DIR_IMG]:
        os.makedirs(path, exist_ok=True)

    # 과거 포스트에서 수집된 링크를 읽어 중복을 피함
    visited_links = set()
    print("🔍 기존 마크다운 파일의 YAML에서 이미 수집된 링크를 분석 중...")
    for filename in os.listdir(DIR_POST):
        if filename.endswith(".md"):
            file_path = os.path.join(DIR_POST, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    # YAML 블록 내의 link: "https://..." 또는 link: https://... 패턴을 포착
                    match = re.search(r"link:\s*\"?(https?://[^\s\"]+)\"?", content)
                    if match:
                        visited_links.add(match.group(1).strip())
            except Exception as file_err:
                print(f"    ⚠️ 파일 읽기 실패 ({filename}): {file_err}")

    print(f"ℹ️  YAML 분석 완료: 기존 파일에서 총 {len(visited_links)}개의 링크를 확인했습니다. 중복 수집에서 건너뜁니다.")

    # Playwright 브라우저 실행(헤드리스)
    async with async_playwright() as p:
        # Chromium을 헤드리스 모드로 실행
        browser = await p.chromium.launch(headless=True)

        # 실제 브라우저 환경처럼 보이도록 컨텍스트 설정
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
            # 검색 결과 페이지 순회 (page_num_min ~ page_num_max)
            for page_no in range(page_num_min, page_num_max + 1):
                search_page = await context.new_page()
                try:
                    # 리스트 페이지 URL 생성
                    search_url = (
                        f"{URL_BASE}/Search/?tabType=recruit&duty={DUTY_CODE}"
                        + (f"&Page_No={page_no}" if page_no > 1 else "")
                    )
                    print(f"\n=== [{page_no}/{page_num_max} 페이지] 리스트 접속 시도: {search_url} ===")

                    # 페이지 로드 및 리스트 선택자 대기
                    await search_page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
                    await search_page.wait_for_selector("div.flex.w-full.gap-5.p-7", timeout=5000)

                    # BeautifulSoup으로 리스트 페이지 파싱
                    soup = BeautifulSoup(await search_page.content(), "html.parser")
                    job_cards = soup.find_all("div", class_="flex w-full gap-5 p-7")

                    if not job_cards:
                        print("수집할 공고 카드가 없습니다. 루프를 종료합니다.")
                        break

                    # 각 공고 카드에서 정보 추출 및 상세 페이지 이동
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

                        # 기존에 저장된 링크면 스킵
                        if job_link.strip() in visited_links:
                            print(f"  ⏭️  [링크 중복 패스] 이미 파일 내에 존재하는 링크입니다: {corp} - {title}")
                            continue

                        print(f"  └ [신규 공고 발견] {corp} - {title}")

                        # 파일명에 사용할 안전한 이름 생성
                        safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"

                        # 상세 페이지 열기
                        detail_page = await context.new_page()
                        try:
                            await detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000)

                            # 상세 페이지 렌더링 안정화 대기
                            await asyncio.sleep(10)

                            img_name = f"{safe_filename}.png"
                            img_path = os.path.join(DIR_IMG, img_name)

                            # 본문 영역 선택자 (원래 사이트 구조에 종속적)
                            python_selector = "div.\\[grid-area\\:content\\]"
                            content_element = await detail_page.query_selector(python_selector)

                            if content_element:
                                # 본문 영역의 스타일을 조정해 캡처 시 여백/사이드바 영향을 제거
                                await detail_page.evaluate("""
                                    const el = document.querySelector('div.\\\\[grid-area\\\\:content\\\\]');
                                    if (el) {
                                        el.style.width = '100%';
                                        el.style.maxWidth = '100%';
                                        el.style.marginLeft = '0';
                                        el.style.marginRight = '0';
                                        el.style.padding = '20px';
                                    }
                                    const aside = document.querySelector('aside, .\\\\[grid-area\\\\:aside\\\\]');
                                    if (aside) aside.style.display = 'none';
                                """)

                                # 캡처용 뷰포트 조정 후 본문 요소만 스크린샷
                                await detail_page.set_viewport_size({"width": 1000, "height": 1024})
                                await content_element.screenshot(path=img_path)
                                # 뷰포트 원상복구
                                await detail_page.set_viewport_size({"width": 1440, "height": 1024})
                                print(f"    🎯 [정밀 커팅 완수] {safe_filename} 하단 찌꺼기 없이 본문만 아웃풋 성공!")
                            else:
                                # 본문 선택자를 찾지 못하면 전체 페이지 캡처로 대체
                                await detail_page.screenshot(path=img_path, full_page=True)
                                print(f"    ⚠️ 본문 구역을 찾지 못해 기본 전체 화면으로 대체 캡처했습니다.")

                            # 마크다운 포스트 파일 생성 (이미지 경로 포함)
                            post_path = os.path.join(DIR_POST, f"{safe_filename}.md")
                            markdown_content = f"""---
title: "{title}"
company: "{corp}"
link: "{job_link}"
---

# {title}

- **회사명**: {corp}
- **경력 요건**: {exp}
- **공고 링크**: [바로가기]({job_link})

## 📄 공고 본문 캡처본

![공고 스크린샷](../images/{img_name})
"""
                            with open(post_path, "w", encoding="utf-8") as f:
                                f.write(markdown_content)

                            print(f"    💾 저장 완료: {safe_filename} (.png / .md 연동 완료)")
                            # 중복 방지를 위해 실시간으로 집합에 추가
                            visited_links.add(job_link.strip())

                        except Exception as detail_err:
                            print(f"    ❌ 상세 페이지 처리 중 에러 발생 (스킵): {detail_err}")
                        finally:
                            await detail_page.close()

                        # 공고 간 랜덤 딜레이 (선비 모드)
                        await asyncio.sleep(random.uniform(10, 15))

                finally:
                    await search_page.close()

                # 페이지 전환 전 딜레이
                print(f"=== 다음 페이지로 넘어가기 전 대기 중... ===")
                await asyncio.sleep(random.uniform(5, 12))

        except Exception as e:
            print(f"크롤링 메인 루프 중 치명적 예외 발생: {e}")
        finally:
            await browser.close()
            print("\n🏁 [성공] 모든 페이지의 공고 아카이빙 순회가 완료되었습니다!")


if __name__ == "__main__":
    asyncio.run(scrape_jobkorea_full())
