from pharma.services.privacy import skeleton_visible


def test_skeleton_only_in_pre_action_windows():
    events = [{'event_type': 'pickup', 'media_time_ms': 14600},
              {'event_type': 'release', 'media_time_ms': 16750},
              {'event_type': 'movement', 'media_time_ms': 20000}]
    for t in [13600, 14000, 14600, 15750, 16750]:
        assert skeleton_visible(t, events)
    for t in [0, 13599, 14601, 15749, 16751, 19500, 44000]:
        assert not skeleton_visible(t, events)
    assert not skeleton_visible(0, [])
