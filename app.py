from crawling.scraper import scrape_jobkorea_full
import asyncio

if __name__ == "__main__":
    asyncio.run(scrape_jobkorea_full())