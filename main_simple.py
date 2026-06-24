"""
シンプル版: リトライ処理を行わず、通信不良時には例外処理でエラーを出すだけの実装。

- main.py との違い:
  * CertificateCredential に RetryPolicy を渡さない（= 既定リトライを無効化: retry_total=0）
  * Graph SDK のカスタムミドルウェア(RetryHandlerOption)を使わない
  * list_users() の手動リトライループを削除
  * 通信エラー・認証エラー・API エラーは try/except でハンドリングして終了する
- リトライ版は main.py を参照してください。
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
from kiota_abstractions.api_error import APIError
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)

# ロガーを取得（azure. で始まるモジュールのログをすべて取得）
logger = logging.getLogger('azure')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
handler.setFormatter(formatter)
logger.addHandler(handler)

try:
    # 環境変数の設定内容の確認
    print("---環境変数の設定内容---")
    print("AZURE_CLIENT_ID", os.getenv("AZURE_CLIENT_ID"))
    print("AZURE_TENANT_ID", os.getenv("AZURE_TENANT_ID"))
    print("AZURE_CLIENT_CERTIFICATE_PATH", os.getenv("AZURE_CLIENT_CERTIFICATE_PATH"))
    print("AZURE_CLIENT_CERTIFICATE_PASSWORD", os.getenv("AZURE_CLIENT_CERTIFICATE_PASSWORD"))
    print("-----------------------")

    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    cert_path = os.getenv("AZURE_CLIENT_CERTIFICATE_PATH")
    cert_password = os.getenv("AZURE_CLIENT_CERTIFICATE_PASSWORD")

    # 認証オブジェクトを取得
    # retry_total=0 で既定のリトライを無効化（通信不良時は即座に例外を送出）
    # connection_timeout: 接続確立までのタイムアウト(秒)
    # read_timeout: 応答待ちのタイムアウト(秒)
    # 証明書が .pfx ファイルの時
    token_credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_path=cert_path,
        password=cert_password,
        retry_total=0,
        connection_timeout=10,
        read_timeout=30,
        logging_enable=True)

    # 証明書が .pem ファイルの時
    # token_credential = CertificateCredential(
    #     tenant_id=tenant_id,
    #     client_id=client_id,
    #     certificate_path=cert_path,
    #     retry_total=0,
    #     connection_timeout=10,
    #     read_timeout=30,
    #     logging_enable=True)

    # 明示的にトークンを取得（通信不良ならここで例外）
    access_token_raw = token_credential.get_token("https://management.azure.com//.default").token
    print("access_token_raw", access_token_raw)

    # Microsoft Graph SDK を使ってユーザーの一覧を取得
    # タイムアウトを指定するため httpx.AsyncClient をカスタム作成して渡す
    # httpx.Timeout: connect=接続, read=読み取り, write=送信, pool=接続プール待ち(秒)
    scopes = ["https://graph.microsoft.com/.default"]
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
    http_client = GraphClientFactory.create_with_default_middleware(
        client=httpx.AsyncClient(timeout=timeout))
    auth_provider = AzureIdentityAuthenticationProvider(token_credential, scopes=scopes)
    request_adapter = GraphRequestAdapter(auth_provider, http_client)
    graph_client = GraphServiceClient(request_adapter=request_adapter)

    async def list_users():
        # users.get() は非同期メソッドなので await で実行する
        # 通信不良時はリトライせず、そのまま例外を送出する
        users_response = await graph_client.users.get()
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
    # 認証エラー（証明書・テナント/クライアントID など）
    logger.error("認証に失敗しました: %s", e.message)
    print("認証エラー:", e.message)
    sys.exit(1)
except (exceptions.ServiceRequestError, exceptions.ServiceResponseError) as e:
    # azure.core 側の通信エラー（接続不可・送信/応答失敗）
    logger.error("Azure への通信に失敗しました: %s", e)
    print("Azure への通信に失敗しました。ネットワーク接続を確認してください:", e)
    sys.exit(2)
except httpx.TransportError as e:
    # Graph SDK(kiota/httpx) 側の通信エラー（接続不可・タイムアウトなど）
    logger.error("Microsoft Graph への通信に失敗しました: %s", e)
    print("Microsoft Graph への通信に失敗しました。ネットワーク接続を確認してください:", e)
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
