"""AWS Budgets Event Handler for Lambda.

This Lambda function handles AWS Budget notifications (via SNS or EventBridge),
evaluates them against guardrail policies, and executes appropriate actions.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..models import CostEvent
from ..notifier_slack import SlackNotifier, get_cost_management_console_url
from ..policy_engine import PolicyEngine, load_policies_from_directory


logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for AWS Budget notifications.

    Args:
        event: Lambda event (SNS or EventBridge format)
        context: Lambda context

    Returns:
        Response dict with status and details

    Environment Variables:
        POLICIES_PATH: Path to policy YAML files (default: /var/task/policies)
        SLACK_WEBHOOK_URL: Slack Incoming Webhook URL (required for notifications)
        DRY_RUN: If "true", skip all actions (default: false)
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Parse the incoming event
        cost_event = parse_event(event)
        logger.info(f"Parsed cost event: {cost_event.event_id}")

        # Load policies
        policies_path = os.getenv("POLICIES_PATH", "/var/task/policies")
        policies = load_policies_from_directory(policies_path)
        logger.info(f"Loaded {len(policies)} policies from {policies_path}")

        if not policies:
            logger.warning("No policies loaded, nothing to evaluate")
            return {"statusCode": 200, "body": "no_policies"}

        # Evaluate event against policies
        engine = PolicyEngine()
        action_plan = engine.evaluate(cost_event, policies)

        if not action_plan.matched:
            logger.info("No policy matched this cost event")
            return {"statusCode": 200, "body": "no_match"}

        logger.info(f"Policy matched: {action_plan.matched_policy_id} (mode: {action_plan.mode})")

        # Check global dry-run override
        global_dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
        if global_dry_run:
            logger.info("Global DRY_RUN enabled, forcing dry_run mode")
            action_plan.mode = "dry_run"

        # Execute based on mode
        result = execute_action_plan(cost_event, action_plan)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "status": "success",
                    "event_id": cost_event.event_id,
                    "policy_id": action_plan.matched_policy_id,
                    "mode": action_plan.mode,
                    "result": result,
                }
            ),
        }

    except Exception as e:
        logger.error(f"Handler failed: {e}", exc_info=True)

        # Try to send error notification
        try:
            slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
            if slack_webhook and "cost_event" in locals():
                notifier = SlackNotifier(slack_webhook)
                notifier.send_error_alert(cost_event, str(e))
        except Exception as notify_error:
            logger.error(f"Failed to send error notification: {notify_error}")

        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(e)}),
        }


def parse_event(event: dict[str, Any]) -> CostEvent:
    """Parse Lambda event into CostEvent model.

    Supports two event formats:
    1. EventBridge: Direct Budget notification
    2. SNS: Budget notification wrapped in SNS message

    Args:
        event: Lambda event dict

    Returns:
        CostEvent object

    Raises:
        ValueError: If event format is invalid or required fields missing
    """
    # Try SNS format first (most common for Budgets)
    if "Records" in event and len(event["Records"]) > 0:
        record = event["Records"][0]
        if record.get("EventSource") == "aws:sns":
            sns_message = json.loads(record["Sns"]["Message"])
            return parse_budgets_notification(sns_message)

    # Try EventBridge format
    if "detail-type" in event and event["detail-type"] == "AWS Budget Notification":
        return parse_budgets_eventbridge(event)

    # Try direct notification format (for testing)
    if "budgetName" in event:
        return parse_budgets_notification(event)

    raise ValueError(f"Unsupported event format: {event.keys()}")


def parse_budgets_notification(notification: dict[str, Any]) -> CostEvent:
    """Parse AWS Budgets notification (SNS message format).

    Args:
        notification: Budget notification dict

    Returns:
        CostEvent object

    Raises:
        ValueError: If required fields missing
    """
    budget_name = notification.get("budgetName")
    if not budget_name:
        raise ValueError("Missing budgetName in notification")

    # Extract spend amount
    calculated_spend = notification.get("calculatedSpend", {})
    actual_spend = calculated_spend.get("actualSpend", {})
    amount = float(actual_spend.get("amount", 0))
    currency = actual_spend.get("unit", "USD")

    if amount <= 0:
        raise ValueError(f"Invalid amount: {amount}")

    # Extract account ID (from ARN or notificationArn)
    account_id = extract_account_id(notification)

    # Generate event ID
    event_id = f"budget-{budget_name}-{int(datetime.utcnow().timestamp())}"

    # Build time window
    time_window = notification.get("time", "unknown")

    return CostEvent(
        event_id=event_id,
        source="budgets",
        account_id=account_id,
        amount=amount,
        time_window=time_window,
        details={
            "budget_name": budget_name,
            "notification_type": notification.get("notificationType", "ACTUAL"),
            "threshold_type": notification.get("thresholdType", "PERCENTAGE"),
            "threshold": notification.get("threshold", 0),
            "comparison_operator": notification.get("comparisonOperator", "GREATER_THAN"),
            "currency": currency,
        },
    )


def parse_budgets_eventbridge(event: dict[str, Any]) -> CostEvent:
    """Parse AWS Budgets EventBridge event.

    Args:
        event: EventBridge event dict

    Returns:
        CostEvent object

    Raises:
        ValueError: If required fields missing
    """
    detail = event.get("detail", {})
    budget_name = detail.get("budgetName")
    if not budget_name:
        raise ValueError("Missing budgetName in EventBridge detail")

    # Extract spend amount
    calculated_spend = detail.get("calculatedSpend", {})
    actual_spend = calculated_spend.get("actualSpend", {})
    amount = float(actual_spend.get("amount", 0))

    if amount <= 0:
        raise ValueError(f"Invalid amount: {amount}")

    # Account ID from event
    account_id = event.get("account", "")
    if not account_id or len(account_id) != 12:
        raise ValueError(f"Invalid account ID: {account_id}")

    # Event ID from EventBridge
    event_id = event.get("id", f"budget-{uuid4()}")

    # Time window
    event_time = event.get("time", datetime.utcnow().isoformat())

    return CostEvent(
        event_id=event_id,
        source="budgets",
        account_id=account_id,
        amount=amount,
        time_window=event_time,
        details={
            "budget_name": budget_name,
            "notification_type": detail.get("notificationType", "ACTUAL"),
            "threshold_type": detail.get("thresholdType", "PERCENTAGE"),
            "threshold": detail.get("threshold", 0),
            "comparison_operator": detail.get("comparisonOperator", "GREATER_THAN"),
            "currency": actual_spend.get("unit", "USD"),
            "region": event.get("region", "us-east-1"),
        },
    )


def extract_account_id(notification: dict[str, Any]) -> str:
    """Extract AWS account ID from budget notification.

    Args:
        notification: Budget notification dict

    Returns:
        12-digit account ID

    Raises:
        ValueError: If account ID cannot be extracted
    """
    # Try notificationArn (format: arn:aws:budgets::123456789012:budget/*)
    notification_arn = notification.get("notificationArn", "")
    if notification_arn:
        parts = notification_arn.split(":")
        if len(parts) >= 5 and len(parts[4]) == 12:
            return parts[4]

    # Try accountId field (may be present in some formats)
    account_id = notification.get("accountId", "")
    if account_id and len(account_id) == 12:
        return account_id

    # Fallback: check environment variable
    account_id = os.getenv("AWS_ACCOUNT_ID", "")
    if account_id and len(account_id) == 12:
        logger.warning("Using AWS_ACCOUNT_ID from environment")
        return account_id

    raise ValueError("Could not extract account ID from notification")


def execute_action_plan(cost_event: CostEvent, action_plan: Any) -> dict[str, Any]:
    """Execute action plan based on mode.

    Args:
        cost_event: Cost event that triggered the plan
        action_plan: ActionPlan from policy evaluation

    Returns:
        Execution result dict

    Raises:
        ValueError: If SLACK_WEBHOOK_URL not set
    """
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not slack_webhook:
        raise ValueError("SLACK_WEBHOOK_URL environment variable required")

    notifier = SlackNotifier(slack_webhook)
    console_url = get_cost_management_console_url(cost_event.account_id)

    if action_plan.mode == "dry_run":
        # Dry-run: Notify only
        success = notifier.send_dry_run_alert(cost_event, action_plan, console_url)
        return {"notification_sent": success, "action": "none"}

    elif action_plan.mode == "manual":
        # Manual: Create execution record and send approval request
        from ..audit_store import AuditStore
        from ..executor_iam import IAMExecutor
        from ..handlers.approval_webhook import ApprovalWebhookHandler

        # Create execution records for each action/principal combination
        audit_store = AuditStore()
        executor = IAMExecutor(dry_run=True)  # Dry-run to generate action plans

        # Execute action plan in dry-run mode to create executions
        executions = executor.execute_action_plan(
            plan=action_plan,
            event_id=cost_event.event_id,
            executed_by="system:budgets_event",
        )

        if not executions:
            logger.error("No executions created for manual approval")
            return {
                "notification_sent": False,
                "action": "error",
                "error": "Failed to create execution records",
            }

        # Save all executions to DynamoDB with status=planned
        for execution in executions:
            execution.status = "planned"  # Override dry-run status
            audit_store.save_execution(execution)

        # Use first execution for approval notification
        primary_execution = executions[0]

        # Generate approval URL
        api_base_url = os.getenv("APPROVAL_API_BASE_URL", "https://api.autoguardrails.example.com")
        webhook_handler = ApprovalWebhookHandler()
        approval_data = webhook_handler.generate_approval_url(
            execution_id=primary_execution.execution_id,
            base_url=api_base_url,
        )

        # Send approval request notification
        success = notifier.send_approval_request(
            cost_event,
            action_plan,
            primary_execution.execution_id,
            approve_url=approval_data["url"],
            reject_url=None,  # TODO: Implement reject functionality
        )

        return {
            "notification_sent": success,
            "execution_id": primary_execution.execution_id,
            "action": "approval_requested",
            "executions_created": len(executions),
        }

    elif action_plan.mode == "auto":
        # Auto: Execute action immediately (no approval required)
        from ..audit_store import AuditStore
        from ..executor_iam import IAMExecutor

        audit_store = AuditStore()
        executor = IAMExecutor(dry_run=False)

        # Execute action plan immediately
        executions = executor.execute_action_plan(
            plan=action_plan,
            event_id=cost_event.event_id,
            executed_by="system:auto",
        )

        if not executions:
            logger.error("Auto mode: No executions created")
            return {
                "notification_sent": False,
                "action": "error",
                "error": "Failed to create executions",
            }

        # Save all executions to DynamoDB
        for execution in executions:
            audit_store.save_execution(execution)

        # Use first execution for notification
        primary_execution = executions[0]

        # Send execution confirmation
        success = notifier.send_execution_confirmation(
            execution=primary_execution,
            rollback_url=None,
        )

        return {
            "notification_sent": success,
            "execution_id": primary_execution.execution_id,
            "action": "executed",
            "executions_created": len(executions),
            "ttl_expires_at": (
                primary_execution.ttl_expires_at.isoformat()
                if primary_execution.ttl_expires_at
                else None
            ),
        }

    else:
        raise ValueError(f"Unknown mode: {action_plan.mode}")
