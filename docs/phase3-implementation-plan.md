# Phase 3 実装計画: Auto Mode + TTL Cleanup

## 現状分析 (2025-01-17時点)

### ✅ 完了済みコンポーネント

#### Phase 0-2 (208 tests)
- **A0-1, A0-2, A0-3**: 基盤整備完了
- **A1-1 Policy Engine** (31 tests, 94% coverage)
  - ✅ Exception機構実装済み (`_is_exempted`, `PolicyExceptions`)
  - ✅ Account allowlist
  - ✅ Principal allowlist (wildcard support)
  - ✅ Time window exemptions
- **A1-2 Slack Notifier** (25 tests, 96% coverage)
- **A1-3 Budgets Event Handler** (24 tests, 95% coverage)
- **A1-4 Phase 1 Integration** (11 tests)
- **A2-1 IAM Executor** (25 tests, 87% coverage)
  - ✅ TTL expiration time設定機能実装済み
  - ✅ Rollback機能実装済み
- **A2-2 Audit Store** (24 tests, 80% coverage)
  - ✅ `query_expired_executions()` 実装済み
- **A2-3 Approval Webhook** (22 tests, 96% coverage)
- **A2-4 Phase 2 Integration** (4 tests)

### ⚠️ 未実装コンポーネント

#### Phase 3 残タスク
1. **A3-1: TTL Cleanup Handler** (未実装)
   - `src/guardrails/handlers/ttl_cleanup.py`
   - EventBridge scheduled trigger
   - Rollback orchestration

2. **A3-2: Exception機構** (✅ **既に実装済み！**)
   - PolicyEngine内に完全実装
   - テスト不足の可能性あり

3. **A3-3: Auto Mode統合** (部分実装)
   - `budgets_event.py` の `mode="auto"` 分岐がスタブ状態
   - E2E統合テスト未作成

---

## Phase 3 実装戦略

### 戦略的判断

**既に80%完成している**ため、以下の順序で効率的に完成させる：

1. **A3-2はスキップ** (既に実装済み、テスト追加のみ)
2. **A3-1 (TTL Cleanup)** を先に完成させる (新規実装が必要)
3. **Auto Mode** を budgets_event.py に統合
4. **A3-3 (統合テスト)** で全体を検証

---

## Phase 3 実装計画 (詳細)

### A3-1: TTL Cleanup Handler (優先度: 最高)

**所要時間**: 2-3時間

#### ファイル構成
```
src/guardrails/handlers/ttl_cleanup.py      # 新規作成
tests/unit/test_ttl_cleanup.py              # 新規作成
tests/integration/test_e2e_phase3.py        # 新規作成（Auto Mode E2E含む）
```

#### 実装内容

**1. TTLCleanupHandler クラス**

```python
class TTLCleanupHandler:
    """Handle TTL-based automatic rollback of guardrail actions."""

    def __init__(self, audit_store=None, executor=None, notifier=None):
        self.audit_store = audit_store or AuditStore()
        self.executor = executor or IAMExecutor()
        self.notifier = notifier or SlackNotifier(...)

    def cleanup_expired_executions(self) -> dict:
        """
        Query and rollback all expired executions.

        Returns:
            {
                "total_found": int,
                "rolled_back": int,
                "failed": int,
                "errors": list[dict]
            }
        """
        pass

    def _rollback_execution(self, execution: ActionExecution) -> bool:
        """Rollback a single execution with error handling."""
        pass
```

**2. Lambda Handler**

```python
def lambda_handler(event, context):
    """
    AWS Lambda handler for TTL cleanup (triggered by EventBridge).

    EventBridge schedule: rate(5 minutes)
    """
    handler = TTLCleanupHandler()
    result = handler.cleanup_expired_executions()

    logger.info(f"TTL cleanup completed: {result}")

    return {
        "statusCode": 200,
        "body": json.dumps(result)
    }
```

#### 設計原則

1. **Idempotency (冪等性)**
   - 同じexecutionを複数回rollbackしても安全
   - `status='executed'` のみ処理（`rolled_back`/`failed`はスキップ）

2. **Error Handling**
   - 個別rollback失敗時も処理継続
   - エラーログ記録 + 次回リトライ
   - 3回連続失敗 → Slack alerting

3. **Performance**
   - バッチサイズ制限（例：100件/回）
   - タイムアウト前に処理完了（Lambda: 5分）

#### テスト計画

**Unit Tests** (15-20 tests)
- `test_cleanup_expired_executions_success`
- `test_cleanup_no_expired_executions`
- `test_rollback_single_execution_success`
- `test_rollback_execution_failure_logged`
- `test_rollback_already_rolled_back_skipped` (idempotency)
- `test_rollback_failed_status_skipped`
- `test_cleanup_handles_partial_failures`
- `test_cleanup_notifies_on_rollback`
- `test_lambda_handler_success`
- `test_lambda_handler_no_executions`

**Integration Tests** (A3-3で実装)
- E2E: Auto mode → Execute → TTL cleanup

---

### A3-2: Exception機構テスト追加 (優先度: 中)

**所要時間**: 1時間

#### 現状
- ✅ `PolicyEngine._is_exempted()` 実装済み
- ✅ `PolicyExceptions` モデル定義済み
- ⚠️ テストカバレッジ不足の可能性

#### 追加すべきテスト

**tests/unit/test_policy_engine.py に追加**

```python
class TestPolicyExceptions:
    """Test exception/allowlist functionality."""

    def test_account_allowlist_exempts_event(self):
        """Account in allowlist should be exempted."""
        pass

    def test_principal_allowlist_exact_match(self):
        """Principal exact match should be exempted."""
        pass

    def test_principal_allowlist_wildcard_match(self):
        """Principal wildcard (e.g., arn:*:role/test-*) should work."""
        pass

    def test_time_window_exemption_business_hours(self):
        """Policy should not execute during business hours."""
        pass

    def test_time_window_exemption_weekends(self):
        """Policy should not execute on weekends."""
        pass

    def test_no_exemption_triggers_policy(self):
        """Event not matching any exemption should trigger."""
        pass

    def test_multiple_exemptions_any_match_exempts(self):
        """Any exemption match should exempt the event."""
        pass
```

**検証**
```bash
python -m pytest tests/unit/test_policy_engine.py::TestPolicyExceptions -v
```

---

### A3-3: Auto Mode統合 (優先度: 高)

**所要時間**: 1.5-2時間

#### 1. budgets_event.py の auto モード実装

**現状 (スタブ)**
```python
elif action_plan.mode == "auto":
    # Auto: Execute action immediately
    # TODO: Phase 3 - Execute IAM actions via executor
    logger.warning("Auto mode not implemented yet (Phase 3)...")
```

**実装後**
```python
elif action_plan.mode == "auto":
    # Auto: Execute action immediately
    from ..audit_store import AuditStore
    from ..executor_iam import IAMExecutor

    audit_store = AuditStore()
    executor = IAMExecutor(dry_run=False)

    # Execute action plan
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

    # Send execution confirmation
    primary_execution = executions[0]
    success = notifier.send_execution_confirmation(
        execution=primary_execution,
        message=f"✅ Auto-executed guardrail (TTL: {action_plan.ttl_minutes}min)",
    )

    return {
        "notification_sent": success,
        "execution_id": primary_execution.execution_id,
        "action": "executed",
        "executions_created": len(executions),
        "ttl_expires_at": primary_execution.ttl_expires_at.isoformat() if primary_execution.ttl_expires_at else None,
    }
```

#### 2. E2E Integration Tests

**tests/integration/test_e2e_phase3.py** (新規作成)

```python
"""End-to-End Integration Tests for Phase 3 (Auto Mode + TTL Cleanup)."""

class TestE2EAutoMode:
    """Test auto mode execution flow."""

    @mock_aws
    def test_auto_mode_end_to_end(self, temp_policies_dir):
        """
        Full auto mode flow:
        1. Budget event triggers lambda
        2. Policy matches (auto mode)
        3. Guardrail executed immediately
        4. IAM deny policy attached
        5. Execution saved to DynamoDB
        6. Confirmation sent to Slack
        """
        pass

    @mock_aws
    def test_auto_mode_with_ttl_cleanup(self, temp_policies_dir):
        """
        Full TTL cleanup flow:
        1. Auto mode executes guardrail
        2. TTL expires
        3. TTL cleanup handler runs
        4. Policy is rolled back
        5. Execution status updated
        """
        pass

    @mock_aws
    def test_auto_mode_respects_exceptions(self, temp_policies_dir):
        """Auto mode should respect exception rules."""
        pass


class TestTTLCleanupIntegration:
    """Test TTL cleanup handler integration."""

    @mock_aws
    def test_ttl_cleanup_rollback_multiple_executions(self):
        """TTL cleanup should rollback all expired executions."""
        pass

    @mock_aws
    def test_ttl_cleanup_idempotency(self):
        """Running TTL cleanup twice should be safe."""
        pass

    @mock_aws
    def test_ttl_cleanup_partial_failure_continues(self):
        """If one rollback fails, others should still proceed."""
        pass
```

---

## 実装順序 (推奨)

### Day 1: TTL Cleanup (2-3時間)

1. **午前**:
   - `src/guardrails/handlers/ttl_cleanup.py` 実装
   - `TTLCleanupHandler` クラス完成
   - Lambda handler実装

2. **午後**:
   - `tests/unit/test_ttl_cleanup.py` 完成
   - ユニットテスト全パス確認

### Day 2: Auto Mode + 統合 (2-3時間)

1. **午前**:
   - `budgets_event.py` の auto mode 実装
   - Exception機構テスト追加（時間があれば）

2. **午後**:
   - `tests/integration/test_e2e_phase3.py` 実装
   - 全E2Eテスト実行・修正
   - Phase 3完了確認

---

## 成功基準

### Phase 3 完了条件

1. **A3-1: TTL Cleanup**
   - ✅ `ttl_cleanup.py` 実装完了
   - ✅ 15+ unit tests パス
   - ✅ カバレッジ > 90%

2. **A3-2: Exception Tests**
   - ✅ 7+ exception tests パス
   - ✅ 既存のPolicy Engineテストと統合

3. **A3-3: Auto Mode Integration**
   - ✅ `budgets_event.py` auto mode実装
   - ✅ 5+ E2E tests パス
   - ✅ Manual/Auto/TTL の全フロー動作確認

### 全体メトリクス

- **総テスト数**: 230+ tests (Phase 3で +22 tests)
- **カバレッジ**: 全モジュール > 85%
- **E2Eテスト**: 20+ tests (Phase 1: 11, Phase 2: 4, Phase 3: 5+)

---

## リスクと対策

### リスク1: TTL Cleanup の複雑性

**リスク**: Rollback失敗時のエラーハンドリングが複雑

**対策**:
- Idempotent設計（何度実行しても安全）
- 個別rollback失敗時も処理継続
- 失敗ログ + Slack通知

### リスク2: Auto Mode の誤動作

**リスク**: Auto modeで誤爆した場合の影響大

**対策**:
- ✅ Exception機構が既に実装済み
- デフォルトTTL設定必須（最低30分）
- Sandbox環境での十分なテスト
- ドキュメント強化（safety checklist）

### リスク3: Phase 2統合テストの一部失敗

**リスク**: Phase 2のE2Eテストで deny actions取得の問題

**対策**:
- Phase 3実装前に修正
- `executor_iam.py` のdry-run diffフォーマット確認
- `approval_webhook.py` のdeny actions抽出ロジック修正

---

## Phase 4への準備

Phase 3完了後、以下に進む：

1. **Infrastructure as Code**
   - CDK/Terraform テンプレート
   - EventBridge Rule定義
   - IAM Role/Policy定義

2. **Documentation**
   - Deployment guide
   - Safety checklist (auto mode)
   - Runbook (operations)

3. **Monitoring & Alerting**
   - CloudWatch dashboards
   - Alarm definitions
   - Log aggregation

---

## まとめ

**Phase 3は既に80%完成**しており、残りは：

1. ✅ **Exception機構**: 実装済み（テスト追加のみ）
2. 🔄 **TTL Cleanup**: 新規実装が必要（2-3時間）
3. 🔄 **Auto Mode**: budgets_event.py に統合（1時間）
4. 🔄 **E2E Tests**: 統合テスト作成（2時間）

**推定作業時間**: 5-7時間（1-2日）

**推奨開始順序**: A3-1 (TTL Cleanup) → Auto Mode → A3-3 (E2E Tests) → A3-2 (Exception Tests)
