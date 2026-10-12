# ai-agent-workflow-lite

CLI から **必要なときだけサブエージェントを起動し、その作業を Herdr の Pane で見える化する**ための小さな実験用テンプレートです。

旧 `ai-agent-workflow-template` のコード・設計契約は引き継ぎません。特に Run Manifest / Evidence Contract / 複雑な再入 Policy は作りません。

## v0 の範囲

- `.agents/skills/orchestrate/SKILL.md`: Root が実装とレビューを必要に応じて委譲する
- `scripts/herdr-context.sh`: 現在の Herdr Pane を実際の CLI 応答で検証
- `scripts/run-agent.sh`: 1体を起動し、短い結果を回収する薄いアダプタ
- `scripts/run-pair.sh`: 独立した2割当を事前確認し、別worktree・別Paneで起動して結果を識別する
- `config/models.json`: light / standard / deep の明示マッピング
- `tests/test-runner.sh`: Herdr を模擬した決定論的なテスト

`bash tests/test-runner.sh` の成功はモックテストの成功であり、Herdr 実機での Worker 起動・監視・結果回収の成功を意味しません。

**前提:** Linux、Bash、Git、jq、sha256sum、Herdr CLI、および選択した Codex または Claude Code が導入・認証済みであること。

単独 Worker の実測結果は [Issue #3 の GitHub Wiki](https://github.com/takaturn/ai-agent-workflow-lite/wiki/Issue-3-Herdr-Single-Worker-E2E) を参照してください。[Issue #5 の公開記録](https://github.com/takaturn/ai-agent-workflow-lite/wiki/Issue-5-Two-Worker-Autonomous-Acceptance)にはCodex light 2体の別worktree・別Paneでの同時working、個別成果検証、pair runnerのexit 0、ターン中断の分離、完了後の追加指示、および同じRootでの判断4ケースの受入演習を記録しています。初回のタスク指示不足と訂正も記録し、CLI成功と成果成功を区別しています。新規Rootの盲検評価・反復安定性・Claude Codeは未検証です。

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
git status --porcelain --untracked-files=all --ignored=matching
git worktree list --porcelain
git branch --list feature/issue4
# 上記を確認し、汚れ・既存branch・既存割当があれば停止して調査する。
# 存在する親ディレクトリの絶対パスを指定する。リポジトリ外を推奨。
git worktree add -b feature/issue4 /absolute/worker-root/issue-4-worker-a HEAD
bash scripts/check-worker-worktree.sh worker-a 4 /absolute/worker-root/issue-4-worker-a
```

確認Shellは読取専用で、成功時に `Worker ID<TAB>Issue<TAB>branch<TAB>絶対パス` を返します。命名は `feature/issue<N>` と `issue-<N>-<worker-id>`。IDは英数字・ハイフン・アンダースコア、Issueは先頭ゼロなしの正整数です。パスは実体の絶対パスへ解決されます。

確認Shellは対象リポジトリ内（既存のlinked worktree内も可）から実行します。呼出元と割当先の実体common Git directoryが一致しなければ拒否します。HEADは実在するcommitへ解決できる必要があり、unborn branchは拒否します。無視対象の生成物・キャッシュも割当前の汚れとして検出し、削除せず停止します。

Root は全割当の出力を比較し、Worker ID・Issue・branch・実体パスが既存の稼働中割当と重複しないことを確認します。このShell単体は稼働中Workerの台帳ではなく、同じ割当を2プロセスへ渡すことまでは検出しません。Gitのbranchチェックも同一worktree内の複数Writerを防ぎません。確認と起動を別のRootから同時に行わないでください。

存在しないパス、worktreeルート以外、命名/branch不一致、detached HEAD、未追跡ファイルを含む汚れは拒否します。既存branchには `-b` が失敗し、別worktreeでcheckout済みのbranchはGitが拒否します。`-B` / `--force` / reset / 自動stashで回避しません。再開時は新規作成せず、Rootが既存割当と差分を確認してから判断します。作業開始後の汚れは正常なので、確認Shellは新規割当前のゲートとして使います。

Workerへのタスクには「指定した絶対worktreeパスだけで編集する。他Workerのパスで操作しない。branch切替、worktree削除、共有Git設定変更、mergeをしない」を含めます。起動先のcwdも確認してください。

## 独立した2成果物の並行実行（Issue #5）

RootはAGENTS.mdからorchestrate Skillを自動適用し、独立した複数成果物では最大2 Workerへの委譲を第一候補にします。先行成果への依存・同一ファイル/共有状態・CI/認証/外部サービスの競合を確認し、解消できなければ理由を一文で示して逐次実行します。軽微な修正のためだけにWorkerを増やしません。

Rootが上記手順で2割当を直列準備し、既存稼働割当との重複も照合した後:

```bash
bash scripts/run-pair.sh codex worker-a 51 /absolute/issue-51-worker-a light /absolute/task-a.txt worker-b 52 /absolute/issue-52-worker-b light /absolute/task-b.txt
bash tests/test-pair.sh
```

51/52は使用例です。実際のIssue・branch・worktreeに置き換えます。引数はprofileの後にWorker ID・Issue・絶対worktree・tier・taskを2組。両方を起動前に検査し、既存run-agent.shを各cwdから並行実行します。出力はWorker/Issue接頭辞付きで、起動応答のPane ID・agent名・外部summaryとの対応を追跡できます。Rootは同じIDで状態と短い出力を読み、各差分とfocused testsを検証します。

一方が失敗しても他方を待ち、全体は非zeroで終了。自動再投入・peer停止・worktree削除・mergeはしません。成功PaneはRoot受入後にidentity/cwd/完了状態を再確認して閉じ、layoutで確認します。失敗Paneは診断用に残します。同時に別Rootから割当・起動しないでください。このShellは全稼働Writerの台帳ではありません。

test-pair.shは模擬runnerで時間的重なり・結果識別・失敗分離・重複拒否を確認するもので、実AI並行稼働の証拠ではありません。実機では別途2 Workerの同時working、片方のターン中断後の他方の継続、完了後の追加指示を確認しています。Esc後の背景プロセス全停止、実行中の追加指示、反復安定性は未検証です。

```bash
bash tests/test-worktree.sh
```

テストは一時Gitリポジトリで2つのworktreeを作成し、割当拒否・別ファイルの差分とbranch・worktree一覧・片方の失敗後に他方がcommitできることを確認します。テスト自身が作った一時領域だけを終了時に削除します。実運用の停止/失敗ではworktreeとbranchを残し、自動cleanupやmergeはしません。

実環境での確認項目: 起動直前のcwd/branch/割当対応、両Workerの差分と `git worktree list --porcelain`、片方を中断した後の他方の継続、他方のファイルが混入しないこと。Issue #5で別Pane・別worktreeの同時稼働、ターン中断の分離、個別成果検証を確認済みです。背景プロセス全停止の保証とは区別します。設計判断と測定結果はWikiへ、親Issue #2へは結果と残課題を共有します。

## 重要なルール

1. 難易度とリスクを分離する。**`.agents/` の編集というだけで deep にしない**。auth・権限・sandbox境界・破壊的変更などは個別に判断する。
2. 権限内での通常の修正、lint/test、指摘の修正は継続する。許可されていない破壊的変更・権限昇格・仕様変更・PR merge は勝手に実行しない。
3. Herdr の存在を環境変数だけでは判定しない。実際の pane と layout を検証し、確認できなければ**明示的に停止**する。無断の代替ランタイム起動はしない。
4. 最大2 Writer、1 worktreeに1 Writer。ReaderとReviewerは合計最大1体を初期上限とし、子エージェント全体は最大3体（Rootを除く）。これはRootの割当規約であり、runnerの自動上限制御ではない。ReaderはWriter枠には数えないが、無制限には増やさない。自動リトライ・再帰委譲は非対応。失敗時に原因が確認できるよう Pane は残す。
5. 出力を大量に再読しない。サブエージェントは最後に短い作業結果を返す。
6. 役割と権限を分ける。**Writer**は承認済み範囲を専用worktreeで編集する。**Reader**はコード読解・依存/仕様調査・原因候補の整理を行い、**Reviewer**は変更の妥当性・リスクを独立評価する。Reader/Reviewerは初回・追加指示とも編集・commit/push・外部状態変更・子起動は禁止で、指摘を得てもWriterへ自動昇格しない。修正とファイル生成が必要なテストはRoot/Writerが担当する。Codexはread-only指定、Claudeのplan/safe-modeは同等の隔離保証ではないため、実効制限を確認できない環境ではClaude Reader/Reviewerを起動しない。読取コマンドの暗黙cache生成も確認対象とし、拒否を権限変更で回避しない。
7. Readerは独立した調査がRootの負担を減らす場合だけ起動する。軽微な検索・決定論的チェックはRootが直接行う。専用Herdr Paneを与えるが、変更しないため別worktreeは必須ではない。固定commitの根拠はSHAとpath:line、変更中のツリーの根拠はlive表記・観測時刻・内容hashで分け、未コミット行をSHAの内容として報告しない。確認済み事実・仮説・未確認を分け、Rootが採用前に根拠を再照合する。起動直前・再開時には既存Pane/agentと全タスク割当を照合し、残存Reader/Reviewerも枠に数える。所有者・役割・全割当を確認できなければ新規起動を止める。並行する複数Rootによる無調整起動は非対応。具体的手順は [orchestrate Skill](.agents/skills/orchestrate/SKILL.md) を参照する。

## 記録の配置

- **[設計原則（GitHub Wiki）](https://github.com/takaturn/ai-agent-workflow-lite/wiki/Design%E2%80%90Principles):** 設計判断、実験結果、失敗から得た知見。初期ページの作成後はここを記録の入口とする。Wikiへの自動書き込み機能はv0にはありません。
- **GitHub Issues:** 修正すべきバグや次の改善タスク。
- **${XDG_STATE_HOME:-~/.local/state}/ai-agent-workflow-lite/**: 実行メタデータ（ユーザー専用権限）。プロンプト本文や大量ログを自動保存しない。
- **${XDG_CACHE_HOME:-~/.cache}/ai-agent-workflow-lite/**: 今後必要になった場合のみ、一時データ。

実行記録はGit管理外です。GitHub Wikiは本体とは別のGitリポジトリで、公開設定に注意してください。実行状態や機密情報をWikiへ貼らないでください。

## 成功基準

3つの現実的なタスクで、単独エージェントと比較し **完了時間・利用者の介入・トークン消費（取得可能な場合）・正確さ・Paneによる進捗確認の容易さ** を確認します。トークン値を取得できなければ `unavailable` とし、節約できたと主張しません。

新しい仕組みを増やす前に、ユーザーにとっての改善を測定します。
