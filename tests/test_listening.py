"""
tests/test_listening.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def make_friendship(a, b):
    """Friendship is a symmetric relationship — both directions must be inserted."""
    a.friends.append(b)
    b.friends.append(a)


@pytest.fixture
def users(app):
    """One 'me' user, two friends, and one stranger (not a friend)."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend_a = User(username="friend_a", email="a@example.com")
        friend_b = User(username="friend_b", email="b@example.com")
        stranger = User(username="stranger", email="stranger@example.com")
        db.session.add_all([me, friend_a, friend_b, stranger])
        db.session.commit()

        make_friendship(me, friend_a)
        make_friendship(me, friend_b)
        db.session.commit()

        yield {
            "me": me.id,
            "friend_a": friend_a.id,
            "friend_b": friend_b.id,
            "stranger": stranger.id,
        }


@pytest.fixture
def songs(app, users):
    with app.app_context():
        song1 = Song(title="Song One", artist="Artist One", shared_by=users["friend_a"])
        song2 = Song(title="Song Two", artist="Artist Two", shared_by=users["friend_b"])
        song3 = Song(title="Song Three", artist="Artist Three", shared_by=users["stranger"])
        db.session.add_all([song1, song2, song3])
        db.session.commit()
        yield {"song1": song1.id, "song2": song2.id, "song3": song3.id}


def log_listen(user_id, song_id, when):
    event = ListeningEvent(user_id=user_id, song_id=song_id, listened_at=when)
    db.session.add(event)
    db.session.commit()


def test_returns_empty_for_user_with_no_friends(app):
    with app.app_context():
        loner = User(username="loner", email="loner@example.com")
        db.session.add(loner)
        db.session.commit()

        result = get_friends_listening_now(loner.id)
        assert result == []


def test_raises_for_unknown_user(app):
    with app.app_context():
        with pytest.raises(ValueError):
            get_friends_listening_now("not-a-real-id")


def test_includes_friend_who_listened_recently(app, users, songs):
    with app.app_context():
        now = datetime.now(timezone.utc)
        log_listen(users["friend_a"], songs["song1"], now - timedelta(minutes=5))

        result = get_friends_listening_now(users["me"])

        assert len(result) == 1
        assert result[0]["friend"]["id"] == users["friend_a"]
        assert result[0]["song"]["id"] == songs["song1"]


def test_excludes_non_friend(app, users, songs):
    with app.app_context():
        now = datetime.now(timezone.utc)
        log_listen(users["stranger"], songs["song3"], now - timedelta(minutes=5))

        result = get_friends_listening_now(users["me"])

        assert result == []


def test_excludes_friend_who_listened_over_24_hours_ago(app, users, songs):
    with app.app_context():
        now = datetime.now(timezone.utc)
        log_listen(users["friend_a"], songs["song1"], now - timedelta(hours=25))

        result = get_friends_listening_now(users["me"])

        assert result == []


def test_deduplicates_to_most_recent_song_per_friend(app, users, songs):
    with app.app_context():
        now = datetime.now(timezone.utc)
        log_listen(users["friend_a"], songs["song1"], now - timedelta(hours=2))
        log_listen(users["friend_a"], songs["song2"], now - timedelta(minutes=10))

        result = get_friends_listening_now(users["me"])

        assert len(result) == 1
        assert result[0]["song"]["id"] == songs["song2"]


def test_orders_by_most_recent_first(app, users, songs):
    with app.app_context():
        now = datetime.now(timezone.utc)
        log_listen(users["friend_a"], songs["song1"], now - timedelta(hours=1))
        log_listen(users["friend_b"], songs["song2"], now - timedelta(minutes=1))

        result = get_friends_listening_now(users["me"])

        assert [r["friend"]["id"] for r in result] == [users["friend_b"], users["friend_a"]]


def test_excludes_friend_who_listened_yesterday(app, users, songs):
    """
    Reported bug: 'Friends Listening Now shows people from yesterday.'

    The current implementation uses a rolling 24-hour window, so a friend
    who listened late last night can still appear as 'listening now' many
    hours into today. This test encodes the product expectation that only
    listens from the current calendar day (in UTC) should count — it will
    fail against the current cutoff = now - 24h implementation and should
    start passing once feed_service.py is fixed to filter by calendar day
    rather than a rolling time delta.
    """
    with app.app_context():
        now = datetime.now(timezone.utc)
        today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        yesterday_late_night = today_start - timedelta(minutes=30)  # ~11:30pm yesterday

        log_listen(users["friend_a"], songs["song1"], yesterday_late_night)

        result = get_friends_listening_now(users["me"])

        assert result == []
