---
name: address-pr-feedback
description: Address PR feedback through verified replies and resolution.
---

# PR指摘対応

## When to use

PRのレビュー指摘を修正する、返信する、スレッドをresolveするときに読む。レビューを新規依頼する手順やPR mergeは含めない。

## Prerequisites

`gh`の認証と対象PRへの権限が必要。`terminal`でGit/ghを実行し、`read_file` / `search_files`で調査、`patch` / `write_file`で編集する。権限不足は回避せず報告する。

## Procedure

1. 対象と現状を確認する。
   最初に今回の依頼と既存の承認から、修正・commit・push・返信・resolveの各工程を「承認済み／未承認／対象外」に分類する。工程ごとの承認を毎回取り直す必要はないが、依頼範囲を勝手に広げない。以降は承認済み工程だけ実行し、他はスキップして理由を最終報告する。修正済みのPRへの返信/resolveだけなら、新たな修正やcommitは不要。
   `git status --short`、`git branch --show-current`、`gh pr view <N> --json headRefName,headRefOid,baseRefName,state`で作業先を確認する。`gh pr view <N> --comments`、`gh api repos/<owner>/<repo>/pulls/<N>/comments --paginate`、`gh api repos/<owner>/<repo>/pulls/<N>/reviews --paginate`で全指摘を読む。GraphQLでreviewThreadsも取得し、全ページを辿ってthread ID・isResolved・返信を確認する。完了条件: 対象PRの全指摘と既存返信を把握した。

2. 指摘を分類する。
   一時メモにコメントID、thread ID、優先度、妥当性、対応/保留、検証、返信/resolve状態を対応付ける。優先度はP0が最優先。ユーザーが「P2以上」と指定した場合はP0/P1/P2を対象とする。自動レビューも鵜呑みにせず現行コードで確認する。対象外、誤指摘、仕様判断待ちは理由を説明し、勝手にresolveしない。完了条件: 全指摘の扱いが明確。

3. 修正して検証する。
   修正が承認済みの場合だけ実行する。返信/resolveだけの場合は既存の修正と検証証跡を確認し、不十分なら未解決として報告する。
   妥当な不具合は回帰テストで修正前のFAILを確認してから最小修正し、PASSを確認する。拒否テストは非ゼロだけでなく拒否理由も確認する。関連する既存テスト・構文/lint・`git diff --check`を実行する。完了条件: 対応対象の各不具合に実行証跡がある。

4. 承認済みならcommit/pushする。
   commitとpushは別々に範囲を確認する。commitのみならpushしない。修正のみなら両方をスキップし、未コミットの差分とローカル検証を報告する。
   commit対象なら対象ファイルだけstageしてcommitを確認する。push対象なら意図したbranchへpushし、`gh pr view <N> --json headRefOid`でPRが修正commitを指すことを確認し、`gh pr checks <N> --watch`で最新CIを確認する。失敗・pendingは成功と報告しない。完了条件: 承認されたcommit/push工程だけが検証まで完了した。

5. 各指摘へ日本語で返信する。
   返信が承認済みの場合だけ投稿する。resolveのみの場合は既存返信を読み戻し、十分な対応説明がなければ投稿へ範囲を広げず、resolveを保留して報告する。
   修正commit（存在する場合）、変更点、テストと結果、残る制約を短く返信する。インライン返信は `gh api repos/<owner>/<repo>/pulls/<N>/comments/<comment-id>/replies -f body='<返信>'`。返された返信IDを `gh api repos/<owner>/<repo>/pulls/comments/<reply-id>`で読み戻す。同内容の返信が既にあれば重複投稿せず、現状に十分か確認する。完了条件: 各対応済み指摘に検証可能な返信がある。

6. 返信とresolveを別工程として実行する。
   修正・検証・返信が揃い、resolveが承認されているスレッドだけGraphQL mutationでresolveする。コメントIDをthread IDに代用しない。以下のqueryを `gh api graphql -f query='<query>' -f id='<thread-id>'`で実行する。
   ```graphql
   mutation($id:ID!) {
     resolveReviewThread(input:{threadId:$id}) {
       thread { id isResolved }
     }
   }
   ```
   resolveが未承認/対象外ならこの工程をスキップする。実行した場合は再度reviewThreadsを取得して対象のisResolvedがtrueであることを確認する。途中で追加された指摘も把握する。完了条件: resolve対象だけが返信済み・resolvedで、対象外や未解決の指摘を隠していない。

7. 短く報告する。
   承認された工程がすべて検証・読み戻しまで完了したら、その依頼範囲の完了として報告する。修正commit（作成した場合）、チェック結果、返信/resolveの確認結果、残課題を報告し、実施しない工程は未承認/対象外と明示する。Wiki/Issue共有は承認された範囲で実行し、公開後に読み戻す。PR mergeやIssue closeは別の承認として扱う。

## Scope examples

- 修正のみ（pushしない）: 修正とローカル検証で依頼完了。commit/push/返信/resolveは承認がなければ実施しない。
- 返信のみ（resolveしない）: 既存修正の証跡確認、返信投稿と読み戻しで依頼完了。新規修正/commit/push/resolveは実施しない。
- 既存返信を確認してresolveのみ: 既存修正・検証・返信を読み戻し、resolveとisResolvedの再取得で依頼完了。重複返信や新規修正/commit/pushは実施しない。証跡不足なら保留する。

## Pitfalls

- commit/push、返信、resolveは別の状態。全工程の対応依頼なら一部だけで完了としない。修正のみ/返信のみ等の限定依頼は、その承認範囲の検証完了で完了とし、未承認工程へ進まない。
- replyを投稿しただけではスレッドはresolvedにならない。
- 全スレッドの一括resolveは禁止。未修正、CI失敗、判断待ち、優先度対象外は残す。
- ページ先頭だけで件数を確定しない。GraphQLのhasNextPageとendCursorを確認する。
- resolveは技術的な対応状態であり、レビュアーの承認やPR mergeを意味しない。

## Verification

- 全指摘を取得・分類した。
- 修正対象なら回帰テストと既存チェックを実行した。既存対応の返信/resolveのみなら、修正と検証証跡を確認した。
- push対象ならPRのhead SHAと最新CIを確認した。commitのみならローカルcommit、修正のみなら差分とローカルチェックを確認した。
- 返信対象なら各返信を読み戻した（既存返信は重複しない）。
- resolve対象なら各対象のisResolvedを再取得して確認した。
- 未承認/対象外の工程は実施せず、その理由を明示した。
- 未対応・保留とその理由を最終報告した。
