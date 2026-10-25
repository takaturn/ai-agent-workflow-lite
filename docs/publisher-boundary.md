# 隔離publisherの実装・動作確認と残る切替条件

この実装は、資格情報のない要求側プロセスと、公開操作を行う信頼側publisherを分けるプロトタイプです。`publisher-sandbox.py` は新しいプロセスを起動します。現在動いているCodexセッションや既存のhost tool/API connectorの権限を変更しません。コードを配置・テストしただけでPR #19のP1を解決済みにしないでください。

## 実装した契約

- 要求はversion、operation、固定target/subject、policy digest、期限（最大5分）、nonce、payloadに限定します。未知fieldを拒否します。
- 信頼側の文脈レビュー後、issuerが要求全体へHMAC承認を発行します。署名鍵・policy・publisher stateは要求側workspaceの外へ置き、要求側へ渡しません。Rootの自己申告だけでは送信できません。HMACのissuerとpublisherは同じ信頼領域で、双方を要求側から隔離する構成です。
- publisherは承認を検証し、nonceをSQLiteで消費してから送信します。再起動後も再利用できません。送信結果が不明でも自動返却・自動再送しません。
- 初期Git操作は単一の許可branchへのfast-forward更新のみです。要求は全履歴を含む単一refのbundle、new OID、expected old OIDを指定します。取り込みを資格情報・networkのないnamespaceで行い、raw commit、tree名、全blobを検査します。tag/submodule/binary/未知objectは拒否します。
- Gitは検査後に再生成したbundleをクリーンなbare senderへ取り込み、固定remote/refへ送信します。旧OIDのleaseと祖先関係の両方を確認し、force更新・追加ref・削除を許可しません。送信側にも既存installerでnative pre-pushを導入・照合し、無効化せず実行します。公開後OIDを読み戻します。
- API操作はpolicyに列挙した固定repository/Issue番号へのコメントPOST、body fieldだけです。PR作成/本文更新、Issue更新、返信、thread resolve、任意endpoint/URL/shellは未対応として拒否します。read-backでbodyと返却IDを確認します。
- GitHub APIの認証は信頼側のgh設定を使用し、Git HTTPSでは固定のgh credential helperを使います。App作成や既存認証の移動/失効は自動実施しません。実際のアカウント・permissionの最小化と確認は配備前の条件です。
- 信頼側toolは受理前にworkspace外から固定コピーし、workspace内の実行ファイルを拒否します。Gitleaks不在・検出・異常・timeout、署名不一致、期限切れ、policy/target/subject不一致、旧OID不一致は公開を止めます。scanner生出力・候補値・認証情報は返しません。

## 動作確認

`python3 tests/test-publisher.py` は使い捨てnamespaceとローカルbare receiver/模擬GitHub APIを使用します。bubblewrapが必要で、利用不能を成功やskipに置き換えません。

確認対象は、許可された公開、要求改変/偽造/期限/別scope/未知field/再送/並行再送、scanner検出/異常、binaryの拒否、旧ref競合、source hook/configを持ち込まない取り込み、ホストの秘密/policy/proc/受信先へのアクセス拒否、hostのlisten socketへの到達拒否、署名の自己発行拒否、再起動後のnonce維持、trusted copyの上書き拒否です。

Git取り込みには512 MiBの仮想アドレス上限、64 MiBの一時filesystem、内容/object/時間上限を設けます。Go/Wasm scannerには16 GiBの仮想アドレス上限と256 MiBのGo soft heap制限を別途設定します。仮想アドレス上限は実メモリ使用量のhard limitではなく、配備時のcgroup等による制御は未検証です。

模擬scanner/模擬GitHub APIの成功は実GitHub受入ではありません。`python3 tests/test-publisher-real.py` は、実Git＋実Gitleaksを同じ隔離publisherに接続して、clean snapshotの公開成功と、生成した実在しない認証情報形式の検出時にローカルreceiverが不変であることを確認しました。実在の秘密や外部GitHubへの試験投稿は使っていません。CIも実Gitleaksを8.30.1・公開release assetのSHA256で固定してこの2ケースを実行します。

## 信頼側のステージング

管理者はworkspace外かつGit管理外に、publisher・sandbox launcher・policy・承認鍵を配置します。Rootが書き換えられるrepo内スクリプトをそのまま信頼側サービスの再起動元にしません。

```sh
python3 scripts/install-publisher.py \
  --workspace /absolute/requester-workspace \
  --home /absolute/protected-publisher \
  --policy /absolute/reviewed-policy.json
```

installerはサービスを起動せず、現在のRootも移行しません。既存の異なるtrusted copyやpolicyを上書きしません。policy例（コメント等は初期無効）:

```json
{
  "target": "owner/repository",
  "subject": "approved-requester",
  "git_remote": "https://github.com/owner/repository.git",
  "git_ref": "refs/heads/approved-branch",
  "api": {}
}
```

`file:///` remoteは隔離試験のローカルreceiver用です。管理者が信頼側で選ぶ固定値であり、要求側には選ばせません。実環境では許可したrepositoryのHTTPS remoteと最小権限の認証を管理者が確認します。

ステージ済みコピーでサービスを起動するコマンド:

```sh
python3 /absolute/protected-publisher/publisher.py serve \
  --policy /absolute/protected-publisher/policy.json \
  --key /absolute/protected-publisher/approval.key \
  --state /absolute/protected-publisher/state \
  --socket /absolute/protected-publisher/publisher.sock \
  --workspace /absolute/requester-workspace
```

信頼できるreviewer/issuerだけが実際の本文・decoded Git snapshot・公開先を文脈確認し、`approve --context-reviewed` を実行します。base64 bundleを目視しただけでは文脈確認としません。要求側へ渡すのは署名済みenvelopeだけです。送信側は固定policy/鍵にアクセスできないため、自分で署名を作れません。

```sh
python3 /absolute/protected-publisher/publisher.py approve \
  --policy /absolute/protected-publisher/policy.json \
  --key /absolute/protected-publisher/approval.key \
  --request /absolute/candidate-request.json \
  --out /absolute/requester-workspace/approved-envelope.json --context-reviewed

python3 /absolute/protected-publisher/publisher-sandbox.py \
  --workspace /absolute/requester-workspace \
  --socket /absolute/protected-publisher/publisher.sock -- \
  /usr/bin/python3 /publisher.py submit \
  --socket /run/publisher.sock --envelope /workspace/approved-envelope.json
```

## P1の受入に残る実環境作業

現在のRootはホスト実行toolとGitHub認証経路を持つままです。新しい子shellの隔離成功で、現在のRootも隔離されたとは扱いません。このセッションの実行権限はrepo内のファイル変更だけでは置き換わりません。

1. Root本体と全tool/API connectorの実行経路を、ホストの資格情報・サービス制御に届かない境界へ切り替える。追加のhost executor/ブラウザー等を残さない。現launcherはshell/requester用で、Codexの推論用接続・認証・全tool routerの移行は未実装/未検証。
2. publisher/issuerの固定code・policy・承認鍵・GitHub権限をRootから変更不能にする。管理者、運用停止、復旧担当を確定する。
3. 未対応API操作を止めるか、個別契約と試験を追加する。Rootに直接の権限を戻す回避をしない。
4. 実環境でRootの旧gh/SSH/別clone/継承経路の拒否とreceiver不変、publisher経由の肯定動作を確認する。
5. 実環境の証拠を根拠にP1への返信・resolveを判断する。namespace fixture、模擬API、文書レビューだけではresolveしない。

失敗時は公開停止とnonce/receiver状態の照合を行い、管理者が復旧します。Rootへ資格情報を戻す自動fallbackはありません。
