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
   `git status --short`、`git branch --show-current`、`gh pr view <N> --json headRefName,headRefOid,baseRefName,state`で作業先を確認する。`gh pr view <N> --comments`、`gh api repos/<owner>/<repo>/pulls/<N>/comments --paginate`、`gh api repos/<owner>/<repo>/pulls/<N>/reviews --paginate`で全指摘を読む。GraphQLでreviewThreadsも取得し、全ページを辿ってthread ID・isResolved・返信を確認する。完了条件: 対象PRの全指摘と既存返信を把握した。

2. 指摘を分類する。
   一時メモにコメントID、thread ID、優先度、妥当性、対応/保留、検証、返信/resolve状態を対応付ける。優先度はP0が最優先。ユーザーが「P2以上」と指定した場合はP0/P1/P2を対象とする。自動レビューも鵜呑みにせず現行コードで確認する。対象外、誤指摘、仕様判断待ちは理由を説明し、勝手にresolveしない。完了条件: 全指摘の扱いが明確。

3. 修正して検証する。
   妥当な不具合は回帰テストで修正前のFAILを確認してから最小修正し、PASSを確認する。拒否テストは非ゼロだけでなく拒否理由も確認する。関連する既存テスト・構文/lint・`git diff --check`を実行する。完了条件: 対応対象の各不具合に実行証跡がある。

4. 承認済みならcommit/pushする。
   対象ファイルだけstageし、意図したbranchへpushする。`gh pr view <N> --json headRefOid`でPRが修正commitを指すことを確認し、`gh pr checks <N> --watch`で最新CIを確認する。失敗・pendingは成功と報告しない。完了条件: 修正がPRに反映され、最新CIの実状態を把握した。

5. 各指摘へ日本語で返信する。
   修正commit、変更点、テストと結果、残る制約を短く返信する。インライン返信は `gh api repos/<owner>/<repo>/pulls/<N>/comments/<comment-id>/replies -f body='<返信>'`。返された返信IDを `gh api repos/<owner>/<repo>/pulls/comments/<reply-id>`で読み戻す。同内容の返信が既にあれば重複投稿せず、現状に十分か確認する。完了条件: 各対応済み指摘に検証可能な返信がある。

6. 返信とresolveを別工程として実行する。
   修正・検証・返信が揃い、resolveが承認されているスレッドだけGraphQL mutationでresolveする。コメントIDをthread IDに代用しない。以下のqueryを `gh api graphql -f query='<query>' -f id='<thread-id>'`で実行する。
   ```graphql
   mutation($id:ID!) {
     resolveReviewThread(input:{threadId:$id}) {
       thread { id isResolved }
     }
   }
   ```
   再度reviewThreadsを取得して対象のisResolvedがtrueであることを確認する。途中で追加された指摘も把握する。完了条件: 対応済みの対象スレッドすべてが返信済み・resolvedで、対象外や未解決の指摘を隠していない。

7. 短く報告する。
   修正commit、チェック結果、返信/resolveの確認結果、残課題を報告する。設計判断・実験結果は必要に応じてWiki/Issueへ共有し、公開後に読み戻す。PR mergeやIssue closeは別の承認として扱う。

## Pitfalls

- commit/push、返信、resolveは別の状態。どれか一つだけで「対応完了」としない。
- replyを投稿しただけではスレッドはresolvedにならない。
- 全スレッドの一括resolveは禁止。未修正、CI失敗、判断待ち、優先度対象外は残す。
- ページ先頭だけで件数を確定しない。GraphQLのhasNextPageとendCursorを確認する。
- resolveは技術的な対応状態であり、レビュアーの承認やPR mergeを意味しない。

## Verification

- 全指摘を取得・分類した。
- 対応した不具合の回帰テストと既存チェックを実行した。
- PRのhead SHAと最新CIを確認した。
- 各返信を読み戻した（既存返信は重複しない）。
- 各対応対象のisResolvedを再取得して確認した。
- 未対応・保留とその理由を最終報告した。
