# 保護対象更新の運用

状態: Issue #85 でローカルの承認フロー（policy guard）を廃止した。以前の設計（承認済みguard・承認state・PreToolUse通知）の経緯はWikiとIssue #22・#36を参照する。

## 現在の境界

- 保護対象（`.agents/config/publication-policy.json`）を変更したcommitも、ローカルの承認なしにcommit・push・PR作成できる。
- 人の確認はPRのmergeに一本化する。CIは保護対象の差分一覧をjob summaryへ表示するが止めない（`.agents/scripts/protected-changes.py`）。
- `install-gitleaks-hook.py --upgrade` は、remoteがHEADとして示す既定branchにmerge済みの版にだけ置き換える。
- 公開前チェックは保護対象のsnapshotをreceiptへ固定し、prepare後の変更で送信を止める。
- AIがmergeしないことは協調的な運用ルールで、技術的には強制されない。raw CLI等の意図的迂回の隔離は保証しない。

既存cloneの移行手順はREADMEの「既存cloneの移行（Issue #85）」にある。
