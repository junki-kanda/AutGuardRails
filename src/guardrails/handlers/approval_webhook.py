"""
Approval Webhook Handler

Handles Slack button interactions for manual approval of guardrail actions.
This module provides HTTP endpoint handling for API Gateway, signature verification,
and orchestration of the approval workflow.

Safety Features:
- Signature verification (HMAC-SHA256)
- Timestamp expiration (1 hour)
- Idempotency (status check before execution)
- Audit trail (executed_by tracking)
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from ..audit_store import AuditStore
from ..executor_iam import IAMExecutor
from ..notifier_slack import SlackNotifier


logger = logging.getLogger(__name__)


class ApprovalWebhookHandler:
    """Handle Slack approval webhook requests."""

    def __init__(
        self,
        audit_store: AuditStore | None = None,
        executor: IAMExecutor | None = None,
        notifier: SlackNotifier | None = None,
        approval_secret: str | None = None,
        approval_timeout_hours: int = 1,
    ):
        """Initialize approval webhook handler.

        Args:
            audit_store: Audit store instance (creates new if None)
            executor: IAM executor instance (creates new if None)
            notifier: Slack notifier instance (creates new if None)
            approval_secret: Secret for signature verification (reads from env if None)
            approval_timeout_hours: Hours until approval link expires (default 1)
        """
        self.audit_store = audit_store or AuditStore()
        self.executor = executor or IAMExecutor()
        self.notifier = notifier or SlackNotifier(webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""))
        self.approval_secret = approval_secret or os.getenv(
            "APPROVAL_SECRET", "default-secret-CHANGE-ME"
        )
        self.approval_timeout_hours = approval_timeout_hours

    def handle_approval(
        self, execution_id: str, signature: str, timestamp: str, user: str = "unknown"
    ) -> dict[str, Any]:
        """Handle approval request.

        Args:
            execution_id: Execution ID to approve
            signature: HMAC signature for verification
            timestamp: Request timestamp (ISO8601)
            user: User who approved (from Slack payload)

        Returns:
            Response dict with statusCode and body

        Raises:
            None (errors are returned as HTTP responses)
        """
        logger.info(f"Processing approval request for execution {execution_id}")

        # 1. Verify signature
        if not self._verify_signature(execution_id, timestamp, signature):
            logger.warning(f"Invalid signature for execution {execution_id}")
            return {"statusCode": 403, "body": "Invalid signature"}

        # 2. Check expiration
        if self._is_expired(timestamp):
            logger.warning(f"Expired approval link for execution {execution_id}")
            return {"statusCode": 410, "body": "Approval link expired"}

        # 3. Load execution
        execution = self.audit_store.get_execution(execution_id)
        if not execution:
            logger.error(f"Execution not found: {execution_id}")
            return {"statusCode": 404, "body": "Execution not found"}

        # 4. Check status (idempotency)
        if execution.status != "planned":
            logger.warning(
                f"Execution {execution_id} already processed (status: {execution.status})"
            )
            return {
                "statusCode": 409,
                "body": f"Already processed (status: {execution.status})",
            }

        # 5. Execute action
        try:
            logger.info(f"Executing approved action for {execution_id}")

            # Create action plan from execution
            from ..models import ActionPlan, PolicyAction

            # Extract deny actions from diff (stored during dry-run)
            deny_actions = execution.diff.get("would_deny")
            if not deny_actions:
                # Fallback: try to get from policy_document (if available)
                policy_doc = execution.diff.get("policy_document", {})
                statements = policy_doc.get("Statement", [])
                if statements:
                    deny_actions = statements[0].get("Action", [])

            if not deny_actions and execution.action == "attach_deny_policy":
                logger.error(f"No deny actions found in execution {execution_id} diff")
                return {"statusCode": 500, "body": "No deny actions found in execution record"}

            action_plan = ActionPlan(
                matched=True,
                matched_policy_id=execution.policy_id,
                mode="manual",
                actions=[
                    PolicyAction(
                        type=execution.action,
                        deny=deny_actions,
                    )
                ],
                ttl_minutes=0,
                target_principals=[execution.target],
            )

            # Execute via executor
            executions = self.executor.execute_action_plan(
                plan=action_plan,
                event_id=execution.event_id,
                executed_by=f"user:{user}",
            )

            if not executions:
                logger.error(f"No executions returned for {execution_id}")
                return {"statusCode": 500, "body": "Execution failed"}

            # Update execution status
            new_execution = executions[0]
            new_execution.execution_id = execution_id  # Keep original ID
            self.audit_store.update_execution(new_execution)

            logger.info(f"Successfully executed and updated execution {execution_id}")

            # 6. Notify Slack
            try:
                self.notifier.send_execution_confirmation(
                    execution=new_execution,
                    rollback_url=None,
                )
            except Exception as e:
                logger.error(f"Failed to send Slack confirmation: {e}")
                # Don't fail the request if notification fails

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": "Guardrail applied successfully",
                        "execution_id": execution_id,
                        "status": new_execution.status,
                    }
                ),
            }

        except Exception as e:
            logger.exception(f"Failed to execute approved action: {e}")
            # Update status to failed
            execution.status = "failed"
            execution.diff = {"error": str(e)}
            self.audit_store.update_execution(execution)

            return {"statusCode": 500, "body": f"Execution failed: {str(e)}"}

    def generate_approval_url(self, execution_id: str, base_url: str) -> dict[str, str]:
        """Generate signed approval URL.

        Args:
            execution_id: Execution ID to approve
            base_url: Base URL of API Gateway (e.g., https://api.example.com)

        Returns:
            Dict with 'url', 'signature', and 'timestamp'
        """
        timestamp = datetime.utcnow().isoformat()
        signature = self._generate_signature(execution_id, timestamp)

        url = f"{base_url}/approve?id={execution_id}&sig={signature}&ts={timestamp}"

        return {"url": url, "signature": signature, "timestamp": timestamp}

    def _verify_signature(self, execution_id: str, timestamp: str, signature: str) -> bool:
        """Verify HMAC signature.

        Args:
            execution_id: Execution ID
            timestamp: Timestamp from request
            signature: Signature from request

        Returns:
            True if signature is valid
        """
        expected_signature = self._generate_signature(execution_id, timestamp)
        return hmac.compare_digest(expected_signature, signature)

    def _generate_signature(self, execution_id: str, timestamp: str) -> str:
        """Generate HMAC signature.

        Args:
            execution_id: Execution ID
            timestamp: Timestamp (ISO8601)

        Returns:
            HMAC-SHA256 hex digest
        """
        message = f"{execution_id}:{timestamp}"
        signature = hmac.new(
            self.approval_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _is_expired(self, timestamp: str) -> bool:
        """Check if timestamp is expired.

        Args:
            timestamp: ISO8601 timestamp

        Returns:
            True if expired
        """
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            now = datetime.utcnow()
            age = now - ts
            return age > timedelta(hours=self.approval_timeout_hours)
        except (ValueError, AttributeError) as e:
            logger.error(f"Invalid timestamp format: {timestamp} - {e}")
            return True  # Treat invalid timestamps as expired


# Lambda handler (API Gateway integration)
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda handler for API Gateway.

    Args:
        event: API Gateway event
        context: Lambda context

    Returns:
        API Gateway response
    """
    logger.info(f"Received approval request: {json.dumps(event)}")

    try:
        # Extract query parameters
        params = event.get("queryStringParameters", {})
        execution_id = params.get("id")
        signature = params.get("sig")
        timestamp = params.get("ts")

        if not all([execution_id, signature, timestamp]):
            return {
                "statusCode": 400,
                "body": "Missing required parameters (id, sig, ts)",
            }

        # Extract user from Slack payload (if available)
        user = "unknown"
        body = event.get("body", "")
        if body:
            try:
                payload = json.loads(body)
                user = payload.get("user", {}).get("name", "unknown")
            except (json.JSONDecodeError, AttributeError):
                pass

        # Handle approval
        handler = ApprovalWebhookHandler()
        response = handler.handle_approval(execution_id, signature, timestamp, user)

        logger.info(f"Approval response: {response}")
        return response

    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
