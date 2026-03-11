"""AT Protocol client — auth, post, reply via the atproto SDK."""

from atproto import Client, models


class BlueskyClient:
    def __init__(self):
        self._client = Client()
        self._did: str | None = None

    def login(self, handle: str, password: str):
        """Authenticate with Bluesky."""
        profile = self._client.login(handle, password)
        self._did = profile.did
        print(f"  [bsky] Logged in as {handle} ({self._did})")

    @property
    def did(self) -> str | None:
        return self._did

    def resolve_post(self, uri: str) -> dict:
        """Fetch a post by AT URI. Returns {uri, cid, record, author}."""
        # uri format: at://did:plc:xxx/app.bsky.feed.post/rkey
        parts = uri.replace("at://", "").split("/")
        repo = parts[0]
        rkey = parts[2]
        resp = self._client.app.bsky.feed.get_posts(
            models.AppBskyFeedGetPosts.Params(uris=[uri])
        )
        if not resp.posts:
            raise ValueError(f"Post not found: {uri}")
        post = resp.posts[0]
        return {
            "uri": post.uri,
            "cid": post.cid,
            "text": post.record.text,
            "author_did": post.author.did,
            "author_handle": post.author.handle,
        }

    def send_post(self, text: str) -> dict:
        """Send a standalone post. Returns {uri, cid}."""
        resp = self._client.send_post(text=text)
        return {"uri": resp.uri, "cid": resp.cid}

    def send_reply(
        self,
        text: str,
        parent_uri: str,
        parent_cid: str,
        root_uri: str | None = None,
        root_cid: str | None = None,
    ) -> dict:
        """Send a reply to an existing post. Returns {uri, cid}."""
        root_ref = models.create_strong_ref(root_uri or parent_uri, root_cid or parent_cid)
        parent_ref = models.create_strong_ref(parent_uri, parent_cid)
        resp = self._client.send_post(
            text=text,
            reply_to=models.AppBskyFeedPost.ReplyRef(
                root=root_ref,
                parent=parent_ref,
            ),
        )
        return {"uri": resp.uri, "cid": resp.cid}

    def get_timeline(self, cursor: str | None = None, limit: int = 50) -> dict:
        """Fetch the authenticated user's timeline.

        Returns {feed: [{post, reason, ...}], cursor: str | None}.
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = self._client.app.bsky.feed.get_timeline(params)
        feed = []
        for item in resp.feed or []:
            post = item.post
            record = post.record
            text = getattr(record, "text", "") or ""
            langs = getattr(record, "langs", None) or []
            feed.append({
                "text": text,
                "author_did": post.author.did,
                "author_handle": post.author.handle,
                "post_uri": post.uri,
                "langs": langs,
                "is_repost": item.reason is not None,
            })
        return {"feed": feed, "cursor": resp.cursor}

    def send_thread(
        self,
        texts: list[str],
        reply_to: dict | None = None,
    ) -> list[dict]:
        """Send a thread of 1-2 posts. If reply_to is given, first post is a reply.

        reply_to should have keys: uri, cid, root_uri, root_cid (optional).
        Returns list of {uri, cid} dicts.
        """
        results = []

        for i, text in enumerate(texts):
            if i == 0 and reply_to:
                resp = self.send_reply(
                    text,
                    parent_uri=reply_to["uri"],
                    parent_cid=reply_to["cid"],
                    root_uri=reply_to.get("root_uri"),
                    root_cid=reply_to.get("root_cid"),
                )
            elif i == 0:
                resp = self.send_post(text)
            else:
                # Thread continuation: reply to our own previous post
                prev = results[-1]
                root = results[0]
                resp = self.send_reply(
                    text,
                    parent_uri=prev["uri"],
                    parent_cid=prev["cid"],
                    root_uri=root["uri"] if reply_to else prev["uri"],
                    root_cid=root["cid"] if reply_to else prev["cid"],
                )
            results.append(resp)

        return results
