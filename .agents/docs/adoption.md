# 導入時のコピー対象

本リポジトリは雛形として、他リポジトリへ**コピー**で導入する。installerやsubtreeは使わない。配布物は `.agents/` に集約しているため、展開先のルート直下に `config/`・`docs/`・`scripts/` は作られない。

## 区分

- `.agents/`: 配布物（skills・scripts・config・docs）
- ルートの `tests/`: 本リポジトリ専用の開発用テスト。コピーしない

## コピー対象と衝突時の方針

| 対象 | 必須 | 展開先に既存がある場合 |
|---|---|---|
| `.agents/` | 必須 | `.agents/skills` などに既存資産があれば、同名ファイルを上書きせず差分を確認してマージする |
| `AGENTS.md` | 必須 | 既存の記述を残し、本リポジトリの規則を追記してマージする |
| `CLAUDE.md` | Claude Code利用時 | 既存の記述を残し、`AGENTS.md` を参照する1行を追記する |
| `.claude/settings.json` | Claude Code利用時 | `hooks.PreToolUse` 配列へ `matcher: "Agent"` の委譲手段hookのentryを追加してマージする。既存のhookは削除しない。委譲手段hookは `$CLAUDE_PROJECT_DIR/.agents/skills/orchestrate/scripts/` を実行し、`sh`、`timeout`、`python3` が必要（ないとAgentの呼び出しが毎回確認になる）。`dontAsk` のセッションではAgentを拒否する |
| `.claude/skills/` | Claude Code利用時 | `.agents/skills/` へのsymlink。同名のskillがあれば上書きせず停止して判断する |
| `.github/ISSUE_TEMPLATE/` | 任意 | 既存テンプレートがあれば上書きしない |
| `.github/workflows/test.yml` | コピーしない | 本リポジトリの `tests/` を実行するCIのため |
| `.herdr/` | コピーしない | 実行時に作られるworktree等で、Git管理外 |
| `README.md`、`LICENSE` | コピーしない | 本リポジトリ自身の説明・ライセンス |

## 導入後の確認

1. 展開先で `python3 .agents/scripts/install-gitleaks-hook.py` を実行する。保護対象の変更はPRで確認し、人がmergeする（ローカルの承認は不要。Issue #85）。
2. `.agents/config/publication-policy.json` の保護対象が展開先に存在することを確認する。
3. 展開先のルート直下に `config/`・`docs/`・`scripts/` が作られていないことを確認する。
