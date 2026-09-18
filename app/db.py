"""SQLite storage. Photos live in the database as BLOBs, so the whole app is
one file you can copy or back up."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256      TEXT NOT NULL UNIQUE,
    mime_type   TEXT NOT NULL,
    filename    TEXT,
    byte_size   INTEGER NOT NULL,
    width       INTEGER,
    height      INTEGER,
    data        BLOB NOT NULL,
    thumbnail   BLOB,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS receipts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id         INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    merchant         TEXT,
    merchant_address TEXT,
    merchant_vat_id  TEXT,
    purchased_on     TEXT,
    purchased_at     TEXT,
    currency         TEXT,
    subtotal         REAL,
    tax              REAL,
    tip              REAL,
    total            REAL,
    payment_method   TEXT,
    category         TEXT,
    notes            TEXT,
    model            TEXT,
    raw_response     TEXT,
    extraction_error TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS line_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id  INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    quantity    REAL,
    unit_price  REAL,
    line_total  REAL
);

CREATE INDEX IF NOT EXISTS idx_receipts_purchased_on ON receipts(purchased_on);
CREATE INDEX IF NOT EXISTS idx_receipts_merchant ON receipts(merchant);
CREATE INDEX IF NOT EXISTS idx_line_items_receipt ON line_items(receipt_id, position);
"""

RECEIPT_FIELDS = (
    "merchant",
    "merchant_address",
    "merchant_vat_id",
    "purchased_on",
    "purchased_at",
    "currency",
    "subtotal",
    "tax",
    "tip",
    "total",
    "payment_method",
    "category",
    "notes",
)

_db_path: Path | None = None


def configure(path: Path) -> None:
    """Point the module at a database file and make sure the schema exists."""
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        raise RuntimeError("db.configure() must be called before use")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def store_image(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    mime_type: str,
    filename: str | None,
    data: bytes,
    thumbnail: bytes | None,
    width: int | None,
    height: int | None,
) -> int:
    """Insert a photo, or return the id of an identical one already stored."""
    existing = conn.execute("SELECT id FROM images WHERE sha256 = ?", (sha256,)).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        """INSERT INTO images (sha256, mime_type, filename, byte_size, width, height, data, thumbnail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (sha256, mime_type, filename, len(data), width, height, data, thumbnail),
    )
    return int(cur.lastrowid)


def create_receipt(
    conn: sqlite3.Connection,
    *,
    image_id: int,
    fields: dict[str, Any],
    line_items: list[dict[str, Any]],
    model: str | None,
    raw_response: str | None,
    extraction_error: str | None = None,
) -> int:
    columns = ["image_id", "model", "raw_response", "extraction_error", *RECEIPT_FIELDS]
    values: list[Any] = [image_id, model, raw_response, extraction_error]
    values += [fields.get(name) for name in RECEIPT_FIELDS]
    placeholders = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO receipts ({', '.join(columns)}) VALUES ({placeholders})", values
    )
    receipt_id = int(cur.lastrowid)
    replace_line_items(conn, receipt_id, line_items)
    return receipt_id


def update_receipt(
    conn: sqlite3.Connection,
    receipt_id: int,
    *,
    fields: dict[str, Any],
    line_items: list[dict[str, Any]],
) -> None:
    assignments = ", ".join(f"{name} = ?" for name in RECEIPT_FIELDS)
    values = [fields.get(name) for name in RECEIPT_FIELDS]
    conn.execute(
        f"UPDATE receipts SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        [*values, receipt_id],
    )
    replace_line_items(conn, receipt_id, line_items)


def replace_line_items(
    conn: sqlite3.Connection, receipt_id: int, line_items: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM line_items WHERE receipt_id = ?", (receipt_id,))
    rows = [
        (
            receipt_id,
            position,
            item.get("description"),
            item.get("quantity"),
            item.get("unit_price"),
            item.get("line_total"),
        )
        for position, item in enumerate(line_items)
    ]
    if rows:
        conn.executemany(
            """INSERT INTO line_items (receipt_id, position, description, quantity, unit_price, line_total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )


def get_receipt(conn: sqlite3.Connection, receipt_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT r.*, i.mime_type, i.filename, i.byte_size, i.width, i.height
           FROM receipts r JOIN images i ON i.id = r.image_id
           WHERE r.id = ?""",
        (receipt_id,),
    ).fetchone()


def get_line_items(conn: sqlite3.Connection, receipt_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM line_items WHERE receipt_id = ? ORDER BY position, id", (receipt_id,)
    ).fetchall()


def list_receipts(conn: sqlite3.Connection, search: str = "") -> list[sqlite3.Row]:
    sql = """SELECT r.*, i.id AS img_id,
                    (SELECT COUNT(*) FROM line_items li WHERE li.receipt_id = r.id) AS item_count
             FROM receipts r JOIN images i ON i.id = r.image_id"""
    params: list[Any] = []
    if search:
        sql += " WHERE r.merchant LIKE ? OR r.notes LIKE ? OR r.category LIKE ?"
        params += [f"%{search}%"] * 3
    sql += " ORDER BY COALESCE(r.purchased_on, '') DESC, r.id DESC"
    return conn.execute(sql, params).fetchall()


def get_image(conn: sqlite3.Connection, image_id: int, thumbnail: bool = False) -> sqlite3.Row | None:
    column = "thumbnail" if thumbnail else "data"
    return conn.execute(
        f"SELECT {column} AS blob, mime_type FROM images WHERE id = ?", (image_id,)
    ).fetchone()


def delete_receipt(conn: sqlite3.Connection, receipt_id: int) -> None:
    row = conn.execute("SELECT image_id FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    # Drop the photo too, unless another receipt still points at it.
    still_used = conn.execute(
        "SELECT 1 FROM receipts WHERE image_id = ? LIMIT 1", (row["image_id"],)
    ).fetchone()
    if not still_used:
        conn.execute("DELETE FROM images WHERE id = ?", (row["image_id"],))
