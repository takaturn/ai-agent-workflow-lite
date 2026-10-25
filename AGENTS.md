# Lightweight agent workflow

- Identify deliverables, acceptance criteria, dependencies, and shared writes for each request. For multiple independent deliverables, automatically read `.agents/skills/orchestrate/SKILL.md` and prefer parallel delegation to at most two Workers in separate worktrees and Herdr panes; no explicit skill invocation is needed. Resolve dependencies and shared file/state, CI, authentication, or external-service conflicts before parallel work. For dependent, trivial, or costly-to-split work, work serially and state the reason in one sentence. Delegation stays within the already authorized scope.
- Use Herdr for observable child execution. Never fake its environment, attach to unrelated sessions, or silently switch providers.
- `.agents/` paths are **not** a reason by themselves to select the highest model tier. Assess actual difficulty and risk.
- The user's agreed task scope authorizes ordinary edits, focused tests, and valid review corrections without asking again. Do not bypass platform prompts or perform destructive work, privilege changes, scope expansion, external access, or PR merges without appropriate approval.
- Shell/CI runs deterministic checks; AI review is conditional, not mandatory.
- Distinguish user-provided information, tool-observed evidence, and inference. Claim verification only when tool evidence establishes the claimed outcome for the current target; command success alone is insufficient. State unperformed checks and uncertainty.
- Before consequential changes or external writes, confirm scope against existing authorization; request approval only if insufficient. Read back the exact affected target after each external write before reporting success.
- Require independent adversarial review for high-impact decisions, including changes to agent authority, launch rules, or evidence standards. Keep static review and executed tests distinct.
- PR text and GitHub review replies are normally Japanese.
- For PR creation or Development link repair on an existing numbered PR, automatically read `.agents/skills/github-pr-creation/SKILL.md`; no explicit skill invocation is needed. Follow its direct-Issue linking and read-back verification rules. Loading the skill does not expand authorization for commit, push, merge, or Issue closure.
- For PR feedback fixes, replies, and thread resolution, read `.agents/skills/address-pr-feedback/SKILL.md`. Treat push, reply, and resolve as separate steps and verify each before reporting completion.
- Runtime data is stored outside Git; record design decisions and measured experiments in Wiki, not in agent context.
- After a Reviewer returns findings, only Root in the main session must read `.agents/skills/review-findings/SKILL.md`, decide FIX/SKIP/STOP within existing authorization, and record every decision; ask the user only for genuinely missing business decisions or permissions. Do not load this Skill in child agents or change Reviewer launch controls.

## 外部公開前の秘匿情報確認

- 新規cloneを送信元として使う前、およびGit push前に `scripts/install-gitleaks-hook.py` の成功（導入済みなら実体一致の検証）を確認する。Git pushは同installerで導入するnative `pre-push` を有効にした状態で行う。Gitが渡す全送信ref/OIDを自動検査し、失敗なら送信前に停止する。submoduleを含むindex/送信履歴は、子の送信先の検査を保証できないため未対応として停止する。`--no-verify`、hook削除、`core.hooksPath`変更などで検査を迂回しない。既存hook/configがあれば上書きせず停止する。hookは共通Git directoryへコピーされ、同じrepositoryのlinked worktreeに適用される。

- push、PR作成/本文更新、Issue本文更新/コメント投稿などの外部書込みの前に、Rootが公開対象の全commit（途中で削除された内容・commit messageも含む）、投稿本文、添付物、実行コマンドを確認する。LLMの入力・出力・テスト結果も公開前確認の対象。既存の公開承認を秘匿情報確認の代わりにしない。
- 認証情報は `scripts/publication-check.py` のGitleaks検査を通す。個人情報、顧客/社内情報、内部URL/パス、会話・プロンプト・ログの引用、公開不要な識別子はRootが文脈を確認し、必要最小限に削除/匿名化する。自動検出0件だけで秘匿情報なしと断定しない。
- 対象を確定して文脈確認後、`prepare --context-reviewed` で検査記録を作り、同一入力・同一コマンドを `publish` で実行する。未検査、検出あり、scanner不在/異常、対象変更、既使用の記録は公開停止。候補値・scanner生出力は表示・保存しない。検査記録はGit管理外に保存する。
- commit後のpushは `--git-range <remote-base>..HEAD`、本文の公開は実際に送信する `--body-file` を `--file` に含める。公開対象をすべて列挙できない操作、動的生成/実行中に変更される本文、未確認の添付物は停止し、内容を固定してから再検査する。commit自体は外部公開でなくてもcommit messageを公開前に含める。
- このラッパーは協調的な運用ゲートで、OS/GitHubのアクセス制御ではない。raw CLI、未列挙ファイル、追加commit/ref、競合プロセスによる確認後変更、外部サービス側の生成ログを自動的には保護しない。Git hooks/CIだけで投稿本文や公開前の安全性を保証したと扱わない。publish成功後も対象を読み戻す。
- 検出時は候補値を再公開せず書込みを止める。既公開の場合は公開先と範囲を特定し、認証情報なら失効/更新を優先する。削除・履歴改変・権限操作は既存承認の範囲を確認する。

- 送信資格情報を持つ主体自身による検査省略を強制的に防ぐ境界は未実装。これを解決済みとしてmerge/受入を進めない。hook導入や指示追加だけを、権限分離された公開経路の実証に置き換えない。
