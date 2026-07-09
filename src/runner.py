"""Streamlit 화면과 실제 크롤러 사이를 이어주는 실행 래퍼입니다."""

import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional

# `src/runner.py`를 직접 실행하거나 Docker에서 모듈로 불러도 프로젝트 루트를 import 경로에 포함합니다.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from src.scraper import scrape_jobkorea_full


async def run_scraper_with_cancel(
    cancel_event,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """취소 이벤트와 로그 콜백을 크롤러에 전달해 실행합니다."""

    try:
        await scrape_jobkorea_full(cancel_event=cancel_event, log_fn=log_fn)
    except asyncio.CancelledError:
        # 취소는 정상 흐름에 가까우므로, 화면 로그에 남긴 뒤 호출자에게 다시 알려줍니다.
        if log_fn:
            log_fn("🛑 수집 작업이 사용자에 의해 중단되었습니다.")
        raise
