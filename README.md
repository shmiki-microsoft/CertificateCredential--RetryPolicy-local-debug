# CertificateCredential リトライポリシー検証

Azure SDK for Python の `CertificateCredential`（証明書ベースの認証）を使って Microsoft Graph からユーザー一覧を取得するサンプルです。**トークン取得時・API 呼び出し時のリトライ（再試行）ポリシーとタイムアウト**の挙動を、用途別のサンプルと 4 パターン検証ハーネス（[verify.py](verify.py)）で観測・デバッグできるように構成しています。

## 目次

- **概要**: [概要](#概要) ・ [ファイル構成](#ファイル構成)
- **準備**: [前提条件](#前提条件) ・ [必要な環境変数](#必要な環境変数) ・ [自己署名証明書の作成方法](#自己署名証明書の作成方法) ・ [セットアップ](#セットアップ)
- **使い方**: [実行方法](#実行方法) ・ [4パターン検証ハーネス](#4パターン検証ハーネスverifypy) ・ [VS Code でのデバッグ](#vs-code-でのデバッグ) ・ [動作内容](#動作内容)
- **リファレンス**: [パラメータ一覧](#リファレンス-パラメータ一覧) ・ [リトライ継続時間の調整](#リファレンス-リトライ継続時間の調整) ・ [タイムアウトの検証方法](#リファレンス-タイムアウトの検証方法) ・ [内部の仕組み](#リファレンス-内部の仕組み) ・ [依存パッケージ](#主な依存パッケージ)
- [注意事項](#注意事項)

## 概要

- 証明書（`.pfx` / `.pem`）を使って `CertificateCredential` で認証
- 取得したトークンで Microsoft Graph SDK (`msgraph-sdk`) を呼び出し、ユーザー一覧を取得
- `azure` ロガーを `DEBUG` レベルで出力し、HTTP リクエスト/レスポンスやリトライの様子を確認可能
- リトライ制御の有無による挙動の違いをローカルでデバッグすることが目的

## ファイル構成

| ファイル | 説明 |
| --- | --- |
| [verify.py](verify.py) | **4 パターン検証ハーネス**（推奨）。`PATTERN`（default/timeout/retry/both）と `TARGET`（azure/graph/both）を環境変数で指定し、リトライとタイムアウトを分離して試行回数・所要時間・バックオフを自動集計する。 |
| [main.py](main.py) | **リトライ版サンプル**。`CertificateCredential` に `retry_*`（`retry_total` / `retry_connect` / `retry_read` など）とタイムアウト（`connection_timeout` / `read_timeout`）を指定。`retry_backoff_factor` と `retry_backoff_max` を同値にして**固定間隔リトライ**（約 1 分）にしている。Graph SDK 側は `RetryHandlerOption` と手動リトライループ。`.pem` 版をコメントで併記。 |
| [main_simple.py](main_simple.py) | **リトライ無効版サンプル**。`retry_total=0` で既定リトライを無効化し、タイムアウトを指定。通信不良時はリトライせず例外でハンドリング。 |
| [main_no_retry.py](main_no_retry.py) | **ベースライン版サンプル**。`retry_*` 引数を一切渡さない素の状態（SDK 既定挙動）。Graph SDK も既定の `GraphServiceClient` を利用。例外処理は最小限。 |
| [requirements.txt](requirements.txt) | 依存パッケージ一覧。 |
| [cert/](cert/) | 認証に使用する証明書（`.pfx` / `.cer`）の配置先。 |

### 3 つのサンプル実装の違い

> 以下は用途別サンプル（`main*.py`）の違いです。リトライ/タイムアウトを 4 パターンで切り替えて検証したい場合は [verify.py](verify.py) を使います。

| 項目 | main.py | main_simple.py | main_no_retry.py |
| --- | --- | --- | --- |
| `CertificateCredential` のリトライ | `retry_total` / `retry_connect` / `retry_read` をカスタム（固定間隔、合計約1分：`retry_total=4`, `factor=max=20`） | `retry_total=0`（無効化） | 指定なし（既定値 `retry_total=10`） |
| タイムアウト指定 | `connection_timeout` / `read_timeout` | `connection_timeout` / `read_timeout` | なし |
| Graph SDK のリトライ | `RetryHandlerOption` でカスタム | カスタムミドルウェアなし（タイムアウトのみ） | 既定の `GraphServiceClient` |
| 接続エラーの手動リトライ | あり（指数バックオフ） | なし | なし |
| 例外処理 | 充実（終了コードあり） | 充実（終了コードあり） | 最小限 |

> **補足**: `CertificateCredential` は `retry_policy` オブジェクトを受け付けず、内部で必ず `RetryPolicy(**kwargs)` を生成するため、`retry_total` などのスカラー引数を直接渡す必要があります（`BlobServiceClient` とは異なる点）。

## 必要な環境変数

実行前に以下の環境変数を設定してください。

| 環境変数 | 説明 |
| --- | --- |
| `AZURE_TENANT_ID` | Microsoft Entra ID のテナント ID |
| `AZURE_CLIENT_ID` | アプリケーション（クライアント）ID |
| `AZURE_CLIENT_CERTIFICATE_PATH` | 証明書ファイル（`.pfx` / `.pem`）のパス |
| `AZURE_CLIENT_CERTIFICATE_PASSWORD` | 証明書のパスワード（`.pfx` の場合） |

PowerShell での設定例:

```powershell
$env:AZURE_TENANT_ID = "<テナントID>"
$env:AZURE_CLIENT_ID = "<クライアントID>"
$env:AZURE_CLIENT_CERTIFICATE_PATH = "cert\AzureSDK-CertificateCredential.pfx"
$env:AZURE_CLIENT_CERTIFICATE_PASSWORD = "<証明書パスワード>"
```

## 前提条件

- Microsoft Entra ID にアプリ登録を行い、証明書を登録済みであること
- アプリに Microsoft Graph の `User.Read.All`（アプリケーション許可）など、ユーザー一覧取得に必要な API アクセス許可と管理者の同意が付与されていること

## 自己署名証明書の作成方法

`CertificateCredential` で使用する自己署名証明書は、以下のいずれかの方法で作成できます。作成した公開証明書（`.crt` / `.cer`）は Microsoft Entra ID のアプリ登録（[証明書とシークレット] > [証明書]）にアップロードし、秘密鍵を含むファイル（`.pem` / `.pfx`）をアプリケーション側で利用します。

> **注意**: 証明書ファイル・秘密鍵・パスワードは機密情報です。リポジトリにコミットしないでください。

### 方法 1: OpenSSL を使って PEM を作る

> OpenSSL は Microsoft の製品ではありません。導入方法は各自でご確認ください。

1. **OpenSSL を導入**する。
2. Azure Portal の **[Microsoft Entra ID] > [アプリの登録] > 該当アプリ** に移動し、概要ページの **アプリケーション (クライアント) ID** と **ディレクトリ (テナント) ID** をメモする。
3. 以下のコマンドを実行して PEM 形式の鍵・証明書を作成する。

   ```powershell
   openssl genrsa -out server.pem 2048
   openssl req -new -key server.pem -out server.csr
   openssl x509 -req -days 365 -in server.csr -signkey server.pem -out server.crt
   ```

   - `-days` オプションで有効期限（既定 365 日）を設定します。
   - 引用元: [ms-identity-python-daemon (Optional - Create a self-signed certificate)](https://github.com/Azure-Samples/ms-identity-python-daemon/tree/master/2-Call-MsGraph-WithCertificate#optional-create-a-self-signed-certificate)

4. `server.pem` と `server.crt` をテキストエディタで開き、**`server.pem` の末尾に `server.crt` の内容を貼り付けて保存**する（秘密鍵＋証明書を 1 つの PEM にまとめる）。
5. Azure Portal の **[アプリの登録] > 該当アプリ > [証明書とシークレット] > [証明書]** タブで、`server.crt` をアップロードする。
6. 環境変数 `AZURE_CLIENT_CERTIFICATE_PATH` に PEM ファイルのパスを設定する（`.pem` のためパスワードは不要）。

### 方法 2: PowerShell を使って PFX を作る

1. 以下のコマンドで自己署名証明書を作成する（出力される **Thumbprint** をメモする）。

   ```powershell
   $cert = New-SelfSignedCertificate -Subject "CN={certificateName}" -CertStoreLocation "Cert:\CurrentUser\My" -KeyExportPolicy Exportable -KeySpec Signature -KeyLength 2048 -KeyAlgorithm RSA -HashAlgorithm SHA256
   $cert
   ```

   - 有効期限を変更する場合は `-NotAfter` オプションを追加します（既定は 1 年）。
   - 公開情報: [New-SelfSignedCertificate (Example 7)](https://learn.microsoft.com/en-us/powershell/module/pki/new-selfsignedcertificate?view=windowsserver2022-ps#example-7)

2. 公開証明書（`.cer`）をエクスポートする。

   ```powershell
   Export-Certificate -Cert $cert -FilePath "C:\Users\admin\Desktop\{certificateName}.cer"
   ```

3. 秘密鍵を含む `.pfx` をエクスポートする。

   ```powershell
   $mypwd = ConvertTo-SecureString -String "{myPassword}" -Force -AsPlainText
   Export-PfxCertificate -Cert $cert -FilePath "C:\Users\admin\Desktop\{privateKeyName}.pfx" -Password $mypwd
   ```

   - `{myPassword}` が `.pfx` のパスワードになります。`AZURE_CLIENT_CERTIFICATE_PASSWORD` に設定します。
   - 公開情報: [自己署名証明書を作成する (オプション 2)](https://learn.microsoft.com/ja-jp/azure/active-directory/develop/howto-create-self-signed-certificate#option-2-create-and-export-your-public-certificate-with-its-private-key)

4. `.cer` ファイルを Microsoft Entra ID のアプリ登録（サービスプリンシパル）にアップロードする。
5. 環境変数 `AZURE_CLIENT_CERTIFICATE_PATH` に `.pfx` のパス、`AZURE_CLIENT_CERTIFICATE_PASSWORD` にパスワードを設定する。

### 方法 3: Azure CLI を使って PEM を作る

1. **Azure CLI を導入**する。インストール手順: [Azure CLI のインストール](https://learn.microsoft.com/ja-jp/cli/azure/install-azure-cli)
2. Azure Portal の **[アプリの登録] > 該当アプリ** で **クライアント ID** と **テナント ID** をメモする。
3. 管理者アカウントでサインインする。

   ```powershell
   az login
   ```

4. 自己署名証明書を作成し、アプリ登録に証明書を登録する。

   ```powershell
   az ad app credential reset --id {アプリケーション(クライアント)ID} --create-cert
   ```

   > **★ 注意**: このコマンドは**アップロード済みの既存の証明書を削除します**。既存証明書を使用中のアプリがある場合、そのアプリで認証ができなくなります。

   - 出力の `fileWithCertAndPrivateKey` に、証明書（秘密鍵）を含む PEM ファイルのパスが表示されます。このファイルをアプリケーション開発者に共有します。
   - 有効期限は既定で 1 年です。`--years` オプションで変更できます（例: `az ad app credential reset --id {クライアントID} --create-cert --years 2`）。

5. Azure Portal の **[アプリの登録] > 該当アプリ > [証明書とシークレット] > [証明書]** で、証明書がアップロードされていることを確認する。
6. 環境変数 `AZURE_CLIENT_CERTIFICATE_PATH` に PEM ファイルのパスを設定する（`.pem` のためパスワードは不要）。

## セットアップ


```powershell
# 仮想環境の作成と有効化
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# 依存パッケージのインストール
pip install -r requirements.txt
```

## 実行方法

```powershell
# 4パターン検証ハーネス（推奨。PATTERN / TARGET で切替）
python verify.py

# リトライ版サンプル
python main.py

# リトライ無効版サンプル
python main_simple.py

# ベースライン版サンプル
python main_no_retry.py
```

> 4パターンの観測が目的なら [verify.py](verify.py) を使ってください（詳細は後述の「4パターン検証ハーネス」）。`main*.py` は用途別の固定サンプルです。

### プロキシ経由のオン/オフ（`DEBUG_PROXY`）

環境変数 `DEBUG_PROXY` で、すべての通信（`CertificateCredential` のトークン取得 / Graph SDK 呼び出し）を検証用プロキシ経由に切り替えられます。プロキシ（Squid など）側で遅延・ブロック・切断を起こして、リトライ・タイムアウトの動作を検証できます。

| `DEBUG_PROXY` の値 | 動作 |
| --- | --- |
| `http://127.0.0.1:3128` などの URL | その URL のプロキシ経由 |
| 未設定 / 空 / `0` / `false` / `off` / `no` | プロキシ無効（直接通信） |

```powershell
# プロキシ経由（URLを環境変数で指定）
$env:DEBUG_PROXY = "http://127.0.0.1:3128"; python main.py

# 別のプロキシを指定
$env:DEBUG_PROXY = "http://127.0.0.1:8888"; python main.py

# 直接通信（プロキシ無効）
$env:DEBUG_PROXY = "0"; python main.py
Remove-Item Env:\DEBUG_PROXY   # 変数を消しても無効になる
```

> 起動時に `DEBUG_PROXY: 経由 ... / 無効（直接通信）` と現在の状態を表示します。実装上、`CertificateCredential` には `proxies={"http":..., "https":...}`（requests 系）、Graph SDK の httpx には `proxy=...`（httpx 0.28+）を渡しています。

## VS Code でのデバッグ

`.vscode/launch.json` にデバッグ構成を用意しています。初回はサンプル [`.vscode/launchーsample.json`](.vscode/launch%E3%83%BCsample.json) を `.vscode/launch.json` にコピーし、`env` の各値（テナント/クライアント ID、証明書パス/パスワード）を自分の環境に書き換えてください。

デバッグ手順:

1. アクティビティバーの **[実行とデバッグ]**（`Ctrl+Shift+D`）を開く。
2. 上部のドロップダウンから構成を選ぶ。
3. **F5** でデバッグ開始。`env` の環境変数がプロセスに渡されます。

用意している構成:

| 構成名 | 対象 | `DEBUG_PROXY` |
| --- | --- | --- |
| `Python: main.py` | main.py | `http://127.0.0.1:3128`（プロキシ経由） |
| `Python: main_simple.py` | main_simple.py | `0`（直接通信） |
| `Python: main_no_retry.py` | main_no_retry.py | `0`（直接通信） |

> `env` 内の `DEBUG_PROXY` にプロキシURLを設定（無効にするなら `0`）するだけで、デバッグ時のプロキシ経由を切り替えられます。プロキシ経由で検証する場合は、事前に検証用プロキシ（例: Squid）を `3128` で起動しておいてください。

> **注意**: `launch.json` には証明書パスワードなどの機密情報が含まれるため、リポジトリにコミットしないでください（`.gitignore` で除外し、サンプルのみ共有します）。

## 4パターン検証ハーネス（verify.py）

[verify.py](verify.py) は、**リトライとタイムアウトを分離して 4 パターンで挙動を観測する**ための統合ハーネスです。SDK の仕組みを理解することが目的で、設定の有無で挙動がどう変わるかを自動集計して表示します。

### 4 パターン（環境変数 `PATTERN`）

| `PATTERN` | リトライ | タイムアウト | ねらい |
| --- | --- | --- | --- |
| `default` | SDK 既定 | SDK 既定 | 何も設定しない基準状態 |
| `timeout` | **無効化**（`retry_total=0` / Graph 再試行なし） | 設定する | タイムアウトを**単独で**観測（リトライの影響を除外） |
| `retry` | 設定する | SDK 既定（長いまま） | リトライ回数・バックオフを単独で観測 |
| `both` | 設定する | 設定する | 既定と異なる値で両方を設定したときの複合挙動 |

> **分離の考え方**: `timeout` パターンでは意図的にリトライを無効化します。リトライが効いていると「タイムアウト × 試行回数」で総時間が決まり、単一のタイムアウト時間が読み取りにくいためです。

### 対象スタック（環境変数 `TARGET`）

| `TARGET` | 対象 |
| --- | --- |
| `azure` | Azure SDK（`azure.core` / `CertificateCredential` のトークン取得） |
| `graph` | Graph SDK（kiota / httpx の API 呼び出し） |
| `both` | 両方（既定） |

> トークン取得は `azure.core`（requests）経由、Graph API 呼び出しは httpx 経由のため、**計測対象が自然に分離**されます。プロキシ側で「Azure SDK の通信だけ許可」「Graph SDK の通信だけ許可」と制限すれば、各スタックを単体で検証できます（Graph を試す場合もトークン取得のため `login.microsoftonline.com` への通信は許可が必要）。

### 計測（自動集計）

- **Azure 側**: `azure` ログの `Request URL:` 行をカウントして試行回数と各試行の時刻を記録
- **Graph 側**: httpx の `event_hooks` でリクエスト送信をカウントして時刻を記録

実行すると、スタックごとに **試行回数・総所要時間・試行間隔（バックオフ）・結果** を集計表示します。

### 実行方法

```powershell
# パターンと対象を環境変数で指定
$env:PATTERN = "timeout"   # default / timeout / retry / both
$env:TARGET  = "both"      # azure / graph / both
$env:DEBUG_PROXY = "http://127.0.0.1:3128"   # 故障注入したいとき（直接通信は 0）
python verify.py
```

主な調整用の環境変数（任意。未設定なら既定値）:

| 環境変数 | 既定 | 対象 |
| --- | --- | --- |
| `AZURE_CONNECTION_TIMEOUT` / `AZURE_READ_TIMEOUT` | `30` / `60` | azure.core タイムアウト(秒) |
| `HTTPX_CONNECT` / `HTTPX_READ` / `HTTPX_WRITE` / `HTTPX_POOL` | `30` / `60` / `60` / `5` | httpx タイムアウト(秒) |
| `RETRY_TOTAL` / `RETRY_CONNECT` / `RETRY_READ` | `4` | azure.core リトライ回数 |
| `RETRY_BACKOFF_FACTOR` / `RETRY_BACKOFF_MAX` | `20` / `20` | azure.core バックオフ |
| `GRAPH_MAX_RETRIES` / `GRAPH_DELAY` | `4` / `5` | Graph(kiota) リトライ |
| `GRAPH_MANUAL_MAX_ATTEMPTS` / `GRAPH_MANUAL_BACKOFF_FACTOR` / `GRAPH_MANUAL_BACKOFF_MAX` | `4` / `20` / `20` | Graph 接続エラー用の手動リトライ |
| `AZURE_SCOPE` | `https://management.azure.com/.default` | トークン取得スコープ |

VS Code では `verify: ① default` 〜 `verify: ④ both` の構成を `.vscode/launch.json` に用意しています。

## 動作内容

各スクリプトは以下を実行します。

1. 設定された環境変数の内容をコンソールに表示
2. `CertificateCredential` で認証オブジェクトを生成
3. （任意）`get_token("https://management.azure.com/.default")` でトークンを明示的に取得（コード中はコメントアウト。有効化すると確認できる）
4. Microsoft Graph SDK でユーザー一覧（`graph_client.users.get()`）を取得し、`@odata.nextLink` をたどって全件表示
5. `azure` ロガーの `DEBUG` ログで HTTP 通信・リトライの様子を確認

### 終了コード（main.py / main_simple.py）

| コード | 意味 |
| --- | --- |
| `1` | 認証エラー（証明書・テナント/クライアント ID など） |
| `2` | 通信エラー（リトライ枯渇 / 接続不可） |
| `3` | API エラー・HTTP 応答エラー |

## 証明書ファイルの種類による切り替え

各スクリプトには `.pfx` 用と `.pem` 用の `CertificateCredential` 生成コードが用意されています。`.pem` を使う場合はコメントアウトされている `.pem` 用のブロックを有効化し、`.pfx` 用のブロックをコメントアウトしてください（`.pem` ではパスワード引数は不要です）。

## 主な依存パッケージ

- `azure-identity` — `CertificateCredential`
- `azure-core` — 例外・リトライポリシー
- `msgraph-sdk` / `msgraph-core` — Microsoft Graph 呼び出し
- `microsoft-kiota-http` / `microsoft-kiota-abstractions` / `microsoft-kiota-authentication-azure` — Graph SDK のミドルウェア・認証連携
- `httpx` — HTTP クライアント（タイムアウト・通信エラー処理）

## リファレンス: パラメータ一覧

ここでは `main.py` で `CertificateCredential` に渡している各パラメータの意味・既定値・出典をまとめます。

### `CertificateCredential`（トークン取得）側のパラメータ

これらは `azure.core` の `RetryPolicy` / `RequestsTransport` が `**kwargs` 経由で解釈します（[`RetryPolicy` クラス](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline.policies.retrypolicy) のキーワード引数）。

| パラメータ | 既定値 | 意味 |
| --- | --- | --- |
| `retry_total` | `10` | 再試行の総数。他のカウンターより**優先**される全体上限 |
| `retry_connect` | `3` | **接続関連エラー**（リクエスト送信前に発生＝サーバー未処理とみなせる）の再試行回数。DNS解決失敗・接続不可など |
| `retry_read` | `3` | **読み取りエラー**（リクエスト送信後に発生＝副作用の可能性あり）の再試行回数 |
| `retry_status` | `3` | **不正なステータスコード**応答時の再試行回数 |
| `retry_backoff_factor` | `0.8` | 2回目以降の試行間に適用するバックオフ係数(秒)。`exponential` モードでは `{factor} * (2 ** ({総再試行回数} - 1))` 秒スリープ。`fixed` モードでは常に `{factor}` 秒スリープ |
| `retry_backoff_max` | `120` | バックオフの最大待機時間(秒)。既定は120秒(2分) |
| `retry_mode` | `exponential` | 試行間の遅延方式（`fixed` または `exponential`）。[`RetryMode`](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline.policies.retrymode) |
| `retry_on_status_codes` | （安全な既定に追加） | 既定の安全なステータス（`408, 429, 500, 502, 503, 504`）に加えて再試行対象とするコード |
| `connection_timeout` | `300` | 接続確立までのタイムアウト(秒)。[`RequestsTransport`](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline.transport.requeststransport) が解釈 |
| `read_timeout` | `300` | 応答受信までのタイムアウト(秒)。同上 |
| `logging_enable` | `False` | HTTP リクエスト/レスポンスの詳細ログを出力するか |

> **本リポジトリでの使い方のポイント**
> - DNS解決失敗のような**接続エラーは `retry_connect`（既定3）で数えられる**ため、`retry_total` だけ増やしても4回（初回+3回）で打ち切られます。回数を増やすには `retry_connect` を上げます。
> - `retry_backoff_factor` と `retry_backoff_max` を**同値**にすると、2回目以降の待機が一定値で固定され、合計リトライ時間を計算しやすくなります（例: ともに `20` にすると 1回20秒固定 → `0 + 20×(retry_total-1)` 秒）。
> - `retry_total` は他カウンターに**優先する上限**なので、`retry_connect` を大きくしても `retry_total` を超えては再試行しません。

#### バックオフ待機時間の計算式（exponential モード）

$$ \text{wait}_n = \min\left( \text{retry\_backoff\_factor} \times 2^{(n-1)},\ \text{retry\_backoff\_max} \right) $$

- $n$ は累積した再試行回数。**初回の再試行のみ待機0秒**（多くのエラーは即時の再試行で解決するため）。
- 例（`factor=0.8`）: `0, 1.6, 3.2, 6.4, 12.8, ...`（`retry_backoff_max` で頭打ち）

### Microsoft Graph SDK（kiota）側のパラメータ

Graph SDK は `azure.core` ではなく **kiota / httpx** のミドルウェアで動くため、別系統の設定を使います。

| パラメータ | 既定値 | 意味 |
| --- | --- | --- |
| `RetryHandlerOption.max_retries` | `3` | 最大再試行回数（kiota 側の上限は 10） |
| `RetryHandlerOption.delay` | `3` | 再試行間の基本遅延(秒)。指数バックオフの基準（上限 180 秒） |
| `RetryHandlerOption.should_retry` | `True` | 再試行を有効化するか |
| `httpx.Timeout(connect=, read=, write=, pool=)` | 各 `5.0` | 接続/読み取り/送信/接続プール待ちの各タイムアウト(秒) |

> kiota の RetryHandler は `429` / `503` / `504` などの **HTTP ステータスコードにのみ**再試行し、**接続エラー（通信不可）は再試行しません**。そのため `main.py` では接続エラー用に手動の指数バックオフ・リトライループを別途実装しています。

## リファレンス: リトライ継続時間の調整

`retry_backoff_factor` と `retry_backoff_max` を**同じ値**にすると、2回目以降の待機がその値で固定されます（初回リトライのみ待機0秒）。これにより合計リトライ時間が `0 + 固定秒 × (retry_total - 1)` で計算でき、目的の時間に合わせやすくなります。

`main.py` は固定間隔20秒・合計約1分のリトライを設定しています（`retry_total=4`）。`retry_total` を変えれば任意の長さにできます。

| 設定例 | `retry_total`（= `retry_connect` / `retry_read`） | `retry_backoff_factor` / `retry_backoff_max` | 合計時間 |
| --- | --- | --- | --- |
| 約1分（main.py の既定） | `4` | `20` / `20` | `0 + 20×3 = 60秒` |
| 約3分 | `10` | `20` / `20` | `0 + 20×9 = 180秒` |

> 合計時間は各試行が即座に返る（DNS解決失敗など）ケースでの概算です。接続タイムアウトが発生する場合は、各試行に `connection_timeout` 秒が加算されて合計時間が延びます。

## リファレンス: タイムアウトの検証方法

`Failed to resolve 'login.microsoftonline.com' (getaddrinfo failed)` のような **DNS解決の失敗**は、接続を張る前に即座に失敗します。このため `connection_timeout` も `read_timeout` も効かず、各リトライはほぼ0秒で返ります（＝待機時間はバックオフ分のみ）。

タイムアウトを実際に効かせるには「**DNS解決は成功するが接続/応答しない**」状況を作る必要があります。**最も簡単なのは上記の `DEBUG_PROXY` で検証用プロキシ経由にし、プロキシ側で接続拒否・遅延・無応答を起こす方法です**。

プロキシを使わない場合の代替手段:

- **`connection_timeout` の確認**: `hosts` ファイル（`C:\Windows\System32\drivers\etc\hosts`）で `login.microsoftonline.com` を到達不能IP（例 `10.255.255.1`）に向ける → 各試行が `connection_timeout` 秒待ってからリトライ。**テスト後は追記行を必ず削除**してください（`hosts` 編集には管理者権限が必要）。
- **`read_timeout` の確認**: 接続はできるが応答を返さないモックサーバーを立てて向ける → 各試行が `read_timeout` 秒待つ。

> タイムアウトが効くと、合計リトライ時間は「**バックオフ合計 + タイムアウト × 試行回数**」に延びます。

## リファレンス: 内部の仕組み

`CertificateCredential` には `retry_total` や `connection_timeout` といった引数が明示的に定義されていません（コンストラクタは `tenant_id`, `client_id`, `certificate_path`, `**kwargs` のみ）。それでもこれらが機能するのは、**`**kwargs` が継承チェーンを通って最下層の HTTP トランスポートまで渡される（パススルーされる）** ためです。

### `CertificateCredential` の継承チェーン

```text
CertificateCredential
  └─ ClientCredentialBase        (azure/identity/_internal/client_credential_base.py)
       ├─ MsalCredential          (azure/identity/_internal/msal_credentials.py)
       └─ GetTokenMixin           (azure/identity/_internal/get_token_mixin.py)
```

直接の親は **`ClientCredentialBase`** で、そこから **`MsalCredential`** と **`GetTokenMixin`** を多重継承しています。

### パラメータが渡る流れ

```text
CertificateCredential(**kwargs)
  → ClientCredentialBase.__init__(**kwargs)
    → MsalCredential.__init__(**kwargs)
        self._client = MsalClient(**kwargs)          # msal_credentials.py
          → build_pipeline(**kwargs)                 # _internal/pipeline.py
              config.retry_policy = RetryPolicy(**kwargs)   # ← リトライはここで生成
              transport = RequestsTransport(**kwargs)       # ← タイムアウトはここで解釈
```

- **リトライ**: `_internal/pipeline.py` の `build_pipeline()` 内で  
  `config.retry_policy = RetryPolicy(**kwargs)` が実行され、`RetryPolicy` が  
  `retry_total` / `retry_backoff_factor` / `retry_backoff_max` / `retry_on_status_codes` を拾います。
- **タイムアウト**: 同じく `build_pipeline()` 内で生成される  
  `azure.core.pipeline.transport.RequestsTransport` が `ConnectionConfiguration(**kwargs)` を作り、  
  `connection_timeout`（接続確立待ち・既定 300 秒）と `read_timeout`（応答待ち・既定 300 秒）を解釈します。

| パラメータ | 最終的に解釈するクラス | 意味 |
| --- | --- | --- |
| `retry_total` / `retry_backoff_factor` / `retry_backoff_max` / `retry_on_status_codes` | `azure.core.pipeline.policies.RetryPolicy` | トークン取得時の再試行制御 |
| `connection_timeout` | `azure.core.pipeline.transport.RequestsTransport`（`ConnectionConfiguration`） | 接続確立までのタイムアウト(秒) |
| `read_timeout` | 同上 | 応答受信までのタイムアウト(秒) |
| `logging_enable` | `NetworkTraceLoggingPolicy` ほか | HTTP ログの出力 |

> これらは `**kwargs` 経由のため、IDE の補完や型チェックには現れません。`CertificateCredential` の docstring にも記載がなく、あくまで azure.core トランスポート/ポリシーの仕様に依存します。

### なぜ `retry_policy` オブジェクトは効かないのか（`BlobServiceClient` との違い）

Microsoft Learn の例では `BlobServiceClient(..., retry_policy=retry_policy)` のように **`RetryPolicy` オブジェクト**を渡しますが、**`CertificateCredential` では同じ書き方が効きません**。これはクラスごとに「`retry_policy` 引数の扱い」が異なるためです。

| クラス | リトライポリシー生成コード | `retry_policy` オブジェクト |
| --- | --- | --- |
| `BlobServiceClient` | `config.retry_policy = kwargs.get("retry_policy") or ExponentialRetry(**kwargs)` | **採用する**（オブジェクトを優先） |
| `CertificateCredential` | `config.retry_policy = RetryPolicy(**kwargs)` | **無視する**（毎回新規生成） |

- `BlobServiceClient`（`azure/storage/blob/_shared/base_client.py`）は `kwargs.get("retry_policy")` を最初に見るため、オブジェクト渡しが成立します。
- `CertificateCredential`（`azure/identity/_internal/pipeline.py`）は `retry_policy` を見ずに必ず `RetryPolicy(**kwargs)` を作り直すため、**渡したオブジェクトは捨てられ既定値（`retry_total=10`）になります**。
- そのため、本リポジトリの `main.py` では `CertificateCredential` に **スカラー引数（`retry_total=4` など）を直接渡す**実装にしています。

### Graph SDK（kiota）側は別系統

Graph SDK（`msgraph-sdk`）は azure.core のパイプラインではなく **kiota / httpx ベースのミドルウェア**で動作します。そのためリトライ・タイムアウトの指定方法も異なります。

| 設定対象 | 指定方法 | 渡す先のクラス |
| --- | --- | --- |
| リトライ | `RetryHandlerOption(delay=..., max_retries=...)` | `kiota_http` の RetryHandler ミドルウェア（`GraphClientFactory.create_with_default_middleware(options=...)` 経由） |
| タイムアウト | `httpx.Timeout(connect=..., read=...)` を設定した `httpx.AsyncClient` | `GraphClientFactory.create_with_default_middleware(client=...)` → `GraphRequestAdapter` → `GraphServiceClient` |

> kiota の RetryHandler は `429` / `503` / `504` などの **HTTP ステータスコードにのみ**再試行し、**接続エラー（通信不可）は再試行しません**。そのため `main.py` では接続エラー用に手動の指数バックオフ・リトライループを別途実装しています。

### 参考リンク（Microsoft Learn）

- [Python 用 Azure SDK ライブラリでの HTTP パイプラインと再試行](https://learn.microsoft.com/ja-jp/azure/developer/python/sdk/fundamentals/http-pipeline-retries) — HTTP パイプラインの概念・既定のリトライ構成・カスタマイズ方法
- [azure.core.pipeline パッケージ](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline) — `Pipeline` / `PipelineRequest` / `PipelineResponse` など
- [azure.core.pipeline.transport.RequestsTransport クラス](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline.transport.requeststransport) — タイムアウト（`connection_timeout` / `read_timeout`）を解釈する HTTP トランスポート
- [azure.core.pipeline.policies.RetryPolicy クラス](https://learn.microsoft.com/ja-jp/python/api/azure-core/azure.core.pipeline.policies.retrypolicy) — `retry_total` / `retry_backoff_factor` / `retry_backoff_max` などのリトライ設定（既定 `retry_total=10`、再試行対象の既定ステータス `[408, 429, 500, 502, 503, 504]`）
- [azure.identity.CertificateCredential クラス](https://learn.microsoft.com/ja-jp/python/api/azure-identity/azure.identity.certificatecredential) — 証明書ベースの認証資格情報（コンストラクタは `tenant_id`, `client_id`, `certificate_path`, `**kwargs`）

### 参考リンク（GitHub ソースコード）

azure-sdk-for-python リポジトリ（[Azure/azure-sdk-for-python](https://github.com/Azure/azure-sdk-for-python)）の該当ソース:

- [`_credentials/certificate.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_credentials/certificate.py) — `CertificateCredential` の定義（`**kwargs` を親へパススルー）
- [`_internal/client_credential_base.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_internal/client_credential_base.py) — 直接の親クラス `ClientCredentialBase`
- [`_internal/msal_credentials.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_internal/msal_credentials.py) — `MsalCredential`（`self._client = MsalClient(**kwargs)`）
- [`_internal/msal_client.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_internal/msal_client.py) — `MsalClient`（`build_pipeline(**kwargs)` を呼ぶ）
- [`_internal/pipeline.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/azure/identity/_internal/pipeline.py) — `RetryPolicy(**kwargs)` / `RequestsTransport(**kwargs)` を生成するパイプライン構築
- [`azure-core: _retry.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/core/azure-core/azure/core/pipeline/policies/_retry.py) — `RetryPolicy` 本体（`retry_total` などを解釈）
- [`azure-core: _requests_basic.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/core/azure-core/azure/core/pipeline/transport/_requests_basic.py) — `RequestsTransport`（`connection_timeout` / `read_timeout` を解釈）
- [`azure-storage-blob: _shared/base_client.py`](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/storage/azure-storage-blob/azure/storage/blob/_shared/base_client.py) — `BlobServiceClient` 側の `config.retry_policy = kwargs.get("retry_policy") or ExponentialRetry(**kwargs)`（オブジェクト渡しを採用する実装）

Graph SDK / kiota 側:

- [microsoftgraph/msgraph-sdk-python](https://github.com/microsoftgraph/msgraph-sdk-python) — `GraphServiceClient` / `GraphRequestAdapter`
- [microsoft/kiota-http-python](https://github.com/microsoft/kiota-http-python) — `RetryHandlerOption` / RetryHandler ミドルウェア

> **バージョン補足**: 本リポジトリで検証した `azure-identity 1.25.3` では、`CertificateCredential` に渡した `retry_policy` オブジェクトは無視されます（`pipeline.py` が必ず `RetryPolicy(**kwargs)` を生成するため）。一方、azure-identity の main ブランチでは `config.retry_policy = kwargs.pop("retry_policy", None) or RetryPolicy(**kwargs)` のように **`retry_policy` の上書きを許可する変更**（[#46072](https://github.com/Azure/azure-sdk-for-python/pull/46072)）が入っています。将来のバージョンではオブジェクト渡しも有効になる可能性があるため、利用中のバージョンの挙動を確認してください。

## 注意事項


- 証明書ファイルや証明書パスワードは機密情報です。リポジトリにコミットしないよう注意してください。
- ログには取得したアクセストークンが平文で表示されるため、デバッグ用途に限定して扱ってください。
