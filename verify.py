"""
リトライ/タイムアウトの 4 パターン検証ハーネス。

目的:
    Azure SDK(azure.core / CertificateCredential) と Graph SDK(kiota / httpx) の
    リトライ・タイムアウトの仕組みを、設定の有無で挙動がどう変わるかを観測して理解する。
    （本番の設定値を決めるためのものではなく、挙動理解のためのサンドボックス）

4 パターン（環境変数 PATTERN で選択）:
    - default : リトライもタイムアウトも設定しない（SDK 既定のまま）
    - timeout : タイムアウトのみ設定。リトライは無効化して「タイムアウト単独」を観測
                （azure.core は retry_total=0、Graph は再試行なし）
    - retry   : リトライのみ設定。タイムアウトは設定しない（SDK 既定の長いまま）
    - both    : リトライ・タイムアウト両方を「既定と異なる値」で設定

対象スタック（環境変数 TARGET で選択）: azure | graph | both
    ※ トークン取得(login.microsoftonline.com)は azure.core(requests) 経由、
      Graph API 呼び出し(graph.microsoft.com)は httpx 経由なので、計測対象が自然に分離される。
    ※ どちらのスタックを実際に通すかは DEBUG_PROXY 側の許可/ブロックでも制御できる。
      （Graph を試す場合もトークン取得のため login への通信は許可しておく必要がある）

計測（自動集計）:
    - Azure 側: azure ログの "Request URL:" 行をカウントして試行回数・各試行の時刻を記録
    - Graph 側: httpx の event_hooks でリクエスト送信をカウントして時刻を記録
    いずれも総所要時間・試行間隔(バックオフ)・結果を集計して表示する。

主な環境変数:
    PATTERN, TARGET, DEBUG_PROXY,
    AZURE_CONNECTION_TIMEOUT, AZURE_READ_TIMEOUT,
    HTTPX_CONNECT, HTTPX_READ, HTTPX_WRITE, HTTPX_POOL,
    AZURE_RETRY_TOTAL, AZURE_RETRY_CONNECT, AZURE_RETRY_READ,
    AZURE_RETRY_BACKOFF_FACTOR, AZURE_RETRY_BACKOFF_MAX,
    GRAPH_MAX_RETRIES, GRAPH_DELAY,
    GRAPH_MANUAL_MAX_ATTEMPTS, GRAPH_MANUAL_BACKOFF_FACTOR, GRAPH_MANUAL_BACKOFF_MAX,
    AZURE_SCOPE
"""
import os
import time
import asyncio
import logging
from dataclasses import dataclass

import httpx
from azure.identity import CertificateCredential
from msgraph import GraphServiceClient, GraphRequestAdapter
from msgraph_core import GraphClientFactory
from kiota_http.middleware.options import RetryHandlerOption
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)

PATTERNS = ("default", "timeout", "retry", "both")
TARGETS = ("azure", "graph", "both")
AZURE_RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def _getenv_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _getenv_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _resolve_proxy() -> str | None:
    """環境変数 DEBUG_PROXY からプロキシ URL を解決する（無効時は None）。"""
    raw = os.getenv("DEBUG_PROXY", "").strip()
    if raw.lower() in ("", "0", "false", "off", "no"):
        return None
    return raw


@dataclass
class Config:
    pattern: str
    target: str
    proxy_url: str | None
    # 認証情報
    tenant_id: str | None
    client_id: str | None
    cert_path: str | None
    cert_password: str | None
    azure_scope: str
    # azure.core タイムアウト
    connection_timeout: float
    read_timeout: float
    # httpx タイムアウト
    httpx_connect: float
    httpx_read: float
    httpx_write: float
    httpx_pool: float
    # azure.core リトライ
    azure_retry_total: int
    azure_retry_connect: int
    azure_retry_read: int
    azure_retry_backoff_factor: float
    azure_retry_backoff_max: float
    # Graph(kiota) リトライ
    graph_max_retries: int
    graph_delay: float
    # Graph 接続エラー用の手動リトライ
    graph_manual_max_attempts: int
    graph_manual_backoff_factor: float
    graph_manual_backoff_max: float

    @property
    def enable_retry(self) -> bool:
        return self.pattern in ("retry", "both")

    @property
    def enable_timeout(self) -> bool:
        return self.pattern in ("timeout", "both")

    @property
    def disable_retry(self) -> bool:
        # timeout パターンはリトライを無効化してタイムアウトを単独観測する
        return self.pattern == "timeout"

    @classmethod
    def from_env(cls) -> "Config":
        pattern = os.getenv("PATTERN", "default").strip().lower()
        if pattern not in PATTERNS:
            raise ValueError(f"PATTERN は {PATTERNS} のいずれか。指定値: {pattern!r}")
        target = os.getenv("TARGET", "both").strip().lower()
        if target not in TARGETS:
            raise ValueError(f"TARGET は {TARGETS} のいずれか。指定値: {target!r}")
        return cls(
            pattern=pattern,
            target=target,
            proxy_url=_resolve_proxy(),
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            cert_path=os.getenv("AZURE_CLIENT_CERTIFICATE_PATH"),
            cert_password=os.getenv("AZURE_CLIENT_CERTIFICATE_PASSWORD"),
            azure_scope=os.getenv("AZURE_SCOPE", "https://management.azure.com/.default"),
            connection_timeout=_getenv_float("AZURE_CONNECTION_TIMEOUT", 30.0),
            read_timeout=_getenv_float("AZURE_READ_TIMEOUT", 60.0),
            httpx_connect=_getenv_float("HTTPX_CONNECT", 30.0),
            httpx_read=_getenv_float("HTTPX_READ", 60.0),
            httpx_write=_getenv_float("HTTPX_WRITE", 60.0),
            httpx_pool=_getenv_float("HTTPX_POOL", 5.0),
            azure_retry_total=_getenv_int("AZURE_RETRY_TOTAL", 4),
            azure_retry_connect=_getenv_int("AZURE_RETRY_CONNECT", 4),
            azure_retry_read=_getenv_int("AZURE_RETRY_READ", 4),
            azure_retry_backoff_factor=_getenv_float("AZURE_RETRY_BACKOFF_FACTOR", 20.0),
            azure_retry_backoff_max=_getenv_float("AZURE_RETRY_BACKOFF_MAX", 20.0),
            graph_max_retries=_getenv_int("GRAPH_MAX_RETRIES", 4),
            graph_delay=_getenv_float("GRAPH_DELAY", 5.0),
            graph_manual_max_attempts=_getenv_int("GRAPH_MANUAL_MAX_ATTEMPTS", 4),
            graph_manual_backoff_factor=_getenv_float("GRAPH_MANUAL_BACKOFF_FACTOR", 20.0),
            graph_manual_backoff_max=_getenv_float("GRAPH_MANUAL_BACKOFF_MAX", 20.0),
        )


# ---------------------------------------------------------------------------
# ロギング & 計測
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    # Azure(azure.core / azure.identity) と Graph SDK(kiota / httpx) 双方の DEBUG ログを出す。
    # Graph SDK は azure ロガーではなく httpx / httpcore / kiota / msgraph のロガーを使うため、
    # それぞれを DEBUG に設定してハンドラーを付ける。
    target_loggers = (
        "azure",                       # azure.core / azure.identity（トークン取得）
        "httpx",                       # Graph SDK が使う HTTP クライアント
        "httpcore",                    # httpx の下層（接続・送受信）
        "msgraph",                     # Microsoft Graph SDK
        "msgraph_core",
        "kiota_http",                  # kiota ミドルウェア（リトライ等）
        "kiota_abstractions",
        "kiota_authentication_azure",
    )
    for name in target_loggers:
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        if not any(isinstance(h, logging.StreamHandler) for h in lg.handlers):
            lg.addHandler(handler)
    return logging.getLogger("verify")


class _AzureRequestCounter(logging.Handler):
    """azure ログの 'Request URL:' 行から、トークン取得の試行時刻を記録するハンドラー。"""

    def __init__(self, timestamps: list[float]):
        super().__init__(level=logging.INFO)
        self._timestamps = timestamps

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - ログ整形失敗で計測を止めない
            return
        if msg.startswith("Request URL:"):
            self._timestamps.append(time.monotonic())


def _deltas(timestamps: list[float]) -> list[float]:
    return [round(b - a, 2) for a, b in zip(timestamps, timestamps[1:])]


def _azure_settings(cfg: Config) -> list[str]:
    """Azure(azure.core) スタックに実際に適用されるリトライ/タイムアウト設定を文字列化。"""
    if cfg.enable_retry:
        retry = (f"retry_total={cfg.azure_retry_total}, retry_connect={cfg.azure_retry_connect}, "
                 f"retry_read={cfg.azure_retry_read}, backoff_factor={cfg.azure_retry_backoff_factor}, "
                 f"backoff_max={cfg.azure_retry_backoff_max}")
    elif cfg.disable_retry:
        retry = "無効化 (retry_total=0)"
    else:
        retry = "SDK既定 (retry_total=10)"
    if cfg.enable_timeout:
        timeout = (f"connection_timeout={cfg.connection_timeout}秒, "
                   f"read_timeout={cfg.read_timeout}秒")
    else:
        timeout = "SDK既定 (≈300秒)"
    return [f"retry   = {retry}", f"timeout = {timeout}"]


def _graph_settings(cfg: Config) -> list[str]:
    """Graph(kiota/httpx) スタックに実際に適用されるリトライ/タイムアウト設定を文字列化。"""
    if cfg.enable_retry:
        retry = (f"max_retries={cfg.graph_max_retries}, delay={cfg.graph_delay}秒, "
                 f"manual_attempts={cfg.graph_manual_max_attempts} "
                 f"(backoff {cfg.graph_manual_backoff_factor}/{cfg.graph_manual_backoff_max})")
    elif cfg.disable_retry:
        retry = "無効 (max_retries=0, 手動リトライなし)"
    else:
        retry = "kiota既定 (max_retries=3)"
    if cfg.enable_timeout:
        timeout = (f"connect={cfg.httpx_connect}秒, read={cfg.httpx_read}秒, "
                   f"write={cfg.httpx_write}秒, pool={cfg.httpx_pool}秒")
    else:
        timeout = "httpx既定 (5秒)"
    return [f"retry   = {retry}", f"timeout = {timeout}"]


# ---------------------------------------------------------------------------
# クライアント構築（パターンに応じて引数を出し分ける）
# ---------------------------------------------------------------------------
def build_credential(cfg: Config) -> CertificateCredential:
    """パターンに応じた CertificateCredential を生成する。"""
    kwargs: dict = dict(
        tenant_id=cfg.tenant_id,
        client_id=cfg.client_id,
        certificate_path=cfg.cert_path,
        password=cfg.cert_password,
        logging_enable=True,  # 計測のため HTTP ログは常に有効
    )
    if cfg.proxy_url:
        kwargs["proxies"] = {"http": cfg.proxy_url, "https": cfg.proxy_url}

    # --- リトライ ---
    if cfg.enable_retry:
        kwargs.update(
            retry_total=cfg.azure_retry_total,
            retry_connect=cfg.azure_retry_connect,
            retry_read=cfg.azure_retry_read,
            retry_backoff_factor=cfg.azure_retry_backoff_factor,
            retry_backoff_max=cfg.azure_retry_backoff_max,
            retry_on_status_codes=AZURE_RETRY_STATUS_CODES,
        )
    elif cfg.disable_retry:
        # timeout パターン: リトライを無効化してタイムアウト単独を観測
        kwargs["retry_total"] = 0
    # default パターンはリトライ引数を渡さない（SDK 既定 retry_total=10）

    # --- タイムアウト ---
    if cfg.enable_timeout:
        kwargs.update(
            connection_timeout=cfg.connection_timeout,
            read_timeout=cfg.read_timeout,
        )
    # default / retry パターンはタイムアウト引数を渡さない（SDK 既定 ≈300 秒）

    return CertificateCredential(**kwargs)


def build_graph_client(
    cfg: Config, credential: CertificateCredential, request_timestamps: list[float]
) -> GraphServiceClient:
    """パターンに応じた GraphServiceClient を生成する。"""
    scopes = ["https://graph.microsoft.com/.default"]

    async def _on_request(_request: httpx.Request) -> None:
        request_timestamps.append(time.monotonic())

    # --- タイムアウト ---
    if cfg.enable_timeout:
        timeout = httpx.Timeout(
            connect=cfg.httpx_connect,
            read=cfg.httpx_read,
            write=cfg.httpx_write,
            pool=cfg.httpx_pool,
        )
    else:
        # default / retry: httpx 既定タイムアウト(5 秒)。計測時に実値を表示する
        timeout = httpx.Timeout(5.0)

    client = httpx.AsyncClient(
        proxy=cfg.proxy_url,
        timeout=timeout,
        event_hooks={"request": [_on_request]},
    )

    # --- リトライ（kiota ミドルウェア） ---
    options: dict = {}
    if cfg.enable_retry:
        rho = RetryHandlerOption(
            delay=cfg.graph_delay, max_retries=cfg.graph_max_retries, should_retry=True)
        options[rho.get_key()] = rho
    elif cfg.disable_retry:
        rho = RetryHandlerOption(delay=0, max_retries=0, should_retry=False)
        options[rho.get_key()] = rho
    # default パターンは options を渡さず kiota 既定の RetryHandler(max_retries=3) を使う

    http_client = GraphClientFactory.create_with_default_middleware(
        options=options or None, client=client)
    auth_provider = AzureIdentityAuthenticationProvider(credential, scopes=scopes)
    request_adapter = GraphRequestAdapter(auth_provider, http_client)
    return GraphServiceClient(request_adapter=request_adapter)


# ---------------------------------------------------------------------------
# プローブ（各スタックを実行して計測結果を返す）
# ---------------------------------------------------------------------------
def run_azure_probe(cfg: Config) -> dict:
    """CertificateCredential.get_token() を実行してリトライ/タイムアウト挙動を計測する。"""
    timestamps: list[float] = []
    counter = _AzureRequestCounter(timestamps)
    http_logger = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
    http_logger.addHandler(counter)

    outcome = "OK"
    start = time.monotonic()
    try:
        credential = build_credential(cfg)
        credential.get_token(cfg.azure_scope)
    except Exception as e:  # noqa: BLE001 - 観測目的なので全例外を記録
        outcome = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - start

    http_logger.removeHandler(counter)
    return {
        "stack": "Azure SDK (azure.core / CertificateCredential)",
        "settings": _azure_settings(cfg),
        "attempts": len(timestamps),
        "elapsed": round(elapsed, 2),
        "deltas": _deltas(timestamps),
        "outcome": outcome,
    }


async def run_graph_probe(cfg: Config, logger: logging.Logger) -> dict:
    """Graph SDK(kiota/httpx) で users.get() を実行してリトライ/タイムアウト挙動を計測する。"""
    timestamps: list[float] = []
    manual_retry = cfg.enable_retry
    attempts_allowed = cfg.graph_manual_max_attempts if manual_retry else 1

    outcome = "OK"
    start = time.monotonic()
    try:
        credential = build_credential(cfg)
        graph_client = build_graph_client(cfg, credential, timestamps)
        last_exc: Exception | None = None
        for attempt in range(1, attempts_allowed + 1):
            try:
                resp = await graph_client.users.get()
                count = len(resp.value) if resp and resp.value else 0
                outcome = f"OK ({count} users)"
                last_exc = None
                break
            except httpx.TransportError as e:
                # kiota の RetryHandler は接続エラーを再試行しないため手動で再試行
                last_exc = e
                if attempt == attempts_allowed:
                    break
                wait = min(
                    cfg.graph_manual_backoff_factor * (2 ** (attempt - 1)),
                    cfg.graph_manual_backoff_max,
                )
                logger.warning(
                    "Graph 接続失敗 (手動試行 %d/%d)。%.1f 秒後に再試行: %r",
                    attempt, attempts_allowed, wait, e)
                await asyncio.sleep(wait)
        if last_exc is not None:
            outcome = f"{type(last_exc).__name__}: {last_exc}"
    except Exception as e:  # noqa: BLE001 - 観測目的なので全例外を記録
        outcome = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - start

    return {
        "stack": "Graph SDK (kiota / httpx)",
        "settings": _graph_settings(cfg),
        "attempts": len(timestamps),
        "elapsed": round(elapsed, 2),
        "deltas": _deltas(timestamps),
        "outcome": outcome,
    }


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def print_header(cfg: Config) -> None:
    print("=" * 70)
    print(f"PATTERN = {cfg.pattern}   TARGET = {cfg.target}")
    print(f"  retry   : {'ON' if cfg.enable_retry else ('DISABLED(0)' if cfg.disable_retry else 'SDK既定')}")
    print(f"  timeout : {'ON' if cfg.enable_timeout else 'SDK既定'}")
    print(f"  proxy   : {cfg.proxy_url or '無効（直接通信）'}")
    if cfg.enable_retry:
        print(f"  [azure.core] retry_total={cfg.azure_retry_total} retry_connect={cfg.azure_retry_connect} "
              f"retry_read={cfg.azure_retry_read} backoff_factor={cfg.azure_retry_backoff_factor} "
              f"backoff_max={cfg.azure_retry_backoff_max}")
        print(f"  [graph]      max_retries={cfg.graph_max_retries} delay={cfg.graph_delay} "
              f"manual_attempts={cfg.graph_manual_max_attempts}")
    if cfg.enable_timeout:
        print(f"  [azure.core] connection_timeout={cfg.connection_timeout} "
              f"read_timeout={cfg.read_timeout}")
        print(f"  [graph/httpx] connect={cfg.httpx_connect} read={cfg.httpx_read} "
              f"write={cfg.httpx_write} pool={cfg.httpx_pool}")
    print("=" * 70)


def print_result(result: dict) -> None:
    print(f"\n--- {result['stack']} ---")
    for line in result.get("settings", []):
        print(f"  設定 {line}")
    print(f"  試行回数(リクエスト数): {result['attempts']}")
    print(f"  総所要時間            : {result['elapsed']} 秒")
    print(f"  試行間隔(バックオフ)  : {result['deltas']} 秒")
    print(f"  結果                  : {result['outcome']}")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main() -> None:
    logger = setup_logging()
    cfg = Config.from_env()
    print_header(cfg)

    results: list[dict] = []
    if cfg.target in ("azure", "both"):
        results.append(run_azure_probe(cfg))
    if cfg.target in ("graph", "both"):
        results.append(asyncio.run(run_graph_probe(cfg, logger)))

    print("\n" + "#" * 70)
    print(f"# サマリ (PATTERN={cfg.pattern}, TARGET={cfg.target})")
    print("#" * 70)
    for result in results:
        print_result(result)


if __name__ == "__main__":
    main()
