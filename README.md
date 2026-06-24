# CertificateCredential リトライポリシー検証

Azure SDK for Python の `CertificateCredential`（証明書ベースの認証）を使って Microsoft Graph からユーザー一覧を取得するサンプルです。**トークン取得時・API 呼び出し時のリトライ（再試行）ポリシー**の挙動を、3 つのパターンで比較・デバッグできるように構成しています。

## 概要

- 証明書（`.pfx` / `.pem`）を使って `CertificateCredential` で認証
- 取得したトークンで Microsoft Graph SDK (`msgraph-sdk`) を呼び出し、ユーザー一覧を取得
- `azure` ロガーを `DEBUG` レベルで出力し、HTTP リクエスト/レスポンスやリトライの様子を確認可能
- リトライ制御の有無による挙動の違いをローカルでデバッグすることが目的

## ファイル構成

| ファイル | 説明 |
| --- | --- |
| [main.py](main.py) | **リトライ版**。`CertificateCredential` に `retry_*` 引数を渡してトークン取得をリトライ。Graph SDK 側は `RetryHandlerOption` でカスタム、さらに接続エラー用に手動リトライループを実装。例外処理も充実。 |
| [main_simple.py](main_simple.py) | **リトライ無効版**。`retry_total=0` で既定リトライを無効化し、タイムアウト（`connection_timeout` / `read_timeout`）を指定。通信不良時はリトライせず例外でハンドリング。 |
| [main_no_retry.py](main_no_retry.py) | **ベースライン版**。`retry_*` 引数を一切渡さない素の状態（SDK 既定挙動）。Graph SDK も既定の `GraphServiceClient` を利用。例外処理は最小限。 |
| [requirements.txt](requirements.txt) | 依存パッケージ一覧。 |
| [cert/](cert/) | 認証に使用する証明書（`.pfx` / `.cer`）の配置先。 |

### 3 つの実装の違い

| 項目 | main.py | main_simple.py | main_no_retry.py |
| --- | --- | --- | --- |
| `CertificateCredential` のリトライ | `retry_total=5` などをカスタム | `retry_total=0`（無効化） | 指定なし（既定値 `retry_total=10`） |
| タイムアウト指定 | なし | `connection_timeout` / `read_timeout` | なし |
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
# リトライ版
python main.py

# リトライ無効版
python main_simple.py

# ベースライン版
python main_no_retry.py
```

## 動作内容

各スクリプトは以下を実行します。

1. 設定された環境変数の内容をコンソールに表示
2. `CertificateCredential` で認証オブジェクトを生成
3. `get_token("https://management.azure.com//.default")` でトークンを明示的に取得して表示
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

## リトライ・タイムアウトの内部仕組み（継承とパラメータの流れ）

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
- そのため、本リポジトリの `main.py` では `CertificateCredential` に **スカラー引数（`retry_total=5` など）を直接渡す**実装にしています。

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
