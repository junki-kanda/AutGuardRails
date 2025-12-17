"""
Unit tests for data models (src/guardrails/models.py).

Tests validation logic, field constraints, and serialization.
"""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from src.guardrails.models import (
    ActionExecution,
    ActionPlan,
    CostEvent,
    GuardrailPolicy,
    NotificationPayload,
    NotificationSettings,
    PolicyAction,
    PolicyExceptions,
    PolicyMatch,
    PolicyScope,
    Principal,
    TimeWindow,
)


# ============================================================================
# CostEvent Tests
# ============================================================================


class TestCostEvent:
    """Test CostEvent model validation."""

    def test_valid_cost_event(self):
        """Test creating a valid CostEvent."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.50,
            time_window="2025-01",
            details={"service": "Amazon EC2"},
        )
        assert event.event_id == "evt-123"
        assert event.source == "budgets"
        assert event.account_id == "123456789012"
        assert event.amount == 250.50

    def test_invalid_account_id_length(self):
        """Test account_id must be 12 digits."""
        with pytest.raises(ValidationError) as exc_info:
            CostEvent(
                event_id="evt-123",
                source="budgets",
                account_id="12345",  # Too short
                amount=100.0,
                time_window="2025-01",
            )
        assert "12-digit" in str(exc_info.value).lower()

    def test_invalid_account_id_non_numeric(self):
        """Test account_id must be numeric."""
        with pytest.raises(ValidationError):
            CostEvent(
                event_id="evt-123",
                source="budgets",
                account_id="abc123456789",
                amount=100.0,
                time_window="2025-01",
            )

    def test_negative_amount(self):
        """Test amount must be positive."""
        with pytest.raises(ValidationError):
            CostEvent(
                event_id="evt-123",
                source="budgets",
                account_id="123456789012",
                amount=-100.0,
                time_window="2025-01",
            )

    def test_invalid_source(self):
        """Test source must be 'budgets' or 'anomaly'."""
        with pytest.raises(ValidationError):
            CostEvent(
                event_id="evt-123",
                source="invalid",
                account_id="123456789012",
                amount=100.0,
                time_window="2025-01",
            )

    def test_details_optional(self):
        """Test details field is optional."""
        event = CostEvent(
            event_id="evt-123",
            source="anomaly",
            account_id="123456789012",
            amount=100.0,
            time_window="2025-01-15",
        )
        assert event.details == {}


# ============================================================================
# TimeWindow Tests
# ============================================================================


class TestTimeWindow:
    """Test TimeWindow model validation."""

    def test_valid_time_window(self):
        """Test creating a valid TimeWindow."""
        tw = TimeWindow(
            start="09:00",
            end="17:00",
            timezone="Asia/Tokyo",
            days=["mon", "tue", "wed", "thu", "fri"],
        )
        assert tw.start == "09:00"
        assert tw.days == ["mon", "tue", "wed", "thu", "fri"]

    def test_invalid_time_format(self):
        """Test time must be HH:MM format."""
        with pytest.raises(ValidationError):
            TimeWindow(
                start="9:00",  # Missing leading zero
                end="17:00",
                timezone="UTC",
                days=["mon"],
            )

    def test_invalid_day(self):
        """Test days must be valid abbreviations."""
        with pytest.raises(ValidationError) as exc_info:
            TimeWindow(
                start="09:00", end="17:00", timezone="UTC", days=["monday"]  # Full name
            )
        assert "invalid day" in str(exc_info.value).lower()

    def test_days_normalized_to_lowercase(self):
        """Test days are normalized to lowercase."""
        tw = TimeWindow(start="09:00", end="17:00", timezone="UTC", days=["MON", "TUE"])
        assert tw.days == ["mon", "tue"]


# ============================================================================
# PolicyMatch Tests
# ============================================================================


class TestPolicyMatch:
    """Test PolicyMatch model validation."""

    def test_valid_policy_match(self):
        """Test creating a valid PolicyMatch."""
        match = PolicyMatch(
            source=["budgets", "anomaly"],
            account_ids=["123456789012"],
            min_amount_usd=100.0,
        )
        assert match.min_amount_usd == 100.0

    def test_max_greater_than_min(self):
        """Test max_amount_usd > min_amount_usd."""
        match = PolicyMatch(
            source=["budgets"],
            account_ids=["123456789012"],
            min_amount_usd=100.0,
            max_amount_usd=500.0,
        )
        assert match.max_amount_usd == 500.0

    def test_max_less_than_min_fails(self):
        """Test max_amount_usd <= min_amount_usd raises error."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyMatch(
                source=["budgets"],
                account_ids=["123456789012"],
                min_amount_usd=500.0,
                max_amount_usd=100.0,
            )
        assert "greater than" in str(exc_info.value).lower()

    def test_invalid_source(self):
        """Test source validation."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyMatch(
                source=["invalid_source"],
                account_ids=["123456789012"],
                min_amount_usd=100.0,
            )
        assert "invalid source" in str(exc_info.value).lower()

    def test_services_optional(self):
        """Test services field is optional."""
        match = PolicyMatch(
            source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
        )
        assert match.services is None


# ============================================================================
# Principal Tests
# ============================================================================


class TestPrincipal:
    """Test Principal model validation."""

    def test_valid_iam_role(self):
        """Test creating a valid IAM role principal."""
        principal = Principal(
            type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
        )
        assert principal.type == "iam_role"

    def test_valid_iam_user(self):
        """Test creating a valid IAM user principal."""
        principal = Principal(
            type="iam_user", arn="arn:aws:iam::123456789012:user/dev-user"
        )
        assert principal.type == "iam_user"

    def test_wildcard_arn_rejected(self):
        """Test ARN with wildcard is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Principal(type="iam_role", arn="arn:aws:iam::123456789012:role/*")
        assert "wildcard" in str(exc_info.value).lower()

    def test_invalid_arn_format(self):
        """Test ARN must start with arn:aws:iam::."""
        with pytest.raises(ValidationError) as exc_info:
            Principal(type="iam_role", arn="invalid-arn")
        assert "must start with" in str(exc_info.value).lower()


# ============================================================================
# PolicyScope Tests
# ============================================================================


class TestPolicyScope:
    """Test PolicyScope model validation."""

    def test_valid_policy_scope(self):
        """Test creating a valid PolicyScope."""
        scope = PolicyScope(
            principals=[
                Principal(
                    type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                )
            ],
            regions=["us-east-1"],
        )
        assert len(scope.principals) == 1

    def test_empty_principals_rejected(self):
        """Test principals list cannot be empty."""
        with pytest.raises(ValidationError):
            PolicyScope(principals=[], regions=["us-east-1"])

    def test_regions_optional(self):
        """Test regions field is optional."""
        scope = PolicyScope(
            principals=[
                Principal(
                    type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                )
            ]
        )
        assert scope.regions is None


# ============================================================================
# PolicyAction Tests
# ============================================================================


class TestPolicyAction:
    """Test PolicyAction model validation."""

    def test_attach_deny_policy_with_deny_list(self):
        """Test attach_deny_policy action with deny list."""
        action = PolicyAction(
            type="attach_deny_policy", deny=["ec2:RunInstances", "ec2:CreateVpc"]
        )
        assert action.type == "attach_deny_policy"
        assert len(action.deny) == 2

    def test_notify_only_no_deny_list(self):
        """Test notify_only action doesn't need deny list."""
        action = PolicyAction(type="notify_only")
        assert action.deny is None

    def test_attach_deny_policy_without_deny_fails(self):
        """Test attach_deny_policy requires deny list."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyAction(type="attach_deny_policy")
        assert "deny" in str(exc_info.value).lower()

    def test_dangerous_action_rejected(self):
        """Test dangerous actions are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyAction(type="attach_deny_policy", deny=["s3:DeleteBucket"])
        assert "dangerous action" in str(exc_info.value).lower()

    def test_multiple_dangerous_actions_rejected(self):
        """Test multiple dangerous actions are rejected."""
        with pytest.raises(ValidationError):
            PolicyAction(
                type="attach_deny_policy",
                deny=["ec2:RunInstances", "dynamodb:DeleteTable"],
            )


# ============================================================================
# GuardrailPolicy Tests
# ============================================================================


class TestGuardrailPolicy:
    """Test GuardrailPolicy model validation."""

    def test_valid_policy(self):
        """Test creating a valid GuardrailPolicy."""
        policy = GuardrailPolicy(
            policy_id="test-policy",
            mode="manual",
            ttl_minutes=180,
            match=PolicyMatch(
                source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
            ),
            scope=PolicyScope(
                principals=[
                    Principal(
                        type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                    )
                ]
            ),
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])],
            notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
        )
        assert policy.policy_id == "test-policy"
        assert policy.enabled is True  # Default

    def test_policy_with_exceptions(self):
        """Test policy with exception rules."""
        policy = GuardrailPolicy(
            policy_id="test-policy",
            mode="auto",
            ttl_minutes=60,
            match=PolicyMatch(
                source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
            ),
            scope=PolicyScope(
                principals=[
                    Principal(
                        type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                    )
                ]
            ),
            actions=[PolicyAction(type="notify_only")],
            notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
            exceptions=PolicyExceptions(
                accounts=["999888777666"],
                time_windows=[
                    TimeWindow(start="09:00", end="17:00", timezone="UTC", days=["mon"])
                ],
            ),
        )
        assert policy.exceptions is not None
        assert len(policy.exceptions.accounts) == 1

    def test_empty_actions_rejected(self):
        """Test actions list cannot be empty."""
        with pytest.raises(ValidationError):
            GuardrailPolicy(
                policy_id="test-policy",
                mode="dry_run",
                ttl_minutes=0,
                match=PolicyMatch(
                    source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
                ),
                scope=PolicyScope(
                    principals=[
                        Principal(
                            type="iam_role",
                            arn="arn:aws:iam::123456789012:role/ci-deployer",
                        )
                    ]
                ),
                actions=[],  # Empty
                notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
            )


# ============================================================================
# ActionPlan Tests
# ============================================================================


class TestActionPlan:
    """Test ActionPlan model validation."""

    def test_matched_action_plan(self):
        """Test creating an ActionPlan with matched=True."""
        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/ci-deployer"],
        )
        assert plan.matched is True
        assert plan.mode == "manual"

    def test_unmatched_action_plan(self):
        """Test creating an ActionPlan with matched=False."""
        plan = ActionPlan(matched=False)
        assert plan.matched is False
        assert plan.matched_policy_id is None

    def test_matched_without_policy_id_fails(self):
        """Test matched=True requires matched_policy_id."""
        with pytest.raises(ValidationError) as exc_info:
            ActionPlan(matched=True, mode="manual", actions=[])
        assert "matched_policy_id" in str(exc_info.value).lower()

    def test_matched_without_mode_fails(self):
        """Test matched=True requires mode."""
        with pytest.raises(ValidationError) as exc_info:
            ActionPlan(matched=True, matched_policy_id="test-policy", actions=[])
        assert "mode" in str(exc_info.value).lower()

    def test_matched_without_actions_fails(self):
        """Test matched=True requires actions."""
        with pytest.raises(ValidationError) as exc_info:
            ActionPlan(
                matched=True, matched_policy_id="test-policy", mode="manual", actions=[]
            )
        assert "actions" in str(exc_info.value).lower()


# ============================================================================
# ActionExecution Tests
# ============================================================================


class TestActionExecution:
    """Test ActionExecution model validation."""

    def test_valid_execution(self):
        """Test creating a valid ActionExecution."""
        now = datetime.utcnow()
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=now,
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/guardrails-1"]},
            ttl_expires_at=now + timedelta(hours=3),
        )
        assert execution.status == "executed"
        assert execution.executed_by == "system:auto"

    def test_planned_execution(self):
        """Test planned execution (not yet executed)."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",
            executed_by="pending",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
        )
        assert execution.status == "planned"
        assert execution.executed_at is None

    def test_rolled_back_execution(self):
        """Test rolled back execution."""
        now = datetime.utcnow()
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="rolled_back",
            executed_at=now,
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
            rolled_back_at=now + timedelta(hours=3),
        )
        assert execution.status == "rolled_back"
        assert execution.rolled_back_at is not None


# ============================================================================
# NotificationPayload Tests
# ============================================================================


class TestNotificationPayload:
    """Test NotificationPayload model validation."""

    def test_dry_run_notification(self):
        """Test creating a dry_run notification."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2025-01",
        )
        policy = GuardrailPolicy(
            policy_id="test-policy",
            mode="dry_run",
            ttl_minutes=0,
            match=PolicyMatch(
                source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
            ),
            scope=PolicyScope(
                principals=[
                    Principal(
                        type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                    )
                ]
            ),
            actions=[PolicyAction(type="notify_only")],
            notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
        )
        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
        )

        payload = NotificationPayload(
            notification_type="dry_run", event=event, policy=policy, action_plan=plan
        )
        assert payload.notification_type == "dry_run"

    def test_approval_request_notification(self):
        """Test creating an approval_request notification."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2025-01",
        )
        policy = GuardrailPolicy(
            policy_id="test-policy",
            mode="manual",
            ttl_minutes=180,
            match=PolicyMatch(
                source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
            ),
            scope=PolicyScope(
                principals=[
                    Principal(
                        type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                    )
                ]
            ),
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])],
            notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
        )
        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])],
        )

        payload = NotificationPayload(
            notification_type="approval_request",
            event=event,
            policy=policy,
            action_plan=plan,
            execution_id="exec-123",
            approval_url="https://api.autoguardrails.com/approve?id=exec-123",
            rejection_url="https://api.autoguardrails.com/reject?id=exec-123",
        )
        assert payload.notification_type == "approval_request"
        assert payload.execution_id == "exec-123"
        assert payload.approval_url is not None


# ============================================================================
# Serialization Tests
# ============================================================================


class TestSerialization:
    """Test JSON serialization and deserialization."""

    def test_cost_event_serialization(self):
        """Test CostEvent can be serialized to JSON and back."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2025-01",
        )
        json_data = event.model_dump_json()
        restored = CostEvent.model_validate_json(json_data)
        assert restored.event_id == event.event_id

    def test_policy_serialization(self):
        """Test GuardrailPolicy can be serialized to JSON and back."""
        policy = GuardrailPolicy(
            policy_id="test-policy",
            mode="manual",
            ttl_minutes=180,
            match=PolicyMatch(
                source=["budgets"], account_ids=["123456789012"], min_amount_usd=100.0
            ),
            scope=PolicyScope(
                principals=[
                    Principal(
                        type="iam_role", arn="arn:aws:iam::123456789012:role/ci-deployer"
                    )
                ]
            ),
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])],
            notify=NotificationSettings(slack_webhook_ssm_param="/guardrails/slack"),
        )
        json_data = policy.model_dump_json()
        restored = GuardrailPolicy.model_validate_json(json_data)
        assert restored.policy_id == policy.policy_id
