"""
One-time tool: "python src/general_index.py [--dry-run]". Posts in the General topic of the movies group the poster
of every existing topic, oldest topic first, with a link to the topic. New movies are posted there by the movies mode.
It can be run again if it stops: the topics already linked from the General topic are skipped.
"""
import argparse
import asyncio
import os
import re

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import ForumTopic, MessageEntityTextUrl

from core.config import Config, ConfigError
from core.constants import PROJECT_ROOT, Step
from core.telegram_api_client import PERMANENT_TELEGRAM_ERRORS, SESSION_PATH, get_topic_link
from core.tmdb import parse_tmdb_id
from core.utils import with_retries_async
from movies.movie import make_general_caption
from movies.telegram_api_client import POSTER_CAPTION_START

GENERAL_TOPIC_ID = 1
TOPICS_PAGE_SIZE = 100
POSTER_SEARCH_MESSAGES = 5  # The poster is one of the first messages of the topic
TOPIC_LINK_REGEX = re.compile(r"t\.me/c/\d+/(\d+)")

def retry(label, operation):
    return with_retries_async(Step.UPLOAD, label, operation, PERMANENT_TELEGRAM_ERRORS)

async def get_all_topics(client, supergroup):
    """
    Returns every topic of the group, page by page, the oldest first. Topic ids grow with time.
    """
    topics = {}
    offset_date, offset_id, offset_topic = None, 0, 0
    while True:
        request = GetForumTopicsRequest(peer=supergroup, offset_date=offset_date, offset_id=offset_id,
                                        offset_topic=offset_topic, limit=TOPICS_PAGE_SIZE)
        response = await retry("topics list", lambda: client(request))
        page = [topic for topic in response.topics if isinstance(topic, ForumTopic)]
        new_topics = [topic for topic in page if topic.id not in topics]
        if not new_topics:
            return sorted(topics.values(), key=lambda topic: topic.id)
        topics.update((topic.id, topic) for topic in new_topics)
        # The next page starts after the last topic of this one
        last = page[-1]
        offset_date = next(message.date for message in response.messages if message.id == last.top_message)
        offset_id, offset_topic = last.top_message, last.id

async def get_linked_topic_ids(client, supergroup):
    """
    Returns the ids of the topics that have a link in a message of the General topic.
    """
    async def read():
        linked = set()
        async for message in client.iter_messages(supergroup, reply_to=GENERAL_TOPIC_ID):
            for entity in message.entities or []:
                match = TOPIC_LINK_REGEX.search(entity.url) if isinstance(entity, MessageEntityTextUrl) else None
                if match:
                    linked.add(int(match.group(1)))
        return linked
    return await retry("General topic", read)

async def find_poster(client, supergroup, topic):
    """
    Returns the poster message of the topic: its first photo, or its text if the movie had no poster. None if it has neither.
    """
    async def read():
        return [message async for message in client.iter_messages(supergroup, reply_to=topic.id, reverse=True, limit=POSTER_SEARCH_MESSAGES)]
    messages = await retry(topic.title, read)
    for message in messages:
        if message.photo or (message.message or "").startswith(POSTER_CAPTION_START):
            return message
    return None

async def post_entry(client, supergroup, topic, poster):
    # Silent, so hundreds of posts do not notify every member. The photo is reused, nothing is uploaded again
    caption = make_general_caption(poster.text, get_topic_link(supergroup, topic.id))
    if poster.photo:
        await retry(topic.title, lambda: client.send_file(entity=supergroup, file=poster.photo, caption=caption, silent=True))
    else:
        await retry(topic.title, lambda: client.send_message(entity=supergroup, message=caption, silent=True, link_preview=False))

async def build_index(client, supergroup, dry_run):
    """
    Posts the missing topics in order and returns the titles of the ones skipped, with the reason.
    A topic that fails after its retries stops the run, as going on would break the order.
    """
    topics = await get_all_topics(client, supergroup)
    linked = await get_linked_topic_ids(client, supergroup)
    print(f"{len(topics)} topics in the group, {len(linked)} already linked from the General topic")

    skipped = []
    posted = 0
    for topic in topics:
        if topic.id == GENERAL_TOPIC_ID or topic.id in linked:
            continue
        if parse_tmdb_id(topic.title) is None:
            skipped.append((topic.title, "no {tmdb-<id>} tag"))
            continue
        poster = await find_poster(client, supergroup, topic)
        if poster is None:
            skipped.append((topic.title, "no poster found"))
            continue
        if not dry_run:
            await post_entry(client, supergroup, topic, poster)
        posted += 1
        print(f"{'Would post' if dry_run else 'Posted'} ({posted}): {topic.title}")

    print(f"\n{'Would post' if dry_run else 'Posted'}: {posted}")
    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for title, reason in skipped:
            print(f"  {title}: {reason}")
    return skipped

async def run(config, dry_run):
    client = TelegramClient(SESSION_PATH, int(os.getenv("API_ID")), os.getenv("API_HASH"))
    try:
        await client.start(os.getenv("PHONE_NUMBER"))
        supergroup = await client.get_entity(config.telegram_group)
        print(f"Group: {supergroup.title}{' (dry run, nothing is posted)' if dry_run else ''}")
        await build_index(client, supergroup, dry_run)
    finally:
        await client.disconnect()

def main():
    parser = argparse.ArgumentParser(description="Posts every existing movie topic in the General topic of the group.")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be posted")
    dry_run = parser.parse_args().dry_run

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    try:
        config = Config("movies")
    except ConfigError as e:
        print(f"Invalid config file: {e}")
        return
    asyncio.run(run(config, dry_run))

if __name__ == "__main__":
    main()
