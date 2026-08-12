"""Who gets an answer at all — driven through the real dispatcher."""

from aiogram.methods import SendMessage

from tests.helpers.bot_harness import BotHarness
from tests.helpers.factories import GUEST_ID, OWNER_ID, STRANGER_ID

TWEET = "https://x.com/someone/status/1234567890"


async def test_the_owner_is_served(harness: BotHarness) -> None:
    await harness.send("/start", user_id=OWNER_ID)
    assert harness.session.calls_of(SendMessage)


async def test_a_guest_is_served_too(harness: BotHarness) -> None:
    await harness.send("/start", user_id=GUEST_ID)
    assert harness.session.calls_of(SendMessage)


async def test_a_stranger_gets_silence_not_a_refusal(harness: BotHarness) -> None:
    await harness.send("/start", user_id=STRANGER_ID)
    assert harness.session.calls == []


async def test_a_stranger_cannot_spend_the_owners_x_session_either(harness: BotHarness) -> None:
    await harness.send(TWEET, user_id=STRANGER_ID)
    assert harness.session.calls == []
    assert harness.queue.load == 0


async def test_group_traffic_is_ignored_even_from_an_allowed_user(harness: BotHarness) -> None:
    await harness.send(TWEET, user_id=OWNER_ID, chat_type="supergroup")
    assert harness.session.calls == []
    assert harness.queue.load == 0


async def test_a_stranger_leaves_nothing_behind_in_memory(harness: BotHarness) -> None:
    # aiogram resolves an FSM context before any gate registered here can run,
    # so the default MemoryStorage (a defaultdict) recorded a key per sender —
    # unbounded growth driven by people who are refused anyway.
    for offset in range(20):
        await harness.send("hi", user_id=STRANGER_ID + offset)

    records = getattr(harness.dp.fsm.storage, "storage", None)
    assert not records
    assert harness.session.calls == []
