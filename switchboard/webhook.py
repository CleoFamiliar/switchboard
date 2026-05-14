"""Webhook handler — FastAPI app for GitHub and internal bd hooks.

Routes:
- POST /webhook/github — GitHub webhook (push, pull_request, create)
- POST /webhook/bd — internal bd CLI hook events
- GET /health — health check

Run via: sw webhook start
"""

import hashlib
import hmac
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request

from .skills import SkillRegistry
from .skills.cross_repo import CrossRepoSkill
from .skills.pr_merge import PRMergeSkill
from .skills.pr_review import PRReviewSkill

app = FastAPI(title="Switchboard Webhook", version="0.1.0")

# Global registry — populated on startup
_registry: Optional[SkillRegistry] = None
_config = None


def get_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _registry.register(CrossRepoSkill())
        _registry.register(PRMergeSkill())
        _registry.register(PRReviewSkill())
    return _registry


def set_config(config) -> None:
    global _config
    _config = config


def _verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify X-Hub-Signature-256 header."""
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "switchboard-webhook"}


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    """Handle GitHub webhook events."""
    body = await request.body()

    # Verify signature if secret is configured
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if secret:
        if not x_hub_signature_256:
            raise HTTPException(status_code=401, detail="Missing signature")
        if not _verify_github_signature(body, x_hub_signature_256, secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = _parse_github_event(x_github_event or "", payload)

    if event:
        registry = get_registry()
        results = registry.run_all(event, _config)
        return {
            "processed": True,
            "event_type": event.get("type"),
            "results": len(results),
        }

    return {"processed": False, "reason": "unhandled event type"}


@app.post("/webhook/bd")
async def bd_webhook(request: Request):
    """Handle internal bd CLI hook events."""
    payload = await request.json()

    event = {
        "type": payload.get("event_type", ""),
        "jack_id": payload.get("jack_id", ""),
        "repo": payload.get("repo", ""),
        "title": payload.get("title", ""),
        "body": payload.get("description", ""),
        "labels": payload.get("labels", []),
        "status": payload.get("status", ""),
    }

    # Map bd event types to internal types
    event_map = {
        "created": "task.created",
        "closed": "task.closed",
        "updated": "task.created",  # treat updates like creates for detection
    }
    event["type"] = event_map.get(event["type"], event["type"])

    if event["type"]:
        registry = get_registry()
        results = registry.run_all(event, _config)
        return {"processed": True, "results": len(results)}

    return {"processed": False}


def _parse_github_event(event_name: str, payload: dict) -> Optional[dict]:
    """Convert GitHub webhook payload to internal event dict."""
    if event_name == "push":
        return {
            "type": "task.created",
            "repo": payload.get("repository", {}).get("name", ""),
            "ref": payload.get("ref", ""),
            "title": payload.get("head_commit", {}).get("message", ""),
            "body": "",
            "labels": [],
            "sha": payload.get("after", ""),
            "merged": False,
        }
    elif event_name == "pull_request":
        pr = payload.get("pull_request", {})
        action = payload.get("action", "")
        merged = pr.get("merged", False)

        if action == "opened":
            event_type = "pr.opened"
        elif action == "closed" and merged:
            event_type = "pr.merged"
        elif action == "closed":
            return None  # closed without merge — skip
        else:
            return None

        return {
            "type": event_type,
            "repo": payload.get("repository", {}).get("name", ""),
            "ref": pr.get("head", {}).get("ref", ""),
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "labels": [l.get("name", "") for l in pr.get("labels", [])],
            "sha": pr.get("head", {}).get("sha", ""),
            "merged": merged,
        }
    elif event_name == "pull_request_review":
        review = payload.get("review", {})
        pr = payload.get("pull_request", {})
        return {
            "type": "pr.reviewed",
            "repo": payload.get("repository", {}).get("name", ""),
            "ref": pr.get("head", {}).get("ref", ""),
            "title": pr.get("title", ""),
            "body": pr.get("body", ""),
            "pr_url": pr.get("html_url", ""),
            "labels": [l.get("name", "") for l in pr.get("labels", [])],
            "sha": pr.get("head", {}).get("sha", ""),
            "merged": pr.get("merged", False),
            "review_state": review.get("state", ""),
            "review_body": review.get("body", ""),
            "reviewer": review.get("user", {}).get("login", ""),
        }
    elif event_name == "create":
        return {
            "type": "task.created",
            "repo": payload.get("repository", {}).get("name", ""),
            "ref": payload.get("ref", ""),
            "title": f"Branch/tag created: {payload.get('ref', '')}",
            "body": payload.get("description", "") or "",
            "labels": [],
            "sha": "",
            "merged": False,
        }

    return None
