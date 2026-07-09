"""`streamlit run app.py` 명령을 위한 호환 진입점입니다.

실제 Streamlit 앱은 `src/app.py`에 있습니다.
여기서는 그 파일을 그대로 불러와 로컬 실행과 Docker 실행이 같은 구현을 쓰게 합니다.
"""

# Streamlit은 import 시점에 화면을 그리므로, src.app의 모든 정의와 실행 흐름을 그대로 사용합니다.
from src.app import *  # noqa: F401,F403
