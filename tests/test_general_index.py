"""
The one-time General index tool with the fake Telegram client of conftest.py: no network and no account needed.
"""
import asyncio

import pytest

import general_index
from conftest import FakeGroup, FakeTopic

GROUP = FakeGroup("group")

@pytest.fixture(autouse=True)
def fake_forum_topic(monkeypatch):
    monkeypatch.setattr(general_index, "ForumTopic", FakeTopic)

def add_movie_topic(telegram, title="Película (2024) {tmdb-1}", poster="**🎬| Película**"):
    topic = telegram.add_topic(title)
    if poster:
        topic.messages.append(telegram.photo_message(poster, "poster.jpg"))
    topic.messages.append(telegram.file_message("file.mkv"))
    return topic

def build(telegram, dry_run=False):
    return asyncio.run(general_index.build_index(telegram, GROUP, dry_run))

def test_entries_are_posted_oldest_topic_first(telegram):
    topics = [add_movie_topic(telegram, f"Movie {number} {{tmdb-{number}}}", f"**🎬| Movie {number}**") for number in range(1, 4)]

    build(telegram)

    assert [message.message.splitlines()[0] for message in telegram.general] == ["🎬| Movie 1", "🎬| Movie 2", "🎬| Movie 3"]
    assert [message.caption.endswith(f"[¡Ir a la película!](https://t.me/c/555/{topic.id})**") for message, topic in zip(telegram.general, topics)] == [True] * 3

def test_entries_are_silent_and_reuse_the_photo(telegram):
    add_movie_topic(telegram)

    build(telegram)

    [entry] = telegram.general
    assert entry.silent
    assert entry.image == "poster.jpg"
    assert telegram.uploaded_files == []

def test_second_run_posts_nothing(telegram):
    add_movie_topic(telegram)
    build(telegram)

    build(telegram)

    assert len(telegram.general) == 1

def test_new_run_only_posts_the_missing_topics(telegram):
    add_movie_topic(telegram, "Old {tmdb-1}")
    build(telegram)
    add_movie_topic(telegram, "New {tmdb-2}", "**🎬| New**")

    build(telegram)

    assert len(telegram.general) == 2
    assert telegram.general[1].message.startswith("🎬| New")

def test_topic_already_linked_by_another_tool_is_skipped(telegram):
    topic = add_movie_topic(telegram)
    telegram.general.append(telegram.photo_message(f"**Serie\n👉 | [¡Ir a la serie!](https://t.me/c/555/{topic.id})**"))

    build(telegram)

    assert len(telegram.general) == 1

def test_dry_run_posts_nothing(telegram, capsys):
    add_movie_topic(telegram)

    build(telegram, dry_run=True)

    assert telegram.general == []
    assert "Would post (1): Película (2024) {tmdb-1}" in capsys.readouterr().out

def test_movie_without_poster_posts_its_text(telegram):
    topic = telegram.add_topic("Película {tmdb-1}")
    topic.messages.append(telegram.text_message("🎬| **Película**"))

    build(telegram)

    [entry] = telegram.general
    assert entry.photo is None
    assert entry.silent
    assert entry.text.endswith(f"[¡Ir a la película!](https://t.me/c/555/{topic.id})**")

def test_topics_without_tag_or_poster_are_reported(telegram):
    add_movie_topic(telegram, "No tag")
    add_movie_topic(telegram, "No poster {tmdb-1}", poster=None)

    assert build(telegram) == [("No tag", "no {tmdb-<id>} tag"), ("No poster {tmdb-1}", "no poster found")]
    assert telegram.general == []

def test_general_topic_is_not_posted(telegram):
    telegram.topics[1] = FakeTopic(1, "General {tmdb-1}")

    assert build(telegram) == []
    assert telegram.general == []

def test_all_pages_are_read(telegram, monkeypatch):
    monkeypatch.setattr(general_index, "TOPICS_PAGE_SIZE", 2)
    for number in range(1, 6):
        add_movie_topic(telegram, f"Movie {number} {{tmdb-{number}}}")

    build(telegram)

    assert len(telegram.general) == 5

def test_failure_stops_the_run_and_next_run_keeps_the_order(telegram, monkeypatch):
    add_movie_topic(telegram, "First {tmdb-1}", "**🎬| First**")
    add_movie_topic(telegram, "Second {tmdb-2}", "**🎬| Second**")
    add_movie_topic(telegram, "Third {tmdb-3}", "**🎬| Third**")
    real_send_file = telegram.send_file
    async def failing_send_file(entity, file, caption, silent):
        if "Second" in caption:
            raise ConnectionError("Connection reset")
        await real_send_file(entity=entity, file=file, caption=caption, silent=silent)
    monkeypatch.setattr(telegram, "send_file", failing_send_file)

    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        build(telegram)
    assert len(telegram.general) == 1

    monkeypatch.setattr(telegram, "send_file", real_send_file)
    build(telegram)

    assert [message.message.splitlines()[0] for message in telegram.general] == ["🎬| First", "🎬| Second", "🎬| Third"]
