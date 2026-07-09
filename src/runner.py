import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from src.scraper import scrape_jobkorea_full


async def run_scraper_with_cancel(
    cancel_event,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    try:
        await scrape_jobkorea_full(cancel_event=cancel_event, log_fn=log_fn)
    except asyncio.CancelledError:
        if log_fn:
            log_fn("🛑 수집 작업이 사용자에 의해 중단되었습니다.")
        raise
