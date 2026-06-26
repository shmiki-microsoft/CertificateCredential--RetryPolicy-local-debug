"""
リトライ版: CertificateCredential にリトライ・タイムアウトを明示設定した実用サンプル。

- CertificateCredential に retry_*(固定間隔) とタイムアウトを指定してトークン取得をリトライ
- Graph SDK(kiota) は RetryHandlerOption でリトライし、接続エラーは手動ループで再試行
- DEBUG_PROXY でプロキシ経由の検証が可能。例外は終了コード付きでハンドリング
- リトライ/タイムアウトを 4 パターンで切り替えて観測したい場合は verify.py を使う。
"""
import os
import logging
import sys
import asyncio
import httpx
from azure.core import exceptions
from azure.identity import CertificateCredential
from msgraph import GraphServiceClient, GraphRequestAdapter
from msgraph_core import GraphClientFactory
from kiota_http.middleware.options import RetryHandlerOption
from kiota_abstractions.api_error import APIError
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)

# ロガーを取得（azure. で始まるモジュールのログをすべて取得）
logger = logging.getLogger('azure')
# 特定のモジュールに絞りたいときは以下のように指定する
# logger = logging.getLogger("azure.storage.blob")   # Blob ストレージのみ
# logger = logging.getLogger("azure.identity")       # 認証(Identity)のみ
# ログレベルを設定
logger.setLevel(logging.DEBUG)
# ログメッセージのフォーマットを設定
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# ログメッセージをコンソールに出力するハンドラーを作成
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
handler.setFormatter(formatter)
# ロガーにハンドラーを追加
logger.addHandler(handler)

# Graph SDK(kiota / httpx) のログも出す（azure ロガーとは別系統のため）
for _name in ("httpx", "httpcore", "msgraph", "msgraph_core",
              "kiota_http", "kiota_abstractions", "kiota_authentication_azure"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.DEBUG)
    _lg.addHandler(handler)

# ログレベルの確認
print(
    f"Logger enabled for ERROR={logger.isEnabledFor(logging.ERROR)}, "
    f"WARNING={logger.isEnabledFor(logging.WARNING)}, "
    f"INFO={logger.isEnabledFor(logging.INFO)}, "
    f"DEBUG={logger.isEnabledFor(logging.DEBUG)}"
)

try:
    # 環境変数の設定内容の確認
    print("---環境変数の設定内容---")
    print("AZURE_CLIENT_ID",os.getenv("AZURE_CLIENT_ID"))
    print("AZURE_CLIENT_SECRET",os.getenv("AZURE_CLIENT_SECRET"))
    print("AZURE_TENANT_ID",os.getenv("AZURE_TENANT_ID"))
    print("AZURE_CLIENT_CERTIFICATE_PATH",os.getenv("AZURE_CLIENT_CERTIFICATE_PATH"))
    print("AZURE_CLIENT_CERTIFICATE_PASSWORD",os.getenv("AZURE_CLIENT_CERTIFICATE_PASSWORD"))
    print("AZURE_USERNAME",os.getenv("AZURE_USERNAME"))
    print("AZURE_PASSWORD",os.getenv("AZURE_PASSWORD"))
    print("-----------------------")

    # 環境変数からテナント/クライアント/証明書情報を取得
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    cert_path = os.getenv("AZURE_CLIENT_CERTIFICATE_PATH")
    cert_password = os.getenv("AZURE_CLIENT_CERTIFICATE_PASSWORD")

    # === 検証用プロキシ設定 =============================================
    # 環境変数 DEBUG_PROXY にプロキシURLを設定すると、その URL 経由で通信する。
    #   - DEBUG_PROXY=http://127.0.0.1:3128 など  → その URL のプロキシ経由
    #   - 未設定 / 空 / 0 / false / off / no    → プロキシを使わず直接通信
    # プロキシ側で遅延・ブロック・切断などを発生させ、本プログラムの
    # リトライポリシーやタイムアウトを検証するための設定。
    #   - CertificateCredential(azure.core/requests): proxies={"http":..., "https":...}
    #   - Graph SDK(httpx 0.28+): httpx.AsyncClient(proxy=...)
    _proxy_env = os.getenv("DEBUG_PROXY", "").strip()
    if _proxy_env.lower() in ("", "0", "false", "off", "no"):
        proxy_url = None                                # プロキシ無効（直接通信）
    else:
        proxy_url = _proxy_env                          # 環境変数で指定された URL を使用
    # azure.core(requests) 用の proxies dict（無効時は None）
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    print(f"DEBUG_PROXY: {'経由 ' + proxy_url if proxy_url else '無効（直接通信）'}")
    # ------------------------------------------------------------------

    # トークン取得時の再試行ポリシーをカスタマイズ
    # ------------------------------------------------------------------
    # 注意: CertificateCredential は retry_policy オブジェクトを受け付けず、
    #       内部で必ず RetryPolicy(**kwargs) を生成するため、retry_* の
    #       スカラー引数を直接渡す必要がある（BlobServiceClient とは異なる）。
    #
    # RetryPolicy はエラーの種類ごとに別々のカウンターを持つ。
    # retry_total だけを増やしても接続エラーは増えない点に注意:
    #   - retry_total   : 全体の再試行上限（既定 10）。各カウンターの総和の上限
    #   - retry_connect : 接続確立失敗(DNS解決失敗・接続不可)の再試行回数（既定 3）
    #   - retry_read    : 応答読み取り失敗の再試行回数（既定 3）
    #   - retry_status  : retry_on_status_codes 応答時の再試行回数（既定 3）
    # 例: DNS解決失敗は retry_connect で数えられるため、retry_total=10 でも
    #     retry_connect を上げないと初回+3回(=4回)で打ち切られる。
    #
    # バックオフ待機時間 = retry_backoff_factor * (2 ** n)（retry_backoff_max で上限）
    # logging_enable=True で HTTP 通信のデバッグログも出力する。
    #
    # retry_backoff_factor と retry_backoff_max を同値にすると待機が固定間隔になり、
    # 合計リトライ時間が「0 + 固定秒 × (retry_total-1)」で計算しやすい。
    # （タイムアウトの検証方法・パラメータ詳細は README / verify.py を参照）
    # ------------------------------------------------------------------
    # 証明書が .pfx ファイルの時（固定20秒 × 3回 = 合計約60秒のリトライ）
    token_credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id, 
        certificate_path=cert_path,
        password=cert_password, 
        proxies=proxies,                                 # 検証用プロキシ経由（DEBUG_PROXY で切替、無効時 None）
        connection_timeout=30,                           # 接続確立までのタイムアウト(秒)
        read_timeout=60,                                 # 応答待ちのタイムアウト(秒)
        retry_total=4,                                   # 全体の再試行上限
        retry_connect=4,                                 # 接続確立失敗(DNS解決失敗・接続不可)の再試行回数。既定3
        retry_read=4,                                    # 応答読み取り失敗の再試行回数。既定3
        retry_backoff_factor=20,                         # 指数バックオフの基本待機時間(秒)。max と同値で固定間隔化
        retry_backoff_max=20,                            # 最大待機時間(秒)。1回あたり20秒で固定
        retry_on_status_codes=[408, 429, 500, 502, 503, 504],  # 再試行対象の HTTP ステータスコード
        logging_enable=True)


    # 証明書が .pem ファイルの時（.pem ではパスワード引数は不要）
    # token_credential = CertificateCredential(
    #     tenant_id=tenant_id,
    #     client_id=client_id, 
    #     certificate_path=cert_path,
    #     proxies=proxies,
    #     connection_timeout=30,
    #     read_timeout=60,
    #     retry_total=4,
    #     retry_connect=4,
    #     retry_read=4,
    #     retry_backoff_factor=20,
    #     retry_backoff_max=20,
    #     retry_on_status_codes=[408, 429, 500, 502, 503, 504],
    #     logging_enable=True)

    # トークンを明示的に取得して確認する
    # （通常は各 Azure SDK のメソッド呼び出し時に自動でトークンが取得される）
    # access_token_raw = token_credential.get_token("https://management.azure.com/.default").token
    # print("access_token_raw",access_token_raw)

    # Microsoft Graph SDK を使ってユーザーの一覧を取得
    # scopes には Graph 用の .default を指定する
    scopes = ["https://graph.microsoft.com/.default"]

    # Graph SDK のリトライをカスタマイズ
    # Graph SDK は azure.core ではなく kiota ミドルウェアを使うため
    # RetryHandlerOption で再試行回数・遅延を設定する
    # 既定値: delay=3.0, max_retries=3。429/503 などは Retry-After を尊重し自動再試行
    retry_handler_option = RetryHandlerOption(
        delay=5,              # 再試行間の基本遅延(秒)
        max_retries=5,        # 最大再試行回数
        should_retry=True)    # 再試行を有効化

    # 認証プロバイダーを作成（CertificateCredential を利用）
    auth_provider = AzureIdentityAuthenticationProvider(
        token_credential, scopes=scopes)

    # カスタムリトライ設定を適用した既定ミドルウェア付き HTTP クライアントを作成
    # DEBUG_PROXY を指定していればそのプロキシ経由にし、タイムアウトも指定する
    # httpx.Timeout: connect=接続, read=読み取り, write=送信, pool=接続プール待ち(秒)
    graph_timeout = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=5.0)
    http_client = GraphClientFactory.create_with_default_middleware(
        options={retry_handler_option.get_key(): retry_handler_option},
        client=httpx.AsyncClient(proxy=proxy_url, timeout=graph_timeout))

    # request adapter を作成して GraphServiceClient を初期化
    request_adapter = GraphRequestAdapter(auth_provider, http_client)
    graph_client = GraphServiceClient(request_adapter=request_adapter)

    async def list_users():
        # kiota の RetryHandler は HTTP ステータスコード(429/503/504 など)にしか
        # 再試行しないため、通信不可（接続エラー）は手動で再試行する
        max_attempts = 5          # 最大試行回数
        backoff_factor = 0.8      # 指数バックオフの基本待機時間(秒)
        backoff_max = 60          # 最大待機時間(秒)

        users_response = None
        for attempt in range(1, max_attempts + 1):
            try:
                # users.get() は非同期メソッドなので await で実行する
                users_response = await graph_client.users.get()
                break
            except httpx.TransportError as e:
                # 接続不可・タイムアウトなどの通信エラー
                if attempt == max_attempts:
                    # 再試行を使い果たしたら例外を再送出して上位でハンドリング
                    logger.error(
                        "Graph への接続に %d 回失敗しました: %r", max_attempts, e)
                    raise
                wait = min(backoff_factor * (2 ** (attempt - 1)), backoff_max)
                logger.warning(
                    "Graph への接続に失敗 (試行 %d/%d)。%.1f 秒後に再試行: %r",
                    attempt, max_attempts, wait, e)
                await asyncio.sleep(wait)

        print("\n--- List of users (Microsoft Graph) ---")
        if users_response and users_response.value:
            for user in users_response.value:
                print(f"{user.display_name} ({user.user_principal_name})")
        # 結果が複数ページに分かれている場合は @odata.nextLink をたどる
        while users_response is not None and users_response.odata_next_link:
            users_response = await graph_client.users.with_url(
                users_response.odata_next_link).get()
            if users_response and users_response.value:
                for user in users_response.value:
                    print(f"{user.display_name} ({user.user_principal_name})")

    # 非同期関数を実行
    asyncio.run(list_users())

except exceptions.ClientAuthenticationError as e:
    # 認証エラー（証明書・テナント/クライアントID など）。再試行しても解消しない
    logger.error("認証に失敗しました: %s", e.message)
    print("認証エラー:", e.message)
    sys.exit(1)
except (exceptions.ServiceRequestError, exceptions.ServiceResponseError) as e:
    # azure.core 側の通信エラー（接続不可・送信/応答失敗）。
    # RetryPolicy のリトライを使い果たした後に到達 = リトライ枯渇
    logger.error("Azure への通信がリトライ枯渇により失敗しました: %s", e)
    print("Azure への通信に失敗しました（リトライ枯渇）。ネットワーク接続を確認してください:", e)
    sys.exit(2)
except httpx.TransportError as e:
    # Graph SDK(kiota/httpx) 側の通信エラー。
    # 手動再試行(max_attempts=5)を使い果たした後に到達 = リトライ枯渇
    logger.error("Microsoft Graph への通信がリトライ枯渇により失敗しました: %s", e)
    print("Microsoft Graph への通信に失敗しました（リトライ枯渇）。ネットワーク接続を確認してください:", e)
    sys.exit(2)
except APIError as e:
    # Graph API が返すエラー応答（権限不足など）
    logger.error("Microsoft Graph API エラー: %s", getattr(e, "message", e))
    print("Microsoft Graph API エラー:", getattr(e, "message", e))
    sys.exit(3)
except exceptions.HttpResponseError as e:
    # その他の HTTP 応答エラー
    logger.error("HTTP 応答エラー: %s", e.message)
    print("HTTP 応答エラー:", e.message)
    sys.exit(3)