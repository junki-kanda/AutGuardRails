"""Audit Store for persisting execution records to DynamoDB.

Provides comprehensive audit trail for all guardrail actions.
"""

import logging
import os
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .models import ActionExecution


logger = logging.getLogger(__name__)


class AuditStore:
    """Store and retrieve execution records in DynamoDB."""

    def __init__(
        self,
        table_name: str | None = None,
        region: str | None = None,
    ):
        """Initialize Audit Store.

        Args:
            table_name: DynamoDB table name (default: from env AUDIT_TABLE_NAME)
            region: AWS region (default: from env AWS_REGION or us-east-1)
        """
        self.table_name = table_name or os.getenv("AUDIT_TABLE_NAME", "autoguardrails-audit")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")

        self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self.table = self.dynamodb.Table(self.table_name)

    def save_execution(self, execution: ActionExecution) -> bool:
        """Save execution record to DynamoDB.

        Args:
            execution: ActionExecution to save

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            item = self._execution_to_item(execution)
            self.table.put_item(Item=item)
            logger.info(f"Saved execution {execution.execution_id} to audit store")
            return True

        except ClientError as e:
            logger.error(
                f"Failed to save execution {execution.execution_id}: {e}",
                exc_info=True,
            )
            return False

    def get_execution(self, execution_id: str) -> ActionExecution | None:
        """Retrieve execution by ID.

        Args:
            execution_id: Execution ID to retrieve

        Returns:
            ActionExecution if found, None otherwise
        """
        try:
            response = self.table.get_item(Key={"execution_id": execution_id})

            if "Item" not in response:
                logger.warning(f"Execution {execution_id} not found")
                return None

            return self._item_to_execution(response["Item"])

        except ClientError as e:
            logger.error(f"Failed to get execution {execution_id}: {e}", exc_info=True)
            return None

    def update_execution(self, execution: ActionExecution) -> bool:
        """Update existing execution record.

        Args:
            execution: ActionExecution with updated fields

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            item = self._execution_to_item(execution)
            self.table.put_item(Item=item)
            logger.info(f"Updated execution {execution.execution_id}")
            return True

        except ClientError as e:
            logger.error(
                f"Failed to update execution {execution.execution_id}: {e}",
                exc_info=True,
            )
            return False

    def query_executions_by_policy(self, policy_id: str, limit: int = 100) -> list[ActionExecution]:
        """Query executions for a specific policy.

        Args:
            policy_id: Policy ID to query
            limit: Maximum number of results (default: 100)

        Returns:
            List of ActionExecution records (sorted by executed_at descending)
        """
        try:
            response = self.table.query(
                IndexName="policy_id-executed_at-index",
                KeyConditionExpression="policy_id = :pid",
                ExpressionAttributeValues={":pid": policy_id},
                Limit=limit,
                ScanIndexForward=False,  # Descending order (newest first)
            )

            return [self._item_to_execution(item) for item in response.get("Items", [])]

        except ClientError as e:
            logger.error(f"Failed to query executions for policy {policy_id}: {e}")
            return []

    def query_expired_executions(self, current_time: datetime) -> list[ActionExecution]:
        """Query executions that have expired TTL.

        Args:
            current_time: Current time to compare against TTL

        Returns:
            List of ActionExecution records with expired TTL
        """
        try:
            # Scan for executions with ttl_expires_at <= current_time
            # and status = 'executed' (not yet rolled back)
            current_time_str = current_time.isoformat()

            response = self.table.scan(
                FilterExpression=(
                    "attribute_exists(ttl_expires_at) AND "
                    "ttl_expires_at <= :current_time AND "
                    "#status = :status"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":current_time": current_time_str,
                    ":status": "executed",
                },
            )

            return [self._item_to_execution(item) for item in response.get("Items", [])]

        except ClientError as e:
            logger.error(f"Failed to query expired executions: {e}")
            return []

    def list_recent_executions(
        self, limit: int = 50, status: str | None = None
    ) -> list[ActionExecution]:
        """List recent executions.

        Args:
            limit: Maximum number of results (default: 50)
            status: Optional status filter

        Returns:
            List of recent ActionExecution records
        """
        try:
            if status:
                response = self.table.scan(
                    FilterExpression="#status = :status",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={":status": status},
                    Limit=limit,
                )
            else:
                response = self.table.scan(Limit=limit)

            items = response.get("Items", [])

            # Sort by executed_at descending (newest first)
            items.sort(
                key=lambda x: x.get("executed_at", ""),
                reverse=True,
            )

            return [self._item_to_execution(item) for item in items[:limit]]

        except ClientError as e:
            logger.error(f"Failed to list recent executions: {e}")
            return []

    # =========================================================================
    # Helpers
    # =========================================================================

    def _execution_to_item(self, execution: ActionExecution) -> dict[str, Any]:
        """Convert ActionExecution to DynamoDB item.

        Args:
            execution: ActionExecution object

        Returns:
            DynamoDB item dict
        """
        item: dict[str, Any] = {
            "execution_id": execution.execution_id,
            "policy_id": execution.policy_id,
            "event_id": execution.event_id,
            "status": execution.status,
            "executed_by": execution.executed_by,
            "action": execution.action,
            "target": execution.target,
            "diff": execution.diff,
        }

        # Optional fields
        if execution.executed_at:
            item["executed_at"] = execution.executed_at.isoformat()

        if execution.ttl_expires_at:
            item["ttl_expires_at"] = execution.ttl_expires_at.isoformat()

        if execution.rolled_back_at:
            item["rolled_back_at"] = execution.rolled_back_at.isoformat()

        return item

    def _item_to_execution(self, item: dict[str, Any]) -> ActionExecution:
        """Convert DynamoDB item to ActionExecution.

        Args:
            item: DynamoDB item dict

        Returns:
            ActionExecution object
        """
        # Parse datetime fields
        executed_at = None
        if "executed_at" in item:
            executed_at = datetime.fromisoformat(item["executed_at"])

        ttl_expires_at = None
        if "ttl_expires_at" in item:
            ttl_expires_at = datetime.fromisoformat(item["ttl_expires_at"])

        rolled_back_at = None
        if "rolled_back_at" in item:
            rolled_back_at = datetime.fromisoformat(item["rolled_back_at"])

        return ActionExecution(
            execution_id=item["execution_id"],
            policy_id=item["policy_id"],
            event_id=item["event_id"],
            status=item["status"],
            executed_at=executed_at,
            executed_by=item["executed_by"],
            action=item["action"],
            target=item["target"],
            diff=item.get("diff", {}),
            ttl_expires_at=ttl_expires_at,
            rolled_back_at=rolled_back_at,
        )


def create_audit_table(
    table_name: str = "autoguardrails-audit",
    region: str = "us-east-1",
) -> bool:
    """Create DynamoDB audit table with required indexes.

    Args:
        table_name: Table name to create
        region: AWS region

    Returns:
        True if created successfully, False otherwise
    """
    dynamodb = boto3.resource("dynamodb", region_name=region)

    try:
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "execution_id", "KeyType": "HASH"},  # Partition key only
            ],
            AttributeDefinitions=[
                {"AttributeName": "execution_id", "AttributeType": "S"},
                {"AttributeName": "policy_id", "AttributeType": "S"},
                {"AttributeName": "executed_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "policy_id-executed_at-index",
                    "KeySchema": [
                        {"AttributeName": "policy_id", "KeyType": "HASH"},
                        {"AttributeName": "executed_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Wait for table to be created
        table.meta.client.get_waiter("table_exists").wait(TableName=table_name)

        logger.info(f"Created audit table {table_name}")
        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            logger.info(f"Table {table_name} already exists")
            return True
        else:
            logger.error(f"Failed to create audit table: {e}")
            return False
