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
