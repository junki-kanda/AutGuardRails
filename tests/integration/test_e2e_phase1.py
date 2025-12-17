"""End-to-End Integration Tests for Phase 1.

These tests verify the complete flow from AWS Budget event to Slack notification:
1. AWS Budget event arrives (SNS or EventBridge)
2. Event is parsed into CostEvent
3. Policies are loaded from YAML files
4. Policy engine evaluates the event
5. Slack notification is sent

This validates that all Phase 1 components work together correctly.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.guardrails.handlers.budgets_event import lambda_handler


@pytest.fixture
def temp_policies_dir():
    """Create temporary directory with test policies."""
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        policies_path = Path(tmpdir)

        # Create dry-run policy
        dry_run_policy = {
            "policy_id": "test-dry-run-policy",
            "enabled": True,
            "mode": "dry_run",
            "ttl_minutes": 0,
            "match": {
                "source": ["budgets"],
                "account_ids": ["123456789012"],
                "min_amount_usd": 100.0,
            },
            "scope": {
                "principals": [
                    {
                        "type": "iam_role",
                        "arn": "arn:aws:iam::123456789012:role/test-role",
                    }
                ]
            },
            "actions": [{"type": "notify_only"}],
            "notify": {"slack_webhook_ssm_param": "/test/webhook"},
        }

        (policies_path / "2-dry-run.yaml").write_text(yaml.dump(dry_run_policy))

        # Create manual approval policy (higher threshold)
        # Named 1-manual.yaml so it loads first (alphabetically before 2-dry-run.yaml)
        manual_policy = {
            "policy_id": "test-manual-policy",
            "enabled": True,
            "mode": "manual",
            "ttl_minutes": 180,
            "match": {
                "source": ["budgets"],
                "account_ids": ["123456789012"],
                "min_amount_usd": 500.0,
            },
            "scope": {
                "principals": [
                    {
                        "type": "iam_role",
                        "arn": "arn:aws:iam::123456789012:role/ci-deployer",
                    }
                ]
            },
            "actions": [
                {
                    "type": "attach_deny_policy",
                    "deny": ["ec2:RunInstances", "ec2:CreateNatGateway"],
                }
            ],
            "notify": {"slack_webhook_ssm_param": "/test/webhook"},
        }

        (policies_path / "1-manual.yaml").write_text(yaml.dump(manual_policy))

        yield str(policies_path)


class TestE2EDryRunFlow:
    """Test complete dry-run flow."""

    def test_sns_event_triggers_dry_run_notification(self, temp_policies_dir):
        """Test that SNS Budget event triggers dry-run notification."""
        # SNS-wrapped Budget notification
        event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps({
                            "budgetName": "monthly-budget",
                            "notificationType": "ACTUAL",
                            "thresholdType": "PERCENTAGE",
                            "threshold": 80,
                            "calculatedSpend": {
                                "actualSpend": {"amount": 250.0, "unit": "USD"}
                            },
                            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly-budget",
                            "time": "2024-01-15T10:30:00Z",
                        })
                    },
                }
            ]
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                # Verify Lambda response
                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert body["status"] == "success"
                assert body["mode"] == "dry_run"
                assert body["policy_id"] == "test-dry-run-policy"

                # Verify Slack notification was sent
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                assert call_args[0][0] == "https://hooks.slack.com/services/test"

                # Verify Slack payload structure
                slack_payload = call_args[1]["json"]
                assert "blocks" in slack_payload
                assert len(slack_payload["blocks"]) > 0

                # Verify header is dry-run alert
                header = slack_payload["blocks"][0]
                assert header["type"] == "header"
                assert "Dry-Run" in header["text"]["text"]

    def test_eventbridge_event_triggers_dry_run_notification(self, temp_policies_dir):
        """Test that EventBridge Budget event triggers dry-run notification."""
        event = {
            "version": "0",
            "id": "evt-12345",
            "detail-type": "AWS Budget Notification",
            "source": "aws.budgets",
            "account": "123456789012",
            "time": "2024-01-15T10:30:00Z",
            "region": "us-east-1",
            "detail": {
                "budgetName": "monthly-budget",
                "notificationType": "ACTUAL",
                "thresholdType": "PERCENTAGE",
                "threshold": 90,
                "calculatedSpend": {"actualSpend": {"amount": 300.0, "unit": "USD"}},
            },
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert body["status"] == "success"
                assert body["mode"] == "dry_run"

                # Verify Slack notification
                mock_post.assert_called_once()


class TestE2EManualApprovalFlow:
    """Test complete manual approval flow."""

    def test_high_cost_event_triggers_manual_approval(self, temp_policies_dir):
        """Test that high-cost event triggers manual approval notification."""
        event = {
            "budgetName": "monthly-budget",
            "notificationType": "ACTUAL",
            "calculatedSpend": {"actualSpend": {"amount": 800.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly-budget",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                # Verify Lambda response
                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert body["status"] == "success"
                assert body["mode"] == "manual"
                assert body["policy_id"] == "test-manual-policy"

                # Verify Slack notification was sent
                mock_post.assert_called_once()

                # Verify Slack payload contains approval request
                slack_payload = mock_post.call_args[1]["json"]
                header = slack_payload["blocks"][0]
                assert "Approval Required" in header["text"]["text"]


class TestE2EPolicyPriority:
    """Test policy evaluation priority."""

    def test_first_matching_policy_wins(self, temp_policies_dir):
        """Test that first matching policy is applied (not highest threshold)."""
        # Amount matches both policies (250 > 100 and > 500 is false)
        # Should match dry-run policy only
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 250.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                body = json.loads(response["body"])
                # Should match dry-run policy (lower threshold)
                assert body["policy_id"] == "test-dry-run-policy"
                assert body["mode"] == "dry_run"


class TestE2ENoMatch:
    """Test cases where no policy matches."""

    def test_low_amount_no_match(self, temp_policies_dir):
        """Test that low-cost event doesn't match any policy."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 50.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            response = lambda_handler(event, context)

            assert response["statusCode"] == 200
            assert response["body"] == "no_match"

    def test_different_account_no_match(self, temp_policies_dir):
        """Test that event from different account doesn't match."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 500.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::999999999999:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            response = lambda_handler(event, context)

            assert response["statusCode"] == 200
            assert response["body"] == "no_match"


class TestE2EGlobalDryRun:
    """Test global DRY_RUN override."""

    def test_global_dry_run_overrides_manual_policy(self, temp_policies_dir):
        """Test that DRY_RUN=true forces dry-run mode even for manual policies."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 800.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
                "DRY_RUN": "true",  # Global override
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                body = json.loads(response["body"])
                # Policy matched is manual, but mode should be overridden to dry_run
                assert body["policy_id"] == "test-manual-policy"
                assert body["mode"] == "dry_run"  # Overridden!

                # Verify dry-run notification was sent (not approval)
                slack_payload = mock_post.call_args[1]["json"]
                header = slack_payload["blocks"][0]
                assert "Dry-Run" in header["text"]["text"]


class TestE2EErrorHandling:
    """Test error handling in integration."""

    def test_invalid_event_returns_error(self, temp_policies_dir):
        """Test that invalid event returns error response."""
        event = {"invalid": "format"}

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            response = lambda_handler(event, context)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert body["status"] == "error"

    def test_missing_slack_webhook_returns_error(self, temp_policies_dir):
        """Test that missing SLACK_WEBHOOK_URL returns error."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 250.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                # SLACK_WEBHOOK_URL not set
                "POLICIES_PATH": temp_policies_dir,
            },
            clear=True,
        ):
            response = lambda_handler(event, context)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert body["status"] == "error"
            assert "SLACK_WEBHOOK_URL" in body["message"]

    def test_slack_network_error_still_completes(self, temp_policies_dir):
        """Test that Slack network error doesn't crash handler."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 250.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                # Simulate network error
                import requests

                mock_post.side_effect = requests.exceptions.ConnectionError("Network error")

                response = lambda_handler(event, context)

                # Handler should still return success (notification failure is logged)
                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert body["status"] == "success"
                assert body["result"]["notification_sent"] is False


class TestE2EMultiplePolicies:
    """Test scenarios with multiple policies."""

    def test_disabled_policy_is_skipped(self, temp_policies_dir):
        """Test that disabled policies are skipped."""
        import yaml

        # Create disabled policy with lower threshold
        disabled_policy = {
            "policy_id": "disabled-policy",
            "enabled": False,  # Disabled
            "mode": "manual",
            "ttl_minutes": 60,
            "match": {
                "source": ["budgets"],
                "account_ids": ["123456789012"],
                "min_amount_usd": 50.0,  # Lower than dry-run policy
            },
            "scope": {
                "principals": [
                    {"type": "iam_role", "arn": "arn:aws:iam::123456789012:role/test"}
                ]
            },
            "actions": [{"type": "notify_only"}],
            "notify": {"slack_webhook_ssm_param": "/test/webhook"},
        }

        policies_path = Path(temp_policies_dir)
        (policies_path / "disabled.yaml").write_text(yaml.dump(disabled_policy))

        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 150.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test",
                "POLICIES_PATH": temp_policies_dir,
            },
        ):
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_post.return_value = mock_response

                response = lambda_handler(event, context)

                body = json.loads(response["body"])
                # Should match dry-run policy, not disabled policy
                assert body["policy_id"] == "test-dry-run-policy"
