"""
Post-cutover monitoring: does the deployed site actually do what we exported?

A sweep loads a migration's approved mappings, probes every old URL against the
live site, and reconciles what it finds with the issues already on file. The
reconciliation is the part that makes this usable rather than noisy — issues
are current state keyed by URL, not an event log, so a redirect that has been
broken for a week is one open row and one email, not seven of each.

Traffic is attached from the 026 baseline. It does not change whether something
is broken, but it is the whole of the priority order: the first line of the
alert is a number of clicks, not a count of URLs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import aiohttp

from redirx.rate_limit import (
    HostRateLimiter,
    WATCH_CAPACITY,
    WATCH_NAMESPACE,
    WATCH_RATE,
    set_limiter,
)
from redirx.redirect_probe import (
    BLOCKED,
    CRITICAL,
    DESCRIPTIONS,
    NO_REDIRECT,
    NOT_FOUND,
    REDIRECT_CHAIN,
    SEVERITY,
    TEMPORARY_REDIRECT,
    UNREACHABLE,
    WRONG_TARGET,
    ProbeResult,
    classify,
    probe,
)
from redirx.safe_fetch import create_safe_connector
from src.redirx.database import SupabaseClient

logger = logging.getLogger(__name__)

# Supabase's PostgREST caps a response at 1000 rows regardless of what we ask
# for, so every read that can exceed it pages explicitly.
PAGE_SIZE = 1000

# Concurrent probes within one sweep. The per-host token bucket is the real
# throttle; this only bounds sockets and memory.
DEFAULT_CONCURRENCY = 6

# A watch covers the URLs that carry the traffic, not every URL ever mapped.
# Sites with 50k mappings would otherwise turn a "monitor" into a recurring
# full crawl, and the tail below this cut has no measurable traffic to lose.
MAX_URLS_PER_SWEEP = 2000

# Cutover breakage shows up immediately, so a new watch checks hard for a week
# and then settles into daily.
INTENSIVE_DAYS = 7

# Transient network failures are not worth an email on first sight; a second
# consecutive sweep saying the same thing is.
TRANSIENT_TYPES = frozenset({UNREACHABLE, BLOCKED})
TRANSIENT_ALERT_THRESHOLD = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bare_host(value: str) -> str:
    """Host of a URL, or of a bare domain string, without `www.`."""
    if not value:
        return ""
    candidate = value.strip()
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _normalize_url_key(url: str) -> str:
    """Match a mapping URL to a baseline URL despite scheme/slash/host drift."""
    from redirx.redirect_probe import normalize_for_compare

    return normalize_for_compare(url)


@dataclass
class SweepOutcome:
    check_id: Optional[str]
    urls_checked: int
    urls_ok: int
    issues_open: int
    issues_new: int
    issues_resolved: int
    clicks_at_risk: int
    alerted: int = 0


def suggest_fix(
    issue_type: str, expected_url: Optional[str], result: ProbeResult
) -> tuple[Optional[str], str]:
    """
    The corrected redirect target, and where that correction came from.

    Every case here resolves to a target Redirx can already justify — either
    the mapping the user approved, or the destination the site itself proved
    reachable. Nothing is re-matched or invented at this point: a monitor that
    quietly proposes a *different* page than the one the user reviewed would be
    laundering a new guess through a screen that says "fix".
    """
    if issue_type in (NO_REDIRECT, NOT_FOUND, WRONG_TARGET):
        # The rule is missing, dead, or pointing at the wrong page. In all
        # three the approved mapping is the answer.
        if expected_url:
            return expected_url, "approved_mapping"
        return None, "none"

    if issue_type == REDIRECT_CHAIN:
        # It works, but through extra hops. Point the first rule at the place
        # the chain already ends, collapsing it to one.
        return (result.final_url or expected_url), "collapse_chain"

    if issue_type == TEMPORARY_REDIRECT:
        # Right destination, wrong status. Same target, issued as a 301.
        return (result.final_url or expected_url), "force_permanent"

    # Loops, outages and blocks are not fixed by choosing a better target.
    return None, "none"


class WatchService:
    def __init__(self, client=None, email_service=None):
        self._client = client
        self._email_service = email_service

    @property
    def client(self):
        if self._client is None:
            # Watch tables are service-role only (RLS on, no policies), and a
            # sweep runs in the worker where there is no user session anyway.
            self._client = SupabaseClient.get_admin_client()
        return self._client

    @property
    def email_service(self):
        if self._email_service is None:
            from backend.services.email_service import EmailService

            self._email_service = EmailService()
        return self._email_service

    # ------------------------------------------------------------------
    # Watch lifecycle
    # ------------------------------------------------------------------

    def create_watch(
        self,
        user_id: str,
        session_id: str,
        alert_email: Optional[str] = None,
        check_interval_minutes: int = 1440,
        start_intensive: bool = True,
    ) -> dict[str, Any]:
        """
        Start watching a migration, or return the existing watch for it.

        Idempotent on session_id: "watch this migration" clicked twice must not
        double the probe traffic against a customer's origin.
        """
        existing = self.get_watch_for_session(session_id)
        if existing:
            if existing.get("status") != "active":
                return self.set_status(existing["id"], "active")
            return existing

        session = (
            self.client.table("migration_sessions")
            .select("id, user_id, old_site_domain, new_site_domain")
            .eq("id", session_id)
            .execute()
        )
        if not session.data:
            raise ValueError("session_not_found")
        record = session.data[0]
        if str(record.get("user_id")) != str(user_id):
            # 404-shaped, matching the v1 API's rule that a credential must not
            # be able to probe for other users' data.
            raise ValueError("session_not_found")

        old_domain = bare_host(record.get("old_site_domain") or "")
        if not old_domain:
            old_domain = self._infer_old_domain(session_id)
        if not old_domain:
            raise ValueError("no_old_domain")

        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "session_id": str(session_id),
            "old_domain": old_domain,
            "new_domain": bare_host(record.get("new_site_domain") or "") or None,
            "status": "active",
            "alert_email": alert_email,
            "check_interval_minutes": check_interval_minutes,
            "next_check_at": _now().isoformat(),
        }
        if start_intensive:
            payload["intensive_until"] = (_now() + timedelta(days=INTENSIVE_DAYS)).isoformat()

        result = self.client.table("redirect_watches").insert(payload).execute()
        return result.data[0]

    def _infer_old_domain(self, session_id: str) -> str:
        """Fall back to the mappings when the session has no domain recorded."""
        rows = (
            self.client.table("url_mappings")
            .select("old_url")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        return bare_host(rows.data[0]["old_url"]) if rows.data else ""

    def get_watch(self, watch_id: str) -> Optional[dict[str, Any]]:
        result = (
            self.client.table("redirect_watches").select("*").eq("id", watch_id).execute()
        )
        return result.data[0] if result.data else None

    def get_watch_for_session(self, session_id: str) -> Optional[dict[str, Any]]:
        result = (
            self.client.table("redirect_watches")
            .select("*")
            .eq("session_id", session_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def list_watches(self, user_id: str) -> list[dict[str, Any]]:
        result = (
            self.client.table("redirect_watches")
            .select("*")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    def set_status(self, watch_id: str, status: str) -> dict[str, Any]:
        if status not in ("active", "paused", "ended"):
            raise ValueError("invalid_status")
        updates: dict[str, Any] = {"status": status, "updated_at": _now().isoformat()}
        if status == "active":
            # Resuming should check now, not at whatever stale time was stored.
            updates["next_check_at"] = _now().isoformat()
        result = (
            self.client.table("redirect_watches")
            .update(updates)
            .eq("id", watch_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def list_issues(
        self, watch_id: str, include_resolved: bool = False
    ) -> list[dict[str, Any]]:
        query = self.client.table("watch_issues").select("*").eq("watch_id", watch_id)
        if not include_resolved:
            query = query.is_("resolved_at", "null")
        result = query.order("clicks_at_risk", desc=True).execute()
        return result.data or []

    def list_checks(self, watch_id: str, limit: int = 20) -> list[dict[str, Any]]:
        result = (
            self.client.table("watch_checks")
            .select("*")
            .eq("watch_id", watch_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ------------------------------------------------------------------
    # Inputs to a sweep
    # ------------------------------------------------------------------

    def approved_mappings(self, session_id: str) -> list[dict[str, Any]]:
        """
        The redirects the user signed off on — the definition of "correct".

        Anything still flagged for review was never promised to the live site,
        so probing it would report failures the user has not agreed to yet.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = (
                self.client.table("url_mappings")
                .select("old_url, new_url")
                .eq("session_id", session_id)
                .eq("needs_review", False)
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = page.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return rows

    def traffic_map(self, user_id: str, domain: str) -> dict[str, int]:
        """
        Monthly clicks per old URL from the most recent captured baseline.

        Returns an empty map when there is no baseline — a site with no Search
        Console connection still gets monitored, it just cannot be sorted by
        what the breakage costs.
        """
        baselines = (
            self.client.table("gsc_traffic_baselines")
            .select("id")
            .eq("user_id", str(user_id))
            .eq("domain", domain)
            .order("captured_at", desc=True)
            .limit(1)
            .execute()
        )
        if not baselines.data:
            return {}

        baseline_id = baselines.data[0]["id"]
        clicks: dict[str, int] = {}
        offset = 0
        while True:
            page = (
                self.client.table("gsc_baseline_urls")
                .select("url, clicks")
                .eq("baseline_id", baseline_id)
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = page.data or []
            for row in batch:
                key = _normalize_url_key(row.get("url") or "")
                if key:
                    clicks[key] = max(clicks.get(key, 0), int(row.get("clicks") or 0))
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return clicks

    def _select_urls(
        self, mappings: list[dict[str, Any]], clicks: dict[str, int]
    ) -> list[dict[str, Any]]:
        """Cap the sweep at the URLs whose failure would actually cost traffic."""
        if len(mappings) <= MAX_URLS_PER_SWEEP:
            return mappings
        ranked = sorted(
            mappings,
            key=lambda m: clicks.get(_normalize_url_key(m.get("old_url") or ""), 0),
            reverse=True,
        )
        return ranked[:MAX_URLS_PER_SWEEP]

    # ------------------------------------------------------------------
    # The sweep
    # ------------------------------------------------------------------

    async def probe_all(
        self, urls: Iterable[str], concurrency: int = DEFAULT_CONCURRENCY
    ) -> dict[str, ProbeResult]:
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(connector=create_safe_connector()) as session:

            async def one(url: str) -> tuple[str, ProbeResult]:
                async with semaphore:
                    return url, await probe(session, url)

            results = await asyncio.gather(*(one(u) for u in urls))
        return dict(results)

    async def run_check(self, watch: dict[str, Any]) -> SweepOutcome:
        """
        Probe one watch's URLs and reconcile the findings. Returns the summary.

        Raises only on failures that should retry the whole sweep (database
        errors); per-URL failures are findings, not exceptions.
        """
        watch_id = watch["id"]
        session_id = watch["session_id"]
        user_id = watch["user_id"]

        check = (
            self.client.table("watch_checks")
            .insert({"watch_id": watch_id, "status": "running"})
            .execute()
        )
        check_id = check.data[0]["id"] if check.data else None

        try:
            mappings = self.approved_mappings(session_id)
            clicks = self.traffic_map(user_id, watch.get("old_domain") or "")
            selected = self._select_urls(mappings, clicks)

            expected_by_url = {
                m["old_url"]: m.get("new_url") for m in selected if m.get("old_url")
            }
            # A watch sweep is the one place in the product that repeatedly
            # touches a customer's production origin, so it gets its own
            # bucket namespace and a deliberately slow refill.
            set_limiter(
                HostRateLimiter(
                    namespace=WATCH_NAMESPACE,
                    capacity=WATCH_CAPACITY,
                    rate=WATCH_RATE,
                )
            )
            results = await self.probe_all(list(expected_by_url))

            findings: dict[str, dict[str, Any]] = {}
            for url, result in results.items():
                expected = expected_by_url.get(url)
                verdict = classify(result, expected)
                if verdict is None:
                    continue
                issue_type, detail = verdict
                suggested, fix_source = suggest_fix(issue_type, expected, result)
                findings[url] = {
                    "issue_type": issue_type,
                    "severity": SEVERITY.get(issue_type, "warning"),
                    "detail": detail,
                    "http_status": result.final_status,
                    "final_url": result.final_url or None,
                    "hops": result.hop_count,
                    "expected_url": expected,
                    "clicks_at_risk": clicks.get(_normalize_url_key(url), 0),
                    "suggested_target": suggested,
                    "fix_source": fix_source,
                }

            reconciled = self._reconcile(watch_id, findings, set(results))
            clicks_at_risk = sum(f["clicks_at_risk"] for f in findings.values())

            outcome = SweepOutcome(
                check_id=check_id,
                urls_checked=len(results),
                urls_ok=len(results) - len(findings),
                issues_open=len(findings),
                issues_new=reconciled["new"],
                issues_resolved=reconciled["resolved"],
                clicks_at_risk=clicks_at_risk,
            )

            if check_id:
                self.client.table("watch_checks").update(
                    {
                        "status": "completed",
                        "finished_at": _now().isoformat(),
                        "urls_checked": outcome.urls_checked,
                        "urls_ok": outcome.urls_ok,
                        "issues_open": outcome.issues_open,
                        "issues_new": outcome.issues_new,
                        "issues_resolved": outcome.issues_resolved,
                        "clicks_at_risk": outcome.clicks_at_risk,
                    }
                ).eq("id", check_id).execute()

            outcome.alerted = self.send_alert_if_needed(watch)
            return outcome

        except Exception as exc:
            if check_id:
                self.client.table("watch_checks").update(
                    {
                        "status": "failed",
                        "finished_at": _now().isoformat(),
                        "error": str(exc)[:500],
                    }
                ).eq("id", check_id).execute()
            raise

    def _reconcile(
        self,
        watch_id: str,
        findings: dict[str, dict[str, Any]],
        probed: set[str],
    ) -> dict[str, int]:
        """
        Fold this sweep's findings into the standing issue rows.

        Three transitions matter and each has a different reporting meaning:
        a problem that is new, a problem that changed shape (the user fixed one
        thing and revealed another — worth re-alerting), and a problem that is
        gone.

        `probed` is what this sweep actually looked at, and closing an issue
        requires membership in it. Absence from `findings` is not evidence of
        repair: MAX_URLS_PER_SWEEP and the sweep deadline both leave URLs
        unchecked, and treating those as fixed would silently close real
        problems on exactly the large sites that need monitoring most.
        """
        existing = {
            row["old_url"]: row
            for row in self.client.table("watch_issues")
            .select("*")
            .eq("watch_id", watch_id)
            .execute()
            .data
            or []
        }

        now = _now().isoformat()
        new_count = 0

        for url, finding in findings.items():
            prior = existing.get(url)
            if prior is None:
                self.client.table("watch_issues").insert(
                    {
                        "watch_id": watch_id,
                        "old_url": url,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "occurrences": 1,
                        **finding,
                    }
                ).execute()
                new_count += 1
                continue

            reopened = prior.get("resolved_at") is not None
            changed = prior.get("issue_type") != finding["issue_type"]
            updates: dict[str, Any] = {
                **finding,
                "last_seen_at": now,
                "resolved_at": None,
            }
            if reopened or changed:
                # A different failure at the same URL is a different problem:
                # restart its history and let it alert again.
                updates["occurrences"] = 1
                updates["alerted_at"] = None
                updates["first_seen_at"] = now
                new_count += 1
            else:
                updates["occurrences"] = int(prior.get("occurrences") or 0) + 1

            self.client.table("watch_issues").update(updates).eq(
                "id", prior["id"]
            ).execute()

        resolved_count = 0
        for url, prior in existing.items():
            if url in findings or prior.get("resolved_at") is not None:
                continue
            if url not in probed:
                continue
            self.client.table("watch_issues").update(
                {"resolved_at": now, "occurrences": 0}
            ).eq("id", prior["id"]).execute()
            resolved_count += 1

        return {"new": new_count, "resolved": resolved_count}

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    def pending_alerts(self, watch_id: str) -> list[dict[str, Any]]:
        """
        Open issues that have never been reported and are worth reporting.

        A network blip clears itself by the next sweep, so transient types wait
        for a second consecutive sighting before they are allowed to wake
        someone up.
        """
        rows = (
            self.client.table("watch_issues")
            .select("*")
            .eq("watch_id", watch_id)
            .is_("resolved_at", "null")
            .is_("alerted_at", "null")
            .order("clicks_at_risk", desc=True)
            .execute()
        ).data or []

        return [
            row
            for row in rows
            if row.get("issue_type") not in TRANSIENT_TYPES
            or int(row.get("occurrences") or 0) >= TRANSIENT_ALERT_THRESHOLD
        ]

    def send_alert_if_needed(self, watch: dict[str, Any]) -> int:
        """Email the unreported issues, then mark them reported. Returns count."""
        watch_id = watch["id"]
        pending = self.pending_alerts(watch_id)
        if not pending:
            return 0

        to_email = watch.get("alert_email") or self._profile_email(watch.get("user_id"))
        if not to_email:
            logger.warning("Watch %s has no alert address; skipping alert", watch_id)
            return 0

        project_name, session_id = self._session_label(watch.get("session_id"))
        critical = [i for i in pending if i.get("severity") == CRITICAL]
        clicks_at_risk = sum(int(i.get("clicks_at_risk") or 0) for i in pending)

        self.email_service.send_watch_alert(
            user_id=str(watch.get("user_id")),
            to_email=to_email,
            project_name=project_name,
            session_id=str(session_id or ""),
            watch_id=str(watch_id),
            domain=watch.get("old_domain") or "",
            issues=[self._issue_for_email(i) for i in pending[:25]],
            total_issues=len(pending),
            critical_count=len(critical),
            clicks_at_risk=clicks_at_risk,
        )

        now = _now().isoformat()
        for issue in pending:
            self.client.table("watch_issues").update({"alerted_at": now}).eq(
                "id", issue["id"]
            ).execute()
        return len(pending)

    @staticmethod
    def _issue_for_email(issue: dict[str, Any]) -> dict[str, Any]:
        issue_type = issue.get("issue_type") or ""
        return {
            "old_url": issue.get("old_url"),
            "label": DESCRIPTIONS.get(issue_type, issue_type.replace("_", " ")),
            "issue_type": issue_type,
            "severity": issue.get("severity"),
            "clicks_at_risk": int(issue.get("clicks_at_risk") or 0),
            "suggested_target": issue.get("suggested_target"),
            "detail": issue.get("detail"),
        }

    def _profile_email(self, user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        result = (
            self.client.table("user_profiles")
            .select("email")
            .eq("id", str(user_id))
            .execute()
        )
        return result.data[0].get("email") if result.data else None

    def _session_label(self, session_id: Optional[str]) -> tuple[str, Optional[str]]:
        if not session_id:
            return "your migration", None
        result = (
            self.client.table("migration_sessions")
            .select("id, project_name")
            .eq("id", str(session_id))
            .execute()
        )
        if not result.data:
            return "your migration", session_id
        return (result.data[0].get("project_name") or "your migration"), session_id

    # ------------------------------------------------------------------
    # Worker entry points
    # ------------------------------------------------------------------

    def claim_next_watch(self, worker_id: str, lease_seconds: int = 900):
        expires = (_now() + timedelta(seconds=lease_seconds)).isoformat()
        result = self.client.rpc(
            "claim_next_watch",
            {"p_worker_id": worker_id, "p_lease_expires_at": expires},
        ).execute()
        rows = result.data or []
        return rows[0] if rows else None

    def release_watch(self, watch_id: str, error: Optional[str] = None) -> None:
        self.client.rpc(
            "release_watch_lease",
            {"p_watch_id": str(watch_id), "p_error": error[:500] if error else None},
        ).execute()
