"""Presentation gating only; stored source footage and CV evidence remain intact."""

def skeleton_visible(time_ms, events, lead_ms=1000):
    return any(e.get('event_type') in ('pickup', 'release')
               and e['media_time_ms'] - lead_ms <= time_ms <= e['media_time_ms']
               for e in events)
