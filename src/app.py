import asyncio
import os
import queue
import re
import threading
import time

import streamlit as st

from src.runner import run_scraper_with_cancel

st.set_page_config(page_title="DE 채용공고 로컬 대시보드 🚀", layout="wide")
st.title("📋 로컬 데이터 엔지니어 채용 공고 모니터링")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIR_BASE = os.path.join(ROOT_DIR, "data", "scraped")
DIR_POST = os.path.join(DIR_BASE, "posts")
DIR_IMG = os.path.join(DIR_BASE, "images")


@st.cache_data(ttl=2)
def load_local_jobs():
    jobs = []
    if not os.path.exists(DIR_POST):
        return jobs

    for filename in sorted(os.listdir(DIR_POST)):
        if filename.endswith(".md"):
            file_path = os.path.join(DIR_POST, filename)
            base_filename = os.path.splitext(filename)[0]
            try:
                content = open(file_path, "r", encoding="utf-8").read()
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
                pass
    return jobs


def _init_session_state():
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
    while True:
        try:
            message = st.session_state.log_queue.get_nowait()
        except queue.Empty:
            break
        st.session_state.logs.append(message)
    st.session_state.logs = st.session_state.logs[-200:]


def run_scraper():
    st.session_state.running = True
    st.session_state.cancel_event = threading.Event()
    st.session_state.logs.clear()
    while not st.session_state.log_queue.empty():
        try:
            st.session_state.log_queue.get_nowait()
        except queue.Empty:
            break

    def log_fn(message: str):
        st.session_state.log_queue.put(message)

    log_fn("🚀 수집을 시작합니다...")

    def target():
        try:
            asyncio.run(
                run_scraper_with_cancel(
                    st.session_state.cancel_event,
                    log_fn=log_fn,
                )
            )
        except asyncio.CancelledError:
            log_fn("🛑 수집이 중지되었습니다.")
        except Exception as e:
            log_fn(f"❌ 오류 발생: {e}")
        finally:
            log_fn("✅ 수집 작업이 종료되었습니다.")

    st.session_state.thread = threading.Thread(target=target, daemon=True)
    st.session_state.thread.start()


def stop_scraper():
    if st.session_state.cancel_event is not None:
        st.session_state.cancel_event.set()
        st.session_state.log_queue.put("⏹ 중지 요청을 보냈습니다...")


drain_log_queue()

if st.session_state.running and st.session_state.thread and not st.session_state.thread.is_alive():
    st.session_state.running = False
    st.session_state.cancel_event = None
    st.session_state.thread = None
    load_local_jobs.clear()

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

st.subheader("📝 수집 로그")
if st.session_state.logs:
    st.code("\n".join(st.session_state.logs), language=None)
else:
    st.caption("수집을 시작하면 로그가 여기에 표시됩니다.")

if st.session_state.running:
    time.sleep(2)
    st.rerun()
