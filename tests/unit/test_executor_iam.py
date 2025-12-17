"""Tests for IAM Executor."""

from datetime import datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from src.guardrails.executor_iam import IAMExecutor
from src.guardrails.models import ActionExecution, ActionPlan, PolicyAction


@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for boto3."""
    import os

    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_iam(aws_credentials):
    """Mock AWS IAM for tests."""
    with mock_aws():
        iam = boto3.client("iam", region_name="us-east-1")
        yield iam


@pytest.fixture
def iam_executor(mock_iam):
    """Create IAM Executor instance within mocked AWS context."""
    return IAMExecutor(dry_run=False)


@pytest.fixture
def iam_executor_dry_run(mock_iam):
    """Create IAM Executor in dry-run mode within mocked AWS context."""
    return IAMExecutor(dry_run=True)


class TestIAMExecutorInit:
    """Test IAM Executor initialization."""

    def test_init_default(self):
        """Test default initialization."""
        executor = IAMExecutor()
        assert executor.dry_run is False

    def test_init_dry_run(self):
        """Test dry-run initialization."""
        executor = IAMExecutor(dry_run=True)
        assert executor.dry_run is True


class TestParsePrincipalARN:
    """Test principal ARN parsing."""

    def test_parse_role_arn(self, iam_executor):
        """Test parsing role ARN."""
        arn = "arn:aws:iam::123456789012:role/MyRole"
        principal_type, principal_name = iam_executor._parse_principal_arn(arn)

        assert principal_type == "role"
        assert principal_name == "MyRole"

    def test_parse_user_arn(self, iam_executor):
        """Test parsing user ARN."""
        arn = "arn:aws:iam::123456789012:user/MyUser"
        principal_type, principal_name = iam_executor._parse_principal_arn(arn)

        assert principal_type == "user"
        assert principal_name == "MyUser"

    def test_parse_role_with_path(self, iam_executor):
        """Test parsing role ARN with path."""
        arn = "arn:aws:iam::123456789012:role/path/to/MyRole"
        principal_type, principal_name = iam_executor._parse_principal_arn(arn)

        assert principal_type == "role"
        assert principal_name == "path/to/MyRole"

    def test_parse_invalid_arn(self, iam_executor):
        """Test parsing invalid ARN."""
        with pytest.raises(ValueError, match="Invalid IAM ARN"):
            iam_executor._parse_principal_arn("invalid-arn")

    def test_parse_non_iam_arn(self, iam_executor):
        """Test parsing non-IAM ARN."""
        with pytest.raises(ValueError, match="Invalid IAM ARN"):
            iam_executor._parse_principal_arn("arn:aws:s3:::bucket")

    def test_parse_unsupported_type(self, iam_executor):
        """Test parsing unsupported principal type."""
        with pytest.raises(ValueError, match="Unsupported principal type"):
            iam_executor._parse_principal_arn("arn:aws:iam::123456789012:group/MyGroup")


class TestExecuteActionPlan:
    """Test action plan execution."""

    def test_execute_unmatched_plan(self, iam_executor):
        """Test executing unmatched plan fails."""
        plan = ActionPlan(
            matched=False,
            matched_policy_id=None,
            mode=None,
            actions=[],
            ttl_minutes=None,
            target_principals=[],
        )

        with pytest.raises(ValueError, match="Cannot execute unmatched plan"):
            iam_executor.execute_action_plan(plan, event_id="evt-123")

    def test_execute_notify_only(self, iam_executor):
        """Test executing notify_only action."""
        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=0,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        executions = iam_executor.execute_action_plan(
            plan, event_id="evt-123", executed_by="test-user"
        )

        assert len(executions) == 1
        assert executions[0].status == "executed"
        assert executions[0].action == "notify_only"
        assert executions[0].diff["no_changes"] is True

    def test_execute_attach_deny_policy_dry_run(self, iam_executor_dry_run):
        """Test executing attach_deny_policy in dry-run mode."""
        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        executions = iam_executor_dry_run.execute_action_plan(plan, event_id="evt-123")

        assert len(executions) == 1
        assert executions[0].status == "executed"
        assert executions[0].action == "attach_deny_policy"
        assert executions[0].diff["dry_run"] is True
        assert "ec2:RunInstances" in executions[0].diff["would_deny"]

    def test_execute_attach_deny_policy_real(self, iam_executor, mock_iam):
        """Test executing attach_deny_policy with real AWS API calls (mocked)."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[
                PolicyAction(
                    type="attach_deny_policy",
                    deny=["ec2:RunInstances", "ec2:CreateNatGateway"],
                )
            ],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/test-role"],
        )

        executions = iam_executor.execute_action_plan(
            plan, event_id="evt-123", executed_by="test-user"
        )

        assert len(executions) == 1
        assert executions[0].status == "executed"
        assert executions[0].action == "attach_deny_policy"
        assert executions[0].executed_by == "test-user"
        assert executions[0].ttl_expires_at is not None

        # Verify diff contains expected fields
        diff = executions[0].diff
        assert "policy_arn" in diff
        assert "policy_name" in diff
        assert "guardrails-deny-" in diff["policy_name"]
        assert diff["principal_type"] == "role"
        assert diff["principal_name"] == "test-role"
        assert "ec2:RunInstances" in diff["denied_actions"]

    def test_execute_multiple_principals(self, iam_executor, mock_iam):
        """Test executing action on multiple principals."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )
        mock_iam.create_user(UserName="test-user")

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:*"])],
            ttl_minutes=0,
            target_principals=[
                "arn:aws:iam::123456789012:role/test-role",
                "arn:aws:iam::123456789012:user/test-user",
            ],
        )

        executions = iam_executor.execute_action_plan(plan, event_id="evt-123")

        assert len(executions) == 2
        assert all(e.status == "executed" for e in executions)
        assert executions[0].target == "arn:aws:iam::123456789012:role/test-role"
        assert executions[1].target == "arn:aws:iam::123456789012:user/test-user"

    def test_execute_with_ttl(self, iam_executor, mock_iam):
        """Test executing action with TTL sets expiration time."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="manual",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:*"])],
            ttl_minutes=120,  # 2 hours
            target_principals=["arn:aws:iam::123456789012:role/test-role"],
        )

        executions = iam_executor.execute_action_plan(plan, event_id="evt-123")

        assert len(executions) == 1
        assert executions[0].ttl_expires_at is not None

        # TTL should be approximately 120 minutes from now
        expected_expiry = datetime.utcnow() + timedelta(minutes=120)
        diff = abs((executions[0].ttl_expires_at - expected_expiry).total_seconds())
        assert diff < 5  # Within 5 seconds tolerance


class TestAttachDenyPolicy:
    """Test attach deny policy implementation."""

    def test_attach_deny_policy_to_role(self, iam_executor, mock_iam):
        """Test attaching deny policy to role."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        result = iam_executor._attach_deny_policy(
            principal_arn="arn:aws:iam::123456789012:role/test-role",
            deny_actions=["s3:DeleteBucket", "s3:DeleteObject"],
            policy_id="test-policy",
        )

        assert "policy_arn" in result
        assert result["principal_type"] == "role"
        assert result["principal_name"] == "test-role"
        assert len(result["denied_actions"]) == 2
        assert "after" in result
        assert len(result["after"]) == 1  # Policy was attached

    def test_attach_deny_policy_to_user(self, iam_executor, mock_iam):
        """Test attaching deny policy to user."""
        # Setup IAM resources
        mock_iam.create_user(UserName="test-user")

        result = iam_executor._attach_deny_policy(
            principal_arn="arn:aws:iam::123456789012:user/test-user",
            deny_actions=["dynamodb:DeleteTable"],
            policy_id="test-policy",
        )

        assert result["principal_type"] == "user"
        assert result["principal_name"] == "test-user"
        assert "dynamodb:DeleteTable" in result["denied_actions"]

    def test_attach_idempotent(self, iam_executor, mock_iam):
        """Test that attaching same policy twice is idempotent."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        # First attach
        result1 = iam_executor._attach_deny_policy(
            principal_arn="arn:aws:iam::123456789012:role/test-role",
            deny_actions=["ec2:RunInstances"],
            policy_id="test-policy",
        )

        # Second attach (should reuse existing policy)
        result2 = iam_executor._attach_deny_policy(
            principal_arn="arn:aws:iam::123456789012:role/test-role",
            deny_actions=["ec2:RunInstances"],
            policy_id="test-policy",
        )

        # Same policy should be used
        assert result1["policy_arn"] == result2["policy_arn"]


class TestRollbackExecution:
    """Test rollback functionality."""

    def test_rollback_not_executed(self, iam_executor):
        """Test rollback of non-executed action fails gracefully."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="planned",
            executed_at=None,
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        result = iam_executor.rollback_execution(execution)
        assert result is False

    def test_rollback_notify_only(self, iam_executor):
        """Test rollback of notify_only action."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="test",
            action="notify_only",
            target="arn:aws:iam::123456789012:role/test",
            diff={"no_changes": True},
        )

        result = iam_executor.rollback_execution(execution)
        assert result is True
        assert execution.status == "rolled_back"
        assert execution.rolled_back_at is not None

    def test_rollback_dry_run_mode(self, iam_executor_dry_run):
        """Test rollback in dry-run mode."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={
                "policy_arn": "arn:aws:iam::123456789012:policy/test-policy",
                "principal_type": "role",
                "principal_name": "test",
            },
        )

        result = iam_executor_dry_run.rollback_execution(execution)
        assert result is True

    def test_rollback_attach_deny_policy(self, iam_executor, mock_iam):
        """Test rollback of attach_deny_policy action."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        # First attach policy
        result = iam_executor._attach_deny_policy(
            principal_arn="arn:aws:iam::123456789012:role/test-role",
            deny_actions=["ec2:RunInstances"],
            policy_id="test-policy",
        )

        # Create execution record
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test-role",
            diff=result,
        )

        # Rollback
        rollback_result = iam_executor.rollback_execution(execution)

        assert rollback_result is True
        assert execution.status == "rolled_back"
        assert execution.rolled_back_at is not None

        # Verify policy was detached
        attached = mock_iam.list_attached_role_policies(RoleName="test-role")
        assert len(attached["AttachedPolicies"]) == 0

    def test_rollback_dry_run_execution(self, iam_executor):
        """Test rollback of dry-run execution."""
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="test",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"dry_run": True, "would_deny": ["ec2:*"]},
        )

        result = iam_executor.rollback_execution(execution)
        assert result is True
        assert execution.status == "rolled_back"


class TestListAttachedPolicies:
    """Test listing attached policies."""

    def test_list_attached_role_policies(self, iam_executor, mock_iam):
        """Test listing attached policies for role."""
        # Setup IAM resources
        mock_iam.create_role(
            RoleName="test-role",
            AssumeRolePolicyDocument='{"Version": "2012-10-17"}',
        )

        policies = iam_executor._list_attached_policies("role", "test-role")
        assert isinstance(policies, list)
        assert len(policies) == 0  # No policies attached initially

    def test_list_attached_user_policies(self, iam_executor, mock_iam):
        """Test listing attached policies for user."""
        # Setup IAM resources
        mock_iam.create_user(UserName="test-user")

        policies = iam_executor._list_attached_policies("user", "test-user")
        assert isinstance(policies, list)
        assert len(policies) == 0

    def test_list_attached_invalid_type(self, iam_executor):
        """Test listing policies for invalid principal type."""
        with pytest.raises(ValueError, match="Unsupported principal type"):
            iam_executor._list_attached_policies("group", "test-group")
