"""Unit tests for EpisodePicker.

Coverage:
- score ordering: negative emotion > positive emotion, high intensity > low intensity
- people deduplication: keep only the highest-scored episode per person
- time-window fallback: expand from 7 days to 30 days when all recent episodes were pushed
- recency decay weights
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from octop.infra.proactive.picker import (
    EpisodePicker,
    PickResult,
    _emotion_weight,
    _recency_weight,
    _score,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_episode(
    *,
    ep_id: str,
    emotion: str = "neutral",
    intensity: int = 3,
    people: list[str] | None = None,
    days_ago: float = 0.5,
    now: datetime | None = None,
) -> MagicMock:
    """Create a mock Episode object."""
    if now is None:
        now = datetime.now(UTC)
    ep = MagicMock()
    ep.id = ep_id
    ep.emotion = emotion
    ep.intensity = intensity
    ep.people = people or []
    ep.occurred_at = now - timedelta(days=days_ago)
    ep.summary = f"episode {ep_id}"
    ep.verbatim_quote = f"quote {ep_id}"
    return ep


# ---------------------------------------------------------------------------
# Emotion weight tests
# ---------------------------------------------------------------------------


def test_emotion_weight_negative():
    """Negative emotions should have weight 1.5."""
    for emotion in ("sad", "angry", "anxious", "frustrated"):
        assert _emotion_weight(emotion) == 1.5, f"emotion={emotion}"


def test_emotion_weight_medium():
    """Tired/reflective emotions should have weight 1.2."""
    for emotion in ("tired", "reflective"):
        assert _emotion_weight(emotion) == 1.2, f"emotion={emotion}"


def test_emotion_weight_positive():
    """Positive and neutral emotions should have weight 1.0."""
    for emotion in ("happy", "excited", "grateful", "neutral"):
        assert _emotion_weight(emotion) == 1.0, f"emotion={emotion}"


def test_emotion_weight_unknown():
    """Unknown emotions should default to weight 1.0."""
    assert _emotion_weight("unknown_emotion") == 1.0


# ---------------------------------------------------------------------------
# Recency weight tests
# ---------------------------------------------------------------------------


def test_recency_weight_within_1_day():
    now = datetime.now(UTC)
    ep_time = now - timedelta(hours=12)
    assert _recency_weight(ep_time, now) == 1.0


def test_recency_weight_1_to_3_days():
    now = datetime.now(UTC)
    ep_time = now - timedelta(days=2)
    assert _recency_weight(ep_time, now) == 0.8


def test_recency_weight_3_to_7_days():
    now = datetime.now(UTC)
    ep_time = now - timedelta(days=5)
    assert _recency_weight(ep_time, now) == 0.6


# ---------------------------------------------------------------------------
# Score ordering tests
# ---------------------------------------------------------------------------


def test_score_negative_emotion_higher_than_positive():
    """Negative episodes should outscore positive episodes at equal intensity and recency."""
    now = datetime.now(UTC)
    ep_sad = _make_episode(ep_id="sad", emotion="sad", intensity=3, days_ago=0.5, now=now)
    ep_happy = _make_episode(ep_id="happy", emotion="happy", intensity=3, days_ago=0.5, now=now)
    assert _score(ep_sad, now) > _score(ep_happy, now)


def test_score_higher_intensity_wins():
    """Higher intensity should score higher for the same emotion."""
    now = datetime.now(UTC)
    ep_high = _make_episode(ep_id="high", emotion="sad", intensity=5, days_ago=0.5, now=now)
    ep_low = _make_episode(ep_id="low", emotion="sad", intensity=2, days_ago=0.5, now=now)
    assert _score(ep_high, now) > _score(ep_low, now)


def test_score_recent_wins_over_old():
    """More recent episodes should score higher for the same emotion and intensity."""
    now = datetime.now(UTC)
    ep_recent = _make_episode(ep_id="recent", emotion="sad", intensity=3, days_ago=0.5, now=now)
    ep_old = _make_episode(ep_id="old", emotion="sad", intensity=3, days_ago=5, now=now)
    assert _score(ep_recent, now) > _score(ep_old, now)


# ---------------------------------------------------------------------------
# EpisodePicker flow tests
# ---------------------------------------------------------------------------


def test_pick_returns_top_k():
    """Should return at most top_k episodes."""
    now = datetime.now(UTC)
    episodes = [
        _make_episode(ep_id=f"ep{i}", emotion="sad", intensity=i + 1, days_ago=0.5, now=now)
        for i in range(5)
    ]
    picker = EpisodePicker(top_k=3)
    result = picker.pick(episodes, pushed_ids=set(), now=now)
    assert isinstance(result, PickResult)
    assert len(result.episodes) == 3
    assert result.window_days == 7


def test_pick_excludes_pushed_ids():
    """Already pushed episodes should be excluded."""
    now = datetime.now(UTC)
    episodes = [
        _make_episode(ep_id="ep1", emotion="sad", intensity=5, days_ago=0.5, now=now),
        _make_episode(ep_id="ep2", emotion="sad", intensity=4, days_ago=0.5, now=now),
        _make_episode(ep_id="ep3", emotion="sad", intensity=3, days_ago=0.5, now=now),
    ]
    result = EpisodePicker(top_k=3).pick(episodes, pushed_ids={"ep1", "ep2"}, now=now)
    assert len(result.episodes) == 1
    assert result.episodes[0].id == "ep3"


def test_pick_empty_when_no_episodes():
    """Should return an empty list when no episodes are available."""
    now = datetime.now(UTC)
    result = EpisodePicker().pick([], pushed_ids=set(), now=now)
    assert result.episodes == []


def test_pick_dedup_by_people():
    """Should keep only the highest-scored episode for the same person."""
    now = datetime.now(UTC)
    # ep1 and ep2 both involve the same person; ep1 scores higher (intensity 5 vs 3).
    ep1 = _make_episode(
        ep_id="ep1", emotion="sad", intensity=5, people=["老婆"], days_ago=0.5, now=now
    )
    ep2 = _make_episode(
        ep_id="ep2", emotion="sad", intensity=3, people=["老婆"], days_ago=0.5, now=now
    )
    ep3 = _make_episode(
        ep_id="ep3", emotion="sad", intensity=4, people=["老板"], days_ago=0.5, now=now
    )

    result = EpisodePicker(top_k=3).pick([ep1, ep2, ep3], pushed_ids=set(), now=now)
    ids = [ep.id for ep in result.episodes]
    # ep1 and ep3 should be selected; ep2 should be removed by deduplication.
    assert "ep1" in ids
    assert "ep3" in ids
    assert "ep2" not in ids


def test_pick_fallback_to_30_days():
    """Should expand to 30 days when every episode in the 7-day window was pushed."""
    now = datetime.now(UTC)
    # ep1 is within 7 days; ep2 is within the 7-30 day fallback range.
    ep1 = _make_episode(ep_id="ep1", emotion="sad", intensity=5, days_ago=3, now=now)
    ep2 = _make_episode(ep_id="ep2", emotion="sad", intensity=4, days_ago=15, now=now)

    # ep1 was pushed; ep2 was not.
    result = EpisodePicker(top_k=3, window_days=7, fallback_days=30).pick(
        [ep1, ep2], pushed_ids={"ep1"}, now=now
    )
    assert len(result.episodes) == 1
    assert result.episodes[0].id == "ep2"
    assert result.window_days == 30


def test_pick_returns_empty_when_all_pushed_in_fallback():
    """Should return empty when all episodes are pushed in both 7-day and 30-day windows."""
    now = datetime.now(UTC)
    ep1 = _make_episode(ep_id="ep1", emotion="sad", intensity=5, days_ago=3, now=now)
    ep2 = _make_episode(ep_id="ep2", emotion="sad", intensity=4, days_ago=15, now=now)

    result = EpisodePicker(top_k=3, window_days=7, fallback_days=30).pick(
        [ep1, ep2], pushed_ids={"ep1", "ep2"}, now=now
    )
    assert result.episodes == []


def test_pick_sorted_by_score_descending():
    """Picked episodes should be sorted by score descending."""
    now = datetime.now(UTC)
    episodes = [
        _make_episode(ep_id="low", emotion="neutral", intensity=1, days_ago=0.5, now=now),
        _make_episode(ep_id="high", emotion="sad", intensity=5, days_ago=0.5, now=now),
        _make_episode(ep_id="mid", emotion="tired", intensity=3, days_ago=0.5, now=now),
    ]
    result = EpisodePicker(top_k=3).pick(episodes, pushed_ids=set(), now=now)
    ids = [ep.id for ep in result.episodes]
    assert ids[0] == "high"  # Highest score first.


def test_pick_episode_without_people_not_deduped():
    """Episodes without people should not be deduplicated."""
    now = datetime.now(UTC)
    ep1 = _make_episode(ep_id="ep1", emotion="sad", intensity=5, people=[], days_ago=0.5, now=now)
    ep2 = _make_episode(ep_id="ep2", emotion="sad", intensity=4, people=[], days_ago=0.5, now=now)

    result = EpisodePicker(top_k=3).pick([ep1, ep2], pushed_ids=set(), now=now)
    assert len(result.episodes) == 2
