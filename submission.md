# Mixtape — Codebase Map

## Main files and what they do

**`app.py`** — Application factory. Creates the Flask app, configures the SQLite DB URI (overridable via `DATABASE_URL` env var), instantiates the shared `db = SQLAlchemy()` object, registers the four blueprints (`songs`, `playlists`, `users`, `feed`) under their URL prefixes, and calls `db.create_all()` on startup. There's no migration tool (no Alembic) — schema changes take effect by re-running `create_all`, which is why `seed_data.py` calls `db.drop_all()` first.

**`models.py`** — Defines 7 SQLAlchemy models plus 3 association tables:
- `User` — has a `listening_streak` int and `last_listened_at` timestamp maintained by `streak_service`, not stored anywhere else.
- `Song` — always has a `shared_by` (the user who shared it) and `share_note`; there's no separate "Share" model, sharing is just creating a `Song` row.
- `Tag`, `Rating`, `ListeningEvent`, `Playlist`, `Notification`.
- `friendships` — symmetric many-to-many self-join on `User` (both directions are inserted explicitly, e.g. in `seed_data.py`'s `add_friendship`).
- `song_tags` — plain many-to-many between `Song` and `Tag`.
- `playlist_entries` — many-to-many between `Playlist` and `Song` that also carries `position`, `added_by`, and `added_at` columns, so a song's place in a playlist is an explicit ordered field, not insertion order.

Every model has a `to_dict()` method — this is the only serialization layer; routes call `.to_dict()` (or a service returns pre-built dicts) and `jsonify()` the result directly. There are no separate schema/serializer classes.

**`routes/`** — One blueprint per resource area, each a thin layer that does request parsing, required-field validation, and HTTP status mapping (`ValueError` from a service → 400/404), then delegates to `services/`:
- `songs.py` — search, get one, rate, log a listen.
- `playlists.py` — create, get one, list songs in it, add a song to it.
- `users.py` — get profile, get streak, list/read notifications.
- `feed.py` — "friends listening now" and general activity feed.

No route touches `db.session` for business logic directly except `users.py`'s `get_user`, which does a direct `db.session.get(User, ...)` lookup since there's no dedicated user service for that.

**`services/`** — All business logic lives here, one module per feature area:
- `streak_service.py` — computes/updates `listening_streak` based on calendar-day deltas between `last_listened_at` and now.
- `feed_service.py` — builds the two friend-activity views from `ListeningEvent` rows.
- `search_service.py` — title/artist substring search over `Song`, joined to tags.
- `notification_service.py` — creates `Notification` rows and is also where `rate_song` and `add_to_playlist` live (rating and playlist-adding are treated as "things that might notify someone," so their logic sits next to notification creation rather than in a separate rating/playlist-membership service).
- `playlist_service.py` — playlist CRUD-ish reads and the ordered song query using `playlist_entries.position`.

**`seed_data.py`** — Wipes and repopulates the DB with 5 users/friendships, 25 songs (deliberately split into 0-tag / 1-tag / 3+-tag groups), 3 playlists, and a mix of recent/old `ListeningEvent`s. The tag-count grouping and recent-vs-old event split aren't incidental — they're structured so that specific known bugs (duplicate search results from tag joins, stale entries in "listening now") are directly observable against seeded data.

**`tests/`** — `test_streaks.py`, `test_search.py`, `test_playlists.py` exercise the corresponding services directly (not through HTTP), each importing `create_app`/`db` for an app-context fixture.

## Data flow — user rates a song

1. Client sends `POST /songs/<song_id>/rate` with `{user_id, score}` → `routes/songs.py:rate()`.
2. The route only validates presence of `user_id`/`score` (not the score range) and casts `score` to `int`, then calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the 1–5 range, loads the `Song` and `User` (404-equivalent via `ValueError` if missing), and does an upsert: queries for an existing `Rating` by `(user_id, song_id)` (enforced unique by a `UniqueConstraint` on `Rating`) and updates its `score` in place, or inserts a new `Rating`.
4. **It never calls `create_notification()`.** Contrast with `add_to_playlist()` in the same file, which — after appending the song to the playlist — notifies `song.shared_by` (skipping self-notification) with a `song_added_to_playlist` notification. `rate_song` has no equivalent call to notify the sharer with a `song_rated`-type notification, so a user who shared a song gets no notification when a friend rates it, even though the seed data and route wiring look like they should support it.
5. The route returns `rating.to_dict()` with a 201 either way — so, from the API surface, rating "succeeds" whether or not anyone gets notified, which is why the gap isn't visible without tracing into the service.

## Other things worth knowing while reading the code

- `playlist_service.get_playlist_songs()` fetches songs ordered by `playlist_entries.position` but returns `songs[:-1]` — it silently drops the last song in the list on every call. Nothing downstream (route, tests) currently guards against this off-by-one.
- `search_service.search_songs()` does an `outerjoin` against `song_tags` without `.distinct()`, so a song with N tags produces N duplicate rows in the result — the "same song shows up twice" symptom scales with tag count, which is exactly why `seed_data.py` seeds a batch of songs with 3+ tags.
- `streak_service.update_listening_streak()` special-cases Sunday (`today.weekday() != 6`) when deciding whether to increment after a 1-day gap, so a listen on Sunday following a listen on Saturday does not increment the streak the way every other consecutive-day pair does.
- `feed_service.get_friends_listening_now()` filters `ListeningEvent.listened_at >= cutoff` where `cutoff = now - 24h`, then deduplicates to the most recent event per friend — so any friend who listened at all within the last 24 hours appears, not just people who are "currently" listening in a narrower sense.

## Patterns in how the app is organized

- **Routes never touch the ORM for business rules.** Every route's job is: parse request → call one service function → map `ValueError` to an HTTP error code → `jsonify`. All validation beyond "is this field present" and all `db.session` query/commit logic lives in `services/`.
- **Errors are communicated via `ValueError`, not custom exception types.** Every service raises a bare `ValueError` with a human-readable message for "not found" or "invalid input," and every route catches `ValueError` the same way and returns it as `{"error": str(e)}`. There's no distinction in the exception type between a 400 (bad input) and 404 (not found) — the route decides the status code, not the exception.
- **No serializer layer — `to_dict()` on the model is the API shape.** Whatever fields `to_dict()` includes are exactly what's exposed over HTTP; there's no separate DTO/view model.
- **Feature logic is grouped by "what it's about," not by CRUD verb**, and that grouping doesn't perfectly match file names: `rate_song` and `add_to_playlist` (which sound like they belong in a "ratings" or "playlists" service) actually live in `notification_service.py`, because the unifying theme of that file is "actions that can produce a notification," not "notification CRUD." Anyone editing playlist or rating behavior needs to know to look there too.
- **IDs are UUID strings everywhere** (`generate_uuid()` default on every model), not auto-incrementing integers, and association tables that need extra metadata (`playlist_entries`) are defined as raw `db.Table` objects rather than mapped classes, while simpler joins (`friendships`, `song_tags`) are also raw tables even though they carry no extra columns — the project doesn't use the SQLAlchemy association-object pattern anywhere.

## AI Usage

I used AI to write a test for the bug in `streak_service.py`. I prompted it to generate a test that would fail with the current implementation of `update_listening_streak()` and pass with the correct implementation. I then ran the test to confirm that it failed, fixed the bug, and ran the test again to confirm that it passed.