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
| `.claude/settings.json` | Claude Code利用時 | `hooks.PreToolUse` 配列へpolicy guardのentryを追加してマージする。既存のhookは削除しない |
| `.claude/skills/` | Claude Code利用時 | `.agents/skills/` へのsymlink。同名のskillがあれば上書きせず停止して判断する |
| `.codex/hooks.json` | Codex利用時 | `hooks.PreToolUse` 配列へpolicy guardのentryを追加してマージする |
| `.github/ISSUE_TEMPLATE/` | 任意 | 既存テンプレートがあれば上書きしない |
| `.github/workflows/test.yml` | コピーしない | 本リポジトリの `tests/` を実行するCIのため |
| `.herdr/` | コピーしない | 実行時に作られるworktree等で、Git管理外 |
| `README.md`、`LICENSE` | コピーしない | 本リポジトリ自身の説明・ライセンス |

## 導入後の確認

1. 展開先でコピー内容をfeature branchへcommitし、ユーザーが内容を確認した後、人の端末で `python3 .agents/scripts/policy-guard.py approve --approval-ref <一意な承認参照>` を実行してから `python3 .agents/scripts/install-gitleaks-hook.py` を実行する（baseに保護対象がない状態ではinstallerのbootstrapが使えないため、approveを先に行う）。pushには送信先remoteにbaseブランチ（例: `origin/main`）が必要。
2. `.agents/config/publication-policy.json` の保護対象が展開先に存在することを確認する。
3. 展開先のルート直下に `config/`・`docs/`・`scripts/` が作られていないことを確認する。
