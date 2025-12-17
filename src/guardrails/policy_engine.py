"""
Policy Engine for AutoGuardRails.

Evaluates cost events against policies to determine what actions to take.
This is a pure, stateless, testable module - no side effects.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from src.guardrails.models import (
    ActionPlan,
    CostEvent,
    GuardrailPolicy,
    PolicyExceptions,
    TimeWindow,
)

logger = logging.getLogger(__name__)


class PolicyEngine:
    """
    Policy evaluation engine.

    Pure functions that evaluate cost events against policies and generate action plans.
    No side effects - safe for testing and concurrent execution.
    """

    def evaluate(
        self, event: CostEvent, policies: list[GuardrailPolicy]
    ) -> ActionPlan:
        """
        Evaluate a cost event against all policies.

        Returns the first matching policy's action plan, or an unmatched plan.

        Args:
            event: The cost event to evaluate
            policies: List of policies to check against

        Returns:
            ActionPlan with matched=True if a policy matched, matched=False otherwise
        """
        logger.info(f"Evaluating event {event.event_id} against {len(policies)} policies")

        for policy in policies:
            if not policy.enabled:
                logger.debug(f"Skipping disabled policy: {policy.policy_id}")
                continue

            if self.match_event(event, policy):
                logger.info(f"Policy matched: {policy.policy_id}")
                return self._build_action_plan(event, policy)

        logger.info("No policies matched")
        return ActionPlan(matched=False)

    def match_event(self, event: CostEvent, policy: GuardrailPolicy) -> bool:
        """
        Check if a cost event matches a policy's conditions.

        This is a pure function with no side effects.

        Args:
            event: The cost event
            policy: The policy to match against

        Returns:
            True if event matches all policy conditions, False otherwise
        """
        # Check source
        if event.source not in policy.match.source:
            logger.debug(
                f"Source mismatch: {event.source} not in {policy.match.source}"
            )
            return False

        # Check account ID
        if event.account_id not in policy.match.account_ids:
            logger.debug(
                f"Account ID mismatch: {event.account_id} not in {policy.match.account_ids}"
            )
            return False

        # Check minimum amount
        if event.amount < policy.match.min_amount_usd:
            logger.debug(
                f"Amount below threshold: {event.amount} < {policy.match.min_amount_usd}"
            )
            return False

        # Check maximum amount (if set)
        if policy.match.max_amount_usd is not None:
            if event.amount > policy.match.max_amount_usd:
                logger.debug(
                    f"Amount above threshold: {event.amount} > {policy.match.max_amount_usd}"
                )
                return False

        # Check services (if specified)
        if policy.match.services is not None:
            event_service = event.details.get("service")
            if event_service not in policy.match.services:
                logger.debug(
                    f"Service mismatch: {event_service} not in {policy.match.services}"
                )
                return False

        # Check regions (if specified)
        if policy.match.regions is not None:
            event_region = event.details.get("region")
            if event_region not in policy.match.regions:
                logger.debug(
                    f"Region mismatch: {event_region} not in {policy.match.regions}"
                )
                return False

        # Check exceptions (allowlist)
        if policy.exceptions and self._is_exempted(event, policy.exceptions):
            logger.info(f"Event exempted by exception rules for policy {policy.policy_id}")
            return False

        return True

    def _is_exempted(self, event: CostEvent, exceptions: PolicyExceptions) -> bool:
        """
        Check if event is exempted by exception rules.

        Args:
            event: The cost event
            exceptions: Exception rules from policy

        Returns:
            True if event is exempted (should NOT trigger policy), False otherwise
        """
        # Check account allowlist
        if exceptions.accounts and event.account_id in exceptions.accounts:
            logger.debug(f"Account {event.account_id} is in exception allowlist")
            return True

        # Check principal allowlist (requires event.details.principal_arn)
        if exceptions.principals:
            event_principal = event.details.get("principal_arn")
            if event_principal and self._principal_matches_allowlist(
                event_principal, exceptions.principals
            ):
                logger.debug(f"Principal {event_principal} is in exception allowlist")
                return True

        # Check time window exemptions
        if exceptions.time_windows and self._in_exempted_time_window(
            exceptions.time_windows
        ):
            logger.debug("Current time is in exempted time window")
            return True

        return False

    def _principal_matches_allowlist(
        self, principal_arn: str, allowlist: list[str]
    ) -> bool:
        """
        Check if principal ARN matches any pattern in allowlist.

        Supports wildcard suffix (e.g., "arn:aws:iam::123456789012:role/production-*")

        Args:
            principal_arn: The principal ARN to check
            allowlist: List of ARN patterns (supports * suffix)

        Returns:
            True if principal matches any allowlist pattern
        """
        for pattern in allowlist:
            if pattern.endswith("*"):
                # Wildcard match
                prefix = pattern[:-1]
                if principal_arn.startswith(prefix):
                    return True
            else:
                # Exact match
                if principal_arn == pattern:
                    return True
        return False

    def _in_exempted_time_window(self, time_windows: list[TimeWindow]) -> bool:
        """
        Check if current time is within any exempted time window.

        Args:
            time_windows: List of time windows to check

        Returns:
            True if current time is in any exempted window
        """
        now = datetime.utcnow()

        for window in time_windows:
            # Check day of week
            current_day = now.strftime("%a").lower()  # mon, tue, wed, etc.
            if current_day not in window.days:
                continue

            # Check time (simplified - assumes UTC, ignores timezone conversion)
            # TODO: Implement proper timezone support using pytz
            start_hour, start_min = map(int, window.start.split(":"))
            end_hour, end_min = map(int, window.end.split(":"))

            start_time = now.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            end_time = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)

            if start_time <= now <= end_time:
                logger.debug(
                    f"Current time {now} is within exempted window {window.start}-{window.end}"
                )
                return True

        return False

    def _build_action_plan(
        self, event: CostEvent, policy: GuardrailPolicy
    ) -> ActionPlan:
        """
        Build an action plan from a matched policy.

        Args:
            event: The matched cost event
            policy: The matched policy

        Returns:
            ActionPlan with all necessary information for execution
        """
        target_principals = [p.arn for p in policy.scope.principals]

        return ActionPlan(
            matched=True,
            matched_policy_id=policy.policy_id,
            mode=policy.mode,
            actions=policy.actions,
            ttl_minutes=policy.ttl_minutes,
            target_principals=target_principals,
        )


# ============================================================================
# Policy Loading Utilities
# ============================================================================


def load_policy_from_file(file_path: str | Path) -> GuardrailPolicy:
    """
    Load a single policy from a YAML file.

    Args:
        file_path: Path to YAML file

    Returns:
        GuardrailPolicy instance

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
        pydantic.ValidationError: If policy validation fails
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Policy file not found: {file_path}")

    logger.info(f"Loading policy from {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    policy = GuardrailPolicy(**data)
    logger.info(f"Loaded policy: {policy.policy_id} (mode={policy.mode})")

    return policy


def load_policies_from_directory(
    directory: str | Path, pattern: str = "*.yaml"
) -> list[GuardrailPolicy]:
    """
    Load all policies from a directory.

    Args:
        directory: Path to directory containing policy YAML files
        pattern: Glob pattern for policy files (default: *.yaml)

    Returns:
        List of GuardrailPolicy instances (only enabled policies)

    Raises:
        FileNotFoundError: If directory doesn't exist
    """
    directory = Path(directory)

    if not directory.exists():
        raise FileNotFoundError(f"Policy directory not found: {directory}")

    if not directory.is_dir():
        raise ValueError(f"Path is not a directory: {directory}")

    policies = []
    policy_files = list(directory.glob(pattern))

    logger.info(f"Found {len(policy_files)} policy files in {directory}")

    for file_path in policy_files:
        try:
            policy = load_policy_from_file(file_path)
            if policy.enabled:
                policies.append(policy)
                logger.info(f"Loaded enabled policy: {policy.policy_id}")
            else:
                logger.info(f"Skipping disabled policy: {policy.policy_id}")
        except Exception as e:
            logger.error(f"Failed to load policy from {file_path}: {e}")
            # Continue loading other policies instead of failing entirely
            continue

    logger.info(f"Loaded {len(policies)} enabled policies")
    return policies


def validate_policy_file(file_path: str | Path) -> tuple[bool, Optional[str]]:
    """
    Validate a policy YAML file without loading it into memory.

    Useful for CI/CD validation.

    Args:
        file_path: Path to policy YAML file

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    try:
        load_policy_from_file(file_path)
        return True, None
    except FileNotFoundError as e:
        return False, f"File not found: {e}"
    except yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    except Exception as e:
        return False, f"Validation error: {e}"
