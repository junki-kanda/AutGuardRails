"""IAM Executor for applying guardrails.

Executes IAM-based guardrails by attaching deny policies to principals.
All operations are designed to be reversible and auditable.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

from .models import ActionExecution, ActionPlan, PolicyAction

logger = logging.getLogger(__name__)


class IAMExecutor:
    """Execute IAM guardrails (attach/detach deny policies)."""

    def __init__(self, dry_run: bool = False):
        """Initialize IAM Executor.

        Args:
            dry_run: If True, simulate actions without executing (default: False)
        """
        self.dry_run = dry_run
        self.iam_client = boto3.client("iam")

    def execute_action_plan(
        self,
        plan: ActionPlan,
        event_id: str,
        executed_by: str = "system:auto",
    ) -> list[ActionExecution]:
        """Execute all actions in the plan.

        Args:
            plan: ActionPlan from policy evaluation
            event_id: Cost event ID that triggered this execution
            executed_by: Who/what is executing (e.g., "user@example.com" or "system:auto")

        Returns:
            List of ActionExecution results (one per action)

        Raises:
            ValueError: If plan is not matched or contains invalid actions
        """
        if not plan.matched:
            raise ValueError("Cannot execute unmatched plan")

        if not plan.matched_policy_id or not plan.actions:
            raise ValueError("Plan must have policy_id and actions")

        executions = []

        for action in plan.actions:
            for principal_arn in plan.target_principals:
                execution = self._execute_single_action(
                    action=action,
                    principal_arn=principal_arn,
                    policy_id=plan.matched_policy_id,
                    event_id=event_id,
                    executed_by=executed_by,
                    ttl_minutes=plan.ttl_minutes,
                )
                executions.append(execution)

        return executions

    def _execute_single_action(
        self,
        action: PolicyAction,
        principal_arn: str,
        policy_id: str,
        event_id: str,
        executed_by: str,
        ttl_minutes: Optional[int],
    ) -> ActionExecution:
        """Execute a single action on a single principal.

        Args:
            action: PolicyAction to execute
            principal_arn: Target principal ARN
            policy_id: Policy ID that triggered this
            event_id: Cost event ID
            executed_by: Executor identity
            ttl_minutes: Time-to-live for auto-rollback (0 = no TTL)

        Returns:
            ActionExecution result
        """
        execution_id = f"exec-{uuid4()}"
        execution = ActionExecution(
            execution_id=execution_id,
            policy_id=policy_id,
            event_id=event_id,
            status="planned",
            executed_at=None,
            executed_by=executed_by,
            action=action.type,
            target=principal_arn,
            diff={},
        )

        if action.type == "notify_only":
            # No action to execute
            execution.status = "executed"
            execution.executed_at = datetime.utcnow()
            execution.diff = {"action": "notify_only", "no_changes": True}
            logger.info(f"Execution {execution_id}: notify_only (no IAM changes)")
            return execution

        elif action.type == "attach_deny_policy":
            if not action.deny:
                raise ValueError("attach_deny_policy requires deny list")

            # Execute the attach
            try:
                if self.dry_run:
                    logger.info(
                        f"DRY-RUN: Would attach deny policy to {principal_arn} "
                        f"denying {len(action.deny)} actions"
                    )
                    execution.status = "executed"
                    execution.executed_at = datetime.utcnow()
                    execution.diff = {
                        "dry_run": True,
                        "would_deny": action.deny,
                        "target": principal_arn,
                    }
                else:
                    result = self._attach_deny_policy(principal_arn, action.deny, policy_id)
                    execution.status = "executed"
                    execution.executed_at = datetime.utcnow()
                    execution.diff = result

                    # Set TTL expiration if configured
                    if ttl_minutes and ttl_minutes > 0:
                        execution.ttl_expires_at = datetime.utcnow() + timedelta(
                            minutes=ttl_minutes
                        )

                    logger.info(
                        f"Execution {execution_id}: attached deny policy "
                        f"{result.get('policy_arn')} to {principal_arn}"
                    )

            except Exception as e:
                logger.error(f"Execution {execution_id} failed: {e}", exc_info=True)
                execution.status = "failed"
                execution.diff = {"error": str(e)}

            return execution

        else:
            raise ValueError(f"Unsupported action type: {action.type}")

    def _attach_deny_policy(
        self, principal_arn: str, deny_actions: list[str], policy_id: str
    ) -> dict[str, Any]:
        """Attach a deny policy to a principal.

        Args:
            principal_arn: Target principal ARN
            deny_actions: List of IAM actions to deny
            policy_id: Policy ID (for naming)

        Returns:
            Diff dict with before/after state

        Raises:
            ClientError: If AWS API calls fail
        """
        # Extract role or user name from ARN
        principal_type, principal_name = self._parse_principal_arn(principal_arn)

        # Create deny policy document
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "GuardrailsDenyPolicy",
                    "Effect": "Deny",
                    "Action": deny_actions,
                    "Resource": "*",
                }
            ],
        }

        # Generate unique policy name (hash of actions + timestamp)
        policy_hash = hashlib.sha256(
            json.dumps(deny_actions, sort_keys=True).encode()
        ).hexdigest()[:8]
        policy_name = f"guardrails-deny-{policy_id}-{policy_hash}"

        # Check if policy already exists (idempotency)
        existing_policy_arn = None
        try:
            account_id = self._get_account_id()
            candidate_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
            self.iam_client.get_policy(PolicyArn=candidate_arn)
            existing_policy_arn = candidate_arn
            logger.info(f"Policy {policy_name} already exists, reusing")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise

        # Create policy if it doesn't exist
        if existing_policy_arn:
            policy_arn = existing_policy_arn
        else:
            response = self.iam_client.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document),
                Description=f"AutoGuardRails deny policy for {policy_id}",
            )
            policy_arn = response["Policy"]["Arn"]
            logger.info(f"Created policy {policy_arn}")

        # Get current attached policies (for diff)
        before_policies = self._list_attached_policies(principal_type, principal_name)

        # Attach policy to principal
        if principal_type == "role":
            self.iam_client.attach_role_policy(
                RoleName=principal_name, PolicyArn=policy_arn
            )
        elif principal_type == "user":
            self.iam_client.attach_user_policy(
                UserName=principal_name, PolicyArn=policy_arn
            )
        else:
            raise ValueError(f"Unsupported principal type: {principal_type}")

        logger.info(f"Attached {policy_arn} to {principal_type} {principal_name}")

        # Get after state
        after_policies = self._list_attached_policies(principal_type, principal_name)

        return {
            "policy_arn": policy_arn,
            "policy_name": policy_name,
            "principal_arn": principal_arn,
            "principal_type": principal_type,
            "principal_name": principal_name,
            "before": before_policies,
            "after": after_policies,
            "denied_actions": deny_actions,
        }

    def rollback_execution(self, execution: ActionExecution) -> bool:
        """Rollback an executed action.

        Args:
            execution: ActionExecution to rollback

        Returns:
            True if rolled back successfully, False otherwise
        """
        if execution.status != "executed":
            logger.warning(
                f"Cannot rollback execution {execution.execution_id}: "
                f"status is {execution.status}, not 'executed'"
            )
            return False

        if self.dry_run:
            logger.info(f"DRY-RUN: Would rollback execution {execution.execution_id}")
            return True

        try:
            if execution.action == "attach_deny_policy":
                self._rollback_attach_deny_policy(execution)
                execution.status = "rolled_back"
                execution.rolled_back_at = datetime.utcnow()
                logger.info(f"Rolled back execution {execution.execution_id}")
                return True

            elif execution.action == "notify_only":
                # Nothing to rollback
                execution.status = "rolled_back"
                execution.rolled_back_at = datetime.utcnow()
                logger.info(f"Execution {execution.execution_id}: notify_only (no rollback needed)")
                return True

            else:
                logger.error(f"Unknown action type: {execution.action}")
                return False

        except Exception as e:
            logger.error(
                f"Failed to rollback execution {execution.execution_id}: {e}",
                exc_info=True,
            )
            return False

    def _rollback_attach_deny_policy(self, execution: ActionExecution) -> None:
        """Rollback attach_deny_policy action.

        Args:
            execution: ActionExecution with diff containing policy info

        Raises:
            ClientError: If AWS API calls fail
        """
        diff = execution.diff

        if diff.get("dry_run"):
            logger.info("Dry-run execution, nothing to rollback")
            return

        policy_arn = diff.get("policy_arn")
        principal_arn = execution.target
        principal_type = diff.get("principal_type")
        principal_name = diff.get("principal_name")

        if not all([policy_arn, principal_type, principal_name]):
            raise ValueError("Execution diff missing required fields for rollback")

        # Detach policy
        if principal_type == "role":
            self.iam_client.detach_role_policy(
                RoleName=principal_name, PolicyArn=policy_arn
            )
        elif principal_type == "user":
            self.iam_client.detach_user_policy(
                UserName=principal_name, PolicyArn=policy_arn
            )

        logger.info(f"Detached {policy_arn} from {principal_type} {principal_name}")

        # Delete policy if it's a guardrails policy (and not attached elsewhere)
        if "guardrails-deny-" in policy_arn:
            try:
                # Check if policy is attached to other principals
                response = self.iam_client.list_entities_for_policy(PolicyArn=policy_arn)
                attached_count = (
                    len(response.get("PolicyRoles", []))
                    + len(response.get("PolicyUsers", []))
                    + len(response.get("PolicyGroups", []))
                )

                if attached_count == 0:
                    # Safe to delete
                    self.iam_client.delete_policy(PolicyArn=policy_arn)
                    logger.info(f"Deleted policy {policy_arn}")
                else:
                    logger.info(
                        f"Policy {policy_arn} still attached to {attached_count} entities, not deleting"
                    )
            except ClientError as e:
                logger.warning(f"Could not delete policy {policy_arn}: {e}")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _parse_principal_arn(self, arn: str) -> tuple[str, str]:
        """Parse principal ARN into type and name.

        Args:
            arn: Principal ARN (e.g., arn:aws:iam::123456789012:role/MyRole)

        Returns:
            Tuple of (principal_type, principal_name)

        Raises:
            ValueError: If ARN format is invalid
        """
        # arn:aws:iam::123456789012:role/MyRole
        # arn:aws:iam::123456789012:user/MyUser
        parts = arn.split(":")
        if len(parts) != 6 or parts[2] != "iam":
            raise ValueError(f"Invalid IAM ARN: {arn}")

        resource_part = parts[5]  # "role/MyRole" or "user/MyUser"
        if "/" not in resource_part:
            raise ValueError(f"Invalid IAM resource: {resource_part}")

        principal_type, principal_name = resource_part.split("/", 1)

        if principal_type not in ["role", "user"]:
            raise ValueError(f"Unsupported principal type: {principal_type}")

        return principal_type, principal_name

    def _list_attached_policies(
        self, principal_type: str, principal_name: str
    ) -> list[str]:
        """List attached managed policies for a principal.

        Args:
            principal_type: "role" or "user"
            principal_name: Principal name

        Returns:
            List of attached policy ARNs
        """
        if principal_type == "role":
            response = self.iam_client.list_attached_role_policies(RoleName=principal_name)
        elif principal_type == "user":
            response = self.iam_client.list_attached_user_policies(UserName=principal_name)
        else:
            raise ValueError(f"Unsupported principal type: {principal_type}")

        return [p["PolicyArn"] for p in response.get("AttachedPolicies", [])]

    def _get_account_id(self) -> str:
        """Get current AWS account ID.

        Returns:
            12-digit account ID
        """
        sts = boto3.client("sts")
        return sts.get_caller_identity()["Account"]
