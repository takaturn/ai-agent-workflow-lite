# Root終了ゲート（Issue #15、実機受入前）

## 目的と境界

Rootの終了時に、明示した必要作業一覧、既存の子run summary、起動ゲートを照合する。未完了を自然言語の「done」で完了へ変更しない。checkerはモデル、Herdr、GitHubを呼ばず、paneの停止・削除、成果の受入、外部記録の更新もしない。

checkerと任意のCodex Stop adapterの初稿を実装し、独立した敵対的レビューの指摘を反映した。実機受入は未実施であり、終了防止が実環境で有効とは主張しない。既存の権限・レビュー・公開ゲートを維持する。

## 入力の契約

Git管理外に、タスクID、明示的に登録したRootのsession ID・親pane ID、canonical worktree、必要作業、所有するrun一覧を保持する。登録値はRootの申告であり、認証や呼出元の自動証明ではない。cwd、focused pane、transcriptから所有者を推定しない。

必要作業は識別子と種別、実行可否、完了確認を持つ。Wiki/Issue/PRの更新はそのタスクで必要になったものだけ列挙し、送信とread-backを別々に確認する。外部書込み権限をこの一覧から新規に与えない。`complete` はRootが一覧を漏れなく作り、確認結果を正しく記録したことに依存する。

子runは実際のsummaryを読み、task_id、parent_pane_id、worktree、run_idを登録済みの割当と照合する。running、未回収、returnedかつroot_acceptance=pending、cleanup未確認を独立して報告する。他Rootや他タスクの入力、不明な状態、欠落、重複、不正JSONは通常完了にしない。宣言した全worktreeのrunner state directoryにあるsummaryを走査し、予約前の失敗を含む未登録runがあれば完了させない。宣言していないworktreeやsummary作成前に失われた試行は自動発見できず、Rootが実際の割当と照合する。

## 判定順序

1. ユーザーの明示停止とタスク全体の予算停止を最優先し、未完了を残した停止として記録する。追加モデル、retry、別providerで回避しない。
2. 入力と所有者を検証する。不明・不正なら継続を自動要求せず、診断を伴う未完了停止にする。
3. 子CLI起動ゲートのexplicit_stopを尊重する。limit_reachedは子の新規起動枠の上限であり、Rootの独立した実行可能作業やタスク全体の予算とは分ける。
4. 作業一覧と実summaryから残作業を求める。真の権限待ち・外部ブロッカーでも、独立した承認済み実行可能作業があれば具体的な次の処理を返す。
5. 継続可能でも、永続化した回数・経過時間上限に到達したら未完了停止する。カウンタはturnごとに初期化せず、同一タスクでlockとatomic更新を使う。
6. 必要作業、回収、Root受入、必要なcleanup、必要な公開・read-backが全て確認できた場合だけ通常完了とする。

停止は背景プロセスの停止を意味しない。稼働中の子の停止能力と結果は別に確認する。

## Codex接続の確認状況

2026-10-25にローカルで `codex-cli 0.164.0` と `hooks stable true` を観測した。提案hookの存在・enabled・trusted・実行成功は未確認であり、有効化していない。

[公式Hooks仕様](https://learn.chatgpt.com/docs/hooks)では、StopはJSONを受け、`decision: "block"` とreasonが次の継続promptになる。`continue: false` は継続より優先する。非managed hookは定義のhashごとのレビュー・trustが必要で、変更後は再確認する。CLIの `/hooks` で実効source、enabled、trustを確認する。trustを迂回するフラグは使わない。

接続例は有効化されない `config/root-exit-hook.example.json` に置く。保護対象 `.codex` や既存hookを変更していない。Claude/Copilotへ同じ仕様を適用せず、対応は未検証とする。

## 入力例と使い方

Rootが既存のタスク固有state directoryに以下を登録する。文字列のIDとpathは実際に照合した値へ置き換える。JSONの重複key、不正な型、不明な列挙値、相対path、非canonical pathは受け付けない。タスクの起動ゲートは既定または既存の固定 `XDG_STATE_HOME` の `ai-agent-workflow-lite/launch-gate/<task-id>/state.json` から読む。検査用に別stateへ切り替えたり、承認を追加したりしない。

```json
{
  "version": 1,
  "task_id": "issue-15",
  "session_id": "ROOT_SESSION_ID",
  "parent_pane_id": "ROOT_PANE_ID",
  "worktree": "/ABSOLUTE/REPO",
  "stop": null,
  "work": [
    {"id": "focused-tests", "kind": "root", "state": "ready"},
    {"id": "required-issue-update", "kind": "publish", "state": "permission"},
    {"id": "required-issue-readback", "kind": "read_back", "state": "permission"}
  ],
  "runs": [],
  "worktrees": ["/ABSOLUTE/REPO"]
}
```

`work.kind` は `root|child_launch|publish|read_back`、stateは `done|ready|permission|external`。必要な公開だけを登録する。`done` はRootの確認済み申告であり、checkerがGitHub等を検証したという意味ではない。Rootが未確認ならdoneにしない。checkerは自然言語の成果報告を読み取らない。

run一覧には `run_id`、`reservation_id`、`worktree`、`summary` の4項目を持つobjectを登録する。全予約を列挙し、実際のsummaryと照合する。ledgerに存在して一覧にない予約も不正として停止する。予約前の失敗summaryには予約IDがないため、走査で検出して停止し、Rootの診断へ戻す。同じtask IDを異なるRootが共有する運用は未対応。`worktrees` にはこのタスクの全割当worktreeとRootのworktreeを列挙する。各worktreeのrunner state directoryが存在し、そこを走査できる必要がある。未作成のdirectoryは不正入力として停止する。summary-homeはRoot worktreeの `${XDG_STATE_HOME:-$HOME/.local/state}/ai-agent-workflow-lite/<repo_id>` のcanonical directoryで、checkerは同じstate rootから来たことも確認する。repo_idはrunnerと同じくworktree物理絶対pathのSHA-256先頭16文字。checkerは他worktreeのhomeを同じstate root内で導出する。runnerのsummaryはRootの受入申告であり、成果の正しさや実pane状態の証明ではない。

```bash
python3 -B .agents/scripts/root-exit-check.py \
  --task /EXTERNAL/STATE/task.json --task-id issue-15 \
  --session-id ROOT_SESSION_ID --parent-pane-id ROOT_PANE_ID \
  --cwd /ABSOLUTE/REPO --summary-home /EXTERNAL/RUN_SUMMARIES
```

通常checkerの終了codeは全登録項目完了時0、それ以外2。JSONのoutcomeは `complete|incomplete|stopped`、reason、nextを返す。生のsummaryやnoteは出力しない。`incomplete` のnextは識別子と次の操作であり、既存の権限を拡張する指示ではない。

Stop接続だけは `--hook --continuation-state /EXTERNAL/STATE/continuation.json` を追加し、stdinにCodexのStop event JSONを受ける。session/cwd/eventを照合するが、parent IDは起動設定と登録値の照合であり、認証ではない。設定を信頼できる単一Rootで管理する。

Rootがcontinuation.jsonと同じstemの `.lock` を事前作成し、両方を所有者のみread/write可能な0600、親directoryを所有者のみアクセス可能な0700にする。state例の時刻は実際の開始時点のUnix秒へ置換する。自動作成・自動リセットはしない。

```json
{
  "version": 1,
  "task_id": "issue-15",
  "session_id": "ROOT_SESSION_ID",
  "parent_pane_id": "ROOT_PANE_ID",
  "worktree": "/ABSOLUTE/REPO",
  "started_at": 0,
  "deadline": 60,
  "max_continuations": 2,
  "continuations": 0,
  "stopped": false
}
```

期限は開始から最大3600秒、継続は最大10回。実際の値はタスクで合意した上限以下にする。lock競合は待たず停止する。counterをlock下でatomic置換してから継続JSONを返す。異なるturnやstop_hook_active=trueでもcounterは共通。上限や停止後はstoppedを保存し、同じstateから継続しない。入力異常でも照合できるstateがあればterminal stopを保存する。state自体が破損・欠落している場合は保存できず、継続を拒否してRootの診断へ戻す。stateの手動書換え・削除を防ぐアクセス制御ではない。Stop adapterのexit codeは常に0で、exit code 2を意図しない継続指示として使わない。

実運用への有効化には、別途承認した対象Rootで接続例の全placeholderを固定し、実効source・enabled・trustを `/hooks` で確認する。必要な保護対象変更は明示承認の手順に従う。モデルなしのJSON試験成功から実効trustや実機継続成功を推定しない。

## 受入

モデルなしfixtureで、全完了、実行中、返却済み未受入、未回収、公開/read-back漏れ、真の権限待ち、独立実行可能作業、起動枠上限、タスク予算、明示停止、他Root/他タスク、不正入力、回数/時間上限、反復呼出しを検証する。

終了・権限・証拠基準に影響するため、実装後に独立した敵対的レビューを実施し、指摘を修正・記録した。fixtureと静的レビューは実機のStop受入と分ける。実機AI試験とhook有効化は別途承認された場合だけ実施する。
