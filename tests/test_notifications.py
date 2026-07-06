"""
tests/test_notifications.py — Mixtape

Tests for notification creation on rating and playlist actions.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_song(app):
    """A song shared by one user, to be rated by another."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Track One", artist="Someone", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_friends_song_notifies_the_sharer(app, sharer_and_song):
    """Rating a song should notify the user who shared it."""
    with app.app_context():
        sharer = sharer_and_song["sharer"]
        rater = sharer_and_song["rater"]
        song = sharer_and_song["song"]

        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"


def test_rating_your_own_song_does_not_notify_yourself(app, sharer_and_song):
    """A user rating their own shared song should not generate a self-notification."""
    with app.app_context():
        sharer = sharer_and_song["sharer"]
        song = sharer_and_song["song"]

        rate_song(sharer.id, song.id, 4)

        notifications = get_notifications(sharer.id)
        assert notifications == []


def test_updating_an_existing_rating_still_notifies(app, sharer_and_song):
    """Re-rating a song (updating the existing Rating row) still notifies the sharer."""
    with app.app_context():
        sharer = sharer_and_song["sharer"]
        rater = sharer_and_song["rater"]
        song = sharer_and_song["song"]

        rate_song(rater.id, song.id, 3)
        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 2
