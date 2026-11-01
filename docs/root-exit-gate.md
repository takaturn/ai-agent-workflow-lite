# Root終了ゲート（Issue #15）

## 目的と境界

Rootが承認済みで実行可能な残作業を持ったまま応答を終了し、ユーザーに継続指示を求めることを減らす。終了時に、Rootが宣言した必要作業と、Rootのpaneから起動した子runのsummaryを照合する。自然言語の「done」では完了としない。

想定する相手は「作業を忘れて終了するAI」であり、意図的な改ざんや迂回ではない。stateの手書き換え・削除・raw CLIでの迂回は防がない。checkerはモデル・Herdr・GitHubを呼ばず、paneの停止・削除、成果の受入、外部記録の更新もしない。

## 仕組み

RootとしてCodexとClaude Codeの両方を使う。各CLIのユーザー設定（`~/.codex/hooks.json`、`~/.claude/settings.json`）に、SessionStartとStopの2つのhookとして同じ `.agents/scripts/root-exit-dispatch.py` を1回だけ登録する。両CLIのhookは入出力（`session_id`、`cwd`、`stop_hook_active`、`decision:"block"`、`continue:false`、`additionalContext`）が共通なので、dispatcherは1つで済む。Rootが手動で登録する手順はない。

1. **対象の判定（自動）**: 次のいずれかに当たるsessionでは何もしない（`{}` を返す）。
   - Herdr外のsession（`HERDR_ENV=1` と `HERDR_PANE_ID` がない）
   - cwdがこのworkflowのrepository（`.agents/scripts/root-exit-check.py` を持つ）の外
   - `run-agent.sh` が子として作ったpane（summaryの `pane_id` に一致し、まだcleanupされていない）

   上記以外のsessionはRootとして扱う。
2. **SessionStart**: そのsessionのIDと、作業一覧の置き場所・書き方をRootの文脈へ渡す。
3. **作業一覧（Rootが書く唯一の入力）**: `${XDG_STATE_HOME:-~/.local/state}/ai-agent-workflow-lite/root-exit/<session_id>/work.json`

   ```json
   {"version": 1, "task_id": "issue-15", "stop": null, "out_of_scope": null,
    "work": [{"id": "focused-tests", "kind": "root", "state": "ready"},
             {"id": "issue-update", "kind": "publish", "state": "permission"}]}
   ```

   - `kind`: `root|child_launch|publish|read_back`
   - `state`: `done|ready|permission|external`
   - 確認済みのものだけを `done` にする。
   - 追跡するタスクがないsessionは、`out_of_scope` に理由を書き、`work` を `[]` にする。`work` が空で `out_of_scope` もない雛形は宣言として受け付けない。
   - `stop` には、ユーザーの明示停止（`explicit_stop`）か予算停止（`task_budget`）のときだけ値を入れる。
   - `task_id` は `LAUNCH_TASK_ID` と同じ値で、起動ゲートの明示停止と起動上限を参照するために使う。
4. **子run（自動）**: `ai-agent-workflow-lite/*/run.*/summary.json` のうち、`parent_pane_id` が自分のpaneのものだけを対象にする。他Rootのrunはpaneが違うので対象外になる。同じpaneを使った以前のsessionの残りを拾わないように、`task_id` があれば一致するrunだけに絞り、なければsession開始（初回hook時に作る `session-began`）以後に更新されたrunだけに絞る。
   - 未回収・未受入・未cleanupは次の作業として扱う。
   - 未解決の失敗は、自動継続しないブロッカーとして扱う。Rootは診断後に `finish-agent.sh --failed <summary> '<診断>'` で解決を記録する（残っている子paneは本人確認のうえ閉じる）。
   - 受入・cleanup済みで、`acceptance_note`（失敗なら `failure_resolution`）があるものは完了として扱う。
   - 読めないsummaryは所有者が分からないので無視する。
5. **Stop**: 次の順に判定する。
   1. 作業一覧がない、または不正なら、sessionごとに1回だけblockして宣言や修正を求める。それでも宣言がなければ `work_not_declared` で停止する。未宣言を完了とは扱わない。
   2. 停止の宣言（work.jsonの `stop`、起動ゲートの `explicit_stop`）を最優先する。
   3. 実行可能な作業があれば `decision: block` で継続させる。継続promptには作業IDと次の操作だけを入れ、ユーザーの文やsummaryの文面は含めない。
   4. 権限待ち・外部待ちだけが残っていれば停止する。何も残っていなければ完了とする。
   5. 継続には上限を設ける。ユーザーが始めたturn（`stop_hook_active=false`）ごとに最大10回・3600秒で、上限に達したら停止する。hookによる継続はすべて同じturnの回数に数える。
   6. lock競合、または案内した後もwork.jsonが不正な場合は `continue:false` で停止する。完了とも継続とも扱わない。

状態はsessionごとのdirectory（`work.json`、`continuation.json`、`.lock`）に置く。

## 導入（人の操作はCLIごとに1回）

`/ABSOLUTE/CHECKOUT` を、採用したrevisionのcheckoutの絶対pathに置き換えてから、次のように追加する。

- Codex: `config/root-exit-hook.example.json` を `~/.codex/hooks.json` の既存のSessionStart・Stop配列へ追加する。sandboxが `workspace-write` の場合は、Rootが `work.json` を書けるように `~/.codex/config.toml` の `[sandbox_workspace_write].writable_roots` へ `~/.local/state/ai-agent-workflow-lite/root-exit` の絶対pathを追加する。
- Claude Code: `config/root-exit-hook.claude.example.json` を `~/.claude/settings.json` の `hooks` へ追加する。

その後、各CLIの `/hooks` で次を確認する。

- sourceがユーザー設定である
- enabledである
- trustedである

Codexではtrustをhookの定義のhashごとに行う。定義を変えたら再確認する。repositoryの `.codex/hooks.json` と `.claude/settings.json` にはStopを置かない。全layerのhookが並行して実行されるため、置くと二重に実行される。

公式仕様（https://learn.chatgpt.com/docs/hooks、2026-11-01確認。ローカルは `codex-cli 0.166.0`）との対応は次のとおり。

- 全layerのhookはマージされる。
- Stopは入力に `session_id`、`cwd`、`stop_hook_active` を含み、出力の `decision:"block"` と `reason` で継続する。`continue:false` は継続より優先する。
- SessionStartは `hookSpecificOutput.additionalContext` で文脈を追加できる。
- hook環境に `HERDR_PANE_ID` が渡ることは、下記の実機確認で両CLIとも確認した。
- Claude Code（https://docs.claude.com/en/docs/claude-code/hooks）もStop/SessionStartで同じ入出力を使う。
- 未確認: resumeやfork後のsession IDの扱い、CLI側の連続blockの上限。

## 手動確認

```bash
python3 -B .agents/scripts/root-exit-check.py --declaration PATH/work.json --parent-pane-id "$HERDR_PANE_ID"
```

完了なら終了code 0、それ以外は2。`python3 -B tests/test-root-exit-check.py` と `tests/test-root-exit-dispatch.py` はfixtureによる検証であり、hookの有効化や実機受入の証拠ではない。

## 実機確認（2026-11-01、revision 882b0e3）

普段の起動経路（Herdrのpane、ユーザー設定のhook）で確認した。

- **Claude Code 2.1.316がRoot：**
  - 作業一覧がないときにStopが1回だけblockした。
  - 宣言した作業を残して終了しようとすると継続させた。
  - 子run（Codex light）が実行中のときは `collect_result`、回収後は `verify_and_accept` を挙げた。
  - `finish-agent.sh` で受入とcleanupをした後は、その子runを残作業から外した。
  - CI結果の確認を作業一覧に入れたところ、結果が出るまで終了させなかった。
  - 権限待ちだけが残ったときは `blocked_work` で終了した。
  - 子runの回答内容は誤りで、受入の記録で不合格としている（今回の確認対象はlifecycle）。
- **Codex 0.166.0（gpt-6-luna）がRoot：**
  - SessionStartの案内を受けて、作業の前に `work.json` を宣言した（writable_roots追加後で、承認は求められなかった）。
  - 完了後は `all_declared_checks_complete` で終了した。
  - 制御入力で、作業をreadyのまま終了するよう指示した。Stopは3回継続させ、作業が済んだ後に完了で終了した。
- **停止優先・継続上限・分離（Codex Root、制御入力）：**
  - readyの作業を実行しないよう指示したところ、Stopは10回継続させた後、`continuation_count_limit` で停止した。
  - 次のturnでwork.jsonを `explicit_stop` にすると、readyの作業が残っていても継続させずに停止した。
  - 別のRoot（Claude Code）のpaneから起動した子runが受入前のまま残っていても、Codex Rootは影響されずに完了で終了した。その子runは、起動したRoot側で `verify_and_accept` として挙がった。
  - Rootの数え間違いで起動枠を使い切ったrunは `prelaunch_error` となり、paneは作られなかった。当時は `failure_resolution` を記録するhelperがなく、Rootがsummaryに直接記録した。その後 `finish-agent.sh --failed` を追加した。
- **事前に分かっていたこと：**
  - 子Claudeの起動は、#24の版チェック（`2.1.314` のみ）に予約前で拒否され、起動枠は消費されなかった。
- **未確認：**
  - forkの扱い
  - Claude Codeのresume後もsession IDが保たれることは確認した。Codexのresumeは未確認

## 旧revisionでのCodex実機確認（2026-10-31）

手動登録方式の旧checkerを、専用環境で制御入力を使って試験した記録。現行のdispatcherの受入証拠には使わない。


Codex CLI 0.166.0、gpt-6-luna/lowをHerdr管理で1回起動し、同一sessionで8ケースを確認した。人が専用projectとStop定義をtrustし、その定義のtrust記録と実際のStop呼出しを確認した。配布するcheckerと同一バイトのソースを使用した。

- 未完了作業を残した終了試行をblockし、CLIが継続してmarkerを作成。その存在を試験adapterが確認して作業stateをdoneにし、次のStopで完了終了した。
- 全完了、真の承認待ちを表すstate、明示停止state、予算停止state、不正入力、継続上限到達stateの各ケースで、期待した停止理由を返した。
- 未完了を保持したケースでは実際に2回blockし、3回目のStopで継続回数上限として停止した。

全11回のStop入力でsession/cwdが一致し、開始から10分以内に終了した。各ケースはRootが独立したfixture入力として初期化している。自然な仕事でAIが必要作業を漏れなく登録できること、実際のprovider予算枯渇やCtrl-Cへの対応を証明する試験ではない。

試験adapterは完了markerからfixtureのdoneを更新する専用補助であり、配布checkerが成果の正しさを自動証明するという意味ではない。本番RootへStop hookは有効化していない。起動時のtrust待ちは同じCLIで人手確認後に復帰し、追加CLIは起動しなかった。runnerの初期failed記録は保持し、試験結果は別の受入記録で管理した。試験後、対象paneだけを閉じ、親layoutを読み戻してcleanupを確認した。
