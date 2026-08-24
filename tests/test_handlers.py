"""What a whitelisted user sees when they send things."""

from pathlib import Path

from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from clipivore.bot import texts
from clipivore.services.overflow import OverflowCatalog
from clipivore.services.providers import ProviderCatalog, ProviderContext
from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import OWNER_ID

TWEET = "https://x.com/someone/status/1234567890"
OTHER_TWEET = "https://x.com/someone/status/9876543210"


async def test_help_states_the_size_limit_that_changes_where_clips_land(
    harness: BotHarness,
) -> None:
    await harness.send("/start", user_id=OWNER_ID)
    help_text = harness.session.sent_texts()[0]
    assert "50 MB" in help_text
    assert "Overflow delivery is off" in help_text


async def test_help_names_the_current_overflow_destination(
    harness: BotHarness, tmp_path: Path
) -> None:
    catalog = OverflowCatalog("tests.helpers.fake_adapters", state_file=tmp_path / "selection")
    catalog.select("test")
    harness.dp["overflow_catalog"] = catalog

    await harness.send("/help", user_id=OWNER_ID)

    assert "Larger clips are delivered through Test destination" in harness.session.sent_texts()[0]


async def test_a_link_is_queued_and_acknowledged(harness: BotHarness) -> None:
    await harness.send(TWEET, user_id=OWNER_ID)

    assert harness.queue.load == 1
    assert harness.session.sent_texts() == [texts.QUEUED]


async def test_a_request_keeps_the_adapter_selected_when_the_link_was_accepted(
    harness: BotHarness, tmp_path: Path
) -> None:
    catalog = OverflowCatalog("tests.helpers.fake_adapters", state_file=tmp_path / "selection")
    catalog.select("test")
    harness.dp["overflow_catalog"] = catalog

    await harness.send(TWEET, user_id=OWNER_ID)
    request = await harness.queue.take()

    assert request.overflow.adapter_id == "test"
    harness.queue.release()


async def test_every_link_in_one_message_gets_its_own_request(harness: BotHarness) -> None:
    await harness.send(f"{TWEET} and also {OTHER_TWEET}", user_id=OWNER_ID)

    assert harness.queue.load == 2
    assert len(harness.session.calls_of(SendMessage)) == 2


async def test_links_from_one_message_share_one_source_message(harness: BotHarness) -> None:
    await harness.send(f"{TWEET} and also {OTHER_TWEET}", user_id=OWNER_ID)

    first = await harness.queue.take()
    harness.queue.release()
    second = await harness.queue.take()
    harness.queue.release()

    # The worker refcounts terminal outcomes on this shared object, so the
    # user's message goes away only once both links were delivered.
    assert first.source is not None
    assert first.source is second.source


async def test_the_position_in_line_is_shown_once_there_is_a_line(harness: BotHarness) -> None:
    await harness.send(f"{TWEET} {OTHER_TWEET}", user_id=OWNER_ID)

    edits = [
        method.text
        for method in harness.session.calls_of(EditMessageText)
        if getattr(method, "text", None)
    ]
    assert edits == [texts.QUEUED_POSITION.format(position=2)]


async def test_a_full_queue_refuses_the_link_instead_of_dropping_it_quietly(
    harness: BotHarness,
) -> None:
    limit = harness.queue.limit
    await harness.send(" ".join(f"https://x.com/a/status/{n}" for n in range(limit)), user_id=1)
    harness.session.clear()

    await harness.send(TWEET, user_id=OWNER_ID)

    assert harness.queue.load == limit
    assert texts.QUEUE_FULL.format(limit=limit) in harness.session.sent_texts()


async def test_a_refused_link_keeps_the_message_even_if_the_accepted_ones_succeed(
    harness: BotHarness,
) -> None:
    limit = harness.queue.limit
    # One message with one link more than the queue holds: the last link is
    # refused, so the batch is tainted and the message must survive.
    links = " ".join(f"https://x.com/a/status/{n}" for n in range(limit + 1))
    await harness.send(links, user_id=OWNER_ID)

    for _ in range(limit):
        request = await harness.queue.take()
        assert request.source is not None
        await request.source.resolve(succeeded=True)
        harness.queue.release()

    assert not harness.session.calls_of(DeleteMessage)


async def test_a_message_without_links_says_so(harness: BotHarness) -> None:
    await harness.send("please download that video I mentioned yesterday", user_id=OWNER_ID)

    assert harness.session.sent_texts() == [texts.NO_LINK]
    assert harness.queue.load == 0


async def test_help_lists_what_the_bot_can_be_sent(harness: BotHarness) -> None:
    # The /help text is generated from the discovered Providers, so adding one
    # cannot leave the help behind.
    await harness.send("/help", user_id=OWNER_ID)

    help_text = harness.session.sent_texts()[0]
    for choice in harness.provider_catalog.ready:
        assert choice.name in help_text


async def test_a_link_claimed_by_a_broken_provider_is_refused_by_name(
    harness: BotHarness,
) -> None:
    # Silence is what an unknown link gets. A platform the bot knows about but
    # cannot serve owes the person an explanation instead — and must not spend
    # a queue slot on it.
    harness.dp["provider_catalog"] = ProviderCatalog(
        ProviderContext(), package="tests.helpers.fake_providers"
    )

    await harness.send("https://broken.example/1", user_id=OWNER_ID)

    assert harness.queue.load == 0
    assert any("Broken" in text for text in edited_texts(harness))
    # The user's message survives, so the link can be retried after a fix.
    assert not harness.session.calls_of(DeleteMessage)


async def test_a_working_provider_in_the_same_message_is_still_queued(
    harness: BotHarness,
) -> None:
    harness.dp["provider_catalog"] = ProviderCatalog(
        ProviderContext(), package="tests.helpers.fake_providers"
    )

    await harness.send("https://broken.example/1 https://good.example/2", user_id=OWNER_ID)

    assert harness.queue.load == 1


def edited_texts(harness: BotHarness) -> list[str]:
    return [
        text
        for method in harness.session.calls_of(EditMessageText)
        if (text := getattr(method, "text", None))
    ]
