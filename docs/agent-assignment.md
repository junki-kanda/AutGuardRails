# エージェント割り当てガイド（Agent Assignment Guide）

## 0. このドキュメントの目的

複数のClaudeエージェントを起動して並行作業を行う際の、**タスク割り当て・起動順序・依存関係管理**の実務ガイド。

---

## 1. エージェントタイプの定義

### 1.1 Foundation Agent（基盤エージェント）
- **役割**: プロジェクト設定・共通型定義・ドキュメント基盤
- **特徴**: 他のエージェントが依存する成果物を作る
- **並行性**: 部分的（A0-1とA0-2は並行可、A0-3はA0-1待ち）

### 1.2 Feature Agent（機能エージェント）
- **役割**: 独立した機能モジュールの実装
- **特徴**: `models.py` 以外のファイルは読み取り専用で参照
- **並行性**: 高い（Phase 1では3つ同時実行可）

### 1.3 Integration Agent（統合エージェント）
- **役割**: Feature Agentの成果物を統合し、E2Eテストを実装
- **特徴**: 既存コードは**変更しない**（新規ファイルのみ作成）
- **並行性**: 低い（Feature Agent完了を待つ）

### 1.4 Infrastructure Agent（インフラエージェント）
- **役割**: AWS CDK/Terraform/CloudFormationの実装
- **特徴**: アプリケーションコードとは独立
- **並行性**: 非常に高い（Phase 1-3と完全並行可）

---

## 2. Phase別エージェント起動手順

### Phase 0: Foundation（所要時間: 2時間）

#### ステップ0-1: 並行起動（A0-1, A0-2）
```bash
# ターミナル1
claude --agent-id="A0-1-foundation" \
  --task-file="docs/parallel-implementation-plan.md#agent-a0-1" \
  --output-log="logs/phase0-a0-1.log" \
  &

# ターミナル2
claude --agent-id="A0-2-foundation" \
  --task-file="docs/parallel-implementation-plan.md#agent-a0-2" \
  --output-log="logs/phase0-a0-2.log" \
  &

# 完了待機
wait
```

**検証**:
```bash
make setup
make lint
```

#### ステップ0-2: 逐次起動（A0-3）
A0-1完了を確認してから起動:
```bash
# A0-1の成果物確認
test -f pyproject.toml || exit 1
test -f Makefile || exit 1

# A0-3起動（最優先タスク）
claude --agent-id="A0-3-models" \
  --task-file="docs/parallel-implementation-plan.md#agent-a0-3" \
  --priority="CRITICAL" \
  --output-log="logs/phase0-a0-3.log"
```

**検証**:
```bash
make test tests/unit/test_models.py
```

**マイルストーン**: `models.py` 確定 ✅

---

### Phase 1: Free Tier（所要時間: 3-4時間）

#### ステップ1-1: 完全並行起動（A1-1, A1-2, A1-3）
A0-3完了を確認してから**3つ同時起動**:
```bash
# 事前確認
test -f src/guardrails/models.py || exit 1

# 並行起動（3並列）
claude --agent-id="A1-1-policy-engine" \
  --task-file="docs/parallel-implementation-plan.md#agent-a1-1" \
  --output-log="logs/phase1-a1-1.log" \
  &

claude --agent-id="A1-2-slack-notifier" \
  --task-file="docs/parallel-implementation-plan.md#agent-a1-2" \
  --output-log="logs/phase1-a1-2.log" \
  &

claude --agent-id="A1-3-budgets-handler" \
  --task-file="docs/parallel-implementation-plan.md#agent-a1-3" \
  --output-log="logs/phase1-a1-3.log" \
  &

# 完了待機
wait
```

**検証**:
```bash
make test tests/unit/test_policy_engine.py
make test tests/unit/test_notifier_slack.py
make test tests/unit/test_budgets_event.py
```

#### ステップ1-2: 統合（A1-4）
A1-1,2,3完了後に起動:
```bash
# 事前確認
test -f src/guardrails/policy_engine.py || exit 1
test -f src/guardrails/notifier_slack.py || exit 1
test -f src/guardrails/handlers/budgets_event.py || exit 1

# 統合エージェント起動
claude --agent-id="A1-4-integration" \
  --task-file="docs/parallel-implementation-plan.md#agent-a1-4" \
  --output-log="logs/phase1-a1-4.log"
```

**検証**:
```bash
make test tests/integration/test_e2e_phase1.py
```

**マイルストーン**: MVP Free Tier完成 ✅

---

### Phase 2: Manual Approval（所要時間: 4-5時間）

#### ステップ2-1: 並行起動（A2-1, A2-2）
```bash
claude --agent-id="A2-1-iam-executor" \
  --task-file="docs/parallel-implementation-plan.md#agent-a2-1" \
  --output-log="logs/phase2-a2-1.log" \
  &

claude --agent-id="A2-2-audit-store" \
  --task-file="docs/parallel-implementation-plan.md#agent-a2-2" \
  --output-log="logs/phase2-a2-2.log" \
  &

wait
```

**検証**:
```bash
make test tests/unit/test_executor_iam.py
make test tests/unit/test_audit_store.py
```

#### ステップ2-2: 逐次起動（A2-3）
A2-1,2完了後:
```bash
# 事前確認
test -f src/guardrails/executor_iam.py || exit 1
test -f src/guardrails/audit_store.py || exit 1

# 承認Webhook実装
claude --agent-id="A2-3-approval-webhook" \
  --task-file="docs/parallel-implementation-plan.md#agent-a2-3" \
  --output-log="logs/phase2-a2-3.log"
```

#### ステップ2-3: 統合（A2-4）
```bash
claude --agent-id="A2-4-integration" \
  --task-file="docs/parallel-implementation-plan.md#agent-a2-4" \
  --output-log="logs/phase2-a2-4.log"
```

**検証**:
```bash
make test tests/integration/test_e2e_phase2.py
```

**マイルストーン**: MVP Pro Tier完成 ✅

---

### Phase 3: Auto Mode（所要時間: 3時間）

#### ステップ3-1: 並行起動（A3-1, A3-2）
```bash
claude --agent-id="A3-1-ttl-cleanup" \
  --task-file="docs/parallel-implementation-plan.md#agent-a3-1" \
  --output-log="logs/phase3-a3-1.log" \
  &

claude --agent-id="A3-2-exception-matcher" \
  --task-file="docs/parallel-implementation-plan.md#agent-a3-2" \
  --output-log="logs/phase3-a3-2.log" \
  &

wait
```

#### ステップ3-2: 統合（A3-3）
```bash
claude --agent-id="A3-3-integration" \
  --task-file="docs/parallel-implementation-plan.md#agent-a3-3" \
  --output-log="logs/phase3-a3-3.log"
```

**マイルストーン**: Full MVP完成 ✅

---

### Phase 4: Infrastructure（所要時間: 3時間）

**Phase 1-3と並行実行可能**（アプリケーションコードと独立）

#### オプションA: CDK
```bash
claude --agent-id="I-1-cdk" \
  --task-file="docs/parallel-implementation-plan.md#agent-i-1" \
  --output-log="logs/infra-i1.log"
```

#### オプションB: Terraform
```bash
claude --agent-id="I-2-terraform" \
  --task-file="docs/parallel-implementation-plan.md#agent-i-2" \
  --output-log="logs/infra-i2.log"
```

**注意**: CDKとTerraformは**排他的**（どちらか一方のみ選択）

---

## 3. エージェント間コンフリクト管理

### 3.1 ファイル編集ルール

| ファイル | 編集可能エージェント | 他エージェントの権限 |
|---------|---------------------|---------------------|
| `src/guardrails/models.py` | A0-3のみ | 読み取り専用 |
| `pyproject.toml` | A0-1のみ | 読み取り専用 |
| `Makefile` | A0-1のみ | 読み取り専用 |
| `policies/*.yaml` | 各Feature Agent | 自分のファイルのみ編集 |
| `tests/unit/test_*.py` | 対応するFeature Agent | 読み取り専用 |
| `tests/integration/*.py` | Integration Agentのみ | 読み取り専用 |

### 3.2 依存関係の追加

新しいPythonライブラリが必要な場合:

1. Feature Agentは `docs/dependencies.md` に記載
   ```markdown
   ## A1-2が必要とする依存
   - requests==2.31.0 (Slack webhook用)
   ```

2. A0-1（または人間）が `pyproject.toml` を更新

3. 全エージェントに通知（`make setup` 再実行）

### 3.3 型定義の変更

`models.py` の変更が必要な場合:

1. Feature Agentは `docs/interfaces.md` に提案を記載
2. 全エージェントの合意を得る（人間が仲裁）
3. A0-3が変更を実施
4. 影響を受けるエージェントがコードを修正

---

## 4. エージェント状態管理

### 4.1 状態ファイル（`status.json`）

各エージェントは完了時に状態を記録:
```json
{
  "agent_id": "A1-1-policy-engine",
  "status": "completed",
  "completed_at": "2025-01-15T10:30:00Z",
  "artifacts": [
    "src/guardrails/policy_engine.py",
    "tests/unit/test_policy_engine.py",
    "policies/example-dry-run.yaml"
  ],
  "tests_passed": true,
  "lint_passed": true
}
```

### 4.2 依存関係チェックスクリプト

```bash
#!/bin/bash
# scripts/check-dependencies.sh

check_agent_ready() {
  local agent_id=$1
  local status_file="status/${agent_id}.json"

  if [ ! -f "$status_file" ]; then
    echo "❌ $agent_id not completed"
    return 1
  fi

  status=$(jq -r '.status' "$status_file")
  if [ "$status" != "completed" ]; then
    echo "❌ $agent_id status: $status"
    return 1
  fi

  echo "✅ $agent_id ready"
  return 0
}

# 使用例
check_agent_ready "A0-3-models" || exit 1
echo "models.py is ready, starting Phase 1 agents..."
```

---

## 5. トラブルシューティング

### 5.1 エージェントがブロックされる

**症状**: エージェントが依存ファイルが無いと報告

**対処**:
1. 依存元エージェントの `status.json` を確認
2. 成果物ファイルの存在を確認
   ```bash
   ls -la src/guardrails/models.py
   ```
3. 必要に応じて依存元エージェントを再実行

### 5.2 テストが失敗する

**症状**: `make test` でエラー

**対処**:
1. 該当エージェントのログを確認
   ```bash
   tail -n 100 logs/phase1-a1-1.log
   ```
2. `models.py` の型定義が最新か確認
3. 必要に応じて該当エージェントを再実行

### 5.3 ファイルコンフリクト

**症状**: Gitでコンフリクトが発生

**対処**:
1. **即座に全エージェントを停止**
2. 人間がコンフリクトを解決
3. 該当エージェントを順次再起動（並行ではなく逐次で）

---

## 6. 並行実行のベストプラクティス

### 6.1 最適なエージェント数

- **Phase 0**: 2-3エージェント（A0-1,2同時、A0-3は待機）
- **Phase 1**: 3エージェント（A1-1,2,3同時）
- **Phase 2**: 2エージェント（A2-1,2同時）
- **Phase 3**: 2エージェント（A3-1,2同時）
- **Infrastructure**: 1エージェント（CDK or Terraform）

**合計最大**: 4-5エージェント同時実行（Phase 1 + Infrastructure）

### 6.2 リソース消費

各エージェントの推定リソース:
- **CPU**: 1-2コア
- **メモリ**: 2-4GB
- **ディスク**: 100MB（ログ含む）

推奨マシンスペック（5エージェント同時実行）:
- **CPU**: 8コア以上
- **メモリ**: 16GB以上
- **ディスク**: 10GB以上の空き

### 6.3 ログ管理

```bash
# ログディレクトリ構造
logs/
  ├── phase0-a0-1.log
  ├── phase0-a0-2.log
  ├── phase0-a0-3.log
  ├── phase1-a1-1.log
  └── ...

# リアルタイムモニタリング
tail -f logs/*.log | grep -E "(ERROR|COMPLETED|FAILED)"
```

---

## 7. 実行スクリプト例

### 7.1 Phase 0完全自動化
```bash
#!/bin/bash
# scripts/run-phase0.sh

set -e

echo "🚀 Starting Phase 0: Foundation"

# Step 1: A0-1, A0-2並行
echo "Step 1: Starting A0-1, A0-2..."
claude --agent-id="A0-1" --task-file="docs/parallel-implementation-plan.md#agent-a0-1" &
PID_A01=$!
claude --agent-id="A0-2" --task-file="docs/parallel-implementation-plan.md#agent-a0-2" &
PID_A02=$!

wait $PID_A01 $PID_A02
echo "✅ A0-1, A0-2 completed"

# Step 2: 検証
make setup || exit 1

# Step 3: A0-3実行
echo "Step 2: Starting A0-3 (CRITICAL)..."
claude --agent-id="A0-3" --task-file="docs/parallel-implementation-plan.md#agent-a0-3"

# Step 4: 検証
make test tests/unit/test_models.py || exit 1

echo "✅ Phase 0 completed"
```

### 7.2 Phase 1完全自動化
```bash
#!/bin/bash
# scripts/run-phase1.sh

set -e

# 事前確認
test -f src/guardrails/models.py || { echo "❌ models.py not found"; exit 1; }

echo "🚀 Starting Phase 1: Free Tier"

# Step 1: A1-1,2,3並行
claude --agent-id="A1-1" --task-file="docs/parallel-implementation-plan.md#agent-a1-1" &
PID_A11=$!
claude --agent-id="A1-2" --task-file="docs/parallel-implementation-plan.md#agent-a1-2" &
PID_A12=$!
claude --agent-id="A1-3" --task-file="docs/parallel-implementation-plan.md#agent-a1-3" &
PID_A13=$!

wait $PID_A11 $PID_A12 $PID_A13
echo "✅ A1-1, A1-2, A1-3 completed"

# Step 2: ユニットテスト
make test tests/unit/ || exit 1

# Step 3: 統合
claude --agent-id="A1-4" --task-file="docs/parallel-implementation-plan.md#agent-a1-4"

# Step 4: E2Eテスト
make test tests/integration/test_e2e_phase1.py || exit 1

echo "✅ Phase 1 completed - MVP Free Tier ready!"
```

---

## 8. 完全自動化（全Phase連続実行）

```bash
#!/bin/bash
# scripts/run-all-phases.sh

set -e

echo "🚀 Starting AutoGuardRails Full Implementation"

./scripts/run-phase0.sh
./scripts/run-phase1.sh
./scripts/run-phase2.sh
./scripts/run-phase3.sh

echo "✅ All phases completed!"
echo "📊 Running full test suite..."
make test

echo "🎉 AutoGuardRails MVP完成！"
echo "次のステップ: make deploy-dry-run"
```

---

## 9. まとめ（Quick Reference）

### エージェント起動順序（必須）
1. **Phase 0**: A0-1,2（並行） → A0-3（逐次） ← 🔥最優先
2. **Phase 1**: A1-1,2,3（並行） → A1-4（統合）
3. **Phase 2**: A2-1,2（並行） → A2-3 → A2-4（統合）
4. **Phase 3**: A3-1,2（並行） → A3-3（統合）
5. **Infrastructure**: I-1 or I-2（Phase 1-3と並行可）

### 並行実行のゴールデンルール
✅ **DO**:
- ファイル単位で担当を分離
- `models.py` を最優先で完成
- テストを必ず書く
- 状態を `status.json` に記録

❌ **DON'T**:
- 同じファイルを複数エージェントが編集
- 依存関係を無視して並行実行
- 統合テスト無しでPhaseを完了
- `models.py` を複数エージェントが変更

### 推定完成時間
- **逐次実行**: 7日間
- **並行実行**: **4日間**（43%短縮）

### 次のステップ
このガイドに従い、`scripts/run-all-phases.sh` を実行すれば、**完全自動でMVPが完成**する。
