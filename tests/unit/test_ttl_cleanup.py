"""
Tests for TTL Cleanup Handler

Tests the TTL cleanup flow including:
- Querying expired executions
- Rolling back IAM policies
- Updating audit trail
- Error handling and idempotency
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.guardrails.handlers.ttl_cleanup import TTLCleanupHandler, lambda_handler
from src.guardrails.models import ActionExecution


class TestTTLCleanupHandlerInit:
    """Test TTLCleanupHandler initialization."""

    @patch("src.guardrails.handlers.ttl_cleanup.SlackNotifier")
    @patch("src.guardrails.handlers.ttl_cleanup.IAMExecutor")
    @patch("src.guardrails.handlers.ttl_cleanup.AuditStore")
    def test_init_with_defaults(self, mock_audit, mock_executor, mock_notifier):
        """Test initialization with default dependencies."""
        handler = TTLCleanupHandler()

        assert handler.audit_store is not None
        assert handler.executor is not None
        assert handler.notifier is not None
        assert handler.batch_size == 100

    def test_init_with_custom_values(self):
        """Test initialization with custom dependencies."""
        mock_audit = MagicMock()
        mock_executor = MagicMock()
        mock_notifier = MagicMock()

        handler = TTLCleanupHandler(
            audit_store=mock_audit,
            executor=mock_executor,
            notifier=mock_notifier,
            batch_size=50,
        )

        assert handler.audit_store is mock_audit
        assert handler.executor is mock_executor
        assert handler.notifier is mock_notifier
        assert handler.batch_size == 50


class TestCleanupExpiredExecutions:
    """Test cleanup_expired_executions method."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        mock_audit = MagicMock()
        mock_executor = MagicMock()
        mock_notifier = MagicMock()

        handler = TTLCleanupHandler(
            audit_store=mock_audit,
            executor=mock_executor,
            notifier=mock_notifier,
        )

        return handler, mock_audit, mock_executor, mock_notifier

    def test_cleanup_no_expired_executions(self, mock_dependencies):
        """Test cleanup when no executions are expired."""
        handler, mock_audit, _, _ = mock_dependencies

        # No expired executions
        mock_audit.query_expired_executions.return_value = []

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 0
        assert result["rolled_back"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == []

    def test_cleanup_single_execution_success(self, mock_dependencies):
        """Test successful cleanup of a single execution."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        # Create expired execution
        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=4),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={"before": [], "after": ["arn:aws:iam::123456789012:policy/test"]},
            ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
        )

        mock_audit.query_expired_executions.return_value = [execution]
        mock_executor.rollback_execution.return_value = True

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 1
        assert result["rolled_back"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 0

        # Verify rollback was called
        mock_executor.rollback_execution.assert_called_once_with(execution)

        # Verify status was updated
        mock_audit.update_execution.assert_called_once()
        updated_execution = mock_audit.update_execution.call_args[0][0]
        assert updated_execution.status == "rolled_back"
        assert updated_execution.rolled_back_at is not None

        # Verify notification was sent
        mock_notifier.send_rollback_confirmation.assert_called_once()

    def test_cleanup_multiple_executions_success(self, mock_dependencies):
        """Test successful cleanup of multiple executions."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        # Create 3 expired executions
        executions = [
            ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target=f"arn:aws:iam::123456789012:role/test-{i}",
                diff={"before": [], "after": [f"arn:aws:iam::123456789012:policy/test-{i}"]},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            for i in range(3)
        ]

        mock_audit.query_expired_executions.return_value = executions
        mock_executor.rollback_execution.return_value = True

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 3
        assert result["rolled_back"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0

        # Verify all were rolled back
        assert mock_executor.rollback_execution.call_count == 3
        assert mock_audit.update_execution.call_count == 3

    def test_cleanup_skips_non_executed_status(self, mock_dependencies):
        """Test that cleanup skips executions not in 'executed' status."""
        handler, mock_audit, mock_executor, _ = mock_dependencies

        # Create executions with various statuses
        executions = [
            ActionExecution(
                execution_id="exec-1",
                policy_id="test-policy",
                event_id="evt-1",
                status="executed",  # Should be rolled back
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test-1",
                diff={},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            ),
            ActionExecution(
                execution_id="exec-2",
                policy_id="test-policy",
                event_id="evt-2",
                status="rolled_back",  # Should be skipped
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test-2",
                diff={},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            ),
            ActionExecution(
                execution_id="exec-3",
                policy_id="test-policy",
                event_id="evt-3",
                status="failed",  # Should be skipped
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target="arn:aws:iam::123456789012:role/test-3",
                diff={},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            ),
        ]

        mock_audit.query_expired_executions.return_value = executions
        mock_executor.rollback_execution.return_value = True

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 3
        assert result["rolled_back"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 2

        # Only exec-1 should be rolled back
        mock_executor.rollback_execution.assert_called_once()

    def test_cleanup_handles_partial_failures(self, mock_dependencies):
        """Test that cleanup continues even if some rollbacks fail."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        # Create 3 expired executions
        executions = [
            ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target=f"arn:aws:iam::123456789012:role/test-{i}",
                diff={},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            for i in range(3)
        ]

        mock_audit.query_expired_executions.return_value = executions

        # Second rollback fails
        mock_executor.rollback_execution.side_effect = [
            True,  # First succeeds
            Exception("IAM error"),  # Second fails
            True,  # Third succeeds
        ]

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 3
        assert result["rolled_back"] == 2
        assert result["failed"] == 1
        assert result["skipped"] == 0
        assert len(result["errors"]) == 0  # Errors captured but not in errors list for exceptions

        # Verify all three were attempted
        assert mock_executor.rollback_execution.call_count == 3

        # Verify failure alert was sent
        mock_notifier.send_error_alert.assert_called_once()

    def test_cleanup_rollback_returns_false(self, mock_dependencies):
        """Test handling when rollback returns False."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=4),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
            ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
        )

        mock_audit.query_expired_executions.return_value = [execution]
        mock_executor.rollback_execution.return_value = False  # Rollback returns False

        result = handler.cleanup_expired_executions()

        assert result["total_found"] == 1
        assert result["rolled_back"] == 0
        assert result["failed"] == 1

        # Verify status was updated to failed
        mock_audit.update_execution.assert_called_once()
        updated_execution = mock_audit.update_execution.call_args[0][0]
        assert updated_execution.status == "failed"

    def test_cleanup_respects_batch_size(self, mock_dependencies):
        """Test that cleanup respects batch_size limit."""
        handler, mock_audit, mock_executor, _ = mock_dependencies
        handler.batch_size = 2  # Set small batch size

        # Create 5 expired executions
        executions = [
            ActionExecution(
                execution_id=f"exec-{i}",
                policy_id="test-policy",
                event_id=f"evt-{i}",
                status="executed",
                executed_at=datetime.utcnow() - timedelta(hours=4),
                executed_by="system:auto",
                action="attach_deny_policy",
                target=f"arn:aws:iam::123456789012:role/test-{i}",
                diff={},
                ttl_expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            for i in range(5)
        ]

        mock_audit.query_expired_executions.return_value = executions
        mock_executor.rollback_execution.return_value = True

        result = handler.cleanup_expired_executions()

        # Should only process first 2 (batch_size)
        assert result["total_found"] == 2
        assert result["rolled_back"] == 2
        assert mock_executor.rollback_execution.call_count == 2


class TestRollbackExecution:
    """Test _rollback_execution method."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies."""
        mock_audit = MagicMock()
        mock_executor = MagicMock()
        mock_notifier = MagicMock()

        handler = TTLCleanupHandler(
            audit_store=mock_audit,
            executor=mock_executor,
            notifier=mock_notifier,
        )

        return handler, mock_audit, mock_executor, mock_notifier

    def test_rollback_execution_success(self, mock_dependencies):
        """Test successful rollback."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=4),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        mock_executor.rollback_execution.return_value = True

        result = handler._rollback_execution(execution)

        assert result == "rolled_back"
        assert execution.status == "rolled_back"
        assert execution.rolled_back_at is not None
        mock_audit.update_execution.assert_called_once()
        mock_notifier.send_rollback_confirmation.assert_called_once()

    def test_rollback_execution_skips_wrong_status(self, mock_dependencies):
        """Test that rollback skips executions with wrong status."""
        handler, mock_audit, mock_executor, _ = mock_dependencies

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="planned",  # Not 'executed'
            executed_at=None,
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        result = handler._rollback_execution(execution)

        assert result == "skipped"
        mock_executor.rollback_execution.assert_not_called()
        mock_audit.update_execution.assert_not_called()

    def test_rollback_execution_failure_updates_status(self, mock_dependencies):
        """Test that rollback failure updates execution status."""
        handler, mock_audit, mock_executor, _ = mock_dependencies

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=4),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        mock_executor.rollback_execution.side_effect = Exception("IAM error")

        result = handler._rollback_execution(execution)

        assert result == "failed"
        assert execution.status == "failed"
        assert "rollback_error" in execution.diff
        mock_audit.update_execution.assert_called_once()

    def test_rollback_notification_failure_non_fatal(self, mock_dependencies):
        """Test that notification failure doesn't fail the rollback."""
        handler, mock_audit, mock_executor, mock_notifier = mock_dependencies

        execution = ActionExecution(
            execution_id="exec-123",
            policy_id="test-policy",
            event_id="evt-456",
            status="executed",
            executed_at=datetime.utcnow() - timedelta(hours=4),
            executed_by="system:auto",
            action="attach_deny_policy",
            target="arn:aws:iam::123456789012:role/test",
            diff={},
        )

        mock_executor.rollback_execution.return_value = True
        mock_notifier.send_rollback_confirmation.side_effect = Exception("Slack error")

        result = handler._rollback_execution(execution)

        # Should still succeed even though notification failed
        assert result == "rolled_back"
        assert execution.status == "rolled_back"
        mock_audit.update_execution.assert_called_once()


class TestLambdaHandler:
    """Test Lambda handler function."""

    @patch("src.guardrails.handlers.ttl_cleanup.TTLCleanupHandler")
    def test_lambda_handler_success(self, mock_handler_class):
        """Test successful Lambda handler execution."""
        # Mock handler instance
        mock_handler = MagicMock()
        mock_handler.cleanup_expired_executions.return_value = {
            "total_found": 3,
            "rolled_back": 3,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        mock_handler_class.return_value = mock_handler

        event = {"source": "aws.events", "detail-type": "Scheduled Event"}
        context = MagicMock()

        response = lambda_handler(event, context)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "success"
        assert body["result"]["total_found"] == 3
        assert body["result"]["rolled_back"] == 3

    @patch("src.guardrails.handlers.ttl_cleanup.TTLCleanupHandler")
    def test_lambda_handler_no_expired(self, mock_handler_class):
        """Test Lambda handler with no expired executions."""
        # Mock handler instance
        mock_handler = MagicMock()
        mock_handler.cleanup_expired_executions.return_value = {
            "total_found": 0,
            "rolled_back": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        mock_handler_class.return_value = mock_handler

        event = {"source": "aws.events"}
        context = MagicMock()

        response = lambda_handler(event, context)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["result"]["total_found"] == 0

    @patch("src.guardrails.handlers.ttl_cleanup.TTLCleanupHandler")
    def test_lambda_handler_exception(self, mock_handler_class):
        """Test Lambda handler with unhandled exception."""
        # Mock handler raising exception
        mock_handler = MagicMock()
        mock_handler.cleanup_expired_executions.side_effect = Exception("Unexpected error")
        mock_handler_class.return_value = mock_handler

        event = {"source": "aws.events"}
        context = MagicMock()

        response = lambda_handler(event, context)

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["status"] == "error"
        assert "Unexpected error" in body["error"]
