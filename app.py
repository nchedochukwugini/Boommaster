"""Flask API for the BoomMaster Nimiq mini app.

Run locally with ``python3 app.py``. API credentials are read from the process
environment or the adjacent .env file and are never sent to the browser.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
from flask import Flask, jsonify, make_response, request, send_from_directory

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite remains available for local development.
    psycopg = None
    dict_row = None


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BOOMMASTER_DB", ROOT / "boommaster.sqlite3"))


def load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
AUDIERA_KEY = os.environ.get("AUDIERA_API_KEY") or os.environ.get("AUDIERA_KEY")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
NIMIQ_RPC_URL = os.environ.get("NIMIQ_RPC_URL", "https://rpc.nimiqwatch.com")
POLYGON_RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
NIM_RECIPIENT = "NQ94 EEEB JCPL 1U89 FKGQ K3GJ RNPP F1VG MNJR"
USDT_RECIPIENT = "0x56aEE4597F10764dB6EB650CD6fddF089be51d9C"
USDT_CONTRACT = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"
USDT_RAW_AMOUNT = 500_000  # Polygon USDT uses 6 decimals.
PRICE_USD = 0.50
NIMIQ_FINALITY_CONFIRMATIONS = 60
POLYGON_FALLBACK_CONFIRMATIONS = 128

ARTISTS = {
    "Hip-Hop": ("Ray", "jyjcnj6t3arzzb5dnzk4p"),
    "Pop": ("Kira", "osipvytdvxuzci9pn2nz1"),
    "R&B": ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Electronic": ("Ray", "jyjcnj6t3arzzb5dnzk4p"),
    "Afrobeat": ("Ray", "jyjcnj6t3arzzb5dnzk4p"),
    "Reggae": ("Ray", "jyjcnj6t3arzzb5dnzk4p"),
    "Soul": ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Rock": ("Ray", "jyjcnj6t3arzzb5dnzk4p"),
    "Latin": ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Jazz": ("Kira", "osipvytdvxuzci9pn2nz1"),
}
MOODS = {
    "Heartbreak", "Euphoric", "Angry", "Nostalgic", "Motivated", "Dark",
    "Romantic", "Melancholic", "Hopeful", "Rebellious", "Lonely",
    "Triumphant", "Anxious", "Carefree", "Spiritual",
}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
_schema_ready = False


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class PostgresConnection:
    """Small adapter so the existing parameterized queries work with psycopg."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, query: str, params=()):
        ignore_conflict = query.lstrip().upper().startswith("INSERT OR IGNORE INTO")
        if ignore_conflict:
            query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1).rstrip().rstrip(";")
            query += " ON CONFLICT DO NOTHING"
        return self.connection.execute(query.replace("?", "%s"), params)

    def commit(self):
        self.connection.commit()

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)


def db():
    global _schema_ready
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("Install psycopg[binary] to connect to DATABASE_URL.")
        conn = PostgresConnection(psycopg.connect(
            DATABASE_URL, row_factory=dict_row, connect_timeout=10, prepare_threshold=None
        ))
        if not _schema_ready:
            for statement in (
                """CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'legacy',
                    style TEXT NOT NULL, mood TEXT NOT NULL, vibe TEXT NOT NULL DEFAULT '',
                    lyrics TEXT NOT NULL DEFAULT '', scout TEXT NOT NULL DEFAULT '', pitch TEXT NOT NULL DEFAULT '',
                    song_title TEXT NOT NULL DEFAULT '', verified_tx TEXT, task_id TEXT, artist_name TEXT,
                    song_url TEXT, deleted_at BIGINT, created_at BIGINT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT 'Music Creator',
                    created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL,
                    setup_complete INTEGER NOT NULL DEFAULT 0)""",
                """CREATE TABLE IF NOT EXISTS auth_challenges (
                    challenge_id TEXT PRIMARY KEY, wallet_address TEXT NOT NULL, message TEXT NOT NULL,
                    created_at BIGINT NOT NULL, used_at BIGINT)""",
                """CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    expires_at BIGINT NOT NULL, created_at BIGINT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS playbooks (
                    id BIGSERIAL PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                    style TEXT NOT NULL, mood TEXT NOT NULL, vibe TEXT NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS payments (
                    tx_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL, network TEXT NOT NULL,
                    amount TEXT NOT NULL, created_at BIGINT NOT NULL)""",
                "CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id)",
                "CREATE INDEX IF NOT EXISTS playbooks_user_idx ON playbooks(user_id)",
                "CREATE INDEX IF NOT EXISTS payments_session_idx ON payments(session_id)",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'legacy'",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS song_url TEXT",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS deleted_at BIGINT",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS song_title TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS setup_complete INTEGER NOT NULL DEFAULT 0",
            ):
                conn.execute(statement)
            conn.commit()
            _schema_ready = True
        return conn

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    if not _schema_ready:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT 'legacy',
                style TEXT NOT NULL,
                mood TEXT NOT NULL,
                vibe TEXT NOT NULL DEFAULT '',
                lyrics TEXT NOT NULL DEFAULT '',
                scout TEXT NOT NULL DEFAULT '',
                pitch TEXT NOT NULL DEFAULT '',
                song_title TEXT NOT NULL DEFAULT '',
                verified_tx TEXT,
                task_id TEXT,
                artist_name TEXT,
                song_url TEXT,
                deleted_at INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT 'Music Creator',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                setup_complete INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS auth_challenges (
                challenge_id TEXT PRIMARY KEY,
                wallet_address TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                used_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(user_id);
            CREATE TABLE IF NOT EXISTS playbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                style TEXT NOT NULL,
                mood TEXT NOT NULL,
                vibe TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS playbooks_user_idx ON playbooks(user_id);
            CREATE TABLE IF NOT EXISTS payments (
                tx_hash TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                network TEXT NOT NULL,
                amount TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS payments_session_idx ON payments(session_id);
            """
        )
        session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "user_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'")
        if "song_url" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN song_url TEXT")
        if "deleted_at" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN deleted_at INTEGER")
        if "song_title" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN song_title TEXT NOT NULL DEFAULT ''")
        profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
        if "setup_complete" not in profile_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN setup_complete INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        _schema_ready = True
    return conn


def require_json() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.")
    return data


def require_session(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", value):
        raise ApiError("A valid sessionId is required.")
    return value


def normalize_nimiq_address(address: Any) -> str:
    if not isinstance(address, str):
        raise ApiError("A Nimiq wallet address is required.")
    compact = re.sub(r"\s+", "", address).upper()
    if not re.fullmatch(r"NQ[0-9]{2}[0-9A-HJ-NP-TV-Y]{32}", compact):
        raise ApiError("The connected wallet returned an invalid Nimiq address.")
    return compact


def _nimiq_checksum(body: str) -> int:
    checksum = 0
    for char in body:
        value = int(char) if char.isdigit() else ord(char) - 55
        if value == 0:
            checksum = (checksum * 10) % 97
            continue
        digits = len(str(value))
        checksum = (checksum * (10 ** digits) + value) % 97
    checksum = (checksum * (10 ** 6) + 232600) % 97
    return 98 - checksum


def nimiq_address_from_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("Nimiq public keys must be 32 bytes.")
    key_hash = hashlib.blake2b(public_key, digest_size=32).digest()[:20]
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVXY"
    value = int.from_bytes(key_hash, "big")
    encoded = "".join(alphabet[(value >> (155 - 5 * index)) & 31] for index in range(32))
    checksum = _nimiq_checksum(encoded)
    return "NQ" + f"{checksum:02d}" + encoded


def nimiq_signed_message_hash(message: str) -> bytes:
    """Hash a message using Nimiq's signed-message domain prefix."""
    # Nimiq signs this prefixed, length-delimited digest rather than the raw
    # UTF-8 message. The challenge currently contains ASCII only, so its
    # character count is also its UTF-8 byte count.
    payload = b"\x16Nimiq Signed Message:\n" + str(len(message)).encode("ascii") + message.encode("utf-8")
    return hashlib.sha256(payload).digest()


def authenticated_user_id() -> str | None:
    token = request.cookies.get("boommaster_session", "")
    if not token or len(token) > 200:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT user_id FROM auth_sessions WHERE token_hash=? AND expires_at>?",
            (token_hash, now),
        ).fetchone()
    return row["user_id"] if row else None


def require_user_id(value: Any = None) -> str:
    user_id = authenticated_user_id()
    if not user_id:
        raise ApiError("Connect and sign in with your Nimiq wallet to access this account.", 401)
    return user_id


def audiera_headers() -> dict[str, str]:
    if not AUDIERA_KEY:
        raise ApiError("AUDIERA_API_KEY is not configured on the server.", 503)
    return {"Authorization": f"Bearer {AUDIERA_KEY}", "Content-Type": "application/json"}


def generate_lyrics_text(inspiration: str) -> str:
    response = requests.post(
        "https://ai.audiera.fi/api/skills/lyrics",
        headers=audiera_headers(), json={"inspiration": inspiration}, timeout=60,
    )
    payload = response.json()
    if not response.ok or not payload.get("success"):
        raise ApiError(payload.get("message", "Audiera could not generate lyrics."), 502)
    lyrics = (payload.get("data") or {}).get("lyrics")
    if not isinstance(lyrics, str) or not lyrics.strip():
        raise ApiError("Audiera returned empty lyrics.", 502)
    return lyrics.strip()


def ask_openrouter(prompt: str) -> str:
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    for model in (
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-4-maverick:free",
        "openrouter/free",
    ):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            payload = response.json()
            choices = payload.get("choices") or []
            if response.ok and choices:
                return choices[0]["message"]["content"]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError("All OpenRouter models failed.")


def split_scout_pitch(text: str) -> tuple[str, str]:
    scout = pitch = ""
    if "## SCOUT" in text:
        start = text.index("## SCOUT") + len("## SCOUT")
        end = text.index("## PITCH") if "## PITCH" in text else len(text)
        scout = text[start:end].strip()
    if "## PITCH" in text:
        pitch = text[text.index("## PITCH") + len("## PITCH"):].strip()
    return scout, pitch


def current_nim_usd() -> float:
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "nimiq-2", "vs_currencies": "usd"}, timeout=12,
    )
    response.raise_for_status()
    rate = float(response.json()["nimiq-2"]["usd"])
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Invalid NIM rate")
    return rate


def rpc(url: str, method: str, params: list[Any]) -> Any:
    try:
        response = requests.post(
            url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ApiError("Could not reach the network verifier. Check its RPC URL and try again.", 503) from exc
    if payload.get("error"):
        raise ApiError("The network verifier could not confirm this transaction yet.", 503)
    return payload.get("result")


def rpc_tx_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    # Current Nimiq PoS RPC wraps values in result.data and may expose
    # chain-state details separately in result.metadata.
    if isinstance(result.get("data"), dict):
        tx = dict(result["data"])
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            tx["_metadata"] = metadata
        return tx
    return result


def rpc_data(result: Any) -> Any:
    return result.get("data") if isinstance(result, dict) and "data" in result else result


def verify_nim(tx_hash: str, amount: Any) -> tuple[bool, str]:
    clean_hash = tx_hash.removeprefix("0x").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", clean_hash):
        return False, "Waiting for a valid Nimiq transaction hash."
    try:
        expected_luna = int(amount)
    except (TypeError, ValueError, OverflowError):
        return False, "The NIM amount is invalid."
    if expected_luna <= 0:
        return False, "The NIM amount is invalid."

    tx = rpc_tx_result(rpc(NIMIQ_RPC_URL, "getTransactionByHash", [clean_hash]))
    if not tx:
        return False, "Transaction not found on Nimiq yet."
    recipient = str(tx.get("recipient", tx.get("to", ""))).replace(" ", "").upper()
    target = NIM_RECIPIENT.replace(" ", "").upper()
    try:
        actual_luna = int(tx.get("value", -1))
    except (TypeError, ValueError):
        return False, "The NIM transfer details are not readable yet."
    if recipient != target or actual_luna != expected_luna:
        return False, "The NIM transaction recipient or amount does not match this order."
    if str(tx.get("network", "main")).lower() not in {"main", "mainnet", "mainalbatross"}:
        return False, "This transaction is not on Nimiq Mainnet."
    if tx.get("executionResult") is False:
        return False, "The NIM transaction did not execute successfully."
    metadata = tx.get("_metadata") or {}
    state = str(tx.get("state", "")).lower()
    if state == "finalized" or bool(tx.get("finalized")):
        final = True
    elif str(tx.get("confirmations", metadata.get("confirmations", ""))).isdigit() and int(
        tx.get("confirmations", metadata.get("confirmations", 0))
    ) >= NIMIQ_FINALITY_CONFIRMATIONS:
        final = True
    else:
        # Require an inclusion height, then wait until a later macro block
        # covers the transaction's micro block (~60 blocks per batch).
        inclusion_height = next((
            tx.get(key) for key in ("blockHeight", "blockNumber", "block_number")
            if tx.get(key) is not None
        ), None)
        if inclusion_height is None:
            inclusion_height = next((
                metadata.get(key) for key in ("blockHeight", "blockNumber", "block_number")
                if metadata.get(key) is not None
            ), None)
        if inclusion_height is None:
            return False, "NIM transfer found; the RPC has not provided its inclusion height for finality checks."
        try:
            current_height = int(rpc_data(rpc(NIMIQ_RPC_URL, "getBlockNumber", [])))
            final = current_height - int(inclusion_height) >= NIMIQ_FINALITY_CONFIRMATIONS
        except (TypeError, ValueError):
            final = False
    if not final:
        return False, "NIM transfer found; waiting for Nimiq finality."

    rate = current_nim_usd()
    if actual_luna / 100_000 * rate < PRICE_USD * 0.98:
        return False, "The NIM amount is below the $0.50 track price at the current rate."
    return True, clean_hash


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def verify_polygon(tx_hash: str, amount: Any) -> tuple[bool, str]:
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
        return False, "Waiting for a valid Polygon transaction hash."
    try:
        expected = int(amount)
    except (TypeError, ValueError, OverflowError):
        return False, "The USDT amount is invalid."
    if expected != USDT_RAW_AMOUNT:
        return False, "The payment amount does not equal 0.50 USDT."

    chain_id = rpc(POLYGON_RPC_URL, "eth_chainId", [])
    if int(chain_id, 16) != 137:
        raise ApiError("The configured EVM RPC is not Polygon Mainnet.", 503)
    receipt = rpc(POLYGON_RPC_URL, "eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        return False, "Transaction not found on Polygon yet."
    if str(receipt.get("status", "")).lower() != "0x1":
        return False, "The Polygon transaction has not succeeded."
    if str(receipt.get("to", "")).lower() != USDT_CONTRACT.lower():
        return False, "The transaction did not call the Polygon USDT contract."

    target_topic = "0x" + USDT_RECIPIENT.lower().removeprefix("0x").rjust(64, "0")
    transfer_found = False
    for log in receipt.get("logs", []):
        topics = log.get("topics") or []
        if (
            str(log.get("address", "")).lower() == USDT_CONTRACT.lower()
            and len(topics) >= 3
            and str(topics[0]).lower() == TRANSFER_TOPIC
            and str(topics[2]).lower() == target_topic
            and int(log.get("data", "0x0"), 16) == USDT_RAW_AMOUNT
        ):
            transfer_found = True
            break
    if not transfer_found:
        return False, "No 0.50 USDT transfer to the BoomMaster wallet was found."

    block_hex = receipt.get("blockNumber")
    if not block_hex:
        return False, "The Polygon payment is not included in a block yet."
    block_number = int(block_hex, 16)
    finalized = rpc(POLYGON_RPC_URL, "eth_getBlockByNumber", ["finalized", False])
    if finalized and finalized.get("number"):
        if block_number > int(finalized["number"], 16):
            return False, "USDT transfer found; waiting for Polygon finality."
    else:
        latest = rpc(POLYGON_RPC_URL, "eth_getBlockByNumber", ["latest", False])
        if not latest or int(latest["number"], 16) - block_number < POLYGON_FALLBACK_CONFIRMATIONS:
            return False, "USDT transfer found; waiting for Polygon confirmations."
    return True, tx_hash.lower()


def status_for_task(task_id: str) -> dict[str, Any]:
    if not AUDIERA_KEY:
        raise ApiError("AUDIERA_API_KEY is not configured on the server.", 503)
    try:
        response = requests.get(
            f"https://ai.audiera.fi/api/skills/music/{unquote(task_id)}",
            headers={"Authorization": f"Bearer {AUDIERA_KEY}"}, timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ApiError("Could not check the Audiera task status.", 502) from exc
    data = payload.get("data") or {}
    if data.get("status") == "completed":
        songs = data.get("musics") or []
        url = songs[0].get("url") if songs else (data.get("music") or {}).get("url")
        return {"done": bool(url), "songUrl": url}
    if data.get("status") in {"failed", "error"}:
        raise ApiError(data.get("message", "Audiera could not finish this track."), 502)
    return {"done": False}


@app.after_request
def secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return jsonify({"error": str(error)}), error.status


@app.errorhandler(requests.RequestException)
def handle_upstream_error(error):
    app.logger.warning("Upstream request failed: %s", type(error).__name__)
    return jsonify({"error": "An upstream service is unavailable. Please try again."}), 502


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "audieraConfigured": bool(AUDIERA_KEY),
        "openrouterConfigured": bool(OPENROUTER_KEY),
        "paymentVerification": "on-chain",
    })


@app.post("/api/auth/challenge")
def api_auth_challenge():
    require_json()
    challenge_id = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    message = (
        "BoomMaster wallet sign-in\n"
        f"Nonce: {nonce}\n"
        "This signature links this Nimiq wallet to your BoomMaster profile. "
        "It does not authorize a payment."
    )
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM auth_challenges WHERE created_at<? OR used_at IS NOT NULL", (now - 600,))
        conn.execute(
            "INSERT INTO auth_challenges(challenge_id,wallet_address,message,created_at) VALUES(?,?,?,?)",
            (challenge_id, "", message, now),
        )
    return jsonify({"challengeId": challenge_id, "message": message})


@app.post("/api/auth/verify")
def api_auth_verify():
    data = require_json()
    challenge_id = data.get("challengeId")
    public_key_hex = data.get("publicKey")
    signature_hex = data.get("signature")
    if not all(isinstance(value, str) for value in (challenge_id, public_key_hex, signature_hex)):
        raise ApiError("The wallet did not return a complete signature proof.")
    if len(challenge_id) > 100 or not re.fullmatch(r"[A-Za-z0-9_-]+", challenge_id):
        raise ApiError("Invalid sign-in challenge.")
    with db() as conn:
        challenge = conn.execute(
            "SELECT * FROM auth_challenges WHERE challenge_id=? AND used_at IS NULL",
            (challenge_id,),
        ).fetchone()
    now = int(time.time())
    if not challenge or now - challenge["created_at"] > 300:
        raise ApiError("The sign-in request expired. Connect your wallet and try again.", 401)
    try:
        public_key = bytes.fromhex(public_key_hex.removeprefix("0x"))
        signature = bytes.fromhex(signature_hex.removeprefix("0x"))
        if len(public_key) != 32 or len(signature) != 64:
            raise ValueError("Unexpected signature length.")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, nimiq_signed_message_hash(challenge["message"])
        )
        derived_address = nimiq_address_from_public_key(public_key)
    except Exception as exc:
        raise ApiError("Could not verify this wallet signature. Please try again.", 401) from exc
    wallet_address = derived_address

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = now + 30 * 24 * 60 * 60
    with db() as conn:
        changed = conn.execute(
            "UPDATE auth_challenges SET used_at=? WHERE challenge_id=? AND used_at IS NULL",
            (now, challenge_id),
        ).rowcount
        if changed != 1:
            raise ApiError("This sign-in request has already been used.", 409)
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (token_hash, wallet_address, expires_at, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO profiles(user_id,display_name,created_at,updated_at,setup_complete) VALUES(?,?,?,?,0)",
            (wallet_address, "Music Creator", now, now),
        )
    response = make_response(jsonify({"authenticated": True, "walletAddress": wallet_address}))
    response.set_cookie("boommaster_session", token, max_age=30 * 24 * 60 * 60,
                        httponly=True, secure=request.is_secure, samesite="Lax", path="/")
    return response


@app.post("/api/auth/migrate-browser-data")
def api_migrate_browser_data():
    wallet_user_id = require_user_id()
    data = require_json()
    legacy_user_id = data.get("browserUserId")
    if not legacy_user_id:
        return jsonify({"migrated": False, "creations": 0, "playbooks": 0})
    legacy_user_id = require_session(legacy_user_id)
    if legacy_user_id == wallet_user_id:
        return jsonify({"migrated": False, "creations": 0, "playbooks": 0})
    with db() as conn:
        legacy_profile = conn.execute(
            "SELECT display_name,setup_complete FROM profiles WHERE user_id=?", (legacy_user_id,)
        ).fetchone()
        target_profile = conn.execute(
            "SELECT display_name,setup_complete FROM profiles WHERE user_id=?", (wallet_user_id,)
        ).fetchone()
        creation_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE user_id=?", (legacy_user_id,)
        ).fetchone()["count"]
        playbook_count = conn.execute(
            "SELECT COUNT(*) AS count FROM playbooks WHERE user_id=?", (legacy_user_id,)
        ).fetchone()["count"]
        conn.execute("UPDATE sessions SET user_id=? WHERE user_id=?", (wallet_user_id, legacy_user_id))
        conn.execute("UPDATE playbooks SET user_id=? WHERE user_id=?", (wallet_user_id, legacy_user_id))
        if legacy_profile:
            if target_profile and not target_profile["setup_complete"] and legacy_profile["display_name"] != "Music Creator":
                conn.execute(
                    "UPDATE profiles SET display_name=?,setup_complete=1,updated_at=? WHERE user_id=?",
                    (legacy_profile["display_name"], int(time.time()), wallet_user_id),
                )
            conn.execute("DELETE FROM profiles WHERE user_id=?", (legacy_user_id,))
    return jsonify({"migrated": bool(creation_count or playbook_count or legacy_profile),
                    "creations": creation_count, "playbooks": playbook_count})


@app.get("/api/auth/session")
def api_auth_session():
    user_id = authenticated_user_id()
    if not user_id:
        return jsonify({"authenticated": False})
    with db() as conn:
        profile = conn.execute(
            "SELECT display_name,setup_complete FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    if not profile:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "walletAddress": user_id,
                    "displayName": profile["display_name"], "setupComplete": bool(profile["setup_complete"])})


@app.post("/api/auth/logout")
def api_auth_logout():
    token = request.cookies.get("boommaster_session", "")
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with db() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
    response = make_response(jsonify({"loggedOut": True}))
    response.delete_cookie("boommaster_session", path="/", secure=request.is_secure, httponly=True, samesite="Lax")
    return response


@app.get("/api/workspace")
def api_workspace():
    user_id = require_user_id(request.args.get("userId"))
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO profiles(user_id,display_name,created_at,updated_at) VALUES(?,?,?,?)",
            (user_id, "Music Creator", now, now),
        )
        profile = conn.execute(
            "SELECT user_id,display_name,created_at,setup_complete FROM profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        rows = conn.execute(
            """SELECT session_id,style,mood,vibe,lyrics,scout,pitch,song_title,verified_tx,
                      task_id,artist_name,song_url,created_at
               FROM sessions WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100""",
            (user_id,),
        ).fetchall()
    creations = []
    for row in rows:
        creation = dict(row)
        creation["status"] = (
            "ready" if creation["song_url"] else
            "producing" if creation["task_id"] else
            "paid" if creation["verified_tx"] else
            "draft"
        )
        creations.append(creation)
    return jsonify({"profile": dict(profile), "creations": creations})


@app.put("/api/workspace/<session_id>/title")
def api_update_song_title(session_id: str):
    user_id = require_user_id()
    session_id = require_session(session_id)
    data = require_json()
    title = data.get("title")
    title = title.strip()[:80] if isinstance(title, str) else ""
    if not title:
        raise ApiError("Enter a song title.")
    with db() as conn:
        changed = conn.execute(
            "UPDATE sessions SET song_title=? WHERE session_id=? AND user_id=? AND deleted_at IS NULL",
            (title, session_id, user_id),
        ).rowcount
    if not changed:
        raise ApiError("This creation was not found for your account.", 404)
    return jsonify({"saved": True, "songTitle": title})


@app.delete("/api/workspace/<session_id>")
def api_remove_workspace_creation(session_id: str):
    user_id = require_user_id()
    session_id = require_session(session_id)
    with db() as conn:
        changed = conn.execute(
            "UPDATE sessions SET deleted_at=? WHERE session_id=? AND user_id=? AND deleted_at IS NULL",
            (int(time.time()), session_id, user_id),
        ).rowcount
    if not changed:
        raise ApiError("This saved creation was not found for your account.", 404)
    return jsonify({"removed": True})


@app.route("/api/profile", methods=["GET", "PUT"])
def api_profile():
    if request.method == "GET":
        user_id = require_user_id(request.args.get("userId"))
        now = int(time.time())
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO profiles(user_id,display_name,created_at,updated_at) VALUES(?,?,?,?)",
                (user_id, "Music Creator", now, now),
            )
            profile = conn.execute(
                "SELECT user_id,display_name,created_at,setup_complete FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
        return jsonify(dict(profile))

    data = require_json()
    user_id = require_user_id(data.get("userId"))
    display_name = str(data.get("displayName", "")).strip()
    if not display_name or len(display_name) > 40:
        raise ApiError("Choose a profile name between 1 and 40 characters.")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            """INSERT INTO profiles(user_id,display_name,created_at,updated_at,setup_complete) VALUES(?,?,?,?,1)
               ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name,updated_at=excluded.updated_at,setup_complete=1""",
            (user_id, display_name, now, now),
        )
    return jsonify({"userId": user_id, "displayName": display_name, "updatedAt": now})


@app.get("/api/playbooks")
def api_get_playbooks():
    user_id = require_user_id(request.args.get("userId"))
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,style,mood,vibe,created_at FROM playbooks WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return jsonify({"playbooks": [dict(row) for row in rows]})


@app.post("/api/playbooks")
def api_create_playbook():
    data = require_json()
    user_id = require_user_id(data.get("userId"))
    name = str(data.get("name", "")).strip()
    style, mood = data.get("style"), data.get("mood")
    vibe = str(data.get("vibe", "")).strip()[:240]
    if not name or len(name) > 40:
        raise ApiError("Choose a playbook name between 1 and 40 characters.")
    if style not in ARTISTS or mood not in MOODS:
        raise ApiError("Choose a supported style and mood.")
    now = int(time.time())
    with db() as conn:
        if DATABASE_URL:
            row = conn.execute(
                "INSERT INTO playbooks(user_id,name,style,mood,vibe,created_at) VALUES(?,?,?,?,?,?) RETURNING id",
                (user_id, name, style, mood, vibe, now),
            ).fetchone()
            playbook_id = row["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO playbooks(user_id,name,style,mood,vibe,created_at) VALUES(?,?,?,?,?,?)",
                (user_id, name, style, mood, vibe, now),
            )
            playbook_id = cursor.lastrowid
    return jsonify({"id": playbook_id, "name": name, "style": style, "mood": mood, "vibe": vibe}), 201


@app.delete("/api/playbooks/<int:playbook_id>")
def api_delete_playbook(playbook_id: int):
    user_id = require_user_id(request.args.get("userId"))
    with db() as conn:
        cursor = conn.execute("DELETE FROM playbooks WHERE id=? AND user_id=?", (playbook_id, user_id))
    if cursor.rowcount == 0:
        raise ApiError("Playbook not found.", 404)
    return jsonify({"deleted": True})


@app.post("/api/generate-lyrics")
def api_generate_lyrics():
    data = require_json()
    session_id = require_session(data.get("sessionId"))
    user_id = require_user_id()
    style, mood = data.get("style"), data.get("mood")
    vibe = str(data.get("vibe", "")).strip()[:240]
    if style not in ARTISTS or mood not in MOODS:
        raise ApiError("Choose a supported style and mood.")
    inspiration = f"{vibe or 'a story'} — {style} style, {mood} mood"
    lyrics = generate_lyrics_text(inspiration)
    artist_name, _ = ARTISTS[style]
    prompt = (
        "You are Boom Master, an AI music A&R agent.\n\n"
        f"Theme: {vibe or 'a story'}\nStyle: {style}\nMood: {mood}\n\n"
        "Do the following:\n"
        "1. SCOUT: 2 sentences on why this will emotionally connect with listeners.\n"
        "2. PITCH: 3-sentence A&R pitch — hook, audience, why it gets plays.\n\n"
        "Use exactly these headers:\n## SCOUT\n## PITCH"
    )
    try:
        scout, pitch = split_scout_pitch(ask_openrouter(prompt))
    except Exception:
        scout, pitch = "", ""
    with db() as conn:
        conn.execute(
            """INSERT INTO sessions(session_id,user_id,style,mood,vibe,lyrics,scout,pitch,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET user_id=excluded.user_id,style=excluded.style,mood=excluded.mood,
               vibe=excluded.vibe,lyrics=excluded.lyrics,scout=excluded.scout,pitch=excluded.pitch,deleted_at=NULL""",
            (session_id, user_id, style, mood, vibe, lyrics, scout, pitch, int(time.time())),
        )
    return jsonify({"lyrics": lyrics, "scout": scout, "pitch": pitch, "artistName": artist_name})


@app.post("/api/verify-payment")
def api_verify_payment():
    data = require_json()
    user_id = require_user_id()
    session_id = require_session(data.get("sessionId"))
    tx_hash = str(data.get("txHash", "")).strip()
    network = str(data.get("network", "")).strip().lower()
    with db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not session or session["user_id"] != user_id:
            raise ApiError("Generate lyrics for this wallet session before paying.", 404)
        if session["verified_tx"]:
            return jsonify({"verified": True, "txHash": session["verified_tx"]})
        prior = conn.execute("SELECT session_id FROM payments WHERE tx_hash=?", (tx_hash.lower(),)).fetchone()
        if prior and prior["session_id"] != session_id:
            return jsonify({"verified": False, "message": "This transaction was already used."})

    if network in {"nimiq", "nim"}:
        verified, result = verify_nim(tx_hash, data.get("amount"))
        network_name = "nimiq"
        amount = str(data.get("amount", ""))
    elif network in {"polygon", "usdt", "polygon-usdt"}:
        verified, result = verify_polygon(tx_hash, data.get("amount"))
        network_name = "polygon-usdt"
        amount = str(data.get("amount", ""))
    else:
        raise ApiError("Unsupported payment network.")

    if not verified:
        return jsonify({"verified": False, "message": result})
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO payments(tx_hash,session_id,network,amount,created_at) VALUES(?,?,?,?,?)",
                (result, session_id, network_name, amount, int(time.time())),
            )
        except (sqlite3.IntegrityError, psycopg.IntegrityError if psycopg else sqlite3.IntegrityError):
            prior = conn.execute("SELECT session_id FROM payments WHERE tx_hash=?", (result,)).fetchone()
            if not prior or prior["session_id"] != session_id:
                return jsonify({"verified": False, "message": "This transaction was already used."})
        conn.execute("UPDATE sessions SET verified_tx=? WHERE session_id=?", (result, session_id))
    return jsonify({"verified": True, "txHash": result})


@app.post("/api/generate-song")
def api_generate_song():
    data = require_json()
    user_id = require_user_id()
    session_id = require_session(data.get("sessionId"))
    with db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if not session or session["user_id"] != user_id:
            raise ApiError("This song session was not found for this wallet.", 404)
        if not session["verified_tx"]:
            raise ApiError("A verified NIM or Polygon USDT payment is required.", 402)
        if session["task_id"]:
            return jsonify({"taskId": session["task_id"], "artistName": session["artist_name"]})
        style, mood, vibe = session["style"], session["mood"], session["vibe"]
        lyrics = data.get("lyrics")
        lyrics = str(lyrics).strip() if isinstance(lyrics, str) else session["lyrics"]
        if not lyrics:
            raise ApiError("Lyrics are missing for this song.")
        artist_name, artist_id = ARTISTS[style]

    try:
        response = requests.post(
            "https://ai.audiera.fi/api/skills/music",
            headers=audiera_headers(),
            json={
                "styles": [style], "artistId": artist_id,
                "inspiration": f"{vibe or 'a story'} — {mood} mood", "lyrics": lyrics,
            }, timeout=35,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ApiError("Audiera could not start song production.", 502) from exc
    task_id = (payload.get("data") or {}).get("taskId")
    if not response.ok or not payload.get("success") or not task_id:
        raise ApiError(payload.get("message", "Audiera did not return a task ID."), 502)
    with db() as conn:
        conn.execute(
            "UPDATE sessions SET task_id=?,artist_name=?,lyrics=? WHERE session_id=? AND verified_tx IS NOT NULL",
            (str(task_id), artist_name, lyrics, session_id),
        )
    return jsonify({"taskId": str(task_id), "artistName": artist_name})


@app.get("/api/song-status")
def api_song_status():
    user_id = require_user_id()
    task_id = request.args.get("taskId", "").strip()
    if not task_id or len(task_id) > 160 or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise ApiError("A valid taskId is required.")
    with db() as conn:
        session = conn.execute("SELECT verified_tx,user_id FROM sessions WHERE task_id=?", (task_id,)).fetchone()
    if not session or not session["verified_tx"] or session["user_id"] != user_id:
        raise ApiError("No paid song task matches this ID.", 404)
    result = status_for_task(task_id)
    if result.get("done") and result.get("songUrl"):
        with db() as conn:
            conn.execute("UPDATE sessions SET song_url=? WHERE task_id=?", (result["songUrl"], task_id))
    return jsonify(result)


@app.get("/<path:filename>")
def static_files(filename: str):
    # The local server is convenient for testing; the production Vercel site
    # serves the same single-file frontend itself.
    return send_from_directory(ROOT, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=False, threaded=True)
