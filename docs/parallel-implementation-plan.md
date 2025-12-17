# 並行エージェント実装プラン（Parallel Agent Implementation Plan）

## 0. 並行実装の原則（Parallel Execution Principles）

### 0.1 基本方針
- **完全独立タスク**：他のエージェントの成果物に依存しないタスクを優先
- **インターフェイス駆動開発**：先にインターフェイス（型定義）を確定し、実装を並行化
- **統合ポイントの最小化**：統合が必要な箇所は明示し、事前に仕様を固める
- **テスト可能性**：各コンポーネントが独立してテスト可能

### 0.2 コンフリクト回避戦略
- ファイル単位で担当を分離（同じファイルを複数エージェントが触らない）
- 共通の型定義は最初に確定（`src/guardrails/models.py`）
- 統合テストは最後に別エージェントが担当

---

## 1. Phase 0: 足場構築（Foundation）【並行度: 高】

### エージェント A0-1: プロジェクト設定
**期間**: 30分
**依存**: なし
**成果物**:
```
pyproject.toml
.python-version
.gitignore
ruff.toml
pytest.ini
Makefile
```

**タスク**:
- [ ] `pyproject.toml` 作成（Python 3.11+, pydantic, boto3, pytest, ruff）
- [ ] `Makefile` 作成（setup/fmt/lint/test/run-local）
- [ ] `.gitignore` 作成（.env, __pycache__, .venv, .aws-sam/）
- [ ] `ruff.toml` 設定（line-length=100, target-version="py311"）
- [ ] `pytest.ini` 設定
- [ ] `README.md` 初版（セットアップ手順のみ）

**検証**:
```bash
make setup
make fmt
make lint
```

---

### エージェント A0-2: ドキュメント基盤
**期間**: 30分
**依存**: なし
**成果物**:
```
docs/product.md
docs/safety.md
docs/policies.md
docs/architecture.md
docs/decisions/.gitkeep
```

**タスク**:
- [ ] `docs/product.md`（ユーザーストーリー、MVPスコープ）
- [ ] `docs/safety.md`（安全原則、IAM権限設計、禁止事項）
- [ ] `docs/policies.md`（ポリシーYAML仕様、バリデーションルール）
- [ ] `docs/architecture.md`（システム構成図、データフロー）
- [ ] `docs/decisions/` ディレクトリ作成

**検証**:
```bash
ls -la docs/
```

---

### エージェント A0-3: 共通型定義（最優先）
**期間**: 45分
**依存**: A0-1完了後
**成果物**:
```
src/guardrails/models.py
tests/unit/test_models.py
```

**タスク**:
- [ ] `CostEvent` モデル（pydantic BaseModel）
- [ ] `GuardrailPolicy` モデル
- [ ] `ActionExecution` モデル
- [ ] `ActionPlan` モデル
- [ ] `NotificationPayload` モデル
- [ ] ユニットテスト（バリデーション、シリアライゼーション）

**検証**:
```bash
make test tests/unit/test_models.py
```

**統合ポイント**:
このファイルは全エージェントが参照するため、**Phase 0で最初に完成させる**

---

## 2. Phase 1: Free Tier（検知/通知）【並行度: 非常に高】

### エージェント A1-1: Policy Engine（コア）
**期間**: 1時間
**依存**: A0-3完了
**成果物**:
```
src/guardrails/policy_engine.py
tests/unit/test_policy_engine.py
policies/example-dry-run.yaml
```

**タスク**:
- [ ] `PolicyEngine` クラス
  - `evaluate(event: CostEvent, policies: List[GuardrailPolicy]) -> ActionPlan`
  - `match_event(event, policy) -> bool`（金額、アカウント、ソースのマッチング）
  - `build_action_plan(event, policy) -> ActionPlan`
- [ ] dry_run モードのみ実装（action.type = "notify_only"）
- [ ] YAML policy loader（`pyyaml` 使用）
- [ ] ユニットテスト（20+ ケース：マッチ/非マッチ、複数ポリシー、境界値）

**検証**:
```bash
make test tests/unit/test_policy_engine.py
```

**インターフェイス（他エージェントと合意）**:
```python
def evaluate(
    event: CostEvent,
    policies: List[GuardrailPolicy]
) -> ActionPlan:
    """純粋関数: 副作用なし、テスト容易"""
    pass
```

---

### エージェント A1-2: Slack Notifier
**期間**: 1時間
**依存**: A0-3完了
**成果物**:
```
src/guardrails/notifier_slack.py
tests/unit/test_notifier_slack.py
```

**タスク**:
- [ ] `SlackNotifier` クラス
  - `send_alert(event: CostEvent, plan: ActionPlan) -> bool`
  - Slack Block Kit フォーマット（リッチな通知）
  - 承認ボタン付きメッセージ（後で有効化）
- [ ] 環境変数 `SLACK_WEBHOOK_URL` からwebhook取得
- [ ] リトライ機構（最大3回、exponential backoff）
- [ ] モック可能な設計（`requests` ライブラリのモック）
- [ ] ユニットテスト（payload検証、リトライ、エラーハンドリング）

**検証**:
```bash
make test tests/unit/test_notifier_slack.py
```

**インターフェイス**:
```python
class SlackNotifier:
    def send_alert(
        self,
        event: CostEvent,
        plan: ActionPlan
    ) -> bool:
        """Slack通知送信（成功/失敗を返す）"""
        pass
```

---

### エージェント A1-3: AWS Budgets Event Handler
**期間**: 1.5時間
**依存**: A0-3完了
**成果物**:
```
src/guardrails/handlers/budgets_event.py
tests/unit/test_budgets_event.py
events/sample-budgets-event.json
```

**タスク**:
- [ ] `BudgetsEventHandler` クラス
  - `parse_sns_event(event: dict) -> CostEvent`（SNS→CostEventの変換）
  - `lambda_handler(event, context)`（Lambda関数エントリポイント）
- [ ] サンプルイベント `events/sample-budgets-event.json` 作成
- [ ] エラーハンドリング（不正な形式、欠損フィールド）
- [ ] ユニットテスト（正常系、異常系、境界値）

**検証**:
```bash
make test tests/unit/test_budgets_event.py
python -m src.guardrails.handlers.budgets_event events/sample-budgets-event.json
```

**インターフェイス**:
```python
def parse_sns_event(event: dict) -> CostEvent:
    """SNSイベントをCostEventに変換"""
    pass
```

---

### エージェント A1-4: 統合ハンドラ（Phase 1完成）
**期間**: 1時間
**依存**: A1-1, A1-2, A1-3完了
**成果物**:
```
src/guardrails/handlers/cost_alert_handler.py
tests/integration/test_e2e_phase1.py
```

**タスク**:
- [ ] `cost_alert_handler.lambda_handler`（統合ロジック）
  - BudgetsEvent → PolicyEngine → SlackNotifier
- [ ] 環境変数読み込み（`POLICY_DIR`, `SLACK_WEBHOOK_URL`）
- [ ] エラー時の通知（Slackに失敗メッセージ送信）
- [ ] 統合テスト（E2E: イベント受信→通知送信）

**検証**:
```bash
make test tests/integration/test_e2e_phase1.py
make run-local  # LocalStack不要、モックで実行
```

---

## 3. Phase 2: Manual Approval（承認→実行）【並行度: 中】

### エージェント A2-1: IAM Executor（コア）
**期間**: 2時間
**依存**: A0-3完了
**成果物**:
```
src/guardrails/executor_iam.py
tests/unit/test_executor_iam.py
docs/iam-permissions.md
```

**タスク**:
- [ ] `IAMExecutor` クラス
  - `execute_action(plan: ActionPlan) -> ActionExecution`
  - `attach_deny_policy(principal_arn, deny_actions) -> dict`
  - `detach_deny_policy(principal_arn, policy_arn) -> dict`
  - `create_managed_policy(policy_name, deny_actions) -> str`（動的ポリシー生成）
- [ ] **Dry-run チェック**（`plan.mode != "auto"` なら実行しない）
- [ ] ロールバック情報の記録（`ActionExecution.diff` に保存）
- [ ] boto3モック（moto使用）によるユニットテスト
- [ ] 必要なIAM権限を `docs/iam-permissions.md` に文書化

**検証**:
```bash
make test tests/unit/test_executor_iam.py
```

**インターフェイス**:
```python
class IAMExecutor:
    def execute_action(
        self,
        plan: ActionPlan
    ) -> ActionExecution:
        """ガードレール実行（Dry-runチェック含む）"""
        pass
```

---

### エージェント A2-2: Audit Store（DynamoDB）
**期間**: 1.5時間
**依存**: A0-3完了
**成果物**:
```
src/guardrails/audit_store.py
tests/unit/test_audit_store.py
infra/dynamodb-table.yaml
```

**タスク**:
- [ ] `AuditStore` クラス
  - `save_execution(execution: ActionExecution) -> bool`
  - `get_execution(execution_id: str) -> ActionExecution`
  - `list_executions(filters: dict) -> List[ActionExecution]`
- [ ] DynamoDB テーブル設計（PK: execution_id, SK: timestamp, GSI: policy_id）
- [ ] CloudFormation テンプレート `infra/dynamodb-table.yaml`
- [ ] boto3モック（moto-dynamodb）によるユニットテスト

**検証**:
```bash
make test tests/unit/test_audit_store.py
```

**インターフェイス**:
```python
class AuditStore:
    def save_execution(
        self,
        execution: ActionExecution
    ) -> bool:
        """実行履歴を保存"""
        pass
```

---

### エージェント A2-3: Approval Webhook
**期間**: 2時間
**依存**: A2-1, A2-2完了
**成果物**:
```
src/guardrails/handlers/approval_webhook.py
tests/unit/test_approval_webhook.py
```

**タスク**:
- [ ] `ApprovalWebhookHandler` クラス
  - `lambda_handler(event, context)`（API Gateway統合）
  - 署名付きURL検証（HMAC-SHA256）
  - 承認トークンの期限チェック（TTL: 1時間）
- [ ] 承認後の実行フロー
  - AuditStore から ActionPlan取得
  - IAMExecutor で実行
  - Slack に結果通知
- [ ] ユニットテスト（署名検証、期限切れ、不正トークン）

**検証**:
```bash
make test tests/unit/test_approval_webhook.py
```

---

### エージェント A2-4: 統合（Phase 2完成）
**期間**: 1時間
**依存**: A2-1, A2-2, A2-3完了
**成果物**:
```
tests/integration/test_e2e_phase2.py
docs/runbook-approval.md
```

**タスク**:
- [ ] E2Eテスト（イベント→通知→承認→実行→監査ログ）
- [ ] `docs/runbook-approval.md`（承認フローの運用手順）

**検証**:
```bash
make test tests/integration/test_e2e_phase2.py
```

---

## 4. Phase 3: Auto Mode（自動実行）【並行度: 中】

### エージェント A3-1: TTL解除機構
**期間**: 2時間
**依存**: A2-1, A2-2完了
**成果物**:
```
src/guardrails/handlers/ttl_cleanup.py
tests/unit/test_ttl_cleanup.py
infra/eventbridge-rule.yaml
```

**タスク**:
- [ ] `TTLCleanupHandler` クラス
  - `lambda_handler(event, context)`（EventBridge scheduled event）
  - AuditStore から期限切れの ActionExecution 取得
  - IAMExecutor で rollback 実行
- [ ] EventBridge Rule（毎5分実行）
- [ ] ロールバック失敗時のリトライ機構
- [ ] ユニットテスト

**検証**:
```bash
make test tests/unit/test_ttl_cleanup.py
```

---

### エージェント A3-2: Allowlist/Exception機構
**期間**: 1.5時間
**依存**: A1-1完了
**成果物**:
```
src/guardrails/exception_matcher.py
tests/unit/test_exception_matcher.py
policies/example-with-exceptions.yaml
```

**タスク**:
- [ ] `ExceptionMatcher` クラス
  - `is_exempted(event: CostEvent, policy: GuardrailPolicy) -> bool`
  - アカウントID例外
  - 時間帯例外（例：営業時間内は実行しない）
  - タグベース例外
- [ ] PolicyEngine への統合
- [ ] ユニットテスト（各例外タイプ）

**検証**:
```bash
make test tests/unit/test_exception_matcher.py
```

---

### エージェント A3-3: Auto Mode統合
**期間**: 1時間
**依存**: A3-1, A3-2完了
**成果物**:
```
tests/integration/test_e2e_phase3.py
docs/safety-checklist-auto.md
```

**タスク**:
- [ ] mode="auto" の実行フロー統合
- [ ] Safety checklist（自動実行前の確認項目）
- [ ] E2Eテスト（自動実行→TTL解除）

**検証**:
```bash
make test tests/integration/test_e2e_phase3.py
```

---

## 5. Infrastructure（並行実行可能）【並行度: 高】

### エージェント I-1: CDK/CloudFormation
**期間**: 3時間
**依存**: A0-3完了（型定義参照）
**成果物**:
```
infra/cdk/
  ├── app.py
  ├── stacks/
  │   ├── lambda_stack.py
  │   ├── dynamodb_stack.py
  │   └── eventbridge_stack.py
  └── requirements.txt
```

**タスク**:
- [ ] CDK プロジェクト初期化
- [ ] Lambda関数スタック（Budgets handler, Approval handler, TTL handler）
- [ ] DynamoDB テーブル
- [ ] EventBridge Rule（TTL cleanup）
- [ ] IAM Role（最小権限）
- [ ] SAM Local対応（ローカルテスト用）

**検証**:
```bash
cd infra/cdk
cdk synth
cdk deploy --dry-run
```

---

### エージェント I-2: Terraform（代替）
**期間**: 3時間
**依存**: A0-3完了
**成果物**:
```
infra/terraform/
  ├── main.tf
  ├── lambda.tf
  ├── dynamodb.tf
  └── variables.tf
```

**タスク**:
- [ ] Terraform設定（CDKと同等の構成）
- [ ] 変数化（アカウントID、リージョン、Slack webhook）

**検証**:
```bash
cd infra/terraform
terraform plan
```

**注意**: CDKとTerraformは排他的（どちらか一方を選択）

---

## 6. 並行実行スケジュール（推奨タイムライン）

### Day 1（Phase 0: 足場）
| 時間 | A0-1 | A0-2 | A0-3 |
|------|------|------|------|
| 00:00-00:30 | プロジェクト設定 | ドキュメント基盤 | 待機 |
| 00:30-01:15 | 完了 | 完了 | **共通型定義** |
| 01:15-02:00 | レビュー | レビュー | ユニットテスト |

**マイルストーン**: `models.py` 確定 → Phase 1 開始可能

---

### Day 2-3（Phase 1: Free Tier）
| エージェント | タスク | 並行可能 |
|-------------|--------|---------|
| A1-1 | Policy Engine | ✅ 独立 |
| A1-2 | Slack Notifier | ✅ 独立 |
| A1-3 | Budgets Handler | ✅ 独立 |

**並行実行**: 全て同時スタート可能
**統合**: A1-4（1時間）で3つを結合

**マイルストーン**: E2Eテスト通過 → **MVP Free Tier完成**

---

### Day 4-5（Phase 2: Manual Approval）
| エージェント | タスク | 並行可能 | 依存 |
|-------------|--------|---------|------|
| A2-1 | IAM Executor | ✅ 独立 | models.py |
| A2-2 | Audit Store | ✅ 独立 | models.py |
| A2-3 | Approval Webhook | ⚠️ 部分的 | A2-1, A2-2 |

**並行実行**: A2-1, A2-2 同時スタート → A2-3

**マイルストーン**: E2Eテスト通過 → **MVP Pro Tier完成**

---

### Day 6（Phase 3: Auto Mode）
| エージェント | タスク | 並行可能 |
|-------------|--------|---------|
| A3-1 | TTL解除 | ✅ 独立 |
| A3-2 | Exception機構 | ✅ 独立 |

**並行実行**: 同時スタート可能

**マイルストーン**: E2Eテスト通過 → **Full MVP完成**

---

### Day 7-8（Infrastructure）
| エージェント | タスク | 並行可能 |
|-------------|--------|---------|
| I-1 | CDK | ✅ 独立 |
| I-2 | Terraform | ✅ 独立（排他的） |

**並行実行**: どちらか一方を選択

**マイルストーン**: `cdk deploy` 成功 → **デプロイ可能**

---

## 7. エージェント間コミュニケーション（Integration Points）

### 7.1 共有成果物（先に確定必須）
1. **`src/guardrails/models.py`**（Phase 0: A0-3）
   - 全エージェントが参照
   - 変更時は全エージェントに通知

2. **`policies/*.yaml` 仕様**（Phase 0: A0-2）
   - Policy Engine（A1-1）と Exception Matcher（A3-2）が参照

3. **環境変数命名規則**（Phase 0: A0-1）
   - 全Lambda関数で統一（例：`SLACK_WEBHOOK_URL`, `POLICY_DIR`, `DYNAMODB_TABLE_NAME`）

### 7.2 インターフェイス契約（Interface Contract）
各エージェントは、自分が提供する関数のシグネチャを `docs/interfaces.md` に記載する。

例:
```python
# PolicyEngine (A1-1)
def evaluate(event: CostEvent, policies: List[GuardrailPolicy]) -> ActionPlan

# IAMExecutor (A2-1)
def execute_action(plan: ActionPlan) -> ActionExecution

# AuditStore (A2-2)
def save_execution(execution: ActionExecution) -> bool
```

### 7.3 統合テスト担当
- **A1-4**: Phase 1統合
- **A2-4**: Phase 2統合
- **A3-3**: Phase 3統合

統合エージェントは、他のエージェントの成果物を**変更しない**（モック/スタブで統合テストのみ）

---

## 8. コンフリクト解決プロトコル

### 8.1 ファイルコンフリクト
- **禁止**: 複数エージェントが同じファイルを同時編集
- **許可**: 新規ファイル作成は並行OK
- **例外**: `models.py` は A0-3 のみが編集（他は読み取り専用）

### 8.2 仕様変更
- 型定義やインターフェイスの変更は `docs/interfaces.md` で提案
- 全エージェントの合意後に実施
- 変更時は影響を受ける全エージェントに通知

### 8.3 依存関係の追加
- `pyproject.toml` への依存追加は A0-1 のみが実施
- 他のエージェントは必要なライブラリを `docs/dependencies.md` に記載

---

## 9. 並行実装の成功指標（Success Metrics）

### 9.1 速度
- **Phase 0**: 2時間以内
- **Phase 1**: 1日以内（並行実行で3時間→1日）
- **Phase 2**: 1日以内
- **Phase 3**: 0.5日以内
- **Infrastructure**: 1日以内
- **合計**: 4日以内（逐次実行なら7日）

### 9.2 品質
- 全ユニットテストが通過
- カバレッジ 80%以上
- lint/format エラー 0

### 9.3 統合
- E2Eテスト（Phase 1, 2, 3）が全て通過
- ドキュメントが完全（README, docs/*, runbooks）

---

## 10. エージェント起動コマンド（実行例）

### Phase 0（同時起動）
```bash
# ターミナル1
claude --task="A0-1: プロジェクト設定" --output="phase0-a1.log"

# ターミナル2
claude --task="A0-2: ドキュメント基盤" --output="phase0-a2.log"

# ターミナル3（A0-1完了を待つ）
claude --task="A0-3: 共通型定義（A0-1完了後）" --output="phase0-a3.log"
```

### Phase 1（完全並行）
```bash
# 全て同時起動
claude --task="A1-1: Policy Engine" --output="phase1-a1.log" &
claude --task="A1-2: Slack Notifier" --output="phase1-a2.log" &
claude --task="A1-3: Budgets Handler" --output="phase1-a3.log" &
wait

# 統合
claude --task="A1-4: 統合ハンドラ" --output="phase1-a4.log"
```

---

## 11. 最後に（Parallel Execution Mantra）

> **"独立して作り、最後に繋ぐ（Build Independently, Integrate Last）"**

- 各エージェントは自分のドメインに集中
- インターフェイスを守る
- テストを書く
- 統合は専任エージェントに任せる

この原則を守れば、**4日でMVP完成**は実現可能。
