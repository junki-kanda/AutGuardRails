# claude.md — Cost Guardrails (A2) / 異常課金 → 自動ガードレール

## 0. このリポジトリの目的（Mission）
AWS環境の**異常課金（コスト急増）**を早期に検知し、**安全に・段階的に・復旧可能な**ガードレール（制限/停止/隔離/承認フロー）を自動適用できるプロダクトを実装する。

- **PLG前提**：導入10〜30分で「価値（異常検知＋通知）」が出る
- **段階課金前提**：無料＝検知/通知、 有料＝自動アクション/承認フロー/監査証跡/高度なスコープ制御
- **Marketplace前提（将来）**：AWSアカウントにデプロイ可能なテンプレ（CloudFormation/CDK/Terraform）を持つ

---

## 1. 本エージェント（Claude Code）への作業方針（Autonomous Implementation Protocol）
あなた（Claude Code）は、以下を厳守して自律的に実装を進める。

### 1.1 優先順位
1) **安全性（誤停止/過剰制限の回避）**
2) **導入摩擦の低さ（read-onlyでまず価値）**
3) **可観測性（ログ/監査証跡/リカバリ）**
4) **拡張性（将来の有料機能を積みやすい構造）**

### 1.2 進め方（必須）
- 変更前に `README.md` / `docs/` / `infra/` / `src/` を読み、現状を把握する
- 実装は**小さく**分割し、各タスクごとにテスト・lintを通してから進む
- 仕様が曖昧なときは「安全側」に倒す（例：デフォルトはDry-run、停止はしない）
- IAM権限は最小化し、**明示的なOpt-in**が無い限り書き込みアクションは実行しない
- 秘密情報（AWSキー、Slack webhook、トークン）をコミットしない
- 既存コードのリファクタは可。ただし挙動互換とテスト維持を最優先

---

## 2. MVPの定義（最初に完成させる範囲）
### 2.1 MVPで提供すること（Must）
- **検知入力（どちらか片方でも良いが両対応が理想）**
  - (A) AWS Cost Anomaly Detection のイベント（または定期ポーリング）
  - (B) AWS Budgets の閾値イベント（実装容易）
- **通知**
  - Slack Incoming Webhook（最初はSlackのみ）
  - 通知テンプレ（異常額、期間、想定原因リンク、対応ボタンのURL）
- **ガードレール（Dry-run → 手動承認 → 自動）**
  - デフォルト：**Dry-run**（何もしない、通知と提案だけ）
  - 手動承認：承認されたら実行（最低限はWebhookでOK）
  - 自動：ポリシー条件に合えば実行
- **実行するガードレール（MVPは“安全で戻せる”ものに限定）**
  - ✅ IAMの「新規リソース作成の制限」（例：特定リージョン/サービスの `Create*` / `RunInstances` / `CreateNatGateway` など）
    - 実装方法：対象ロールへ **Deny Policyのアタッチ** or **Permission Boundaryの切替**
  - ✅ “Quarantine mode”として**特定のIAMロール/ユーザ/CIロール**のみ制限対象にできる
  - ✅ 解除（Rollback）機能（TTLで自動解除 or ワンクリック解除）
- **監査証跡**
  - いつ・誰が・何を根拠に・何を実行したかを記録（DynamoDB等）
- **最小の管理UI（UI無しでも良い）**
  - まずはCLI/JSON設定でOK（`policies/*.yaml` など）
  - 後からWeb UIを足せる構造にする

### 2.2 MVPでやらないこと（Non-goals）
- いきなりSCP（AWS Organizations）で全社強制（誤爆時の影響が大きい）
- 勝手にEC2停止/ASGスケール0などの**破壊的操作**（第二段階以降）
- 多クラウド（Azure/GCP）
- 高度な原因推定（Root cause analysis）— 最初はリンク集/ダッシュボードで十分
- エンタープライズSSO・複雑な権限管理（後回し）

---

## 3. プロダクト原則（Safety by Design）
ガードレールは「効く」より「**安全に効く**」が重要。以下を必須要件として実装する。

1) **デフォルトはDry-run**
2) **対象スコープを最小化**（アカウント全体ではなく、ロール/ユーザ/タグ/OUなどで絞る）
3) **解除が簡単**（TTL自動解除 + 明示解除API）
4) **例外（Allowlist）**を持てる（特定アカウント/ロール/時間帯）
5) **二段階承認**（少なくとも「通知→承認→実行」を用意）
6) **必ず監査ログ**（誰が、なぜ、何を、いつ、どれだけの期間）

---

## 4. 想定ユーザーストーリー（User Stories）
### 4.1 Free（検知/通知）
- 経理/情シス：「昨夜の異常課金を朝Slackで知りたい」
- SRE：「どのアカウント/サービスが急増したかを把握したい」

### 4.2 Pro（自動ガードレール）
- SRE：「夜間にNAT Gatewayや高額GPUの作成が暴走したら、作成だけ止めたい」
- 開発責任者：「本番は止めず、サンドボックスだけ自動隔離したい」
- 情シス：「CIロールが暴走したら、CIロールだけ作成権限を一時停止したい」

---

## 5. システム構成（Reference Architecture）
### 5.1 推奨アーキテクチャ（イベント駆動）
- **Event Sources**
  - AWS Budgets → SNS → Lambda
  - Cost Anomaly Detection →（EventBridge/SNS/ポーリングのいずれか）→ Lambda
- **Core**
  - `policy-engine`：ポリシー評価（YAML/JSON）→ 実行計画（Action Plan）生成
  - `notifier`：Slack通知（Dry-run/承認/実行結果）
  - `executor`：IAM等のガードレール実行（要Opt-in）
  - `audit-store`：DynamoDB（events/actions/approvals）
- **Optional**
  - Step Functions：承認・再試行・タイムアウト・TTL解除のオーケストレーション
  - SQS：スパイク時のバッファ

### 5.2 データモデル（最低限）
- `CostEvent`
  - `event_id`, `source`(budgets|anomaly), `account_id`, `amount`, `time_window`, `details`
- `GuardrailPolicy`
  - `policy_id`, `mode`(dry_run|manual|auto), `thresholds`, `scope`, `actions`, `ttl_minutes`
- `ActionExecution`
  - `execution_id`, `policy_id`, `event_id`, `status`(planned|approved|executed|rolled_back|failed), `executed_at`, `rolled_back_at`, `diff`

---

## 6. リポジトリ構成（提案）
（既存があればそれを尊重し、必要に応じてこの形に寄せる）

```

.
├── README.md
├── claude.md
├── docs/
│   ├── product.md
│   ├── safety.md
│   ├── policies.md
│   └── runbooks.md
├── infra/
│   ├── cdk/               # or terraform/
│   └── templates/
├── src/
│   ├── guardrails/
│   │   ├── policy_engine.py
│   │   ├── models.py
│   │   ├── notifier_slack.py
│   │   ├── executor_iam.py
│   │   ├── audit_store.py
│   │   └── handlers/
│   │       ├── budgets_event.py
│   │       ├── anomaly_event.py
│   │       └── approval_webhook.py
│   └── api/               # optional: FastAPI for approvals/admin
├── policies/
│   ├── example.yaml
│   └── ...
├── tests/
│   ├── unit/
│   └── integration/
├── pyproject.toml
└── Makefile

````

---

## 7. 実装ルール（Coding Standards）
- Python 3.11+（原則）
- 主要ライブラリ
  - 型：`pydantic`（モデル定義）
  - テスト：`pytest`
  - フォーマット：`ruff format`（または `black`）
  - lint：`ruff`
- ルール
  - 例外は握りつぶさない。失敗時は `ActionExecution.status=failed` を残し通知
  - 外部I/O（AWS/Slack）はインターフェイス化し、ユニットテストでモック可能に
  - **Policy evaluationは純粋関数**として切り出し、再現性のあるテストを用意

---

## 8. セキュリティ & IAM（最重要）
### 8.1 権限設計の原則
- Read-only（検知/分析）ロールと、Write（ガードレール実行）ロールを分離
- Writeロールは**明示的に有効化**する（デフォルト無効）
- 監査証跡を必須で残す

### 8.2 絶対にやってはいけないこと
- 管理者権限（`AdministratorAccess`）を前提にする
- “なんとなく便利”なワイルドカード許可（`iam:*` など）
- 顧客の本番停止に繋がる操作をデフォルトでONにする

---

## 9. ポリシー仕様（MVP）
`policies/*.yaml` の例（この仕様を基準に実装する）

```yaml
policy_id: "budget-spike-ci-quarantine"
mode: "manual"          # dry_run | manual | auto
ttl_minutes: 180        # 解除までの時間（0なら手動解除のみ）
match:
  source: ["budgets", "anomaly"]
  account_ids: ["123456789012"]
  min_amount_usd: 200
scope:
  principals:
    - type: "iam_role"
      arn: "arn:aws:iam::123456789012:role/ci-deployer"
  regions: ["ap-northeast-1"]
actions:
  - type: "attach_deny_policy"
    deny:
      - "ec2:RunInstances"
      - "ec2:CreateNatGateway"
      - "ec2:CreateVpc"
notify:
  slack_webhook_ssm_param: "/guardrails/slack_webhook"
  channel_hint: "#alerts"
````

---

## 10. コマンド（Claudeが実行する前提の標準コマンド）

### Windows環境（PowerShell推奨）
詳細は **[docs/windows-setup.md](docs/windows-setup.md)** を参照。

* セットアップ
  * `.\scripts\make.ps1 setup`
* フォーマット & lint
  * `.\scripts\make.ps1 fmt`
  * `.\scripts\make.ps1 lint`
* テスト
  * `.\scripts\make.ps1 test`
  * `.\scripts\make.ps1 test-unit`
  * `.\scripts\make.ps1 test-integration`
* ローカル実行（可能なら）
  * `.\scripts\make.ps1 run-local`
* デプロイ（本番は人間が実行。Claudeはテンプレ生成まで）
  * `.\scripts\make.ps1 synth`
  * `.\scripts\make.ps1 deploy-dry-run`
  * `.\scripts\make.ps1 deploy`（※実行前に必ず「危険操作が無い」ことを確認する）

### Linux/Mac環境
（Makefileが無ければ作成してOK）

* セットアップ
  * `make setup`
* フォーマット & lint
  * `make fmt`
  * `make lint`
* テスト
  * `make test`
* ローカル実行（可能なら）
  * `make run-local`
* デプロイ（本番は人間が実行。Claudeはテンプレ生成まで）
  * `make synth`
  * `make deploy`（※実行前に必ず「危険操作が無い」ことを確認する）

---

## 11. テスト方針（必須）

* **unit**

  * policy engine（入力イベント×ポリシー → action plan）
  * executor（IAM API呼び出しはモックで差分生成まで）
  * notifier（Slack payload）
* **integration（任意）**

  * LocalStack等は後回しでも良い
* 追加で必須のテスト観点

  * Dry-run時に一切の書き込みが走らない
  * TTL解除のロールバックが必ず動く（失敗時も再試行可能）

---

## 12. 変更管理（Changelog / Docs）

* 重要な変更（ポリシー仕様、IAM、実行アクション）は `docs/safety.md` に必ず追記
* 破壊的変更（policy schemaなど）はバージョンを切る（例：`schema_version`）

---

## 13. 実装ロードマップ（Claudeが自走するための段取り）

### 🔥 実装プラン

**推奨**: **[docs/practical-workflow.md](docs/practical-workflow.md)** を参照（実用的な逐次実装）
- Claude Codeは対話型ツールのため、段階的な逐次実装が最も効率的
- Phase 0 → Phase 1 → Phase 2 → Phase 3 の順に確実に進める
- 各タスク完了ごとにテスト・lint・コミット

**上級者向け**: **[docs/parallel-implementation-plan.md](docs/parallel-implementation-plan.md)** （理論上の並行実装）
- 複数のClaude Codeセッションを同時実行する場合の設計
- 以下の原則を厳守：
  1. **Phase 0で `models.py` を最優先で完成**させる（全エージェントが参照）
  2. **ファイル単位で担当分離**（同じファイルを複数エージェントが触らない）
  3. **インターフェイス駆動開発**（型定義→実装→統合テスト）
  4. **統合は専任エージェントが担当**（A1-4, A2-4, A3-3）

### Phase 0: 足場（並行度: 高）

* [ ] **A0-1**: `pyproject.toml` / `ruff` / `pytest` / `Makefile` 整備
* [ ] **A0-2**: `docs/product.md` と `docs/safety.md` の叩き台
* [ ] **A0-3**: 共通型定義 `models.py`（最優先、全員が待つ）

### Phase 1: Free（検知/通知）【並行度: 非常に高】

* [ ] **A1-1**: Policy Engine（独立タスク）
* [ ] **A1-2**: Slack Notifier（独立タスク）
* [ ] **A1-3**: Budgets Event Handler（独立タスク）
* [ ] **A1-4**: 統合ハンドラ（A1-1,2,3完了後）

### Phase 2: Manual（承認→実行）【並行度: 中】

* [ ] **A2-1**: IAM Executor（独立タスク）
* [ ] **A2-2**: Audit Store DynamoDB（独立タスク）
* [ ] **A2-3**: 承認Webhook（A2-1,2完了後）
* [ ] **A2-4**: 統合テスト（A2-1,2,3完了後）

### Phase 3: Auto（安全な自動化）【並行度: 中】

* [ ] **A3-1**: TTL解除機構（独立タスク）
* [ ] **A3-2**: Exception/Allowlist機構（独立タスク）
* [ ] **A3-3**: 統合テスト（A3-1,2完了後）

### Phase 4: Infrastructure（並行実行可能）

* [ ] **I-1**: CDK/CloudFormation（Phase 1-3と並行可）
* [ ] **I-2**: Terraform（代替、排他的）

### Phase 5: Marketplace/PLG強化（後）

* [ ] ワンクリックデプロイ（テンプレ完成）
* [ ] UI/セルフサーブ（最小の管理画面）
* [ ] 料金境界（free/proの機能フラグ）

---

## 14. 重要な設計判断（Decision Log）

Claudeは、重要な設計判断を行ったら `docs/decisions/NNN-title.md` を作り、理由と代替案を残すこと。

例：

* なぜBudgetsイベントを先に実装したか
* なぜSCPではなくIAM attachをMVPにしたか
* なぜデフォルトDry-runにしたか

---

## 15. “安全なガードレール”の具体例（MVPで採用する推奨セット）

MVPで提供するアクションは、まず以下のみに限定する（増やす場合は safety review を必須とする）。

* `attach_deny_policy`（推奨）
* `detach_deny_policy`（rollback）
* `set_permission_boundary`（上級、後回しでも可）
* `notify_only`（dry-run）

※ `stop_instances` / `scale_to_zero` / `delete_*` は Phase 3以降、明示Opt-in + 強いスコープ制限 + 監査証跡 + ロールバック手順必須。

---

## 16. 最後に（成功の定義）

* **導入直後**に「異常課金イベント → Slack通知」が動く
* **誤爆しない**（デフォルトdry-run、対象スコープが狭い）
* **解除が簡単**（TTL/手動で復旧できる）
* **証跡が残る**（監査・説明責任に耐える）
