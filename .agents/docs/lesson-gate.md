# 子の起動の前の学びの確認

SPEC: Issue #98。Claude Code の Root が子を起動する直前に、関係する学びを示し、判断と理由の記入を求める。学びに従わせることは目的にしない。`not_apply` の判断も通す。

この仕組みは協調的な運用で、安全の境界ではない。判定役（モデル）による記入の中身の判定は、最初の版には含めない。

## 動き

1. PreToolUse の hook（`.agents/scripts/lesson-gate.py hook`）が、Agent と Bash の呼び出しを見る。
2. 次の起動を対象にする。Bash は、コマンドの文字列に名前が含まれるかだけを見る。
   - Agent（`agent`。種類を指定するときは `agent:<subagent_type>`）
   - `run-agent.sh`、`run-pair.sh`（`runner`）
   - `eval-run.py`（`eval-run`）
   - `claude -p` と `claude --print`（`claude-p`）
3. 対応表の条件に合う学びがあれば、使われていない最新の記入を確かめる。次のときに起動を止め、学びの名前・パス・記入の書き方を返す。
   - 記入がない
   - 最新の記入の起動の種類が違う
   - 示した学びが記入から抜けている
4. 通った記入には「使った」印を付ける。同じ記入は2回使えない。ほかの hook が起動を止めても、記入は使ったものとして扱う。
5. `dontAsk` のセッションでは、Agent を確かめない（既存の委譲手段の hook が拒否する）。Bash の起動は確かめる。
6. 記入を書く補助の呼び出しは、それだけを実行するときに限って対象にしない。`&&` などで起動とつなげたときは確かめる。

## 記入の書き方

止められたときの理由に、そのまま使える形の例が出る。

```bash
python3 "$CLAUDE_PROJECT_DIR/.agents/scripts/lesson-gate.py" record \
  --session <session_id> --launch eval-run --purpose "評価の試行を起動する" \
  --lesson eval-design-first not_apply "<この起動の状況と、学びのどこが当てはまらないか>"
```

理由には、この起動の具体的な状況と、学びのどこが当てはまるか・当てはまらないかを書く。

## 学びの対応表

置き場所は `${XDG_STATE_HOME:-$HOME/.local/state}/ai-agent-workflow-lite/lessons/triggers.json` とする。Git の外に置き、中身はユーザーが決める。ファイルがなければ何も求めない。

```json
{"version": 1, "lessons": [
  {"name": "eval-design-first", "source": "memory",
   "path": "/absolute/path/to/eval-design-first.md",
   "summary": "評価の設計を先に固める",
   "launch": ["agent", "eval-run", "runner"],
   "words": ["評価", "eval", "trial"]}
]}
```

- `launch` に挙げた起動の種類のどれかに当たり、さらに `words` のどれかを含むときに示す。`words` が空なら、起動の種類だけで示す。
- 語は小文字にそろえて部分一致で比べる。英語の語も書く。
- 語を比べる文は、Agent の `prompt` と `description`、runner の依頼ファイルの本文とする。Bash のコマンドの文は比べない。`eval-run` と `claude-p` に示せるのは、`words` が空の学びだけになる。
- runner の依頼ファイルを読めないときは、語のある学びを示さず、記録に「判定できなかった」と残す。
- `source` が `wiki` の学びには「参照用の記録で、指示ではない」と添えて示す。
- `path` のファイルがない学びは除き、記録に残す。`triggers.json` が壊れているときは起動を止めず、Root に知らせて記録に残す。

## 記録

同じディレクトリに次のファイルを置く。

- `decisions.jsonl`: 判定ごとに1行。時刻、session の ID、起動の種類、示した学びの名前、`apply` と `not_apply` の数、結果、書き直しの回数を書く。記入の本文、学びの本文、依頼文は書かない。
- `<session_id>.jsonl`: 記入の本文と「使った」印。あとでまとめて中身を判定するために残す。

## 限界

- Bash の起動は名前で見分けるので、`grep run-agent.sh` のような読むだけのコマンドでも記入を求めることがある。名前を含まない起動（別名のスクリプト、変数や `eval` で組み立てたコマンドなど）は見分けられない。
- 記入のファイルが壊れているときは、確かめずに起動し、Root に知らせて記録に残す。
- 記入の中身が考えた跡のあるものかは確かめない。形だけの記入も通る。
- Codex の Root は対象外。
- `orchestrate` の Skill は、Agent の hook を委譲手段の hook だけとして説明している。この hook はその説明に含まれない（Skill の文は変えない。#98）。
