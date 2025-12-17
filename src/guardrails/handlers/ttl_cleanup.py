"""
TTL Cleanup Handler

Handles automatic rollback of guardrails after TTL (Time-To-Live) expiration.
This module is triggered by EventBridge on a schedule (every 5 minutes) to
query expired executions and roll them back.

Safety Features:
- Idempotent (safe to run multiple times)
- Individual failure isolation (one failure doesn't stop others)
- Comprehensive logging and error tracking
- Status validation (only rolls back 'executed' status)
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

from ..audit_store import AuditStore
from ..executor_iam import IAMExecutor
from ..models import ActionExecution
from ..notifier_slack import SlackNotifier


logger = logging.getLogger(__name__)


class TTLCleanupHandler:
    """Handle TTL-based automatic rollback of guardrail actions."""

    def __init__(
        self,
        audit_store: AuditStore | None = None,
        executor: IAMExecutor | None = None,
        notifier: SlackNotifier | None = None,
        batch_size: int = 100,
    ):
        """Initialize TTL cleanup handler.

        Args:
            audit_store: Audit store instance (creates new if None)
            executor: IAM executor instance (creates new if None)
            notifier: Slack notifier instance (creates new if None)
            batch_size: Maximum number of executions to process per run
        """
        self.audit_store = audit_store or AuditStore()
        self.executor = executor or IAMExecutor()
        self.notifier = notifier or SlackNotifier(webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""))
        self.batch_size = batch_size

    def cleanup_expired_executions(self) -> dict[str, Any]:
        """Query and rollback all expired executions.

        This method:
        1. Queries executions with expired TTL
        2. Filters to only 'executed' status (idempotency)
        3. Attempts rollback for each
        4. Updates audit trail
        5. Sends notifications

        Returns:
            Dict with:
                - total_found: Number of expired executions found
                - rolled_back: Number successfully rolled back
                - failed: Number that failed to rollback
                - skipped: Number skipped (wrong status)
                - errors: List of error details
        """
        logger.info("Starting TTL cleanup run")

        try:
            # Query expired executions
            now = datetime.utcnow()
            expired_executions = self.audit_store.query_expired_executions(now)

            logger.info(f"Found {len(expired_executions)} expired executions")

            if not expired_executions:
                return {
                    "total_found": 0,
                    "rolled_back": 0,
                    "failed": 0,
                    "skipped": 0,
                    "errors": [],
                }

            # Limit batch size
            if len(expired_executions) > self.batch_size:
                logger.warning(
                    f"Found {len(expired_executions)} executions, limiting to {self.batch_size}"
                )
                expired_executions = expired_executions[: self.batch_size]

            # Process each execution
            rolled_back = 0
            failed = 0
            skipped = 0
            errors = []

            for execution in expired_executions:
                try:
                    result = self._rollback_execution(execution)

                    if result == "rolled_back":
                        rolled_back += 1
                    elif result == "skipped":
                        skipped += 1
                    elif result == "failed":
                        failed += 1

                except Exception as e:
                    logger.exception(f"Unexpected error rolling back {execution.execution_id}: {e}")
                    failed += 1
                    errors.append(
                        {
                            "execution_id": execution.execution_id,
                            "error": str(e),
                            "type": "unexpected_error",
                        }
                    )

            # Log summary
            logger.info(
                f"TTL cleanup completed: {rolled_back} rolled back, "
                f"{failed} failed, {skipped} skipped"
            )

            # Send alert if failures
            if failed > 0:
                self._send_failure_alert(failed, errors)

            return {
                "total_found": len(expired_executions),
                "rolled_back": rolled_back,
                "failed": failed,
                "skipped": skipped,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"TTL cleanup run failed: {e}")
            return {
                "total_found": 0,
                "rolled_back": 0,
                "failed": 0,
                "skipped": 0,
                "errors": [{"error": str(e), "type": "cleanup_run_failure"}],
            }

    def _rollback_execution(self, execution: ActionExecution) -> str:
        """Rollback a single execution with error handling.

        Args:
            execution: ActionExecution to rollback

        Returns:
            Status string: "rolled_back", "skipped", or "failed"
        """
        execution_id = execution.execution_id

        # Idempotency: Only rollback if status is 'executed'
        if execution.status != "executed":
            logger.info(f"Skipping {execution_id}: status is '{execution.status}' (not 'executed')")
            return "skipped"

        # Rollback via executor
        try:
            logger.info(f"Rolling back execution {execution_id}")

            success = self.executor.rollback_execution(execution)

            if success:
                # Update execution status
                execution.status = "rolled_back"
                execution.rolled_back_at = datetime.utcnow()
                self.audit_store.update_execution(execution)

                logger.info(f"Successfully rolled back {execution_id}")

                # Send confirmation notification
                try:
                    self.notifier.send_rollback_confirmation(execution=execution)
                except Exception as notify_error:
                    logger.error(
                        f"Failed to send rollback notification for {execution_id}: {notify_error}"
                    )
                    # Don't fail the rollback if notification fails

                return "rolled_back"

            else:
                logger.error(f"Rollback returned False for {execution_id}")
                execution.status = "failed"
                execution.diff = execution.diff or {}
                execution.diff["rollback_error"] = "Rollback returned False"
                self.audit_store.update_execution(execution)
                return "failed"

        except Exception as e:
            logger.exception(f"Failed to rollback {execution_id}: {e}")

            # Update execution status to failed
            try:
                execution.status = "failed"
                execution.diff = execution.diff or {}
                execution.diff["rollback_error"] = str(e)
                self.audit_store.update_execution(execution)
            except Exception as update_error:
                logger.error(
                    f"Failed to update execution status after rollback error: {update_error}"
                )

            return "failed"

    def _send_failure_alert(self, failed_count: int, errors: list[dict]) -> None:
        """Send Slack alert for rollback failures.

        Args:
            failed_count: Number of failed rollbacks
            errors: List of error details
        """
        try:
            # Build error summary
            error_summary = "\n".join(
                [
                    f"• {err.get('execution_id', 'unknown')}: {err.get('error', 'unknown error')}"
                    for err in errors[:5]  # Limit to first 5
                ]
            )

            if len(errors) > 5:
                error_summary += f"\n... and {len(errors) - 5} more"

            message = (
                f"⚠️ TTL Cleanup Failures\n\n"
                f"Failed to rollback {failed_count} executions:\n\n"
                f"{error_summary}\n\n"
                f"Check CloudWatch logs for details."
            )

            # Create synthetic event for error notification
            # Use 'budgets' as source with marker in details
            from ..models import CostEvent

            synthetic_event = CostEvent(
                event_id=f"ttl-cleanup-{int(datetime.utcnow().timestamp())}",
                source="budgets",  # Use valid source
                account_id="000000000000",  # Dummy account ID
                amount=0.01,  # Minimum valid amount
                time_window=datetime.utcnow().isoformat(),
                details={
                    "type": "ttl_cleanup_error",
                    "failed_rollbacks": failed_count,
                },
            )
            self.notifier.send_error_alert(
                event=synthetic_event,
                error_message=message,
            )

        except Exception as e:
            logger.error(f"Failed to send failure alert: {e}")


# Lambda handler (EventBridge integration)
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda handler for TTL cleanup.

    Triggered by EventBridge schedule (rate: 5 minutes).

    Args:
        event: EventBridge scheduled event
        context: Lambda context

    Returns:
        Lambda response with cleanup results
    """
    logger.info(f"TTL cleanup triggered: {json.dumps(event)}")

    try:
        handler = TTLCleanupHandler()
        result = handler.cleanup_expired_executions()

        logger.info(f"TTL cleanup completed: {result}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "status": "success",
                    "result": result,
                }
            ),
        }

    except Exception as e:
        logger.exception(f"TTL cleanup handler failed: {e}")

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "status": "error",
                    "error": str(e),
                }
            ),
        }
