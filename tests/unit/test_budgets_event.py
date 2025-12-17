"""Tests for AWS Budgets Event Handler."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.guardrails.handlers.budgets_event import (
    execute_action_plan,
    extract_account_id,
    lambda_handler,
    parse_budgets_eventbridge,
    parse_budgets_notification,
    parse_event,
)
from src.guardrails.models import ActionPlan, CostEvent, PolicyAction


class TestParseBudgetsNotification:
    """Test parsing AWS Budgets notifications (SNS format)."""

    def test_valid_notification(self):
        """Test parsing valid budget notification."""
        notification = {
            "budgetName": "monthly-budget",
            "notificationType": "ACTUAL",
            "thresholdType": "PERCENTAGE",
            "comparisonOperator": "GREATER_THAN",
            "threshold": 80,
            "calculatedSpend": {"actualSpend": {"amount": 250.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/monthly-budget",
            "time": "2024-01-15T10:30:00Z",
        }

        event = parse_budgets_notification(notification)

        assert event.source == "budgets"
        assert event.account_id == "123456789012"
        assert event.amount == 250.0
        assert event.details["budget_name"] == "monthly-budget"
        assert event.details["threshold"] == 80

    def test_notification_without_budget_name(self):
        """Test parsing notification without budget name."""
        notification = {"calculatedSpend": {"actualSpend": {"amount": 100.0, "unit": "USD"}}}

        with pytest.raises(ValueError, match="Missing budgetName"):
            parse_budgets_notification(notification)

    def test_notification_with_zero_amount(self):
        """Test parsing notification with zero amount."""
        notification = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        with pytest.raises(ValueError, match="Invalid amount"):
            parse_budgets_notification(notification)

    def test_notification_with_different_currency(self):
        """Test parsing notification with non-USD currency."""
        notification = {
            "budgetName": "eur-budget",
            "calculatedSpend": {"actualSpend": {"amount": 500.0, "unit": "EUR"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/eur-budget",
        }

        event = parse_budgets_notification(notification)

        assert event.amount == 500.0
        assert event.details["currency"] == "EUR"


class TestParseBudgetsEventBridge:
    """Test parsing AWS Budgets EventBridge events."""

    def test_valid_eventbridge_event(self):
        """Test parsing valid EventBridge event."""
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
                "notificationType": "FORECASTED",
                "thresholdType": "ABSOLUTE_VALUE",
                "threshold": 1000,
                "calculatedSpend": {"actualSpend": {"amount": 800.0, "unit": "USD"}},
            },
        }

        cost_event = parse_budgets_eventbridge(event)

        assert cost_event.source == "budgets"
        assert cost_event.account_id == "123456789012"
        assert cost_event.amount == 800.0
        assert cost_event.event_id == "evt-12345"
        assert cost_event.details["budget_name"] == "monthly-budget"
        assert cost_event.details["region"] == "us-east-1"

    def test_eventbridge_without_budget_name(self):
        """Test EventBridge event without budget name."""
        event = {
            "account": "123456789012",
            "detail": {"calculatedSpend": {"actualSpend": {"amount": 100.0}}},
        }

        with pytest.raises(ValueError, match="Missing budgetName"):
            parse_budgets_eventbridge(event)

    def test_eventbridge_with_invalid_account(self):
        """Test EventBridge event with invalid account ID."""
        event = {
            "account": "invalid",
            "detail": {
                "budgetName": "test",
                "calculatedSpend": {"actualSpend": {"amount": 100.0}},
            },
        }

        with pytest.raises(ValueError, match="Invalid account ID"):
            parse_budgets_eventbridge(event)


class TestExtractAccountId:
    """Test account ID extraction."""

    def test_extract_from_notification_arn(self):
        """Test extraction from notificationArn."""
        notification = {"notificationArn": "arn:aws:budgets::987654321098:budget/test-budget"}

        account_id = extract_account_id(notification)
        assert account_id == "987654321098"

    def test_extract_from_account_id_field(self):
        """Test extraction from accountId field."""
        notification = {"accountId": "111111111111"}

        account_id = extract_account_id(notification)
        assert account_id == "111111111111"

    def test_extract_from_environment(self):
        """Test extraction from environment variable."""
        notification = {}

        with patch.dict(os.environ, {"AWS_ACCOUNT_ID": "222222222222"}):
            account_id = extract_account_id(notification)
            assert account_id == "222222222222"

    def test_extract_fails_without_account(self):
        """Test extraction fails when no account ID available."""
        notification = {}

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Could not extract account ID"):
                extract_account_id(notification)


class TestParseEvent:
    """Test event parsing dispatcher."""

    def test_parse_sns_event(self):
        """Test parsing SNS-wrapped event."""
        sns_event = {
            "Records": [
                {
                    "EventSource": "aws:sns",
                    "Sns": {
                        "Message": json.dumps(
                            {
                                "budgetName": "test-budget",
                                "calculatedSpend": {
                                    "actualSpend": {"amount": 300.0, "unit": "USD"}
                                },
                                "notificationArn": "arn:aws:budgets::123456789012:budget/test",
                            }
                        )
                    },
                }
            ]
        }

        cost_event = parse_event(sns_event)

        assert cost_event.source == "budgets"
        assert cost_event.account_id == "123456789012"
        assert cost_event.amount == 300.0

    def test_parse_eventbridge_event(self):
        """Test parsing EventBridge event."""
        eb_event = {
            "detail-type": "AWS Budget Notification",
            "account": "123456789012",
            "detail": {
                "budgetName": "test",
                "calculatedSpend": {"actualSpend": {"amount": 400.0}},
            },
        }

        cost_event = parse_event(eb_event)

        assert cost_event.source == "budgets"
        assert cost_event.amount == 400.0

    def test_parse_direct_notification(self):
        """Test parsing direct notification (for testing)."""
        direct_event = {
            "budgetName": "direct-test",
            "calculatedSpend": {"actualSpend": {"amount": 500.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/direct-test",
        }

        cost_event = parse_event(direct_event)

        assert cost_event.source == "budgets"
        assert cost_event.amount == 500.0

    def test_parse_unsupported_event(self):
        """Test parsing unsupported event format."""
        unsupported_event = {"unknown": "format"}

        with pytest.raises(ValueError, match="Unsupported event format"):
            parse_event(unsupported_event)


class TestExecuteActionPlan:
    """Test action plan execution."""

    def test_execute_dry_run(self):
        """Test executing dry-run mode."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-15",
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

        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx"}):
            with patch(
                "src.guardrails.handlers.budgets_event.SlackNotifier"
            ) as mock_notifier_class:
                mock_notifier = MagicMock()
                mock_notifier.send_dry_run_alert.return_value = True
                mock_notifier_class.return_value = mock_notifier

                result = execute_action_plan(event, plan)

                assert result["notification_sent"] is True
                assert result["action"] == "none"
                mock_notifier.send_dry_run_alert.assert_called_once()

    def test_execute_manual_mode(self):
        """Test executing manual approval mode."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=500.0,
            time_window="2024-01-15",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="manual-policy",
            mode="manual",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:*"])],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx"}):
            with patch(
                "src.guardrails.handlers.budgets_event.SlackNotifier"
            ) as mock_notifier_class:
                mock_notifier = MagicMock()
                mock_notifier.send_approval_request.return_value = True
                mock_notifier_class.return_value = mock_notifier

                result = execute_action_plan(event, plan)

                assert result["notification_sent"] is True
                assert result["action"] == "approval_requested"
                assert "execution_id" in result
                mock_notifier.send_approval_request.assert_called_once()

    def test_execute_auto_mode_executes_immediately(self):
        """Test executing auto mode (implemented in Phase 3)."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=1000.0,
            time_window="2024-01-15",
            details={},
        )

        plan = ActionPlan(
            matched=True,
            matched_policy_id="auto-policy",
            mode="auto",
            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:*"])],
            ttl_minutes=180,
            target_principals=["arn:aws:iam::123456789012:role/test"],
        )

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx",
                "DYNAMODB_TABLE_NAME": "autoguardrails-audit",
            },
        ):
            with patch(
                "src.guardrails.handlers.budgets_event.SlackNotifier"
            ) as mock_notifier_class:
                with patch("src.guardrails.executor_iam.IAMExecutor") as mock_executor_class:
                    with patch("src.guardrails.audit_store.AuditStore") as mock_audit_class:
                        # Setup mocks
                        mock_notifier = MagicMock()
                        mock_notifier.send_execution_confirmation.return_value = True
                        mock_notifier_class.return_value = mock_notifier

                        mock_execution = MagicMock()
                        mock_execution.execution_id = "exec-123"
                        mock_execution.ttl_expires_at = None

                        mock_executor = MagicMock()
                        mock_executor.execute_action_plan.return_value = [mock_execution]
                        mock_executor_class.return_value = mock_executor

                        mock_audit = MagicMock()
                        mock_audit_class.return_value = mock_audit

                        result = execute_action_plan(event, plan)

                        # Should execute immediately
                        assert result["action"] == "executed"
                        assert result["execution_id"] == "exec-123"
                        assert "executions_created" in result
                        mock_executor.execute_action_plan.assert_called_once()
                        mock_audit.save_execution.assert_called_once()

    def test_execute_without_slack_webhook(self):
        """Test execution fails without SLACK_WEBHOOK_URL."""
        event = CostEvent(
            event_id="evt-123",
            source="budgets",
            account_id="123456789012",
            amount=250.0,
            time_window="2024-01-15",
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

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="SLACK_WEBHOOK_URL"):
                execute_action_plan(event, plan)


class TestLambdaHandler:
    """Test Lambda handler integration."""

    def test_handler_success_dry_run(self):
        """Test handler with successful dry-run execution."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 300.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx",
                "POLICIES_PATH": "policies",
            },
        ):
            with patch(
                "src.guardrails.handlers.budgets_event.load_policies_from_directory"
            ) as mock_load:
                with patch(
                    "src.guardrails.handlers.budgets_event.PolicyEngine"
                ) as mock_engine_class:
                    with patch(
                        "src.guardrails.handlers.budgets_event.execute_action_plan"
                    ) as mock_execute:
                        # Mock policy loading
                        mock_load.return_value = [MagicMock()]

                        # Mock policy engine
                        mock_engine = MagicMock()
                        mock_plan = ActionPlan(
                            matched=True,
                            matched_policy_id="test-policy",
                            mode="dry_run",
                            actions=[PolicyAction(type="notify_only")],
                            ttl_minutes=0,
                            target_principals=[],
                        )
                        mock_engine.evaluate.return_value = mock_plan
                        mock_engine_class.return_value = mock_engine

                        # Mock execution result
                        mock_execute.return_value = {"notification_sent": True, "action": "none"}

                        response = lambda_handler(event, context)

                        assert response["statusCode"] == 200
                        body = json.loads(response["body"])
                        assert body["status"] == "success"
                        assert body["mode"] == "dry_run"

    def test_handler_no_policies(self):
        """Test handler with no policies loaded."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 300.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(os.environ, {"POLICIES_PATH": "policies"}):
            with patch(
                "src.guardrails.handlers.budgets_event.load_policies_from_directory"
            ) as mock_load:
                mock_load.return_value = []

                response = lambda_handler(event, context)

                assert response["statusCode"] == 200
                assert response["body"] == "no_policies"

    def test_handler_no_match(self):
        """Test handler with no policy match."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 50.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(os.environ, {"POLICIES_PATH": "policies"}):
            with patch(
                "src.guardrails.handlers.budgets_event.load_policies_from_directory"
            ) as mock_load:
                with patch(
                    "src.guardrails.handlers.budgets_event.PolicyEngine"
                ) as mock_engine_class:
                    mock_load.return_value = [MagicMock()]

                    mock_engine = MagicMock()
                    mock_plan = ActionPlan(
                        matched=False,
                        matched_policy_id=None,
                        mode=None,
                        actions=[],
                        ttl_minutes=None,
                        target_principals=[],
                    )
                    mock_engine.evaluate.return_value = mock_plan
                    mock_engine_class.return_value = mock_engine

                    response = lambda_handler(event, context)

                    assert response["statusCode"] == 200
                    assert response["body"] == "no_match"

    def test_handler_global_dry_run_override(self):
        """Test handler with global DRY_RUN override."""
        event = {
            "budgetName": "test-budget",
            "calculatedSpend": {"actualSpend": {"amount": 1000.0, "unit": "USD"}},
            "notificationArn": "arn:aws:budgets::123456789012:budget/test",
        }

        context = MagicMock()

        with patch.dict(
            os.environ,
            {
                "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx",
                "POLICIES_PATH": "policies",
                "DRY_RUN": "true",
            },
        ):
            with patch(
                "src.guardrails.handlers.budgets_event.load_policies_from_directory"
            ) as mock_load:
                with patch(
                    "src.guardrails.handlers.budgets_event.PolicyEngine"
                ) as mock_engine_class:
                    with patch(
                        "src.guardrails.handlers.budgets_event.execute_action_plan"
                    ) as mock_execute:
                        mock_load.return_value = [MagicMock()]

                        mock_engine = MagicMock()
                        mock_plan = ActionPlan(
                            matched=True,
                            matched_policy_id="auto-policy",
                            mode="auto",  # Should be overridden
                            actions=[PolicyAction(type="attach_deny_policy", deny=["ec2:*"])],
                            ttl_minutes=180,
                            target_principals=["arn:aws:iam::123456789012:role/test"],
                        )
                        mock_engine.evaluate.return_value = mock_plan
                        mock_engine_class.return_value = mock_engine

                        # Mock execution result
                        mock_execute.return_value = {"notification_sent": True, "action": "none"}

                        response = lambda_handler(event, context)

                        assert response["statusCode"] == 200
                        body = json.loads(response["body"])
                        # Mode should be overridden to dry_run
                        assert body["mode"] == "dry_run"

    def test_handler_error(self):
        """Test handler with error."""
        event = {"invalid": "event"}

        context = MagicMock()

        with patch.dict(os.environ, {"POLICIES_PATH": "policies"}):
            response = lambda_handler(event, context)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert body["status"] == "error"
