"""
Data models for AutoGuardRails.

All models use Pydantic for validation and serialization.
These models are shared across all components (policy engine, executor, notifier, etc.).
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# Cost Event Models
# ============================================================================


class CostEvent(BaseModel):
    """
    Represents a cost event from AWS Budgets or Cost Anomaly Detection.

    This is the normalized input to the policy engine.
    """

    event_id: str = Field(..., description="Unique identifier for this event")
    source: Literal["budgets", "anomaly"] = Field(
        ..., description="Source of the cost event"
    )
    account_id: str = Field(..., description="12-digit AWS account ID")
    amount: float = Field(..., gt=0, description="Cost amount in USD")
    time_window: str = Field(
        ...,
        description="Time period (e.g., '2025-01' for monthly, '2025-01-15' for daily)",
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata (service, region, etc.)"
    )

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v: str) -> str:
        """Validate AWS account ID is 12 digits."""
        if not v.isdigit() or len(v) != 12:
            raise ValueError("account_id must be a 12-digit string")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "evt-abc123",
                "source": "budgets",
                "account_id": "123456789012",
                "amount": 250.50,
                "time_window": "2025-01",
                "details": {
                    "service": "Amazon Elastic Compute Cloud",
                    "region": "us-east-1",
                },
            }
        }


# ============================================================================
# Policy Models
# ============================================================================


class TimeWindow(BaseModel):
    """Time window for exception rules (e.g., business hours)."""

    start: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="Start time (HH:MM)")
    end: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="End time (HH:MM)")
    timezone: str = Field(..., description="IANA timezone (e.g., 'Asia/Tokyo', 'UTC')")
    days: list[str] = Field(
        ..., description="Days of week: mon, tue, wed, thu, fri, sat, sun"
    )

    @field_validator("days")
    @classmethod
    def validate_days(cls, v: list[str]) -> list[str]:
        """Validate day abbreviations."""
        valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        for day in v:
            if day.lower() not in valid_days:
                raise ValueError(f"Invalid day: {day}. Must be one of {valid_days}")
        return [d.lower() for d in v]


class PolicyExceptions(BaseModel):
    """Exception rules (allowlist) for policies."""

    accounts: Optional[list[str]] = Field(
        default=None, description="Account IDs to always exempt"
    )
    principals: Optional[list[str]] = Field(
        default=None, description="Principal ARNs to always exempt (supports * suffix)"
    )
    time_windows: Optional[list[TimeWindow]] = Field(
        default=None, description="Time periods when policy should NOT execute"
    )


class PolicyMatch(BaseModel):
    """Match conditions for cost events."""

    source: list[str] = Field(..., description="Event sources: budgets, anomaly")
    account_ids: list[str] = Field(..., description="AWS account IDs to match")
    min_amount_usd: float = Field(..., gt=0, description="Minimum cost to trigger")
    max_amount_usd: Optional[float] = Field(
        default=None, gt=0, description="Optional maximum cost"
    )
    services: Optional[list[str]] = Field(
        default=None, description="AWS service names (e.g., 'Amazon EC2')"
    )
    regions: Optional[list[str]] = Field(
        default=None, description="AWS regions (e.g., 'us-east-1')"
    )

    @field_validator("account_ids")
    @classmethod
    def validate_account_ids(cls, v: list[str]) -> list[str]:
        """Validate all account IDs are 12 digits."""
        for account_id in v:
            if not account_id.isdigit() or len(account_id) != 12:
                raise ValueError(f"Invalid account_id: {account_id}. Must be 12 digits")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: list[str]) -> list[str]:
        """Validate source values."""
        valid_sources = {"budgets", "anomaly"}
        for source in v:
            if source not in valid_sources:
                raise ValueError(f"Invalid source: {source}. Must be one of {valid_sources}")
        return v

    def model_post_init(self, __context: Any) -> None:
        """Validate max_amount_usd > min_amount_usd if set."""
        if self.max_amount_usd is not None and self.max_amount_usd <= self.min_amount_usd:
            raise ValueError("max_amount_usd must be greater than min_amount_usd")


class Principal(BaseModel):
    """IAM principal (role or user)."""

    type: Literal["iam_role", "iam_user"] = Field(..., description="Principal type")
    arn: str = Field(..., description="Full ARN of principal")

    @field_validator("arn")
    @classmethod
    def validate_arn(cls, v: str) -> str:
        """Validate ARN format and no wildcards."""
        if not v.startswith("arn:aws:iam::"):
            raise ValueError("ARN must start with 'arn:aws:iam::'")
        if "*" in v:
            raise ValueError("Wildcard principals (*) not allowed in ARN")
        return v


class PolicyScope(BaseModel):
    """Target scope for guardrails."""

    principals: list[Principal] = Field(
        ..., min_length=1, description="IAM principals to restrict"
    )
    regions: Optional[list[str]] = Field(default=None, description="AWS regions")


class PolicyAction(BaseModel):
    """Action to execute when policy matches."""

    type: Literal["attach_deny_policy", "notify_only"] = Field(
        ..., description="Action type"
    )
    deny: Optional[list[str]] = Field(
        default=None, description="IAM actions to deny (for attach_deny_policy)"
    )

    @field_validator("deny")
    @classmethod
    def validate_deny_actions(cls, v: Optional[list[str]], info) -> Optional[list[str]]:
        """Validate deny actions and block dangerous operations."""
        if v is None:
            return v

        # Block dangerous data deletion actions
        dangerous_actions = {
            "s3:DeleteBucket",
            "dynamodb:DeleteTable",
            "rds:DeleteDBInstance",
            "ec2:TerminateInstances",  # MVP: not allowed
            "ec2:DeleteVolume",
        }

        for action in v:
            if action in dangerous_actions:
                raise ValueError(
                    f"Dangerous action '{action}' not allowed in deny list. "
                    f"AutoGuardRails only supports safe, reversible actions."
                )

        return v

    def model_post_init(self, __context: Any) -> None:
        """Validate deny list is present for attach_deny_policy."""
        if self.type == "attach_deny_policy" and not self.deny:
            raise ValueError("'deny' list is required for attach_deny_policy action")


class NotificationSettings(BaseModel):
    """Notification configuration."""

    slack_webhook_ssm_param: str = Field(
        ..., description="SSM parameter path containing Slack webhook URL"
    )
    channel_hint: Optional[str] = Field(
        default=None, description="Slack channel name (informational)"
    )
    mention_users: Optional[list[str]] = Field(
        default=None, description="Users/groups to mention (e.g., '@platform-team')"
    )


class GuardrailPolicy(BaseModel):
    """
    Complete guardrail policy definition.

    Loaded from YAML files in the policies/ directory.
    """

    policy_id: str = Field(..., description="Unique policy identifier")
    description: Optional[str] = Field(default=None, description="Human-readable description")
    enabled: bool = Field(default=True, description="Whether policy is active")
    mode: Literal["dry_run", "manual", "auto"] = Field(..., description="Execution mode")
    ttl_minutes: int = Field(..., ge=0, description="Minutes until auto-rollback (0 = manual)")

    match: PolicyMatch = Field(..., description="Match conditions")
    scope: PolicyScope = Field(..., description="Target resources")
    actions: list[PolicyAction] = Field(
        ..., min_length=1, description="Actions to execute"
    )
    notify: NotificationSettings = Field(..., description="Notification settings")
    exceptions: Optional[PolicyExceptions] = Field(
        default=None, description="Exception rules"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "policy_id": "ci-ec2-spike",
                "description": "Quarantine CI role on EC2 spike",
                "enabled": True,
                "mode": "manual",
                "ttl_minutes": 180,
                "match": {
                    "source": ["budgets"],
                    "account_ids": ["123456789012"],
                    "min_amount_usd": 200,
                },
                "scope": {
                    "principals": [
                        {"type": "iam_role", "arn": "arn:aws:iam::123456789012:role/ci-deployer"}
                    ]
                },
                "actions": [
                    {"type": "attach_deny_policy", "deny": ["ec2:RunInstances"]}
                ],
                "notify": {
                    "slack_webhook_ssm_param": "/guardrails/slack_webhook",
                    "channel_hint": "#alerts",
                },
            }
        }


# ============================================================================
# Action Plan Models
# ============================================================================


class ActionPlan(BaseModel):
    """
    Result of policy evaluation.

    Contains what actions should be executed (if any).
    """

    matched: bool = Field(..., description="Whether any policy matched")
    matched_policy_id: Optional[str] = Field(default=None, description="ID of matched policy")
    mode: Optional[Literal["dry_run", "manual", "auto"]] = Field(
        default=None, description="Execution mode"
    )
    actions: list[PolicyAction] = Field(default_factory=list, description="Actions to execute")
    ttl_minutes: Optional[int] = Field(default=None, description="TTL for auto-rollback")
    target_principals: list[str] = Field(
        default_factory=list, description="Principal ARNs to target"
    )

    def model_post_init(self, __context: Any) -> None:
        """Validate consistency of fields."""
        if self.matched:
            if not self.matched_policy_id:
                raise ValueError("matched_policy_id is required when matched=True")
            if not self.mode:
                raise ValueError("mode is required when matched=True")
            if not self.actions:
                raise ValueError("actions list cannot be empty when matched=True")


# ============================================================================
# Execution Models
# ============================================================================


class ActionExecution(BaseModel):
    """
    Record of a guardrail action execution.

    Stored in DynamoDB for audit trail.
    """

    execution_id: str = Field(..., description="Unique execution ID (UUID)")
    policy_id: str = Field(..., description="Policy that triggered this execution")
    event_id: str = Field(..., description="Cost event that triggered this")
    status: Literal["planned", "approved", "executed", "rolled_back", "failed"] = Field(
        ..., description="Execution status"
    )
    executed_at: Optional[datetime] = Field(
        default=None, description="When the action was executed"
    )
    executed_by: str = Field(
        ..., description="Who executed (email or 'system:auto')"
    )
    action: str = Field(..., description="Action type (e.g., 'attach_deny_policy')")
    target: str = Field(..., description="Target principal ARN")
    diff: dict[str, Any] = Field(
        default_factory=dict,
        description="State diff: {'before': [...], 'after': [...]}",
    )
    ttl_expires_at: Optional[datetime] = Field(
        default=None, description="When auto-rollback should occur"
    )
    rolled_back_at: Optional[datetime] = Field(
        default=None, description="When rollback occurred"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "execution_id": "exec-abc123",
                "policy_id": "ci-ec2-spike",
                "event_id": "evt-def456",
                "status": "executed",
                "executed_at": "2025-01-15T10:30:00Z",
                "executed_by": "system:auto",
                "action": "attach_deny_policy",
                "target": "arn:aws:iam::123456789012:role/ci-deployer",
                "diff": {
                    "before": [],
                    "after": ["arn:aws:iam::123456789012:policy/guardrails-deny-abc123"],
                },
                "ttl_expires_at": "2025-01-15T13:30:00Z",
                "rolled_back_at": None,
            }
        }


# ============================================================================
# Notification Models
# ============================================================================


class NotificationPayload(BaseModel):
    """
    Payload for Slack notifications.

    Contains all information needed to construct a rich Slack message.
    """

    notification_type: Literal["dry_run", "approval_request", "execution_confirmed", "rollback"] = Field(
        ..., description="Type of notification"
    )
    event: CostEvent = Field(..., description="The cost event that triggered this")
    policy: GuardrailPolicy = Field(..., description="The matched policy")
    action_plan: ActionPlan = Field(..., description="The action plan")
    execution_id: Optional[str] = Field(
        default=None, description="Execution ID (for approval/confirmation)"
    )
    approval_url: Optional[str] = Field(
        default=None, description="URL for approval button (manual mode)"
    )
    rejection_url: Optional[str] = Field(
        default=None, description="URL for rejection button (manual mode)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "notification_type": "approval_request",
                "event": {
                    "event_id": "evt-abc123",
                    "source": "budgets",
                    "account_id": "123456789012",
                    "amount": 250.50,
                    "time_window": "2025-01",
                    "details": {},
                },
                "policy": {
                    "policy_id": "ci-ec2-spike",
                    "mode": "manual",
                    "ttl_minutes": 180,
                },
                "action_plan": {"matched": True, "mode": "manual"},
                "execution_id": "exec-abc123",
                "approval_url": "https://api.autoguardrails.com/approve?id=exec-abc123",
            }
        }
