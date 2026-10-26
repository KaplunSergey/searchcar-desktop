def test_live_update_broker_only_delivers_a_scan_to_its_owner() -> None:
    from app.live_updates import LiveUpdateBroker

    broker = LiveUpdateBroker()
    owner_events = broker.subscribe(7)
    other_events = broker.subscribe(8)
    try:
        broker.publish(owner_id=7, scan_id=41)

        assert owner_events.get_nowait() == {"type": "scan_changed", "scan_id": 41}
        assert other_events.empty()
    finally:
        broker.unsubscribe(owner_events)
        broker.unsubscribe(other_events)


def test_live_update_broker_keeps_only_the_latest_invalidation() -> None:
    from app.live_updates import LiveUpdateBroker

    broker = LiveUpdateBroker()
    events = broker.subscribe(7)
    try:
        broker.publish(owner_id=7, scan_id=41)
        broker.publish(owner_id=7, scan_id=42)

        assert events.get_nowait() == {"type": "scan_changed", "scan_id": 42}
        assert events.empty()
    finally:
        broker.unsubscribe(events)


def test_close_wakes_all_subscribers_and_cannot_be_overwritten_by_updates():
    from app.live_updates import LiveUpdateBroker

    broker = LiveUpdateBroker()
    first, second = broker.subscribe(7), broker.subscribe(8)
    broker.publish(owner_id=7, scan_id=41)  # Even a full queue must close.
    broker.close()
    broker.close()  # Repeated shutdown is harmless.
    broker.publish(owner_id=7, scan_id=42)
    assert first.get(timeout=0.2) is None
    assert second.get(timeout=0.2) is None
    assert broker.subscribe(9).get(timeout=0.2) is None
    assert first.empty() and second.empty()
