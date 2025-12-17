# 実用的なワークフロー（Practical Workflow）

## 0. 前提

Claude Codeは通常の対話型ツールであり、`--agent-id`のようなバッチモードオプションはサポートしていません。
このドキュメントでは、**実際に使える**ワークフローを提案します。

---

## 1. 推奨アプローチ: 段階的な逐次実装

### Phase 0: 基盤構築（2時間）

**ターミナル1（Claude Code）**:
```
Phase 0を開始します。以下を実装してください：

A0-1: プロジェクト設定
- pyproject.toml の作成
- .gitignore の作成
- ruff.toml の作成
- pytest.ini の作成
- README.md の初版

参考: docs/parallel-implementation-plan.md の「エージェント A0-1」セクション
```

完了後、同じターミナルで続行：
```
Phase 0を続けます。以下を実装してください：

A0-2: ドキュメント基盤
- docs/product.md の作成
- docs/safety.md の作成
- docs/policies.md の作成
- docs/architecture.md の作成

参考: docs/parallel-implementation-plan.md の「エージェント A0-2」セクション
```

完了後、検証：
```powershell
.\scripts\make.ps1 setup
```

最後に最優先タスク：
```
Phase 0の最終ステップです。以下を実装してください：

A0-3: 共通型定義（最優先）
- src/guardrails/models.py の実装
- tests/unit/test_models.py の実装

この型定義は全モジュールが参照するため、完璧に仕上げてください。

参考: docs/parallel-implementation-plan.md の「エージェント A0-3」セクション
```

検証：
```powershell
.\scripts\make.ps1 test
```

---

### Phase 1: Free Tier（3-4時間）

**オプション1: 逐次実装（シンプル、推奨）**

ターミナル1で順次実行：
```
Phase 1 タスク1: Policy Engine
- src/guardrails/policy_engine.py の実装
- tests/unit/test_policy_engine.py の実装
- policies/example-dry-run.yaml の作成

参考: docs/parallel-implementation-plan.md の「エージェント A1-1」セクション
```

完了後：
```
Phase 1 タスク2: Slack Notifier
- src/guardrails/notifier_slack.py の実装
- tests/unit/test_notifier_slack.py の実装

参考: docs/parallel-implementation-plan.md の「エージェント A1-2」セクション
```

完了後：
```
Phase 1 タスク3: Budgets Event Handler
- src/guardrails/handlers/budgets_event.py の実装
- tests/unit/test_budgets_event.py の実装
- events/sample-budgets-event.json の作成

参考: docs/parallel-implementation-plan.md の「エージェント A1-3」セクション
```

最後に統合：
```
Phase 1 タスク4: 統合ハンドラ
- src/guardrails/handlers/cost_alert_handler.py の実装
- tests/integration/test_e2e_phase1.py の実装

これまでのモジュール（PolicyEngine, SlackNotifier, BudgetsEventHandler）を統合してください。

参考: docs/parallel-implementation-plan.md の「エージェント A1-4」セクション
```

検証：
```powershell
.\scripts\make.ps1 test
```

---

**オプション2: 真の並行実行（上級者向け）**

Windows Terminalで3つのタブを開き、それぞれで別のタスクを同時実行：

**タブ1（Policy Engine）**:
```
Phase 1 タスク1: Policy Engine の実装をお願いします
参考: docs/parallel-implementation-plan.md の「エージェント A1-1」
```

**タブ2（Slack Notifier）**:
```
Phase 1 タスク2: Slack Notifier の実装をお願いします
参考: docs/parallel-implementation-plan.md の「エージェント A1-2」
```

**タブ3（Budgets Handler）**:
```
Phase 1 タスク3: Budgets Event Handler の実装をお願いします
参考: docs/parallel-implementation-plan.md の「エージェント A1-3」
```

3つ全て完了後、タブ1で統合：
```
Phase 1 タスク4: 統合ハンドラの実装をお願いします
参考: docs/parallel-implementation-plan.md の「エージェント A1-4」
```

---

## 2. タスク管理テンプレート

各Phaseの開始時に、Claude Codeに以下のようなプロンプトを送信：

```
# Phase X タスクリスト

以下のタスクを順次実装してください。各タスク完了ごとに、テストを実行して動作確認します。

## タスク1: [タスク名]
**成果物**:
- file1.py
- file2.py
- test_file1.py

**参考ドキュメント**:
- docs/parallel-implementation-plan.md の「エージェント AX-1」セクション

**完了条件**:
- .\scripts\make.ps1 test-unit が通る
- .\scripts\make.ps1 lint でエラーが出ない

---

実装をお願いします。
```

---

## 3. 推奨ワークフロー（現実的なタイムライン）

### Day 1: Phase 0（基盤構築）
- **午前**: A0-1（プロジェクト設定）+ A0-2（ドキュメント基盤）
- **午後**: A0-3（共通型定義）+ テスト
- **成果**: `models.py` が完成し、全ての基盤が整う

### Day 2: Phase 1（Free Tier）
- **午前**: A1-1（Policy Engine）+ A1-2（Slack Notifier）
- **午後**: A1-3（Budgets Handler）+ A1-4（統合）
- **夕方**: E2Eテスト + ドキュメント更新
- **成果**: MVP Free Tier完成（異常検知→Slack通知が動く）

### Day 3: Phase 2（Manual Approval）
- **午前**: A2-1（IAM Executor）+ A2-2（Audit Store）
- **午後**: A2-3（Approval Webhook）+ A2-4（統合）
- **成果**: 承認フロー完成（通知→承認→実行→監査ログ）

### Day 4: Phase 3（Auto Mode）
- **午前**: A3-1（TTL解除）+ A3-2（Exception機構）
- **午後**: A3-3（統合）+ ドキュメント完成
- **成果**: Full MVP完成

### Day 5: Infrastructure
- **終日**: CDK/Terraform実装 + ローカルテスト
- **成果**: デプロイ可能なテンプレート完成

---

## 4. チェックリスト（各Phase完了時）

### Phase 0 完了チェック
```powershell
# ファイル存在確認
Test-Path pyproject.toml
Test-Path src/guardrails/models.py
Test-Path tests/unit/test_models.py
Test-Path docs/product.md
Test-Path docs/safety.md

# テスト実行
.\scripts\make.ps1 setup
.\scripts\make.ps1 test

# Git コミット
git add .
git commit -m "Phase 0: Foundation complete"
```

### Phase 1 完了チェック
```powershell
# ファイル存在確認
Test-Path src/guardrails/policy_engine.py
Test-Path src/guardrails/notifier_slack.py
Test-Path src/guardrails/handlers/budgets_event.py
Test-Path src/guardrails/handlers/cost_alert_handler.py
Test-Path tests/integration/test_e2e_phase1.py

# テスト実行
.\scripts\make.ps1 test

# E2Eテスト
pytest tests/integration/test_e2e_phase1.py -v

# Git コミット
git add .
git commit -m "Phase 1: Free Tier MVP complete"
```

### Phase 2 完了チェック
```powershell
# ファイル存在確認
Test-Path src/guardrails/executor_iam.py
Test-Path src/guardrails/audit_store.py
Test-Path src/guardrails/handlers/approval_webhook.py
Test-Path tests/integration/test_e2e_phase2.py

# テスト実行
.\scripts\make.ps1 test
pytest tests/integration/test_e2e_phase2.py -v

# Git コミット
git add .
git commit -m "Phase 2: Manual Approval complete"
```

---

## 5. トラブルシューティング

### テストが失敗する
```powershell
# 詳細表示
pytest tests/unit/test_models.py -v -s

# 特定のテストのみ実行
pytest tests/unit/test_models.py::test_cost_event_validation -v

# デバッグモード
pytest tests/unit/test_models.py --pdb
```

### lintエラーが出る
```powershell
# 自動修正
.\scripts\make.ps1 lint

# フォーマット
.\scripts\make.ps1 fmt

# 再チェック
ruff check src/ tests/
```

### インポートエラー
```powershell
# 再インストール
.\scripts\make.ps1 setup

# または
pip install -e .
```

---

## 6. Claude Codeへの効果的なプロンプト例

### 良い例✅
```
Phase 1のタスク1（Policy Engine）を実装してください。

**実装内容**:
- src/guardrails/policy_engine.py
  - PolicyEngine クラス
  - evaluate() メソッド（純粋関数）
  - YAML policy loader
- tests/unit/test_policy_engine.py
  - 20+ケースのユニットテスト
- policies/example-dry-run.yaml
  - サンプルポリシー

**参考**:
- docs/parallel-implementation-plan.md の「エージェント A1-1」セクション
- docs/policies.md のYAML仕様
- src/guardrails/models.py の型定義

**完了条件**:
- pytest tests/unit/test_policy_engine.py が全て通る
- ruff check でエラーなし

順番に実装していきましょう。まず PolicyEngine クラスの基本構造から始めてください。
```

### 悪い例❌
```
A1-1を実装して
```
（情報が不足、Claude Codeは何をすべきか分からない）

---

## 7. 最速で完成させるコツ

### 1. 一度に1つのタスクに集中
複数タブで並行実行しようとせず、1つずつ確実に完成させる方が結果的に速い。

### 2. テストを先に書く（TDD）
```
Policy Engine を実装する前に、まず tests/unit/test_policy_engine.py を書いてください。
テストケース:
1. イベントがポリシーにマッチする
2. イベントがポリシーにマッチしない
3. 複数ポリシーがある場合
4. 金額の境界値チェック
...
```

### 3. 小さくコミット
各ファイル完成ごとにコミット：
```powershell
git add src/guardrails/policy_engine.py
git commit -m "Add PolicyEngine class with evaluate() method"

git add tests/unit/test_policy_engine.py
git commit -m "Add PolicyEngine unit tests (20 cases)"
```

### 4. ドキュメントを活用
Claude Codeに常に参照ドキュメントを明示：
```
参考ドキュメント:
- docs/parallel-implementation-plan.md
- docs/safety.md
- docs/policies.md
```

---

## 8. まとめ

### 理想（並行実装プラン）
- 複数エージェントが同時に作業
- 4日で完成

### 現実（このワークフロー）
- 1人のClaude Codeが順次作業
- 5-6日で完成（それでも十分速い！）

### 重要なのは
✅ 段階的に確実に進める
✅ 各ステップでテストを通す
✅ ドキュメントを参照しながら実装
✅ 小さくコミット

---

## 9. 次のステップ

Phase 0を開始する準備ができました。以下のプロンプトをClaude Codeに送信してください：

```
Phase 0: 基盤構築を開始します。

タスク A0-1: プロジェクト設定
以下のファイルを作成してください：
- pyproject.toml（Python 3.11+, pydantic, boto3, pytest, ruff）
- .gitignore（.venv, __pycache__, .env, *.pyc, .pytest_cache, .ruff_cache）
- ruff.toml（line-length=100, target-version="py311"）
- pytest.ini（testpaths = tests, python_files = test_*.py）
- README.md（プロジェクト概要とセットアップ手順）

参考: docs/parallel-implementation-plan.md の「エージェント A0-1」セクション

完了したら .\scripts\make.ps1 setup で検証します。
```
