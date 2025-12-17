"""
Tests for Approval Webhook Handler

Tests the approval webhook flow including signature verification,
expiration checking, execution orchestration, and Lambda handler.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.guardrails.handlers.approval_webhook import (
    ApprovalWebhookHandler,
    lambda_handler,
)
from src.guardrails.models import ActionExecution


class TestApprovalWebhookHandlerInit:
    """Test ApprovalWebhookHandler initialization."""

    @patch("src.guardrails.handlers.approval_webhook.SlackNotifier")
    @patch("src.guardrails.handlers.approval_webhook.IAMExecutor")
    @patch("src.guardrails.handlers.approval_webhook.AuditStore")
    def test_init_with_defaults(self, mock_audit, mock_executor, mock_notifier):
        """Test initialization with default dependencies."""
        handler = ApprovalWebhookHandler()

        assert handler.audit_store is not None
        assert handler.executor is not None
        assert handler.notifier is not None
        assert handler.approval_secret is not None
        assert handler.approval_timeout_hours == 1

    def test_init_with_custom_values(self):
        """Test initialization with custom dependencies."""
        mock_audit = MagicMock()
        mock_executor = MagicMock()
        mock_notifier = MagicMock()

        handler = ApprovalWebhookHandler(
            audit_store=mock_audit,
            executor=mock_executor,
            notifier=mock_notifier,
            approval_secret="test-secret",
            approval_timeout_hours=2,
        )

        assert handler.audit_store is mock_audit
        assert handler.executor is mock_executor
        assert handler.notifier is mock_notifier
        assert handler.approval_secret == "test-secret"
        assert handler.approval_timeout_hours == 2


class TestSignatureGeneration:
    """Test signature generation and verification."""

    @pytest.fixture
    def handler(self):
        """Create handler with mocked dependencies."""
        return ApprovalWebhookHandler(
            audit_store=MagicMock(),
            executor=MagicMock(),
            notifier=MagicMock(),
            approval_secret="test-secret",
        )

    def test_generate_signature_deterministic(self, handler):
        """Test that signature generation is deterministic."""

        execution_id = "exec-123"
        timestamp = "2024-01-01T00:00:00"

        sig1 = handler._generate_signature(execution_id, timestamp)
        sig2 = handler._generate_signature(execution_id, timestamp)

        assert sig1 == sig2
        assert len(sig1) == 64  # SHA256 hex digest

    def test_verify_signature_valid(self, handler):
        """Test signature verification with valid signature."""
        execution_id = "exec-123"
        timestamp = "2024-01-01T00:00:00"
        signature = handler._generate_signature(execution_id, timestamp)

        assert handler._verify_signature(execution_id, timestamp, signature) is True

    def test_verify_signature_invalid(self, handler):
        """Test signature verification with invalid signature."""
        execution_id = "exec-123"
        timestamp = "2024-01-01T00:00:00"
        signature = "invalid-signature"

        assert handler._verify_signature(execution_id, timestamp, signature) is False

    def test_verify_signature_wrong_secret(self):
        """Test signature verification with different secret."""
        handler1 = ApprovalWebhookHandler(
            audit_store=MagicMock(),
            executor=MagicMock(),
            notifier=MagicMock(),
            approval_secret="secret-1",
        )
        handler2 = ApprovalWebhookHandler(
            audit_store=MagicMock(),
            executor=MagicMock(),
            notifier=MagicMock(),
            approval_secret="secret-2",
        )

        execution_id = "exec-123"
        timestamp = "2024-01-01T00:00:00"
        signature = handler1._generate_signature(execution_id, timestamp)

        assert handler2._verify_signature(execution_id, timestamp, signature) is False


class TestTimestampExpiration:
    """Test timestamp expiration logic."""

    @pytest.fixture
    def handler(self):
        """Create handler with mocked dependencies."""
        return ApprovalWebhookHandler(
            audit_store=MagicMock(),
            executor=MagicMock(),
            notifier=MagicMock(),
            approval_timeout_hours=1,
        )

    def test_not_expired(self, handler):
        """Test timestamp within expiration window."""

        # 30 minutes ago
        timestamp = (datetime.utcnow() - timedelta(minutes=30)).isoformat()

        assert handler._is_expired(timestamp) is False

    def test_expired(self, handler):
        """Test timestamp beyond expiration window."""
        # 2 hours ago
        timestamp = (datetime.utcnow() - timedelta(hours=2)).isoformat()

        assert handler._is_expired(timestamp) is True

    def test_exactly_at_expiration(self, handler):
        """Test timestamp exactly at expiration boundary."""
        # Exactly 1 hour ago (should be expired due to > comparison)
        timestamp = (datetime.utcnow() - timedelta(hours=1, seconds=1)).isoformat()

        assert handler._is_expired(timestamp) is True

    def test_invalid_timestamp_format(self, handler):
        """Test invalid timestamp format (treated as expired)."""

        assert handler._is_expired("invalid-timestamp") is True
        assert handler._is_expired("") is True


class TestGenerateApprovalUrl:
    """Test approval URL generation."""

    def test_generate_approval_url(self):
        """Test approval URL generation with all components."""
        handler = ApprovalWebhookHandler(
            audit_store=MagicMock(),
            executor=MagicMock(),
            notifier=MagicMock(),
            approval_secret="test-secret",
        )

        result = handler.generate_approval_url(
            execution_id="exec-123", base_url="https://api.example.com"
        )

        assert "url" in result
        assert "signature" in result
        assert "timestamp" in result

        # URL should contain all required parameters
        url = result["url"]
        assert "https://api.example.com/approve" in url
        assert "id=exec-123" in url
        assert "sig=" in url
        assert "ts=" in url

        # Signature and timestamp should match
        assert result["signature"] in url
        assert result["timestamp"] in url


class TestHandleApproval:
    """Test approval handling flow."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        mock_audit = MagicMock()
        mock_executor = MagicMock()
        mock_notifier = MagicMock()

        handler = ApprovalWebhookHandler(
            audit_store=mock_audit,
            executor=mock_executor,
            notifier=mock_notifier,
            approval_secret="test-secret",
        )

        return handler, mock_audit, mock_executor, mock_notifier

    def test_handle_approval_invalid_signature(self, mock_dependencies):
        """Test handling approval with invalid signature."""
        handler, _, _, _ = mock_dependencies

        timestamp = datetime.utcnow().isoformat()
        response = handler.handle_approval(
            execution_id="exec-123",
            signature="invalid-sig",
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 403
        assert "Invalid signature" in response["body"]

    def test_handle_approval_expired_link(self, mock_dependencies):
        """Test handling approval with expired link."""
        handler, _, _, _ = mock_dependencies

        # 2 hours ago
        timestamp = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 410
        assert "expired" in response["body"].lower()

    def test_handle_approval_execution_not_found(self, mock_dependencies):
        """Test handling approval when execution doesn't exist."""
        handler, mock_audit, _, _ = mock_dependencies

        mock_audit.get_execution.return_value = None

        timestamp = datetime.utcnow().isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 404
        assert "not found" in response["body"].lower()

    def test_handle_approval_already_processed(self, mock_dependencies):
        """Test handling approval when already processed (idempotency)."""
        handler, mock_audit, _, _ = mock_dependencies

        # Mock execution with non-planned status
        mock_execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",  # Already executed
            executed_by="system",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )
        mock_audit.get_execution.return_value = mock_execution

        timestamp = datetime.utcnow().isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 409
        assert "already processed" in response["body"].lower()

    def test_handle_approval_success(self, mock_dependencies):
        """Test successful approval handling."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        # Mock planned execution
        mock_execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",
            executed_by="system",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"policy_document": {"Statement": [{"Action": ["ec2:RunInstances"]}]}},
        )
        mock_audit.get_execution.return_value = mock_execution

        # Mock executor returning successful execution
        executed_execution = ActionExecution(
            execution_id="exec-new",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_by="user:test-user",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/test"]},
        )
        mock_executor.execute_action_plan.return_value = [executed_execution]

        timestamp = datetime.utcnow().isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 200
        assert "successfully" in response["body"].lower()

        # Verify executor was called
        mock_executor.execute_action_plan.assert_called_once()

        # Verify audit store was updated
        mock_audit.update_execution.assert_called_once()

        # Verify notification was sent
        mock_notifier.send_execution_confirmation.assert_called_once()

    def test_handle_approval_execution_failure(self, mock_dependencies):
        """Test approval handling when execution fails."""
        handler, mock_audit, mock_executor, _ = mock_dependencies

        # Mock planned execution
        mock_execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",
            executed_by="system",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"policy_document": {"Statement": [{"Action": ["ec2:RunInstances"]}]}},
        )
        mock_audit.get_execution.return_value = mock_execution

        # Mock executor raising exception
        mock_executor.execute_action_plan.side_effect = Exception("IAM error")

        timestamp = datetime.utcnow().isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        assert response["statusCode"] == 500
        assert "failed" in response["body"].lower()

        # Verify execution status was updated to failed
        mock_audit.update_execution.assert_called_once()
        updated_execution = mock_audit.update_execution.call_args[0][0]
        assert updated_execution.status == "failed"

    def test_handle_approval_notification_failure_non_fatal(self, mock_dependencies):
        """Test that notification failure doesn't fail the approval."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        # Mock planned execution
        mock_execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",
            executed_by="system",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"policy_document": {"Statement": [{"Action": ["ec2:RunInstances"]}]}},
        )
        mock_audit.get_execution.return_value = mock_execution

        # Mock successful execution
        executed_execution = ActionExecution(
            execution_id="exec-new",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_by="user:test-user",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/test"]},
        )
        mock_executor.execute_action_plan.return_value = [executed_execution]

        # Mock notification failure
        mock_notifier.send_execution_confirmation.side_effect = Exception("Slack error")

        timestamp = datetime.utcnow().isoformat()
        signature = handler._generate_signature("exec-123", timestamp)

        response = handler.handle_approval(
            execution_id="exec-123",
            signature=signature,
            timestamp=timestamp,
            user="test-user",
        )

        # Should still succeed despite notification failure
        assert response["statusCode"] == 200


class TestLambdaHandler:
    """Test Lambda handler function."""

    def test_lambda_handler_missing_parameters(self):
        """Test Lambda handler with missing parameters."""
        event = {"queryStringParameters": {}}

        response = lambda_handler(event, None)

        assert response["statusCode"] == 400
        assert "missing" in response["body"].lower()

    @patch("src.guardrails.handlers.approval_webhook.ApprovalWebhookHandler")
    def test_lambda_handler_success(self, mock_handler_class):
        """Test successful Lambda handler execution."""
        # Mock handler instance
        mock_handler = MagicMock()
        mock_handler.handle_approval.return_value = {
            "statusCode": 200,
            "body": "Success",
        }
        mock_handler_class.return_value = mock_handler

        event = {
            "queryStringParameters": {
                "id": "exec-123",
                "sig": "test-sig",
                "ts": "2024-01-01T00:00:00",
            }
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert "Success" in response["body"]

        # Verify handler was called
        mock_handler.handle_approval.assert_called_once_with(
            "exec-123", "test-sig", "2024-01-01T00:00:00", "unknown"
        )

    @patch("src.guardrails.handlers.approval_webhook.ApprovalWebhookHandler")
    def test_lambda_handler_with_slack_user(self, mock_handler_class):
        """Test Lambda handler extracting user from Slack payload."""
        # Mock handler instance
        mock_handler = MagicMock()
        mock_handler.handle_approval.return_value = {
            "statusCode": 200,
            "body": "Success",
        }
        mock_handler_class.return_value = mock_handler

        event = {
            "queryStringParameters": {
                "id": "exec-123",
                "sig": "test-sig",
                "ts": "2024-01-01T00:00:00",
            },
            "body": json.dumps({"user": {"name": "alice"}}),
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200

        # Verify user was extracted
        mock_handler.handle_approval.assert_called_once_with(
            "exec-123", "test-sig", "2024-01-01T00:00:00", "alice"
        )

    @patch("src.guardrails.handlers.approval_webhook.ApprovalWebhookHandler")
    def test_lambda_handler_exception(self, mock_handler_class):
        """Test Lambda handler with unhandled exception."""
        # Mock handler raising exception
        mock_handler = MagicMock()
        mock_handler.handle_approval.side_effect = Exception("Unexpected error")
        mock_handler_class.return_value = mock_handler

        event = {
            "queryStringParameters": {
                "id": "exec-123",
                "sig": "test-sig",
                "ts": "2024-01-01T00:00:00",
            }
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 500
        assert "error" in response["body"].lower()
