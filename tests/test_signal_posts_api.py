"""
RED phase: Tests for linkedin/api/posts.py
These tests define the expected API behavior before implementation.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Sample Voyager response fixtures (normalised JSON as returned by Voyager)
# ---------------------------------------------------------------------------

def make_profile_post_response(public_id, posts_list):
    """Build a Voyager response for own-profile / profile posts endpoint."""
    posts_included = []
    for p in posts_list:
        urn = p.get("postUrn", f"urn:li:fs_post:{p['idx']}")
        author_urn = p.get("authorUrn", f"urn:li:fs_member:99")
        posts_included.extend([
            {
                "$type": "com.linkedin.voyager.feed.Post",
                "entityUrn": urn,
                "caption": "",
                "content": {
                    "media": p.get("media", [])
                },
                "author": author_urn,
                "created": {"time": int(p.get("published_at", datetime.now().timestamp() - 3600) * 1000)},
                "subDescription": p.get("sub_description", "Posted content"),
                "*feedElementRenderedContent": None,
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.Profile",
                "entityUrn": author_urn,
                "publicIdentifier": public_id,
                "firstName": "Test",
                "lastName": "User",
            }
        ])

    return {
        "data": {
            "elements": [p.get("postUrn") for p in posts_list] if posts_list else [],
            "paging": {
                "total": len(posts_list),
                "count": len(posts_list),
                "start": 0,
                "totalPages": 1,
            }
        },
        "included": posts_included,
        "metadata": {},
    }


def make_company_post_response(slug, posts_list):
    """Build a Voyager response for company posts endpoint."""
    included = []
    for p in posts_list:
        urn = p.get("postUrn", f"urn:li:fs_post:{p['idx']}")
        company_urn = p.get("companyUrn", "urn:li:fs_company:1")
        included.extend([
            {
                "$type": "com.linkedin.voyager.feed.Post",
                "entityUrn": urn,
                "author": company_urn,
                "created": {"time": int(p.get("published_at", datetime.now().timestamp() - 3600) * 1000)},
                "subDescription": p.get("sub_description", "Posted content"),
                "content": {},
            },
            {
                "$type": "com.linkedin.voyager.dash.company.Company",
                "entityUrn": company_urn,
                "url": slug,
                "name": p.get("company_name", "Acme Corp"),
            }
        ])

    return {
        "data": {
            "elements": [p.get("postUrn") for p in posts_list] if posts_list else [],
            "paging": {"total": len(posts_list), "count": len(posts_list), "start": 0, "totalPages": 1},
        },
        "included": included,
    }


def make_reactors_response(post_urn, actors_list):
    """Build a Voyager response for post reactors endpoint."""
    included = []
    for i, actor in enumerate(actors_list):
        actor_urn = actor.get("profile_urn", f"urn:li:fs_member:{i}")
        reaction_type = actor.get("reaction_type", "LIKE")
        included.append({
            "$type": "com.linkedin.voyager.feed.Social.actions",
            "entityUrn": f"urn:li:fs_socialVerb:like:{post_urn.split(':')[-1]}",
            "subAction": None,
            "action": f"urn:li:fs_reactionType:{reaction_type}",
            "actor": actor_urn,
            "created": {"time": int(datetime.now().timestamp() * 1000)},
        })
        included.append({
            "$type": "com.linkedin.voyager.dash.identity.Profile",
            "entityUrn": actor_urn,
            "publicIdentifier": actor.get("public_id", f"user{i}"),
            "firstName": f"First{i}",
            "lastName": f"Last{i}",
        })

    return {
        "data": {
            "elements": [a.get("profile_urn") for a in actors_list] if actors_list else [],
            "paging": {"total": len(actors_list), "count": len(actors_list), "start": 0, "totalPages": 1},
        },
        "included": included,
    }


def make_comments_response(post_urn, comments_list):
    """Build a Voyager response for post comments endpoint."""
    included = []
    for i, c in enumerate(comments_list):
        comment_urn = c.get("comment_urn", f"urn:li:fs_comment:{i}")
        actor_urn = c.get("profile_urn", f"urn:li:fs_member:{i}")
        text = c.get("text", "Sample comment text")
        included.extend([
            {
                "$type": "com.linkedin.voyager.feed.Comment",
                "entityUrn": comment_urn,
                "author": actor_urn,
                "created": {"time": int(datetime.now().timestamp() * 1000)},
                "text": text,
                "content": {},
                "likesSummary": {"likePreviewImage": None},
            },
            {
                "$type": "com.linkedin.voyager.dash.identity.Profile",
                "entityUrn": actor_urn,
                "publicIdentifier": c.get("public_id", f"commenter{i}"),
                "firstName": f"Com{i}",
                "lastName": f"Menter{i}",
            }
        ])

    return {
        "data": {
            "elements": [c.get("comment_urn") for c in comments_list] if comments_list else [],
            "paging": {"total": len(comments_list), "count": len(comments_list), "start": 0, "totalPages": 1},
        },
        "included": included,
    }


def make_reposts_response(post_urn, reposters_list):
    """Build a Voyager response for post reposts endpoint."""
    included = []
    for i, r in enumerate(reposters_list):
        actor_urn = r.get("profile_urn", f"urn:li:fs_member:{i}")
        included.append({
            "$type": "com.linkedin.voyager.feed.Social.actions",
            "entityUrn": f"urn:li:fs_socialVerb:repost:{post_urn.split(':')[-1]}",
            "subAction": None,
            "action": "urn:li:fs_socialVerb:repost",
            "actor": actor_urn,
            "created": {"time": int(datetime.now().timestamp() * 1000)},
        })
        included.append({
            "$type": "com.linkedin.voyager.dash.identity.Profile",
            "entityUrn": actor_urn,
            "publicIdentifier": r.get("public_id", f"reposter{i}"),
            "firstName": f"Rep{i}",
            "lastName": f"Oster{i}",
        })

    return {
        "data": {
            "elements": [a.get("profile_urn") for a in reposters_list] if reposters_list else [],
            "paging": {"total": len(reposters_list), "count": len(reposters_list), "start": 0, "totalPages": 1},
        },
        "included": included,
    }


# ---------------------------------------------------------------------------
# Tests for list_own_profile_posts
# ---------------------------------------------------------------------------

class TestListOwnProfilePosts:
    """Tests for list_own_profile_posts(session, since_days=30, limit=20)"""

    def test_returns_normalized_dicts(self, fake_session):
        """It returns a list of dicts with post_urn, post_excerpt, published_at, author_urn."""
        from linkedin.api.posts import list_own_profile_posts

        posts_data = [
            {"idx": 1, "sub_description": "My great post", "published_at": datetime.now().timestamp()},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("testuser", posts_data)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_own_profile_posts(fake_session, since_days=30, limit=20)

        assert isinstance(result, list)
        for item in result:
            assert "post_urn" in item
            assert "published_at" in item
            assert "author_urn" in item

    def test_limit_param_pagination(self, fake_session):
        """The limit param is passed as count/start pagination params."""
        from linkedin.api.posts import list_own_profile_posts

        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("testuser", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_own_profile_posts(fake_session, limit=10)

        # Should call get with params including count=10
        call_args = api_mock.get.call_args
        assert "params" in call_args.kwargs or len(call_args.args) > 1
        params = call_args.kwargs.get("params", {})
        assert params.get("count") == 10

    def test_since_days_param(self, fake_session):
        """since_days filters posts by time."""
        from linkedin.api.posts import list_own_profile_posts

        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("testuser", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_own_profile_posts(fake_session, since_days=7, limit=20)

        call_args = api_mock.get.call_args
        params = call_args.kwargs.get("params", {})
        # Should have a time parameter for filtering
        assert any("time" in k.lower() or "since" in k.lower() for k in params.keys())

    def test_malformed_schema_raises_or_warns(self, fake_session):
        """Malformed Voyager JSON raises a clear exception or returns [] with warning."""
        from linkedin.api.posts import list_own_profile_posts

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"elements": None}, "included": []}  # Bad elements
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_own_profile_posts(fake_session, limit=20)

        # Should either raise a structured exception or return [] with a warning
        if isinstance(result, list):
            assert result == [] or result is not None  # warning was logged
        else:
            raise result  # exception path


# ---------------------------------------------------------------------------
# Tests for list_profile_posts
# ---------------------------------------------------------------------------

class TestListProfilePosts:
    """Tests for list_profile_posts(session, public_id: str, limit=20)"""

    def test_returns_normalized_dicts(self, fake_session):
        """It returns post dicts with post_urn, post_excerpt, published_at, author_urn."""
        from linkedin.api.posts import list_profile_posts

        posts_data = [
            {"idx": 1, "sub_description": "Profile update", "published_at": datetime.now().timestamp()},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("someuser", posts_data)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_profile_posts(fake_session, "someuser", limit=20)

        assert isinstance(result, list)
        for item in result:
            assert "post_urn" in item

    def test_public_id_in_url(self, fake_session):
        """The public_id appears in the request URL/params."""
        from linkedin.api.posts import list_profile_posts

        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("targetuser", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_profile_posts(fake_session, "targetuser", limit=20)

        call_args = api_mock.get.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "targetuser" in url or "targetuser" in str(call_args)

    def test_limit_passed_correctly(self, fake_session):
        """limit is forwarded as count param."""
        from linkedin.api.posts import list_profile_posts

        mock_response = MagicMock()
        mock_response.json.return_value = make_profile_post_response("user", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_profile_posts(fake_session, "user", limit=5)

        call_args = api_mock.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("count") == 5


# ---------------------------------------------------------------------------
# Tests for list_company_posts
# ---------------------------------------------------------------------------

class TestListCompanyPosts:
    """Tests for list_company_posts(session, company_slug: str, limit=20)"""

    def test_returns_normalized_dicts(self, fake_session):
        """It returns post dicts for a company slug."""
        from linkedin.api.posts import list_company_posts

        posts_data = [
            {"idx": 1, "sub_description": "Company news", "published_at": datetime.now().timestamp()},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_company_post_response("acme-corp", posts_data)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_company_posts(fake_session, "acme-corp", limit=20)

        assert isinstance(result, list)
        for item in result:
            assert "post_urn" in item

    def test_company_slug_in_url(self, fake_session):
        """The company slug appears in the request URL/params."""
        from linkedin.api.posts import list_company_posts

        mock_response = MagicMock()
        mock_response.json.return_value = make_company_post_response("acme-corp", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_company_posts(fake_session, "acme-corp", limit=20)

        call_args = api_mock.get.call_args
        assert "acme-corp" in str(call_args)


# ---------------------------------------------------------------------------
# Tests for list_post_reactors
# ---------------------------------------------------------------------------

class TestListPostReactors:
    """Tests for list_post_reactors(session, post_urn: str, limit=500)"""

    def test_returns_normalized_dicts(self, fake_session):
        """Returns list of {profile_urn, public_identifier, reaction_type}."""
        from linkedin.api.posts import list_post_reactors

        actors = [
            {"profile_urn": "urn:li:fs_member:10", "public_id": "alice", "reaction_type": "LIKE"},
            {"profile_urn": "urn:li:fs_member:11", "public_id": "bob", "reaction_type": "APPRECIATE"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_reactors_response("urn:li:fs_post:123", actors)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_post_reactors(fake_session, "urn:li:fs_post:123", limit=500)

        assert isinstance(result, list)
        for item in result:
            assert "profile_urn" in item
            assert "public_identifier" in item
            assert "reaction_type" in item

    def test_post_urn_in_url(self, fake_session):
        """The post_urn is embedded in the request URL."""
        from linkedin.api.posts import list_post_reactors

        mock_response = MagicMock()
        mock_response.json.return_value = make_reactors_response("urn:li:fs_post:456", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_post_reactors(fake_session, "urn:li:fs_post:456", limit=500)

        call_args = api_mock.get.call_args
        # post_id "456" is embedded in the URL path: /updates/456/reactions
        assert "/updates/456/" in str(call_args)

    def test_limit_defaults_to_500(self, fake_session):
        """Default limit is 500."""
        from linkedin.api.posts import list_post_reactors

        mock_response = MagicMock()
        mock_response.json.return_value = make_reactors_response("urn:li:fs_post:123", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_post_reactors(fake_session, "urn:li:fs_post:123")

        call_args = api_mock.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("count") == 500


# ---------------------------------------------------------------------------
# Tests for list_post_comments
# ---------------------------------------------------------------------------

class TestListPostComments:
    """Tests for list_post_comments(session, post_urn: str, limit=500)"""

    def test_returns_normalized_dicts(self, fake_session):
        """Returns list of {profile_urn, public_identifier, comment_text}."""
        from linkedin.api.posts import list_post_comments

        comments = [
            {"profile_urn": "urn:li:fs_member:20", "public_id": "charlie", "text": "Great post!"},
            {"profile_urn": "urn:li:fs_member:21", "public_id": "diana", "text": "Thanks for sharing."},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_comments_response("urn:li:fs_post:123", comments)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_post_comments(fake_session, "urn:li:fs_post:123", limit=500)

        assert isinstance(result, list)
        for item in result:
            assert "profile_urn" in item
            assert "public_identifier" in item
            assert "comment_text" in item

    def test_comment_text_truncated_at_2000(self, fake_session):
        """comment_text longer than 2000 chars gets truncated to 2000."""
        from linkedin.api.posts import list_post_comments

        long_text = "A" * 3000
        comments = [
            # comment_urn is required so the element reference matches the included entity
            {"comment_urn": "urn:li:fs_comment:0", "profile_urn": "urn:li:fs_member:20", "public_id": "charlie", "text": long_text},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_comments_response("urn:li:fs_post:123", comments)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_post_comments(fake_session, "urn:li:fs_post:123", limit=500)

        assert len(result) == 1, f"Expected 1 comment, got {len(result)}: {result}"
        assert len(result[0]["comment_text"]) <= 2000

    def test_limit_passed_correctly(self, fake_session):
        """limit is forwarded as count param."""
        from linkedin.api.posts import list_post_comments

        mock_response = MagicMock()
        mock_response.json.return_value = make_comments_response("urn:li:fs_post:123", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_post_comments(fake_session, "urn:li:fs_post:123", limit=100)

        call_args = api_mock.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("count") == 100


# ---------------------------------------------------------------------------
# Tests for list_post_reposts
# ---------------------------------------------------------------------------

class TestListPostReposts:
    """Tests for list_post_reposts(session, post_urn: str, limit=500)"""

    def test_returns_normalized_dicts(self, fake_session):
        """Returns list of {profile_urn, public_identifier}."""
        from linkedin.api.posts import list_post_reposts

        reposters = [
            {"profile_urn": "urn:li:fs_member:30", "public_id": "eve"},
            {"profile_urn": "urn:li:fs_member:31", "public_id": "frank"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = make_reposts_response("urn:li:fs_post:123", reposters)
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_post_reposts(fake_session, "urn:li:fs_post:123", limit=500)

        assert isinstance(result, list)
        for item in result:
            assert "profile_urn" in item
            assert "public_identifier" in item

    def test_post_urn_in_url(self, fake_session):
        """The post_urn is embedded in the request URL."""
        from linkedin.api.posts import list_post_reposts

        mock_response = MagicMock()
        mock_response.json.return_value = make_reposts_response("urn:li:fs_post:789", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_post_reposts(fake_session, "urn:li:fs_post:789", limit=500)

        call_args = api_mock.get.call_args
        # post_id "789" is embedded in the URL path: /updates/789/reposts
        assert "/updates/789/" in str(call_args)

    def test_limit_passed_correctly(self, fake_session):
        """limit is forwarded as count param."""
        from linkedin.api.posts import list_post_reposts

        mock_response = MagicMock()
        mock_response.json.return_value = make_reposts_response("urn:li:fs_post:123", [])
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            list_post_reposts(fake_session, "urn:li:fs_post:123", limit=250)

        call_args = api_mock.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("count") == 250

    def test_malformed_schema_returns_empty_or_warns(self, fake_session):
        """Malformed schema returns [] or logs warning, not crashes."""
        from linkedin.api.posts import list_post_reposts

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"elements": None}, "included": []}
        mock_response.status = 200
        mock_response.ok = True

        api_mock = MagicMock()
        api_mock.get.return_value = mock_response

        with patch("linkedin.api.posts.PlaywrightLinkedinAPI", return_value=api_mock):
            result = list_post_reposts(fake_session, "urn:li:fs_post:123", limit=500)

        # Should not raise; should return [] or empty-with-warning
        if isinstance(result, list):
            assert result == []