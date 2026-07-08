import os
import re
import random
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

async def scrape_jobkorea_full():
    """잡코리아 채용 공고를 순회하며, 기존 마크다운 YAML에 저장된 
    공고 링크와 중복되지 않는 신규 공고만 스크린샷과 마크다운으로 저장합니다."""
    
    # ==========================================
    # 1. [설정 및 재료 모음] 모든 변수와 기본 설정
    # ==========================================
    URL_BASE = "https://www.jobkorea.co.kr"
    DUTY_CODE = "1000236"  # 데이터엔지니어링 직무 코드
    
    page_num_min = 1
    page_num_max = 1  # 최대 30페이지까지 순회 (테스트 시 2 등으로 조정 가능)

    DIR_BASE = "jobcrawliya"
    DIR_POST = os.path.join(DIR_BASE, "post")
    DIR_IMG = os.path.join(DIR_BASE, "img")

    # 저장할 폴더 자동 생성 (없으면 새로 만듦)
    for path in [DIR_POST, DIR_IMG]:
        os.makedirs(path, exist_ok=True)

    # ==========================================
    # 2. [과거 링크 스캔] YAML 영역의 고유 링크 추출
    # ==========================================
    visited_links = set()
    
    print("🔍 기존 마크다운 파일의 YAML에서 이미 수집된 링크를 분석 중...")
    for filename in os.listdir(DIR_POST):
        if filename.endswith(".md"):
            file_path = os.path.join(DIR_POST, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    
                    # YAML 구역 내의 link: "주소" 또는 link: 주소 형태에서 URL만 정확히 파싱
                    match = re.search(r"link:\s*\"?(https?://[^\s\"]+)\"?", content)
                    if match:
                        visited_links.add(match.group(1).strip())
            except Exception as file_err:
                print(f"    ⚠️ 파일 읽기 실패 ({filename}): {file_err}")

    print(f"ℹ️  YAML 분석 완료: 기존 파일에서 총 {len(visited_links)}개의 링크를 확인했습니다. 중복 수집에서 건너뜁니다.")

    # ==========================================
    # 3. [브라우저 실행] Playwright 로봇 및 진짜 크롬 가면 세팅
    # ==========================================
    async with async_playwright() as p:
        # 백그라운드(화면 없음)에서 크롬 엔진 실행
        browser = await p.chromium.launch(headless=True) 
        
        # 차단 유발 문자(...)가 없는 완벽한 대한민국 윈도우 크롬 신분증 세팅
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 1024},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        
        try:
            # ==========================================
            # 4. [메인 루프] 리스트 페이지 순회
            # ==========================================
            for page_no in range(page_num_min, page_num_max + 1):
                search_page = await context.new_page()
                try:
                    # 주소 자동 조립 (1페이지와 2페이지 분기)
                    search_url = f"{URL_BASE}/Search/?tabType=recruit&duty={DUTY_CODE}" + (f"&Page_No={page_no}" if page_no > 1 else "")
                    print(f"\n=== [{page_no}/{page_num_max} 페이지] 리스트 접속 시도: {search_url} ===")

                    # 페이지 접속 및 대기 (안정적인 타임아웃 세팅)
                    await search_page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
                    await search_page.wait_for_selector("div.flex.w-full.gap-5.p-7", timeout=5000)

                    # BeautifulSoup으로 리스트 화면 파싱
                    soup = BeautifulSoup(await search_page.content(), "html.parser")
                    job_cards = soup.find_all("div", class_="flex w-full gap-5 p-7")

                    if not job_cards:
                        print("수집할 공고 카드가 없습니다. 루프를 종료합니다.")
                        break

                    # ==========================================
                    # 5. [데이터 추출 및 상세 페이지 이동]
                    # ==========================================
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
                        
                        # 🌟 [링크 기반 중복 검사] 
                        # 방금 추출한 job_link가 기존 YAML 링크 주머니에 있다면 완벽하게 패스!
                        if job_link.strip() in visited_links:
                            print(f"  ⏭️  [링크 중복 패스] 이미 파일 내에 존재하는 링크입니다: {corp} - {title}")
                            continue 

                        print(f"  └ [신규 공고 발견] {corp} - {title}")
                        
                        # 파일명 특수문자 정제
                        safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"
                        
                        # 새 탭을 열어서 공고 상세 페이지로 진짜 접속
                        detail_page = await context.new_page()
                        try:
                            await detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000)
                            
                            # ⏰ 선비 모드 딜레이 1: 상세 페이지가 온전히 그려지고 글 읽는 척 10초 대기
                            await asyncio.sleep(10) 
                            
                            # ① 스크린샷 이미지 저장 (.png)
                            img_path = os.path.join(DIR_IMG, f"{safe_filename}.png")
                            await detail_page.screenshot(path=img_path, full_page=True)
                            
                            # ② 마크다운 파일 저장 (.md) - 질문자님의 YAML 서식에 맞춰 저장
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
"""
                            with open(post_path, "w", encoding="utf-8") as f:
                                f.write(markdown_content)
                                
                            print(f"    💾 저장 완료: {safe_filename} (.png / .md)")
                            
                            # 실시간 중복 방지 주머니 업데이트
                            visited_links.add(job_link.strip())
                            
                        except Exception as detail_err:
                            print(f"    ❌ 상세 페이지 처리 중 에러 발생 (스킵): {detail_err}")
                        finally:
                            await detail_page.close()
                            
                        # ⏰ 선비 모드 딜레이 2: 공고 하나 저장하고 다음 공고 클릭하기 전 10~15초 랜덤 대기
                        await asyncio.sleep(random.uniform(10, 15))
                        
                finally:
                    await search_page.close()

                # ⏰ 선비 모드 딜레이 3: 한 페이지 다 훑고 다음 리스트 페이지 넘어가기 전 5~12초 랜덤 대기
                print(f"=== 다음 페이지로 넘어가기 전 대기 중... ===")
                await asyncio.sleep(random.uniform(5, 12))
                
        except Exception as e:
            print(f"크롤링 메인 루프 중 치명적 예외 발생: {e}")
        finally:
            await browser.close()
            print("\n🏁 [성공] 모든 페이지의 공고 아카이빙 순회가 완료되었습니다!")

# ==========================================
# 6. [진짜 실행 구역] 주피터/코랩 환경 전용 시동 코드
# ==========================================
if __name__ == "__main__":
    # asyncio 라이브러리를 사용해 비동기 올인원 함수 실행
    asyncio.run(scrape_jobkorea_full())