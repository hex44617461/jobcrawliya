"""잡코리아 채용 공고를 수집해 마크다운과 캡처 이미지로 저장하는 크롤러입니다."""

import asyncio
import json
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
PAGE_NUM_MAX = 30

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


def _clean_text(value: str) -> str:
    """HTML에서 가져온 텍스트의 공백과 불필요한 버튼 문구를 정리합니다."""

    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.replace("지도보기", "").strip()
    return value


def _yaml_string(value: str) -> str:
    """마크다운 YAML 값으로 안전하게 넣기 위해 JSON 문자열 형태로 감쌉니다."""

    return json.dumps(value or "", ensure_ascii=False)


def _first_text(soup: BeautifulSoup, selector: str) -> str:
    """CSS selector에 맞는 첫 요소의 텍스트를 반환합니다."""

    element = soup.select_one(selector)
    return _clean_text(element.get_text(" ", strip=True)) if element else ""


def _meta_content(soup: BeautifulSoup, name: str = "", prop: str = "") -> str:
    """meta name 또는 property 값에 해당하는 content를 가져옵니다."""

    selector = f'meta[name="{name}"]' if name else f'meta[property="{prop}"]'
    element = soup.select_one(selector)
    return _clean_text(element.get("content", "")) if element else ""


def _parse_json_ld(soup: BeautifulSoup) -> dict:
    """상세 페이지의 구조화 데이터(JSON-LD)를 dict로 읽습니다."""

    script = soup.select_one('script[type="application/ld+json"]')
    if not script or not script.string:
        return {}
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_item_value(soup: BeautifulSoup, component: str, label: str) -> str:
    """잡코리아 상세 페이지의 라벨/값 쌍 컴포넌트에서 값을 추출합니다."""

    for item in soup.select(f'[data-sentry-component="{component}"]'):
        label_element = item.select_one("span.min-w-\\[80px\\]")
        if not label_element or _clean_text(label_element.get_text(" ", strip=True)) != label:
            continue

        label_element.extract()
        return _clean_text(item.get_text(" ", strip=True))
    return ""


def _extract_company_box_value(soup: BeautifulSoup, label: str) -> str:
    """기업 정보 카드에서 사원수/기업구분/산업/위치 값을 추출합니다."""

    for box in soup.select('[data-sentry-component="CorpInformationBox"]'):
        texts = [_clean_text(text) for text in box.stripped_strings]
        texts = [text for text in texts if text and text != "지도보기"]
        for index, text in enumerate(texts):
            if text == label and index + 1 < len(texts):
                return texts[index + 1]
    return ""


def _extract_skills_from_next_data(html: str) -> str:
    """Next.js 데이터에 들어있는 스킬명을 찾아 쉼표로 연결합니다."""

    names = []
    for name in re.findall(r'\\"name\\":\\"([^"\\]+)\\"', html):
        if name and name not in names:
            names.append(name)

    # 직무 키워드가 먼저 나오고 실제 스킬은 skillTypeCode와 함께 나오므로, 스킬다운 값만 우선 남깁니다.
    skill_names = []
    for match in re.finditer(r'\{\\"name\\":\\"([^"\\]+)\\"[^{}]*?\\"skillTypeCode\\":\\"HARD_SKILL\\"', html):
        name = match.group(1)
        if name and name not in skill_names:
            skill_names.append(name)

    return ", ".join(skill_names or names)


def _normalize_experience(value: str) -> str:
    """상세 페이지의 경력 텍스트를 사용자가 보기 좋은 형태로 정리합니다."""

    value = _clean_text(value)
    match = re.search(r"\(([^)]+)\)", value)
    if match:
        return match.group(1)
    return re.sub(r"^경력\s*", "", value).strip() or value


def _extract_time_range(value: str) -> str:
    """근무시간 텍스트에서 시간대가 있으면 시간대만 우선 추출합니다."""

    match = re.search(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", value or "")
    return match.group(0) if match else _clean_text(value)


def parse_detail_metadata(html: str, fallback: dict) -> dict:
    """상세 페이지 본문에서 저장용 메타데이터를 추출하고 카드값으로 보완합니다."""

    soup = BeautifulSoup(html, "html.parser")
    json_ld = _parse_json_ld(soup)
    meta_description = _meta_content(soup, name="description")

    company = (
        _first_text(soup, '[data-sentry-component="CompanyName"] h2')
        or json_ld.get("hiringOrganization", {}).get("name", "")
        or _meta_content(soup, name="writer")
        or fallback.get("company", "")
        or "회사명 없음"
    )
    title = (
        _first_text(soup, '[data-sentry-component="TitleContent"] h1')
        or json_ld.get("title", "")
        or fallback.get("title", "")
        or "제목 없음"
    )

    exp = _normalize_experience(_extract_item_value(soup, "QualificationItem", "경력"))
    if not exp:
        match = re.search(r"경력\s*:\s*([^,]+)", meta_description)
        exp = _clean_text(match.group(1)) if match else fallback.get("experience", "")

    education = _extract_item_value(soup, "QualificationItem", "학력")
    if not education:
        education = json_ld.get("educationRequirements", "")
    if not education:
        match = re.search(r"학력\s*:\s*([^,]+)", meta_description)
        education = _clean_text(match.group(1)) if match else ""

    skills = _extract_item_value(soup, "QualificationItem", "스킬")
    if not skills or skills.startswith(","):
        skills = _extract_skills_from_next_data(html) or skills.lstrip(", ")

    employment = _extract_item_value(soup, "RecruitmentItem", "고용형태")
    work_time = _extract_time_range(_extract_item_value(soup, "RecruitmentItem", "근무시간"))
    work_address = _extract_item_value(soup, "RecruitmentItem", "근무지주소")
    if not work_address:
        work_address = json_ld.get("jobLocation", {}).get("address", {}).get("streetAddress", "")

    return {
        "title": title,
        "company": company,
        "experience": exp or "경력 정보 없음",
        "education": education or "학력 정보 없음",
        "skills": skills or "스킬 정보 없음",
        "employment": employment or "근무 형태 정보 없음",
        "work_time": work_time or "근무 시간 정보 없음",
        "work_address": work_address or "근무 주소 정보 없음",
        "industry": _extract_company_box_value(soup, "산업(업종)") or "산업 업종 정보 없음",
        "company_type": _extract_company_box_value(soup, "기업구분") or "기업 구분 정보 없음",
        "company_size": _extract_company_box_value(soup, "사원수") or "기업 인원 정보 없음",
        "company_location": _extract_company_box_value(soup, "위치") or "기업 위치 정보 없음",
    }


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
            # 페이지를 순회합니다.
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
                        # 카드는 상세 링크 확보와 진행 로그/fallback 용도로만 가볍게 파싱합니다.
                        exp_tag = card_element.find("span", class_="flex-shrink-0 text-gray700 text-typo-c1-13")
                        corp_tag = card_element.find("span", class_="truncate text-gray700 text-typo-b2-16")
                        title_tag = card_element.find("span", class_="truncate font-semibold text-typo-b1-18 text-gray900")
                        link_tag = card_element.find("a", href=True)

                        card_meta = {
                            "experience": exp_tag.get_text(strip=True) if exp_tag else "경력 정보 없음",
                            "company": corp_tag.get_text(strip=True) if corp_tag else "회사명 없음",
                            "title": title_tag.get_text(strip=True) if title_tag else "제목 없음",
                        }

                        if not link_tag:
                            continue

                        # 상대 경로 링크는 잡코리아 도메인을 붙여 절대 URL로 바꿉니다.
                        raw_link = link_tag["href"]
                        job_link = raw_link if raw_link.startswith("http") else URL_BASE + raw_link

                        if job_link.strip() in visited_links:
                            _log(f"  ⏭️ [링크 중복 패스] {card_meta['company']} - {card_meta['title']}", log_fn)
                            continue

                        _log(f"  └ [신규 공고 발견] {card_meta['company']} - {card_meta['title']}", log_fn)

                        detail_page = await context.new_page()
                        try:
                            # 상세 페이지를 열고 본문 렌더링이 안정될 시간을 줍니다.
                            await _await_with_cancel(
                                detail_page.goto(job_link, wait_until="domcontentloaded", timeout=10000),
                                cancel_event,
                            )
                            await _interruptible_sleep(10, cancel_event)

                            # 상세 페이지 본문/구조화 데이터에서 실제 저장할 메타데이터를 우선 파싱합니다.
                            detail_html = await detail_page.content()
                            detail_meta = parse_detail_metadata(detail_html, card_meta)
                            title = detail_meta["title"]
                            corp = detail_meta["company"]

                            # 파일명에 쓸 수 없는 문자는 밑줄로 바꿔 저장 실패를 막습니다.
                            safe_filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", f"{corp}_{title}").strip("._") or "job"
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
link: {_yaml_string(job_link)}
title: {_yaml_string(title)}
company: {_yaml_string(corp)}
experience: {_yaml_string(detail_meta["experience"])}
education: {_yaml_string(detail_meta["education"])}
skills: {_yaml_string(detail_meta["skills"])}
employment: {_yaml_string(detail_meta["employment"])}
work_time: {_yaml_string(detail_meta["work_time"])}
work_address: {_yaml_string(detail_meta["work_address"])}
industry: {_yaml_string(detail_meta["industry"])}
company_type: {_yaml_string(detail_meta["company_type"])}
company_size: {_yaml_string(detail_meta["company_size"])}
company_location: {_yaml_string(detail_meta["company_location"])}
---

# {title}

- **회사명**: {corp}
- **필요 경력**: {detail_meta["experience"]}
- **필요 학력**: {detail_meta["education"]}
- **필요 스킬**: {detail_meta["skills"]}
- **근무 형태**: {detail_meta["employment"]}
- **근무 시간**: {detail_meta["work_time"]}
- **근무 주소**: {detail_meta["work_address"]}
- **산업 업종**: {detail_meta["industry"]}
- **기업 구분**: {detail_meta["company_type"]}
- **기업 인원**: {detail_meta["company_size"]}
- **기업 위치**: {detail_meta["company_location"]}
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
