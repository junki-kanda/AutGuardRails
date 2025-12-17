"""Slack Notifier for Cost Guardrails.

Sends rich notifications to Slack using Incoming Webhooks.
Supports dry-run alerts, approval requests, and execution confirmations.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from pydantic import HttpUrl

from .models import ActionExecution, ActionPlan, CostEvent, NotificationPayload

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Send notifications to Slack via Incoming Webhook."""

    def __init__(self, webhook_url: str, timeout: int = 10):
        """Initialize Slack Notifier.

        Args:
            webhook_url: Slack Incoming Webhook URL
            timeout: HTTP request timeout in seconds (default: 10)

        Raises:
            ValueError: If webhook_url is empty or invalid
        """
        if not webhook_url or not webhook_url.strip():
            raise ValueError("webhook_url cannot be empty")

        self.webhook_url = webhook_url.strip()
        self.timeout = timeout

    def send_dry_run_alert(
        self, event: CostEvent, plan: ActionPlan, console_url: Optional[str] = None
    ) -> bool:
        """Send dry-run notification (no action will be taken).

        Args:
            event: Cost event that triggered the alert
            plan: Action plan (matched policy)
            console_url: Optional AWS Console URL for quick access

        Returns:
            True if notification sent successfully, False otherwise
        """
        payload = self._build_dry_run_payload(event, plan, console_url)
        return self._send_to_slack(payload)

    def send_approval_request(
        self,
        event: CostEvent,
        plan: ActionPlan,
        execution_id: str,
        approve_url: Optional[str] = None,
        reject_url: Optional[str] = None,
    ) -> bool:
        """Send manual approval request notification.

        Args:
            event: Cost event that triggered the alert
            plan: Action plan requiring approval
            execution_id: Unique execution ID for approval tracking
            approve_url: URL to approve the action
            reject_url: URL to reject the action

        Returns:
            True if notification sent successfully, False otherwise
        """
        payload = self._build_approval_payload(
            event, plan, execution_id, approve_url, reject_url
        )
        return self._send_to_slack(payload)

    def send_execution_confirmation(
        self, execution: ActionExecution, rollback_url: Optional[str] = None
    ) -> bool:
        """Send execution confirmation notification.

        Args:
            execution: Completed action execution
            rollback_url: Optional URL to trigger manual rollback

        Returns:
            True if notification sent successfully, False otherwise
        """
        payload = self._build_execution_payload(execution, rollback_url)
        return self._send_to_slack(payload)

    def send_rollback_confirmation(self, execution: ActionExecution) -> bool:
        """Send rollback confirmation notification.

        Args:
            execution: Rolled-back action execution

        Returns:
            True if notification sent successfully, False otherwise
        """
        payload = self._build_rollback_payload(execution)
        return self._send_to_slack(payload)

    def send_error_alert(
        self, event: CostEvent, error_message: str, execution_id: Optional[str] = None
    ) -> bool:
        """Send error notification.

        Args:
            event: Cost event that caused the error
            error_message: Error description
            execution_id: Optional execution ID for reference

        Returns:
            True if notification sent successfully, False otherwise
        """
        payload = self._build_error_payload(event, error_message, execution_id)
        return self._send_to_slack(payload)

    # =========================================================================
    # Payload Builders
    # =========================================================================

    def _build_dry_run_payload(
        self, event: CostEvent, plan: ActionPlan, console_url: Optional[str]
    ) -> dict[str, Any]:
        """Build Slack Block Kit payload for dry-run notification."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚨 Cost Alert (Dry-Run)"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Account:* `{event.account_id}`"},
                    {"type": "mrkdwn", "text": f"*Amount:* ${event.amount:.2f}"},
                    {"type": "mrkdwn", "text": f"*Source:* {event.source}"},
                    {"type": "mrkdwn", "text": f"*Period:* {event.time_window}"},
                ],
            },
        ]

        if plan.matched_policy_id:
            blocks.append(
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Matched Policy:* `{plan.matched_policy_id}`",
                        },
                        {"type": "mrkdwn", "text": f"*Mode:* {plan.mode}"},
                    ],
                }
            )

        # Recommended actions
        if plan.actions:
            action_desc = self._format_actions(plan.actions)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Recommended Action:*\n{action_desc}",
                    },
                }
            )

        # Target principals
        if plan.target_principals:
            principals_text = "\n".join(f"• `{p}`" for p in plan.target_principals)
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Targets:*\n{principals_text}"},
                }
            )

        # Console link
        if console_url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View in AWS Console"},
                            "url": console_url,
                        }
                    ],
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"🔍 *Dry-run mode* - No action will be taken automatically | Event ID: `{event.event_id}`",
                    }
                ],
            }
        )

        return {"blocks": blocks}

    def _build_approval_payload(
        self,
        event: CostEvent,
        plan: ActionPlan,
        execution_id: str,
        approve_url: Optional[str],
        reject_url: Optional[str],
    ) -> dict[str, Any]:
        """Build Slack Block Kit payload for approval request."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "⚠️ Cost Alert - Approval Required",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Account:* `{event.account_id}`"},
                    {"type": "mrkdwn", "text": f"*Amount:* ${event.amount:.2f}"},
                    {"type": "mrkdwn", "text": f"*Source:* {event.source}"},
                    {"type": "mrkdwn", "text": f"*Period:* {event.time_window}"},
                ],
            },
        ]

        if plan.matched_policy_id:
            blocks.append(
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Policy:* `{plan.matched_policy_id}`",
                        },
                    ],
                }
            )

        # Actions to be executed
        if plan.actions:
            action_desc = self._format_actions(plan.actions)
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Proposed Action:*\n{action_desc}",
                    },
                }
            )

        # Target principals
        if plan.target_principals:
            principals_text = "\n".join(f"• `{p}`" for p in plan.target_principals)
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Targets:*\n{principals_text}"},
                }
            )

        # TTL info
        if plan.ttl_minutes and plan.ttl_minutes > 0:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⏱️ *Auto-rollback:* {plan.ttl_minutes} minutes after execution",
                    },
                }
            )

        # Approval buttons
        action_elements: list[dict[str, Any]] = []
        if approve_url:
            action_elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "url": approve_url,
                    "style": "primary",
                }
            )
        if reject_url:
            action_elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "url": reject_url,
                    "style": "danger",
                }
            )

        if action_elements:
            blocks.append({"type": "actions", "elements": action_elements})

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Execution ID: `{execution_id}` | Event ID: `{event.event_id}`",
                    }
                ],
            }
        )

        return {"blocks": blocks}

    def _build_execution_payload(
        self, execution: ActionExecution, rollback_url: Optional[str]
    ) -> dict[str, Any]:
        """Build Slack Block Kit payload for execution confirmation."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "✅ Guardrail Applied"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Action:* {execution.action}"},
                    {"type": "mrkdwn", "text": f"*Target:* `{execution.target}`"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Executed By:* {execution.executed_by}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Time:* {execution.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if execution.executed_at else 'N/A'}",
                    },
                ],
            },
        ]

        # TTL info
        if execution.ttl_expires_at:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⏱️ *Auto-rollback at:* {execution.ttl_expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    },
                }
            )

        # Rollback button
        if rollback_url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔄 Rollback Now"},
                            "url": rollback_url,
                            "style": "danger",
                        }
                    ],
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Execution ID: `{execution.execution_id}` | Policy: `{execution.policy_id}`",
                    }
                ],
            }
        )

        return {"blocks": blocks}

    def _build_rollback_payload(self, execution: ActionExecution) -> dict[str, Any]:
        """Build Slack Block Kit payload for rollback confirmation."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔄 Guardrail Rolled Back"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Action:* {execution.action}"},
                    {"type": "mrkdwn", "text": f"*Target:* `{execution.target}`"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Originally Executed:* {execution.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if execution.executed_at else 'N/A'}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Rolled Back:* {execution.rolled_back_at.strftime('%Y-%m-%d %H:%M:%S UTC') if execution.rolled_back_at else 'N/A'}",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Execution ID: `{execution.execution_id}` | Policy: `{execution.policy_id}`",
                    }
                ],
            },
        ]

        return {"blocks": blocks}

    def _build_error_payload(
        self, event: CostEvent, error_message: str, execution_id: Optional[str]
    ) -> dict[str, Any]:
        """Build Slack Block Kit payload for error notification."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "❌ Guardrail Error"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Account:* `{event.account_id}`"},
                    {"type": "mrkdwn", "text": f"*Event ID:* `{event.event_id}`"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_message}```"},
            },
        ]

        if execution_id:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Execution ID: `{execution_id}`"}
                    ],
                }
            )

        return {"blocks": blocks}

    # =========================================================================
    # Helpers
    # =========================================================================

    def _format_actions(self, actions: list[Any]) -> str:
        """Format list of PolicyAction objects into readable text."""
        lines = []
        for action in actions:
            if action.type == "notify_only":
                lines.append("• Notify only (no action)")
            elif action.type == "attach_deny_policy":
                deny_list = action.deny or []
                deny_text = ", ".join(f"`{d}`" for d in deny_list[:3])
                if len(deny_list) > 3:
                    deny_text += f" (+{len(deny_list) - 3} more)"
                lines.append(f"• Attach deny policy: {deny_text}")
            elif action.type == "detach_deny_policy":
                lines.append("• Detach deny policy")
            elif action.type == "set_permission_boundary":
                lines.append(f"• Set permission boundary: `{action.boundary_arn}`")
            else:
                lines.append(f"• {action.type}")
        return "\n".join(lines)

    def _send_to_slack(self, payload: dict[str, Any]) -> bool:
        """Send payload to Slack webhook.

        Args:
            payload: Slack Block Kit payload

        Returns:
            True if sent successfully (HTTP 200), False otherwise
        """
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            logger.info("Slack notification sent successfully")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False


def get_cost_management_console_url(account_id: str, region: str = "us-east-1") -> str:
    """Generate AWS Cost Management console URL.

    Args:
        account_id: AWS account ID
        region: AWS region (default: us-east-1)

    Returns:
        Console URL for Cost Explorer
    """
    return f"https://console.aws.amazon.com/cost-management/home?region={region}#/cost-explorer"


def generate_approval_url(
    base_url: str, execution_id: str, action: str, signature: Optional[str] = None
) -> str:
    """Generate approval/rejection URL with signature.

    Args:
        base_url: Base API URL (e.g., https://api.autoguardrails.com)
        execution_id: Unique execution ID
        action: "approve" or "reject"
        signature: Optional HMAC signature for security

    Returns:
        Full approval URL
    """
    params = {"id": execution_id}
    if signature:
        params["sig"] = signature

    query_string = urlencode(params)
    return f"{base_url}/{action}?{query_string}"
