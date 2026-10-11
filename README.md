# ai-agent-workflow-lite

CLI から **必要なときだけサブエージェントを起動し、その作業を Herdr の Pane で見える化する**ための小さな実験用テンプレートです。

旧 `ai-agent-workflow-template` のコード・設計契約は引き継ぎません。特に Run Manifest / Evidence Contract / 複雑な再入 Policy は作りません。

## v0 の範囲

- `.agents/skills/orchestrate/SKILL.md`: Root が実装とレビューを必要に応じて委譲する
- `scripts/herdr-context.sh`: 現在の Herdr Pane を実際の CLI 応答で検証
- `scripts/run-agent.sh`: 1体を起動し、短い結果を回収する薄いアダプタ
- `config/models.json`: light / standard / deep の明示マッピング
- `tests/test-runner.sh`: Herdr を模擬した決定論的なテスト

**前提:** Linux、Bash、Git、jq、sha256sum、Herdr CLI、および選択した Codex または Claude Code が導入・認証済みであること。Herdr の実機 E2E はまだ未検証です。

## 使い方

Herdr が管理する Pane 内で:

```bash
bash scripts/herdr-context.sh
bash tests/test-runner.sh

# デモ用タスクファイル。秘密情報は書かない。
printf '%s\n' 'READMEの誤字を一箇所だけ修正し、差分を確認して報告する。' > /tmp/ai-work-demo.txt
bash scripts/run-agent.sh implement codex light /tmp/ai-work-demo.txt
```

`run-agent.sh` の引数は `<implement|review> <codex|claude-only> <light|standard|deep> <task-file>` です。**ユーザーが実行を承認した作業**を Root が委譲することを想定しています。Shell/CI のテストは Root が直接実行し、検証専用AIはデフォルトで起動しません。CLI 側の権限・組織ポリシーを上書きするものではありません。

## Worker の branch / worktree 分離（Issue #4）

原則は **1 Issue = 1 branch = 1 worktree = 1 PR**。Root が割当を直列に準備し、Worker ID・Issue・branch・絶対パスの対応をタスクに明記します。これは Git の編集分離であり、他のディレクトリへのアクセスを禁止する sandbox ではありません。

新規割当の例（リポジトリ内から実行）:

```bash
git status --porcelain --untracked-files=all
git worktree list --porcelain
git branch --list feature/issue4
# 上記を確認し、汚れ・既存branch・既存割当があれば停止して調査する。
# 存在する親ディレクトリの絶対パスを指定する。リポジトリ外を推奨。
git worktree add -b feature/issue4 /absolute/worker-root/issue-4-worker-a HEAD
bash scripts/check-worker-worktree.sh worker-a 4 /absolute/worker-root/issue-4-worker-a
```

確認Shellは読取専用で、成功時に `Worker ID<TAB>Issue<TAB>branch<TAB>絶対パス` を返します。命名は `feature/issue<N>` と `issue-<N>-<worker-id>`。IDは英数字・ハイフン・アンダースコア、Issueは先頭ゼロなしの正整数です。パスは実体の絶対パスへ解決されます。

Root は全割当の出力を比較し、Worker ID・Issue・branch・実体パスが既存の稼働中割当と重複しないことを確認します。このShell単体は稼働中Workerの台帳ではなく、同じ割当を2プロセスへ渡すことまでは検出しません。Gitのbranchチェックも同一worktree内の複数Writerを防ぎません。確認と起動を別のRootから同時に行わないでください。

存在しないパス、worktreeルート以外、命名/branch不一致、detached HEAD、未追跡ファイルを含む汚れは拒否します。既存branchには `-b` が失敗し、別worktreeでcheckout済みのbranchはGitが拒否します。`-B` / `--force` / reset / 自動stashで回避しません。再開時は新規作成せず、Rootが既存割当と差分を確認してから判断します。作業開始後の汚れは正常なので、確認Shellは新規割当前のゲートとして使います。

Workerへのタスクには「指定した絶対worktreeパスだけで編集する。他Workerのパスで操作しない。branch切替、worktree削除、共有Git設定変更、mergeをしない」を含めます。起動先のcwdも確認してください。現行 `run-agent.sh` の起動機構は変更しておらず、Herdrの並行起動は #5 の範囲です。

```bash
bash tests/test-worktree.sh
```

テストは一時Gitリポジトリで2つのworktreeを作成し、割当拒否・別ファイルの差分とbranch・worktree一覧・片方の失敗後に他方がcommitできることを確認します。テスト自身が作った一時領域だけを終了時に削除します。実運用の停止/失敗ではworktreeとbranchを残し、自動cleanupやmergeはしません。

実環境での確認項目: 起動直前のcwd/branch/割当対応、両Workerの差分と `git worktree list --porcelain`、片方を停止した後の他方の継続、他方のファイルが混入しないこと。Herdr実Workerの並行・停止検証は未実施です。設計判断と測定結果はWikiへ、親Issue #2へは結果と残課題を共有します。

## 重要なルール

1. 難易度とリスクを分離する。**`.agents/` の編集というだけで deep にしない**。auth・権限・sandbox境界・破壊的変更などは個別に判断する。
2. 権限内での通常の修正、lint/test、指摘の修正は継続する。許可されていない破壊的変更・権限昇格・仕様変更・PR merge は勝手に実行しない。
3. Herdr の存在を環境変数だけでは判定しない。実際の pane と layout を検証し、確認できなければ**明示的に停止**する。無断の代替ランタイム起動はしない。
4. 初版は1体ずつ。並列 writer と自動リトライは非対応。失敗時に原因が確認できるよう Pane は残す。
5. 出力を大量に再読しない。サブエージェントは最後に短い作業結果を返す。

## 記録の配置

- **[設計原則（GitHub Wiki）](https://github.com/takaturn/ai-agent-workflow-lite/wiki/Design%E2%80%90Principles):** 設計判断、実験結果、失敗から得た知見。初期ページの作成後はここを記録の入口とする。Wikiへの自動書き込み機能はv0にはありません。
- **GitHub Issues:** 修正すべきバグや次の改善タスク。
- **${XDG_STATE_HOME:-~/.local/state}/ai-agent-workflow-lite/**: 実行メタデータ（ユーザー専用権限）。プロンプト本文や大量ログを自動保存しない。
- **${XDG_CACHE_HOME:-~/.cache}/ai-agent-workflow-lite/**: 今後必要になった場合のみ、一時データ。

実行記録はGit管理外です。GitHub Wikiは本体とは別のGitリポジトリで、公開設定に注意してください。実行状態や機密情報をWikiへ貼らないでください。

## 成功基準

3つの現実的なタスクで、単独エージェントと比較し **完了時間・利用者の介入・トークン消費（取得可能な場合）・正確さ・Paneによる進捗確認の容易さ** を確認します。トークン値を取得できなければ `unavailable` とし、節約できたと主張しません。

新しい仕組みを増やす前に、ユーザーにとっての改善を測定します。
