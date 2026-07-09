"""Package entry point so you can run the scraper with `python -m crawling`."""
from .scraper import scrape_jobkorea_full
import asyncio


def main():
    asyncio.run(scrape_jobkorea_full())


if __name__ == "__main__":
    main()
