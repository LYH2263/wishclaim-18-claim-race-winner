from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines import projection, race_gate

app = FastAPI(title="Wishclaim", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def now(): return datetime.now(timezone.utc)

def ttl():
    c = connect(); row = c.execute("SELECT value FROM settings WHERE key='ttl_seconds'").fetchone(); c.close()
    return int(row["value"] if row else 86400)

def _raise(result: race_gate.GateResult):
    raise HTTPException(result.status_code, result.reason)

@app.get("/api/health")
def health(): return {"ok": True, "project": "wishclaim"}

@app.get("/api/wishes")
def list_wishes():
    c = connect()
    try:
        return projection.wall(c, now())
    finally:
        c.close()

@app.get("/api/wishes/{wid}")
def get_wish(wid: int):
    c = connect()
    try:
        row = projection.detail(c, wid, now())
    finally:
        c.close()
    if not row: raise HTTPException(404, "not found")
    return row

class WishIn(BaseModel):
    title: str
    note: str = ""

@app.post("/api/wishes")
def create_wish(body: WishIn):
    c = connect()
    cur = c.execute("INSERT INTO wishes(title,note,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.note, "open", "clean"))
    c.commit(); wid = cur.lastrowid; c.close(); return {"id": wid}

class ClaimIn(BaseModel):
    claimer: str

@app.post("/api/wishes/{wid}/claim")
def claim(wid: int, body: ClaimIn):
    c = connect()
    try:
        result = race_gate.claim_wish(c, wid, body.claimer, now(), ttl())
    finally:
        c.close()
    if not result.ok:
        _raise(result)
    return result.payload

@app.post("/api/wishes/{wid}/release")
def release(wid: int):
    c = connect()
    try:
        result = race_gate.release_wish(c, wid)
    finally:
        c.close()
    if not result.ok:
        _raise(result)
    return result.payload

@app.post("/api/wishes/{wid}/fulfill")
def fulfill(wid: int):
    c = connect()
    try:
        result = race_gate.fulfill_wish(c, wid)
    finally:
        c.close()
    if not result.ok:
        _raise(result)
    return result.payload

@app.get("/api/mine")
def mine(claimer: str):
    c = connect()
    try:
        return projection.mine(c, claimer, now())
    finally:
        c.close()

@app.get("/api/done")
def done():
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM wishes WHERE status='fulfilled'")]; c.close(); return rows

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.get("/api/rules")
def rules():
    return {
        "mutex": "同一愿望同时只能被一人认领",
        "ttl": "认领超时未核销则自动释放",
        "fulfill": "核销后状态变为 fulfilled",
        "race": "并发认领同一愿望时恰好一人胜出，败方返回 locked 且不改动锁",
    }
