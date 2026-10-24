# ai-agent-workflow-lite

CLI から **必要なときだけサブエージェントを起動し、その作業を Herdr の Pane で見える化する**ための小さな実験用テンプレートです。

旧 `ai-agent-workflow-template` のコード・設計契約は引き継ぎません。特に Run Manifest / Evidence Contract / 複雑な再入 Policy は作りません。

## v0 の範囲

- `.agents/skills/orchestrate/SKILL.md`: Root が実装とレビューを必要に応じて委譲する
- `scripts/herdr-context.sh`: 現在の Herdr Pane を実際の CLI 応答で検証
- `scripts/run-agent.sh`: 1体を起動し、短い結果を回収する薄いアダプタ
- `scripts/finish-agent.sh`: Rootの明示的な成果受入を記録し、本人・状態照合とlayout確認付きで当該Paneを片付ける
- `scripts/run-pair.sh`: 独立した2割当を事前確認し、別worktree・別Paneで起動して結果を識別する
- `scripts/launch-gate.py`: 承認済みタスクの子CLI起動回数を外部stateで予約する
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
export LAUNCH_TASK_ID=issue-51-demo # 同じタスクの全worktree・再開で固定
# 以下はユーザーの明示承認が既にある場合だけ実行する。
python3 scripts/launch-gate.py --task-id "$LAUNCH_TASK_ID" --worktree "$PWD" approve \
  --approval-ref '<承認記録の参照>' --provider codex --model gpt-6-luna \
  --additional-launches 1 --paths run-agent
bash scripts/run-agent.sh implement codex light /tmp/ai-work-demo.txt
```

`run-agent.sh` の引数は `<implement|review> <codex|claude-only> <light|standard|deep> <task-file>` です。**ユーザーが実行を承認した作業**を Root が委譲することを想定しています。Shell/CI のテストは Root が直接実行し、検証専用AIはデフォルトで起動しません。CLI 側の権限・組織ポリシーを上書きするものではありません。

## タスク単位の起動ゲート（Issue #16）

`LAUNCH_TASK_ID` は明示必須です。run名、Pane、worktree、再開に依存しない同じIDを同じ承認済みタスクの全実行で使ってください。別タスクとは重複しないIDにします。`XDG_STATE_HOME` も全worktreeで共通のGit管理外ディレクトリに固定します。policyと予約は `${XDG_STATE_HOME:-$HOME/.local/state}/ai-agent-workflow-lite/launch-gate/<task-id>/state.json` に保存され、ユーザー専用のlockで排他的に更新されます。単位は **予約された子CLI起動枠1回** です。Pane作成の前に枠を予約するため、Pane作成失敗も枠を消費します。

`approve` は既に得たユーザーの明示承認を `--approval-ref` で**申告して記録する**コマンドです。ツールは会話上の承認を証明しません。初回作成、上限追加、provider/model/経路の追加にはそれぞれ新しい明示承認が必要です。承認履歴は追記され、既存の予約は減りません。各予約は承認レコードに紐付き、異なるprovider/model向けの枠を流用しません。許可済みの残枠内なら毎回の再承認は不要です。provider/modelは実際の `config/models.json` の選択値と一致させ、予約経路は共通の `run-agent` を指定します（`run-pair.sh` もこのrunnerを呼びます）。例えば追加承認後は `--additional-launches 2 --paths run-agent` で履歴を追記します。承認済みの組み合わせ以外、policy不在、状態不正、明示停止は起動を拒否します。

```bash
python3 scripts/launch-gate.py --task-id "$LAUNCH_TASK_ID" --worktree "$PWD" status
# ユーザーが停止を明示したとき:
python3 scripts/launch-gate.py --task-id "$LAUNCH_TASK_ID" --worktree "$PWD" stop
python3 tests/test-launch-gate.py
```

各予約は `reserved` から `returned` / `failed` / `interrupted` / `prelaunch_error` に更新されます。強制終了などで `reserved` のままでも消費済みとして数え、自動返却や自動retryはしません。上限到達と明示停止は `stop.reason=limit_reached|explicit_stop` として残し、#15が参照できます。追加承認は上限到達だけを解除し、明示停止を解除しません。この停止状態は新規起動の拒否であり、稼働中の子を停止した証拠ではありません。子の所有者、実効停止能力、停止結果は別途確認してください。`LAUNCH_PATH` による経路の自己申告は受け付けません。使用量と単価は `unknown` のままであり、請求確定額や絶対的な課金上限を保証しません。

対象はこの `run-agent.sh` のCodex/Claude起動と、これを使う `run-pair.sh` です。後者では両worktreeで同じ `LAUNCH_TASK_ID` と `XDG_STATE_HOME` を渡し、policyに共通の `run-agent` 経路を許可してください。直接の `codex` / `claude` / `herdr agent start`、他のrunner、Root自身の推論、稼働中エージェントへの追加promptはこのゲートを通りません。policy/stateの手動編集や異なるstate homeの利用も防げないため、Rootが実際の割当とIDを照合します。モデルなしテストは予約動作の確認であり、実機AI起動や請求の検証ではありません。

## 開始・結果回収・受入・片付け

開始前に `run_id`・親Pane・agent名・cwd・summaryパスを出力し、Pane作成後は子IDも記録します。タスクは一度読み込んだ送信対象テキスト（Shellの末尾改行正規化後）のSHA-256で識別し、本文を保存しません。外部summaryの `phase` は最後に確認した段階（initialized / pane_created / agent_ready / prompt_wait / result_collected）、`status` はrunning / returned / failed / interrupted / prelaunch_errorです。prompt_waitは呼出し中であり、投入成功の証明ではありません。各checkpointは一時ファイルから置換しますが、強制終了後にプロセスがまだ生きているかは実Paneを再確認します。

結果回収後も `root_acceptance=pending`・`cleanup=retained` のままです。Rootが成果・focused testsを独立確認してから、返されたsummaryと短い検証メモで明示的に受け入れます。

```bash
bash scripts/finish-agent.sh /absolute/run.XXXXXXXX/summary.json 'Root検証: 成果とfocused testsを確認、秘密情報なし'
```

helperは同じ親Root・子Pane・agent名・cwd・idle/doneを照合し、受入記録→当該Paneのclose→親layout読戻しの順に処理します。これはRootの検証済みという申告を記録するもので、成果の正しさを自動判定しません。受入とclose結果は別管理で、close未確認ならacceptedでもretainedを残して停止します。close応答の消失も再送せずlayoutで確認し、closedはlayoutで裏付けられた場合だけ記録します。close後の記録失敗は、明示的な再実行で同じ受入Rootと元の検証メモを保持し、親が存在・子が不在ならcloseを再送せずclosedへ収束させます。受入済み・片付け済みの再実行はlayoutと一致する場合だけno-opです。

close要求前に `close_attempted=true` を保存します。要求後も子が見える場合、応答失敗や反映遅延の可能性があるため再実行でもcloseを再送せず、Rootの手動確認で停止します。信頼できる当該タスクのsummaryだけを使い、処理中にPaneを移動・再割当しないでください。失敗・非完了・本人不一致は保持して停止し、自動retry・worktree削除・mergeは行いません。旧summaryや失敗からの手動回復は、既存の本人・状態照合とRoot判断が必要です。決定論的な検証は `python3 tests/test-finish-agent.py`（Herdr模擬）で、実機検証と区別します。

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
LAUNCH_TASK_ID=issue-51-52 bash scripts/run-pair.sh codex worker-a 51 /absolute/issue-51-worker-a light /absolute/task-a.txt worker-b 52 /absolute/issue-52-worker-b light /absolute/task-b.txt
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


## 外部公開前チェック

`publication-check.py` は外部書込み前の協調的な運用ゲートです。Gitleaksが必要です。Rootは自動検査に先立ち、個人情報・顧客/社内情報・内部URL/パス・LLM会話やログの引用などを文脈確認します。`--context-reviewed` はその申告であり、内容の安全性を自動証明しません。

例えば、投稿本文をファイルに固定し、対象と実行コマンドを明示します（既に投稿承認がある場合だけ実行）。`prepare` が出力した記録パスを同じ対象の `publish` へ渡します。

```bash
python3 scripts/publication-check.py prepare --target owner/repo:pr:19 \
  --file /tmp/pr-body.md --context-reviewed -- \
  gh pr edit https://github.com/owner/repo/pull/19 --body-file /tmp/pr-body.md
python3 scripts/publication-check.py publish --target owner/repo:pr:19 \
  --file /tmp/pr-body.md --receipt /absolute/path/printed-by-prepare.json -- \
  gh pr edit https://github.com/owner/repo/pull/19 --body-file /tmp/pr-body.md
```

pushは同様に `--git-range origin/main..HEAD` と送信する正確なrefspecを指定します。全送信commit・途中で削除した内容・commit message・merge固有差分を検査します。rangeは両端をcommitへ解決した `base..head` 形式だけを受け付けます。binary patch、NULを含むファイル、UTF-8でない入力は対応外として公開を停止します。画像・アーカイブなどは専用の内容確認手順が必要です。複数ref、tag、添付物などは全対象を明示して確認できる場合に限ります。`--file` は複数指定できます。target文字列はRootが実送信先と一致を確認します。secretをcommand引数に含めず、認証は既存の安全な設定を使ってください。

未検査・scanner失敗/不在・候補検出・本文/commit範囲/command/cwd/target変更・既使用記録は拒否します。直前に自動再検査し、送信要求の前に記録を使用済みにします。失敗した送信を再試行する場合も新しい検査記録が必要です。記録は `${XDG_STATE_HOME:-$HOME/.local/state}/ai-agent-workflow-lite/publication-check/` に対象ファイルのパスとハッシュ、commit範囲のハッシュ、commandのハッシュ、時刻、文脈確認申告だけを保存します。秘密の候補値・本文・scanner生出力は保存しません。

raw CLIによる迂回、対象の列挙漏れ、確認後に別プロセスが入力を変更する競合、外部サービス側で生成されるログは自動保護しません。対象を固定し、単一Rootで操作し、送信後に実対象を読み戻します。自動検出0件を「秘匿情報なし」の保証にしません。モデルなしの動作検証は `python3 tests/test-publication-check.py` です。
