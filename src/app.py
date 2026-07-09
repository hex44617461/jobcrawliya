"""채용 공고 수집 결과를 보여주고 크롤러를 제어하는 Streamlit 앱입니다."""

import asyncio
import os
import queue
import re
import sys
import threading
import time

import streamlit as st

# 프로젝트 루트를 import 경로에 넣어 Docker와 로컬 실행 경로 차이를 흡수합니다.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.runner import run_scraper_with_cancel

# Streamlit 페이지 기본 설정과 상단 제목입니다.
st.set_page_config(page_title="DE 채용공고 로컬 대시보드 🚀", layout="wide")
st.title("📋 로컬 데이터 엔지니어 채용 공고 모니터링")

# 크롤러가 저장하는 마크다운과 이미지 위치입니다.
DIR_BASE = os.path.join(ROOT_DIR, "data", "scraped")
DIR_POST = os.path.join(DIR_BASE, "posts")
DIR_IMG = os.path.join(DIR_BASE, "images")


@st.cache_data(ttl=2)
def load_local_jobs():
    """저장된 마크다운 공고를 읽어 화면에 표시할 목록으로 변환합니다."""

    jobs = []
    if not os.path.exists(DIR_POST):
        return jobs

    # 파일명을 기준으로 정렬해 화면에서 목록 순서가 매번 흔들리지 않게 합니다.
    for filename in sorted(os.listdir(DIR_POST)):
        if filename.endswith(".md"):
            file_path = os.path.join(DIR_POST, filename)
            base_filename = os.path.splitext(filename)[0]
            try:
                # 크롤러가 생성한 YAML/본문 일부를 정규식으로 읽어 카드 정보를 구성합니다.
                with open(file_path, "r", encoding="utf-8") as post_file:
                    content = post_file.read()
                title_match = re.search(r'title:\s*"([^"]+)"', content)
                company_match = re.search(r'company:\s*"([^"]+)"', content)
                link_match = re.search(r'link:\s*"([^"]+)"', content)
                exp_match = re.search(r'-\s*\*\*경력 요건\*\*:\s*([^\n]+)', content)

                title = title_match.group(1) if title_match else "제목 없음"
                company = company_match.group(1) if company_match else "회사명 없음"
                link = link_match.group(1) if link_match else ""
                exp = exp_match.group(1).strip() if exp_match else "경력 정보 없음"
                expected_png_path = os.path.join(DIR_IMG, f"{base_filename}.png")

                jobs.append({
                    "title": title,
                    "company": company,
                    "link": link,
                    "experience": exp,
                    "img_path": expected_png_path,
                })
            except Exception:
                # 깨진 마크다운 하나 때문에 전체 화면 로딩이 멈추지 않도록 건너뜁니다.
                pass
    return jobs


def _init_session_state():
    """Streamlit rerun 사이에 유지할 크롤러 상태값을 초기화합니다."""

    defaults = {
        "logs": [],
        "log_queue": queue.Queue(),
        "running": False,
        "cancel_event": None,
        "thread": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


_init_session_state()


def drain_log_queue():
    """백그라운드 스레드가 쌓아둔 로그를 Streamlit 상태로 옮깁니다."""

    while True:
        try:
            message = st.session_state.log_queue.get_nowait()
        except queue.Empty:
            break
        st.session_state.logs.append(message)
    # 화면 로그가 무한히 길어지지 않도록 최근 200줄만 유지합니다.
    st.session_state.logs = st.session_state.logs[-200:]


def run_scraper():
    """수집 버튼을 눌렀을 때 백그라운드 스레드로 크롤러를 시작합니다."""

    if st.session_state.running:
        return

    cancel_event = threading.Event()
    # 스레드 안에서는 st.session_state를 직접 만지지 않도록 큐 객체만 캡처합니다.
    log_queue = st.session_state.log_queue
    st.session_state.running = True
    st.session_state.cancel_event = cancel_event
    st.session_state.logs.clear()
    # 이전 실행에서 남은 로그 메시지를 비워 새 수집 로그만 보이게 합니다.
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break

    def log_fn(message: str):
        """크롤러 스레드가 화면으로 보낼 로그를 큐에 넣습니다."""

        log_queue.put(message)

    log_fn("🚀 수집을 시작합니다...")

    def target():
        """별도 스레드에서 async 크롤러를 실행하는 실제 작업 함수입니다."""

        try:
            asyncio.run(
                run_scraper_with_cancel(
                    cancel_event,
                    log_fn=log_fn,
                )
            )
        except asyncio.CancelledError:
            log_fn("🛑 수집이 중지되었습니다.")
        except Exception as e:
            log_fn(f"❌ 오류 발생: {e}")
        finally:
            log_fn("✅ 수집 작업이 종료되었습니다.")

    # daemon=True로 앱 종료 시 백그라운드 스레드가 프로세스를 붙잡지 않게 합니다.
    st.session_state.thread = threading.Thread(target=target, daemon=True)
    st.session_state.thread.start()


def stop_scraper():
    """중지 버튼을 눌렀을 때 크롤러에 취소 신호를 보냅니다."""

    if st.session_state.cancel_event is not None and not st.session_state.cancel_event.is_set():
        st.session_state.cancel_event.set()
        st.session_state.log_queue.put("⏹ 중지 요청을 보냈습니다...")


# 매 rerun마다 백그라운드 로그를 화면 상태로 옮깁니다.
drain_log_queue()

# 스레드가 끝났으면 UI 상태를 정리하고 공고 목록 캐시를 갱신합니다.
if st.session_state.thread and not st.session_state.thread.is_alive():
    st.session_state.running = False
    st.session_state.cancel_event = None
    st.session_state.thread = None
    load_local_jobs.clear()

# 사이드바에는 크롤러 시작/중지 버튼을 둡니다.
st.sidebar.header("🧰 수집 제어")
col1, col2 = st.sidebar.columns(2)
with col1:
    if st.button("수집 시작", disabled=st.session_state.running):
        run_scraper()
        st.rerun()
with col2:
    if st.button("중지", disabled=not st.session_state.running):
        stop_scraper()
        st.rerun()

if st.session_state.running:
    st.sidebar.warning("⏳ 수집 진행 중...")
else:
    st.sidebar.info("수집 중에는 로그가 실시간으로 표시됩니다.")

# 저장된 공고를 읽고 왼쪽 목록/오른쪽 상세 화면으로 나눠 보여줍니다.
jobs_list = load_local_jobs()
if not jobs_list:
    st.info(f"현재 폴더에 마크다운 파일이 없습니다.\n📂 경로: {DIR_POST}")
else:
    col_list, col_detail = st.columns([1, 1.4])
    with col_list:
        st.subheader(f"🔥 수집된 공고 리스트 (총 {len(jobs_list)}건)")
        job_labels = [f"[{job['company']}] {job['title']}" for job in jobs_list]
        selected_label = st.radio("상세히 볼 공고를 선택하세요 👇", job_labels)
        selected_idx = job_labels.index(selected_label)
        selected_job = jobs_list[selected_idx]

    with col_detail:
        st.subheader("🔍 공고 상세 정보")
        st.write(f"🏢 **회사명:** {selected_job['company']}")
        st.write(f"🎓 **경력 조건:** {selected_job['experience']}")
        if selected_job['link']:
            st.write(f"🔗 **원본 링크:** [{selected_job['link']}]({selected_job['link']})")

        st.divider()
        st.subheader("📄 공고 본문 캡처본")
        img_path = selected_job['img_path']
        if img_path and os.path.exists(img_path):
            st.image(img_path, use_container_width=True)
        else:
            st.error("⚠️ 이미지 파일을 찾을 수 없습니다!")
            st.info(f"스트림릿이 찾아간 경로: {img_path}")

# 수집 중인 로그를 앱 하단에 표시합니다.
st.subheader("📝 수집 로그")
if st.session_state.logs:
    st.code("\n".join(st.session_state.logs), language=None)
else:
    st.caption("수집을 시작하면 로그가 여기에 표시됩니다.")

# Streamlit은 자동 push가 없으므로 수집 중에는 주기적으로 rerun해 로그를 갱신합니다.
if st.session_state.running:
    time.sleep(2)
    st.rerun()
