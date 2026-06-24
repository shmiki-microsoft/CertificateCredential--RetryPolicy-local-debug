"""
比較用ベースライン版: リトライポリシーを一切実装していないシンプルなコード。

- main.py（リトライ版）/ main_simple.py（リトライ無効化＋例外処理）との違い:
  * CertificateCredential に retry_* 引数を一切渡さない（= SDK 既定の挙動のまま）
  * Graph SDK もカスタムミドルウェアを使わず既定の GraphServiceClient を利用
  * 手動リトライループ・タイムアウト指定なし
  * 例外処理は最小限（認証エラー / HTTP 応答エラーのみ）
- このファイルは「リトライ制御を入れていない素の状態」を比較するためのもの。
"""
import os
import logging
import asyncio
from azure.core import exceptions
from azure.identity import CertificateCredential
from msgraph import GraphServiceClient

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

    # 認証オブジェクトを取得（リトライ設定なし = SDK 既定の挙動）
    # 証明書が .pfx ファイルの時
    token_credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_path=cert_path,
        password=cert_password,
        logging_enable=True)

    # 証明書が .pem ファイルの時
    # token_credential = CertificateCredential(
    #     tenant_id=tenant_id,
    #     client_id=client_id,
    #     certificate_path=cert_path,
    #     logging_enable=True)

    # 明示的にトークンを取得
    access_token_raw = token_credential.get_token("https://management.azure.com//.default").token
    print("access_token_raw", access_token_raw)

    # Microsoft Graph SDK を使ってユーザーの一覧を取得
    # カスタムミドルウェアを使わず、既定の GraphServiceClient を利用
    scopes = ["https://graph.microsoft.com/.default"]
    graph_client = GraphServiceClient(credentials=token_credential, scopes=scopes)

    async def list_users():
        # users.get() は非同期メソッドなので await で実行する
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

except (
    exceptions.ClientAuthenticationError,
    exceptions.HttpResponseError
) as e:
    print(e.message)
