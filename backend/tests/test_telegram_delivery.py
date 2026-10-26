"""Synthetic chats and disposable SQLite outboxes; never contacts Telegram."""
import pytest

from app.telegram_delivery import (
    BOT_INTERVAL_MS, CHAT_INTERVAL_MS, GROUP_INTERVAL_MS, RETRY_MARGIN_MS,
    TelegramOutbox, destination_from_chat, retry_after_from_response,
)


def test_telegram_supplies_private_destination_type_without_a_manual_selector():
    result = destination_from_chat({"id": 123456, "type": "private", "first_name": "Alex"})
    assert result == {"chat_id": 123456, "chat_type": "private", "title": "Alex"}


def test_telegram_429_reports_when_to_retry_not_a_remaining_quota_query():
    assert retry_after_from_response(429, {"ok": False, "parameters": {"retry_after": 17}}) == 17


def test_confirmed_recipient_and_automatic_type_are_saved_for_the_project(tmp_path):
    path = tmp_path / "notifications" / "telegram-outbox.sqlite3"
    box = TelegramOutbox(path)
    result = box.configure_project(owner_id=1, project_id=42, bot_id=123456, binding_id="a" * 32,
        destination={"chat_id": 555, "chat_type": "private", "title": "Alex"}, now_ms=1000)
    assert result == {"owner_id": 1, "project_id": 42, "bot_id": 123456, "binding_id": "a" * 32,
        "chat_id": 555, "chat_type": "private", "title": "Alex", "verified_at_ms": 1000}
    reopened = TelegramOutbox(path)
    assert reopened.project_recipient(owner_id=1, project_id=42) == result
    assert reopened.project_recipient(owner_id=2, project_id=42) is None
    assert reopened.project_recipient(owner_id=1, project_id=43) is None


def test_changing_recipient_does_not_send_an_old_project_queue_to_either_target(outbox):
    original = {"chat_id": -100123, "chat_type": "group", "title": "Original"}
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="a" * 32,
                             destination=original, now_ms=0)
    queued = enqueue(outbox)
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="b" * 32,
        destination={"chat_id": -100456, "chat_type": "group", "title": "New"}, now_ms=1)
    assert outbox.get(queued["id"], owner_id=1)["state"] == "CANCELLED"
    assert outbox.claim_ready(999999) is None
    with pytest.raises(ValueError, match="telegram_recipient_changed"):
        enqueue(outbox, now=2)  # A late result still carries the old destination.


def test_changing_one_project_recipient_preserves_another_projects_pending_post(outbox):
    original = {"chat_id": -100123, "chat_type": "group", "title": "Original"}
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="a" * 32,
                             destination=original, now_ms=0)
    queued = enqueue(outbox)
    enqueue(outbox, project=2)
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="b" * 32,
        destination={"chat_id": -100456, "chat_type": "group", "title": "New"}, now_ms=1)
    claimed = outbox.claim_ready(1)
    assert claimed["id"] == queued["id"]
    assert claimed["active_project_ids"] == [2]


def test_recipient_cannot_change_under_an_in_flight_publication(outbox):
    original = {"chat_id": -100123, "chat_type": "group", "title": "Original"}
    saved = outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="a" * 32,
                                     destination=original, now_ms=0)
    enqueue(outbox)
    assert outbox.claim_ready(0)
    with pytest.raises(ValueError, match="telegram_recipient_busy"):
        outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="b" * 32,
            destination={"chat_id": -100456, "chat_type": "group", "title": "New"}, now_ms=1)
    assert outbox.project_recipient(owner_id=1, project_id=1) == saved


def test_a_fresh_intent_from_another_project_can_revive_a_never_sent_cancelled_post(outbox):
    original = {"chat_id": -100123, "chat_type": "group", "title": "Original"}
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="a" * 32,
                             destination=original, now_ms=0)
    queued = enqueue(outbox)
    outbox.configure_project(owner_id=1, project_id=1, bot_id=123456, binding_id="b" * 32,
        destination={"chat_id": -100456, "chat_type": "group", "title": "New"}, now_ms=1)
    assert outbox.get(queued["id"], owner_id=1)["state"] == "CANCELLED"
    fresh = enqueue(outbox, project=2, now=2)
    assert fresh["id"] == queued["id"]
    assert fresh["state"] == "QUEUED"
    assert outbox.claim_ready(2)["active_project_ids"] == [2]


@pytest.mark.parametrize("kind,chat_id", [("private", 123), ("group", -123),
    ("supergroup", -100123), ("channel", -100999)])
def test_real_telegram_types_are_retained(kind, chat_id):
    assert destination_from_chat({"id": chat_id, "type": kind}) == {
        "chat_id": chat_id, "chat_type": kind, "title": str(chat_id)}


@pytest.mark.parametrize("chat", [None, [], {}, {"id": True, "type": "private"},
    {"id": "123", "type": "private"}, {"id": 2**52, "type": "private"},
    {"id": 0, "type": "group"}, {"id": -123, "type": "private"},
    {"id": 123, "type": "group"}, {"id": 123, "type": "person"}, {"id": 123, "type": []}])
def test_unknown_or_malformed_metadata_never_defaults_to_the_less_restrictive_type(chat):
    with pytest.raises(ValueError, match="telegram_destination_invalid"):
        destination_from_chat(chat)


def enqueue(box, key="copart/lot/round/initial", *, owner=1, project=1, bot=123456,
            chat=-100123, kind="group", now=0):
    return box.enqueue(owner_id=owner, project_id=project, bot_id=bot,
        destination={"chat_id": chat, "chat_type": kind, "title": "Fixture"},
        notification_key=key, now_ms=now)


@pytest.fixture
def outbox(tmp_path):
    return TelegramOutbox(tmp_path / "notifications" / "telegram-outbox.sqlite3")


def test_project_retry_deduplication_keeps_project_context_but_not_foreign_owner_data(outbox):
    first = enqueue(outbox)
    second = enqueue(outbox, project=2)
    assert first["id"] == second["id"]
    assert second["project_ids"] == [1, 2]
    assert second["chat_type"] == "group"
    assert outbox.get(first["id"], owner_id=2) is None
    foreign = enqueue(outbox, owner=2, project=4)
    assert foreign["id"] != first["id"]
    assert outbox.get(foreign["id"], owner_id=1) is None


@pytest.mark.parametrize("kind", ["group", "supergroup"])
def test_30_cars_in_a_group_are_spread_out_instead_of_a_burst(outbox, kind):
    for number in range(30):
        enqueue(outbox, f"copart/{number}/round/initial", project=1 + number % 3, kind=kind)
    admissions = []
    for number in range(30):
        instant = number * GROUP_INTERVAL_MS
        assert outbox.next_wake_ms(instant) == instant
        job = outbox.claim_ready(instant)
        assert job is not None
        admissions.append(instant)
        assert outbox.claim_ready(instant) is None
        outbox.sent(job["id"], job["attempt_id"], message_id=number + 1)
    assert outbox.next_wake_ms(admissions[-1]) is None
    assert admissions[-1] == 92800
    assert all(sum(start <= value < start + 60000 for value in admissions) <= 20 for start in admissions)


@pytest.mark.parametrize("kind,chat", [("private", 555), ("channel", -555)])
def test_private_chat_and_channel_attempts_use_their_own_smooth_interval(outbox, kind, chat):
    for number in range(2):
        enqueue(outbox, f"iaa/{number}/round/initial", kind=kind, chat=chat)
    assert outbox.claim_ready(0)
    assert outbox.claim_ready(CHAT_INTERVAL_MS - 1) is None
    assert outbox.claim_ready(CHAT_INTERVAL_MS)


def test_one_destination_shares_its_budget_across_projects_sources_and_owners(outbox):
    enqueue(outbox, "copart/one/round/initial", project=1)
    enqueue(outbox, "iaa/two/round/initial", project=2)
    enqueue(outbox, "copart/three/round/initial", owner=2, project=3)
    assert outbox.claim_ready(0)
    assert outbox.next_wake_ms(0) == GROUP_INTERVAL_MS
    assert outbox.claim_ready(GROUP_INTERVAL_MS - 1) is None
    assert outbox.claim_ready(GROUP_INTERVAL_MS)
    assert outbox.claim_ready(GROUP_INTERVAL_MS) is None


def test_bot_budget_spans_different_chats_but_another_bot_has_a_separate_budget(outbox):
    enqueue(outbox, chat=1, kind="private")
    enqueue(outbox, chat=2, kind="private", project=2)
    first = outbox.claim_ready(0)
    assert first
    assert outbox.claim_ready(BOT_INTERVAL_MS - 1) is None
    assert outbox.claim_ready(BOT_INTERVAL_MS)
    enqueue(outbox, bot=999, chat=3, kind="private")
    assert outbox.claim_ready(BOT_INTERVAL_MS)["bot_id"] == 999


def test_blocked_group_does_not_block_a_ready_private_chat(outbox):
    enqueue(outbox, "copart/1/round/initial")
    assert outbox.claim_ready(0)
    enqueue(outbox, "copart/2/round/initial")
    direct = enqueue(outbox, "iaa/3/round/initial", kind="private", chat=555)
    assert outbox.claim_ready(BOT_INTERVAL_MS)["id"] == direct["id"]


def test_large_owner_backlog_does_not_starve_another_ready_owner(outbox):
    for number in range(10):
        enqueue(outbox, f"copart/{number}/round/initial", kind="private", chat=100 + number)
    assert outbox.claim_ready(0)["owner_id"] == 1
    second = enqueue(outbox, owner=2, kind="private", chat=222)
    assert outbox.claim_ready(BOT_INTERVAL_MS)["id"] == second["id"]

def test_large_project_batch_does_not_starve_another_project_of_the_same_owner_and_chat(outbox):
    for number in range(10):
        enqueue(outbox, f'copart/{number}/manual', project=1, kind='private', chat=555)
    first=outbox.claim_ready(0);assert first['active_project_ids']==[1]
    enqueue(outbox, 'iaa/second/manual', project=2, kind='private', chat=555, now=1)
    assert outbox.claim_ready(CHAT_INTERVAL_MS)['active_project_ids']==[2]
    assert outbox.claim_ready(CHAT_INTERVAL_MS*2)['active_project_ids']==[1]


def test_429_from_external_activity_defers_the_bot_across_all_targets_and_survives_restart(outbox):
    group = enqueue(outbox)
    enqueue(outbox, "iaa/private/round/initial", kind="private", chat=555, project=2, now=1)
    attempt = outbox.claim_ready(0)
    outbox.rate_limited(attempt["id"], attempt["attempt_id"], retry_after=17, now_ms=100)
    restarted = TelegramOutbox(outbox.path)
    until = 100 + 17000 + RETRY_MARGIN_MS
    assert restarted.next_wake_ms(100) == until
    assert restarted.claim_ready(until - 1) is None
    assert restarted.get(group["id"], owner_id=1)["error_code"] == "TELEGRAM_RATE_LIMIT"
    assert restarted.claim_ready(until)


def test_apps_schedule_locally_and_do_not_share_admission_or_cooldowns(tmp_path):
    first = TelegramOutbox(tmp_path / "app-one" / "telegram-outbox.sqlite3")
    second = TelegramOutbox(tmp_path / "app-two" / "telegram-outbox.sqlite3")
    enqueue(first)
    enqueue(second)
    # Same bot/chat, but each app only knows its own attempts.
    attempt = first.claim_ready(0)
    other_attempt = second.claim_ready(0)
    assert attempt and other_attempt
    first.rate_limited(attempt["id"], attempt["attempt_id"], retry_after=17, now_ms=100)
    second.sent(other_attempt["id"], other_attempt["attempt_id"], message_id=123)
    enqueue(second, key="iaa/next/round/initial", project=2, now=100)
    assert first.next_wake_ms(100) == 17100 + RETRY_MARGIN_MS
    assert second.next_wake_ms(100) == GROUP_INTERVAL_MS
    assert second.claim_ready(GROUP_INTERVAL_MS)
    assert first.claim_ready(GROUP_INTERVAL_MS) is None


def test_late_shorter_429_does_not_shorten_an_existing_bot_pause(outbox):
    enqueue(outbox, "copart/one/round/initial", kind="private", chat=111)
    enqueue(outbox, "copart/two/round/initial", kind="private", chat=222)
    first = outbox.claim_ready(0)
    second = outbox.claim_ready(50)
    outbox.rate_limited(first["id"], first["attempt_id"], retry_after=60, now_ms=100)
    outbox.rate_limited(second["id"], second["attempt_id"], retry_after=1, now_ms=200)
    assert outbox.next_wake_ms(200) == 61100


def test_stale_callbacks_cannot_acknowledge_a_new_attempt(outbox):
    enqueue(outbox)
    first = outbox.claim_ready(0)
    outbox.rate_limited(first["id"], first["attempt_id"], retry_after=1, now_ms=0)
    second = outbox.claim_ready(GROUP_INTERVAL_MS)
    assert second["id"] == first["id"]
    assert second["attempt_id"] != first["attempt_id"]
    with pytest.raises(ValueError, match="telegram_attempt_not_current"):
        outbox.sent(first["id"], first["attempt_id"], message_id=99)
    outbox.sent(second["id"], second["attempt_id"], message_id=100)
    assert outbox.get(first["id"], owner_id=1)["state"] == "SENT"


def test_uncertain_result_is_not_automatically_resent_even_after_reopen(outbox):
    enqueue(outbox)
    attempt = outbox.claim_ready(0)
    outbox.uncertain(attempt["id"], attempt["attempt_id"])
    reopened = TelegramOutbox(outbox.path)
    assert reopened.claim_ready(9999999) is None
    assert reopened.next_wake_ms(9999999) is None
    assert reopened.get(attempt["id"], owner_id=1)["state"] == "UNKNOWN"


def test_reopening_does_not_blindly_retry_a_still_in_flight_attempt(outbox):
    enqueue(outbox)
    first = outbox.claim_ready(0)
    reopened = TelegramOutbox(outbox.path)
    assert reopened.claim_ready(9999999) is None
    assert reopened.get(first["id"], owner_id=1)["state"] == "IN_FLIGHT"


def test_concurrent_admission_has_one_atomic_winner_for_the_shared_bot(outbox):
    from concurrent.futures import ThreadPoolExecutor
    for number in range(4):
        enqueue(outbox, f"copart/{number}/round/initial", kind="private", chat=number + 1)
    boxes = [TelegramOutbox(outbox.path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda box: box.claim_ready(0), boxes))
    assert sum(item is not None for item in results) == 1


@pytest.mark.parametrize("status,body", [(200, {"ok": False, "parameters": {"retry_after": 7}}),
    (429, {"ok": True, "parameters": {"retry_after": 7}}), (429, {"ok": False}),
    (429, {"ok": False, "parameters": {"retry_after": True}}),
    (429, {"ok": False, "parameters": {"retry_after": "7"}}),
    (429, {"ok": False, "parameters": {"retry_after": -7}}), (429, [])])
def test_untrusted_error_fields_do_not_create_an_invalid_schedule(status, body):
    assert retry_after_from_response(status, body) is None


def test_zero_retry_after_still_gets_a_minimum_safe_delay():
    assert retry_after_from_response(429, {"ok": False, "parameters": {"retry_after": 0}}) == 1


def test_conflicting_destination_type_cannot_bypass_the_group_limit(outbox):
    enqueue(outbox)
    with pytest.raises(ValueError, match="telegram_destination_type_conflict"):
        enqueue(outbox, "iaa/2/round/initial", kind="channel")


@pytest.mark.parametrize("change", [{"owner_id": True}, {"project_id": -1}, {"bot_id": 0},
    {"notification_key": "bad\nkey"}, {"now_ms": -1}, {"now_ms": True}])
def test_invalid_intents_are_rejected_before_storage(outbox, change):
    body = dict(owner_id=1, project_id=1, bot_id=123456, notification_key="copart/one/round/initial", now_ms=0,
                destination={"chat_id": -100123, "chat_type": "group", "title": "Fixture"})
    with pytest.raises(ValueError):
        outbox.enqueue(**{**body, **change})
    assert outbox.next_wake_ms(0) is None
