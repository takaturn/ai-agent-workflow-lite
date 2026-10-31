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

- 新規cloneを送信元として使う前、およびGit push前に `scripts/install-gitleaks-hook.py` の成功（導入済みなら実体一致の検証）を確認する。Git pushは同installerで導入するnative `pre-push` を有効にした状態で行う。Gitが渡す全送信ref/OIDを自動検査し、失敗なら送信前に停止する。submoduleを含むindex/送信履歴は、子の送信先の検査を保証できないため未対応として停止する。`--no-verify`、hook削除、`core.hooksPath`変更などで検査を迂回しない。既存hook/configがあれば上書きせず停止する。ただし保護対象の変更をcommitし人の端末で再承認した後、既存hookが以前の承認済みcommit内の同一ソースと完全一致する場合だけ、`scripts/install-gitleaks-hook.py --upgrade-approved` で原子的に更新できる。symlink、未知のhook、`core.hooksPath` 設定、承認前の更新は拒否する。hookは共通Git directoryへコピーされ、同じrepositoryのlinked worktreeに適用される。

- push、PR作成/本文更新、Issue本文更新/コメント投稿などの外部書込みの前に、Rootが公開対象の全commit（途中で削除された内容・commit messageも含む）、投稿本文、添付物、実行コマンドを確認する。LLMの入力・出力・テスト結果も公開前確認の対象。既存の公開承認を秘匿情報確認の代わりにしない。
- 認証情報は `scripts/publication-check.py` のGitleaks検査を通す。個人情報、顧客/社内情報、内部URL/パス、会話・プロンプト・ログの引用、公開不要な識別子はRootが文脈を確認し、必要最小限に削除/匿名化する。自動検出0件だけで秘匿情報なしと断定しない。
- 対象を確定して文脈確認後、`prepare --context-reviewed` で検査記録を作り、同一入力・同一コマンドを `publish` で実行する。未検査、検出あり、scanner不在/異常、対象変更、既使用の記録は公開停止。候補値・scanner生出力は表示・保存しない。検査記録はGit管理外に保存する。
- commit後のpushは `--git-range <remote-base>..HEAD`、本文の公開は実際に送信する `--body-file` を `--file` に含める。公開対象をすべて列挙できない操作、動的生成/実行中に変更される本文、未確認の添付物は停止し、内容を固定してから再検査する。commit自体は外部公開でなくてもcommit messageを公開前に含める。
- このラッパーは協調的な運用ゲートで、OS/GitHubのアクセス制御ではない。raw CLI、未列挙ファイル、追加commit/ref、競合プロセスによる確認後変更、外部サービス側の生成ログを自動的には保護しない。Git hooks/CIだけで投稿本文や公開前の安全性を保証したと扱わない。publish成功後も対象を読み戻す。
- 検出時は候補値を再公開せず書込みを止める。既公開の場合は公開先と範囲を特定し、認証情報なら失効/更新を優先する。削除・履歴改変・権限操作は既存承認の範囲を確認する。

- 公開対策の合意範囲はrepo管理・本ルール・hookによる誤操作防止とする。送信主体が意図的にhookを無効化できるリスクはユーザーが受容した。権限分離・迂回不能を実装済みと主張しない。Root全面隔離やpublisher拡張は今回の受入条件に含めない。
- `config/publication-policy.json` の保護対象、hook、承認記録をユーザーの明示承認なしに変更・削除・無効化・迂回する行為は禁止する。検査を通すための自己承認、保護対象の除外、別clone/stateへの切替、検査失敗の無視も禁止する。
- 保護対象の承認は、文脈確認・ユーザー承認済みの正確なcommitに対して `scripts/policy-guard.py approve --approval-ref <一意な承認参照>` で記録する。承認参照は会話の承認を証明するものではない。installerやCI成功を承認の代わりにしない。保護対象の未承認変更は通知し、既存の権限内でAIによる修正・検証・ローカルcommitを許可する。commit作成は承認ではない。公開と実効guard更新は、正確なcommitへのユーザー承認後、人の端末で再承認するまで停止する。承認記録の破損、実効guard不一致、対象識別失敗はtool実行も停止する。hookの停止/削除を更新手順にしない。
- 新規・既存refの保護対象は、送信先として指定した登録済みremoteのbaseブランチに含まれないcommitについて検査する。baseブランチに含まれるcommitは受入済みと扱う。既定のbaseは `refs/remotes/<remote>/HEAD`、`develop` 等では `git -c workflow.baseBranch=develop push ...` と明示する。どのbranchを受入済みbaseとするかは人の運用判断であり、Rootは公開前に選択を確認する。guardは送信先のbase refのOIDと手元のremote-tracking refを照合し、remote名・実送信先URLの不一致、OIDの不一致、base refの欠落、無関係な履歴では停止する。この照合は別接続で、push時の受信側状態を原子的には証明しない。新規cloneでは、現在の保護対象とguardソースが照合済みbaseと完全一致する場合に限り `bootstrap` でbaseのローカル状態を再構成でき、選択したbaseにないfeature側の保護対象変更を承認しない。誤ったbase選択自体は検出できない。状態が消え、featureの保護対象がbaseと異なる場合は停止して正確なcommitの人手再承認を要する。native Gitleaksは新規refの全到達可能履歴を別途検査し、Rootの個別pushの公開前確認も省略しない。

- publication-checkは `--policy-root <canonical repository root>` を必須とし、承認時に配置したguardでprepare時とdispatch直前に検査する。Wiki等の別repositoryから実行する際も、作業を管理する本体のpolicy rootを明示する。検査root・common directory・実効guard識別をreceiptへ固定する。これは協調的運用であり、raw CLIや改変したラッパーの意図的な迂回を隔離しない。
