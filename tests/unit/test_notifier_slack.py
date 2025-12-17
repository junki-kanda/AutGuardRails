"""Tests for Slack Notifier."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests

from src.guardrails.models import (
    ActionExecution,
    ActionPlan,
    CostEvent,
    PolicyAction,
)
from src.guardrails.notifier_slack import (
    SlackNotifier,
    generate_approval_url,
    get_cost_management_console_url,
)


class TestSlackNotifierInit:
    """Test SlackNotifier initialization."""

    def test_valid_init(self):
        """Test valid initialization."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")
        assert notifier.webhook_url == "https://hooks.slack.com/services/xxx"
        assert notifier.timeout == 10

    def test_init_with_custom_timeout(self):
        """Test initialization with custom timeout."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx", timeout=30)
        assert notifier.timeout == 30

    def test_init_with_empty_url(self):
        """Test initialization with empty webhook URL."""
        with pytest.raises(ValueError, match="webhook_url cannot be empty"):
            SlackNotifier("")

    def test_init_with_whitespace_url(self):
        """Test initialization with whitespace-only webhook URL."""
        with pytest.raises(ValueError, match="webhook_url cannot be empty"):
            SlackNotifier("   ")

    def test_init_strips_whitespace(self):
        """Test that webhook URL is stripped of whitespace."""
        notifier = SlackNotifier("  https://hooks.slack.com/services/xxx  ")
        assert notifier.webhook_url == "https://hooks.slack.com/services/xxx"


class TestSendDryRunAlert:
    """Test dry-run alert notifications."""

    def test_send_dry_run_alert_success(self):
        """Test successful dry-run alert."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_dry_run_alert(event, plan)

            assert result is True
            mock_post.assert_called_once()

            # Verify payload structure
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert "blocks" in payload
            assert len(payload["blocks"]) > 0
            assert payload["blocks"][0]["type"] == "header"

    def test_send_dry_run_alert_with_console_url(self):
        """Test dry-run alert with console URL."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=0,
            target_principals=[],
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            console_url = "https://console.aws.amazon.com/cost-management"
            result = notifier.send_dry_run_alert(event, plan, console_url)

            assert result is True

            # Verify console URL is in payload
            payload = mock_post.call_args[1]["json"]
            action_blocks = [b for b in payload["blocks"] if b["type"] == "actions"]
            assert len(action_blocks) > 0

    def test_send_dry_run_alert_network_error(self):
        """Test dry-run alert with network error."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="test-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=0,
            target_principals=[],
        )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("Network error")

            result = notifier.send_dry_run_alert(event, plan)

            assert result is False


class TestSendApprovalRequest:
    """Test approval request notifications."""

    def test_send_approval_request_success(self):
        """Test successful approval request."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=500.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="manual-policy",
            mode="manual",
            actions=[
                PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"])
            ],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/ci-deployer"],
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_approval_request(
                event,
                plan,
                execution_id="exec-123",
                approve_url="https://api.example.com/approve?id=exec-123",
                reject_url="https://api.example.com/reject?id=exec-123",
            )

            assert result is True
            mock_post.assert_called_once()

            # Verify payload has approval buttons
            payload = mock_post.call_args[1]["json"]
            action_blocks = [b for b in payload["blocks"] if b["type"] == "actions"]
            assert len(action_blocks) > 0

    def test_send_approval_request_without_urls(self):
        """Test approval request without approval/reject URLs."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=500.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="manual-policy",
            mode="manual",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=0,
            target_principals=[],
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_approval_request(event, plan, execution_id="exec-123")

            assert result is True


class TestSendExecutionConfirmation:
    """Test execution confirmation notifications."""

    def test_send_execution_confirmation_success(self):
        """Test successful execution confirmation."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="auto-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/deny-ec2"]},
            ttl_expires_at=datetime.utcnow() + timedelta(hours=3),
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_execution_confirmation(execution)

            assert result is True
            mock_post.assert_called_once()

            # Verify payload structure
            payload = mock_post.call_args[1]["json"]
            assert payload["blocks"][0]["text"]["text"] == "✅ Guardrail Applied"

    def test_send_execution_confirmation_with_rollback_url(self):
        """Test execution confirmation with rollback URL."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="auto-policy",
            event_id="evt-123",
            status="executed",
            executed_at=datetime.utcnow(),
            executed_by="user@example.com",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
            diff={},
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            rollback_url = "https://api.example.com/rollback?id=exec-123"
            result = notifier.send_execution_confirmation(execution, rollback_url)

            assert result is True

            # Verify rollback button is in payload
            payload = mock_post.call_args[1]["json"]
            action_blocks = [b for b in payload["blocks"] if b["type"] == "actions"]
            assert len(action_blocks) > 0


class TestSendRollbackConfirmation:
    """Test rollback confirmation notifications."""

    def test_send_rollback_confirmation_success(self):
        """Test successful rollback confirmation."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="auto-policy",
            event_id="evt-123",
            status="rolled_back",
            executed_at=datetime.utcnow() - timedelta(hours=2),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/ci-deployer",
            diff={},
            rolled_back_at=datetime.utcnow(),
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_rollback_confirmation(execution)

            assert result is True
            mock_post.assert_called_once()

            # Verify payload structure
            payload = mock_post.call_args[1]["json"]
            assert payload["blocks"][0]["text"]["text"] == "🔄 Guardrail Rolled Back"


class TestSendErrorAlert:
    """Test error alert notifications."""

    def test_send_error_alert_success(self):
        """Test successful error alert."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_error_alert(
                event, "IAM permission denied", execution_id="exec-123"
            )

            assert result is True
            mock_post.assert_called_once()

            # Verify payload structure
            payload = mock_post.call_args[1]["json"]
            assert payload["blocks"][0]["text"]["text"] == "❌ Guardrail Error"

    def test_send_error_alert_without_execution_id(self):
        """Test error alert without execution ID."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-01 to 2024-01-31",
            details={},
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send_error_alert(event, "Unknown error")

            assert result is True


class TestFormatActions:
    """Test action formatting."""

    def test_format_notify_only(self):
        """Test formatting notify_only action."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        actions = [PolicyAction(type="notify_only")]
        formatted = notifier._format_actions(actions)

        assert "Notify only" in formatted

    def test_format_attach_deny_policy(self):
        """Test formatting attach_deny_policy action."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        actions = [
            PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances", "ec2:*"])
        ]
        formatted = notifier._format_actions(actions)

        assert "Attach deny policy" in formatted
        assert "ec2:RunInstances" in formatted

    def test_format_multiple_actions(self):
        """Test formatting multiple actions."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        actions = [
            PolicyAction(type="attach_deny_policy", deny=["ec2:RunInstances"]),
            PolicyAction(type="notify_only"),
        ]
        formatted = notifier._format_actions(actions)

        assert "Attach deny policy" in formatted
        assert "Notify only" in formatted

    def test_format_long_deny_list(self):
        """Test formatting with long deny list (should truncate)."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        actions = [
            PolicyAction(
                type="attach_deny_policy",
                deny=["ec2:*", "s3:*", "rds:*", "lambda:*", "dynamodb:*"],
            )
        ]
        formatted = notifier._format_actions(actions)

        assert "+2 more" in formatted  # Should show first 3 + count


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_cost_management_console_url(self):
        """Test console URL generation."""
        url = get_cost_management_console_url("123456789012")
        assert "console.aws.amazon.com" in url
        assert "cost-management" in url

    def test_get_cost_management_console_url_with_region(self):
        """Test console URL generation with custom region."""
        url = get_cost_management_console_url("123456789012", region="eu-west-1")
        assert "region=eu-west-1" in url

    def test_generate_approval_url(self):
        """Test approval URL generation."""
        url = generate_approval_url(
            "https://api.example.com", "exec-123", "approve", signature="abc123"
        )

        assert "https://api.example.com/approve" in url
        assert "id=exec-123" in url
        assert "sig=abc123" in url

    def test_generate_approval_url_without_signature(self):
        """Test approval URL generation without signature."""
        url = generate_approval_url("https://api.example.com", "exec-123", "reject")

        assert "https://api.example.com/reject" in url
        assert "id=exec-123" in url
        assert "sig=" not in url


class TestSlackNotifierIntegration:
    """Integration tests for SlackNotifier."""

    def test_complete_dry_run_workflow(self):
        """Test complete dry-run workflow."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id=f"evt-{uuid4()}",
            source="budgets",
            account_id="123456789012",
            amount=300.0,
            time_window="2024-01-01 to 2024-01-31",
            details={"budget_name": "monthly-budget"},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="dry-run-policy",
            mode="dry_run",
            actions=[PolicyAction(type="notify_only")],
            ttl_minutes=0,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            console_url = get_cost_management_console_url(event.account_id)
            result = notifier.send_dry_run_alert(event, plan, console_url)

            assert result is True

    def test_complete_approval_workflow(self):
        """Test complete approval workflow."""
        notifier = SlackNotifier("https://hooks.slack.com/services/xxx")

        event = CostEvent(
            event_id=f"evt-{uuid4()}",
            source="anomaly",
            account_id="123456789012",
            amount=800.0,
            time_window="2024-01-15 00:00 to 2024-01-15 23:59",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="manual-approval-policy",
            mode="manual",
            actions=[
                PolicyAction(
                    type="attach_deny_policy",
                    deny=["ec2:RunInstances", "ec2:CreateNatGateway"],
                )
            ],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/ci-deployer"],
        )

        execution_id = f"exec-{uuid4()}"

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            approve_url = generate_approval_url(
                "https://api.example.com", execution_id, "approve"
            )
            reject_url = generate_approval_url(
                "https://api.example.com", execution_id, "reject"
            )

            result = notifier.send_approval_request(
                event, plan, execution_id, approve_url, reject_url
            )

            assert result is True
