import os
import re
import streamlit as st

st.set_page_config(page_title="DE 채용공고 로컬 대시보드 🚀", layout="wide")
st.title("📋 로컬 데이터 엔지니어 채용 공고 모니터링")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIR_BASE = os.path.join(ROOT_DIR, "jobcrawliya", "data", "scraped")
DIR_POST = os.path.join(DIR_BASE, "posts")
DIR_IMG = os.path.join(DIR_BASE, "images")


@st.cache_data(ttl=2)
def load_local_jobs():
    jobs = []
    if not os.path.exists(DIR_POST):
        return jobs

    for filename in os.listdir(DIR_POST):
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
