# icloudpd-x8

[icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader) 1.32.3を使う、最大8並列の写真取得CLI。GitHub上のforkではなく、元OSSを依存として利用する独立ツールです。`x8` は8並列対応の意味で、8倍高速という意味ではありません。

写真を指定フォルダやNAS共有へ直接保存します。PCに全写真分の作業領域を必要としません。元OSSのファイル名・Live Photos・部分取得再開処理を使います。

## 導入

Python 3.10以降を用意して、このフォルダで実行してください。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe cli.py --help
```

macOS/Linuxでは `.venv/bin/python` を使います。CLIのみで、TUI/GUIはありません。

## 使い方

```powershell
# 認証。パスワードと確認コードは画面へ直接入力
.venv\Scripts\python.exe cli.py authenticate --username you@example.com --directory "\\nas\photos" --state-dir .local/my-transfer

# 直近100件で動作確認
.venv\Scripts\python.exe cli.py download --username you@example.com --directory "\\nas\photos" --state-dir .local/my-transfer --workers 8 --recent 100

# 全件取得、自動再試行、最後の全件確認
.venv\Scripts\python.exe cli.py run --username you@example.com --directory "\\nas\photos" --state-dir .local/my-transfer --workers 8

# 別のターミナルから状態確認・停止要求
.venv\Scripts\python.exe cli.py status --username you@example.com --directory "\\nas\photos" --state-dir .local/my-transfer
.venv\Scripts\python.exe cli.py stop --username you@example.com --directory "\\nas\photos" --state-dir .local/my-transfer
```

取得はフォアグラウンドで実行します。閉じると終了するため、常時実行する場合は利用者のOS標準タスク等で管理してください。Windowsでは実行中だけ自動スリープを防ぎます。PC再起動後の自動開始は設定しません。

`.local/` は認証セッション・アカウント参照・ログ・再開位置を含む私有領域です。共有・公開しないでください。`--state-dir` を変更すると別の処理として開始します。同じ状態フォルダを別アカウントや保存先で共有することは禁止しています。

## 取得と再開

- 並列数は1・2・4・6・8。通信セッションを分け、待ち行列を並列数の2倍までに制限
- 256KiBのストリーム書き込み。同名・同じ内容の部分ファイルは同時に書かない
- 既存ファイル情報を最大256フォルダまでキャッシュ。新規ファイルは実ファイルを確認
- 処理済みページは全並列処理の成功を待ってから位置を保存。失敗ページを飛ばさない
- iCloud一覧の変更で位置がずれる可能性があるため、末尾到達後に先頭から全件を確認。この確認の成功まで完了扱いにしない
- HTTP 429の要求制限が確認された場合だけ並列数を下げ、それ以外のエラーは維持して再試行
- 再試行は30分、60分、最大120分待機。1回6時間まで。再認証の確認コードは利用者が入力

iCloud写真の削除機能は公開入口にありません。NAS側で別の取得処理を同時実行しないでください。取得完了は元OSSの存在・サイズ確認に基づき、全ライブラリの内容ハッシュ監査とは異なります。再開位置からの取得に続く最終確認も、変更が続くライブラリの厳密なスナップショット保証ではありません。

## 検証

```powershell
.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```

人工データで内容一致・部分取得再開・並列の保存先衝突・失敗伝播・再開位置の更新順・最終確認後だけ成功とする処理を検証します。テストでは実アカウントを使いません。

開発時の同一100件の単回比較は4並列33.9秒、6並列27.8秒、8並列23.4秒。別の1・2・4比較は56.0秒、38.0秒、23.5秒。試験ファイルのハッシュ一致を確認しました。ネットワークとApple側の制限で結果は変わり、8並列が常に最適とは限りません。

## ライセンスと互換性

MIT。元OSSの著作権表記は [LICENSE](LICENSE) に保持しています。依存を固定し、実行時に元OSSの処理を最小差分で拡張します。インストール済み依存ファイルは書き換えません。未知のソース構成では処理を停止します。上流の更新時には適用箇所とテストの確認が必要です。
