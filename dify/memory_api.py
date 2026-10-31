import json
import random
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymysql

DB = dict(host="127.0.0.1", port=2882, user="root", password="", database="dify_memory", charset="utf8mb4")


def conn():
    return pymysql.connect(**DB, autocommit=True)


def init_db():
    with conn() as c, c.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                   id BIGINT AUTO_INCREMENT PRIMARY KEY,
                   user_id VARCHAR(64) NOT NULL,
                   content TEXT NOT NULL,
                   mood VARCHAR(64) DEFAULT '',
                   created_at DATETIME DEFAULT CURRENT_TIMESTAMP
               )"""
        )


def keywords(text):
    return [w for w in re.findall(r"[\w\u4e00-\u9fff]+", text) if len(w) >= 2][:6]



def compose_reply(query, memories, total):
    import random
    personas = [
        ("夸夸怪", ["这也太会生活了吧！", "能把日子过成这样的，不多了。", "今天的你，值得被记一笔。"]),
        ("冷静观察员", ["已记录。今天的你比昨天多了一点具体。", "这条信息有效，建议保留。", "样本 +1，世界更完整了。"]),
        ("文艺青年", ["风把这句话吹进了罐头里。", "有些日子，就是用来被记住的。", "时间会走，句子会留下。"]),
        ("吐槽役", ["又来了又来了，罐头快装不下了。", "这条我替你保密，除非你请我喝奶茶。", "行吧，今天也算没白过。"]),
    ]
    name, lines = random.choice(personas)
    line = random.choice(lines)
    if memories:
        old = memories[0]["content"]
        return (
            "咚——罐头开了。\n"
            + line
            + "\n翻到一条旧回忆：「" + old + "」。\n今天这句「" + query + "」，也替你收好了。\n"
            + "罐头里现在有 " + str(total) + " 条回忆。（人格：" + name + "）"
        )
    return (
        "咚——罐头是空的，你是第一条。\n"
        + line
        + "\n「" + query + "」已经收好，下次再来就能翻到它了。（人格：" + name + "）"
    )

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send({"ok": True})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        try:
            body = self._body()
        except Exception:
            return self._send({"error": "bad json"}, 400)
        path = self.path.split("?")[0]
        user = str(body.get("user_id") or "anonymous")[:64]
        try:
            if path == "/remember":
                content = str(body.get("content") or "").strip()
                if not content:
                    return self._send({"error": "empty content"}, 400)
                with conn() as c, c.cursor() as cur:
                    cur.execute(
                        "INSERT INTO memories (user_id, content, mood) VALUES (%s, %s, %s)",
                        (user, content[:4000], str(body.get("mood") or "")[:64]),
                    )
                    memory_id = cur.lastrowid
                self._send({"ok": True, "id": memory_id})
            elif path == "/recall":
                query = str(body.get("query") or "").strip()
                words = keywords(query)
                rows = []
                with conn() as c, c.cursor() as cur:
                    if words:
                        cond = " OR ".join(["content LIKE %s"] * len(words))
                        cur.execute(
                            "SELECT id, content, mood, created_at FROM memories WHERE user_id=%s AND (%s) ORDER BY id DESC LIMIT 5"
                            % ("%s", cond),
                            (user, *[f"%{w}%" for w in words]),
                        )
                        rows = cur.fetchall()
                    if not rows:
                        cur.execute(
                            "SELECT id, content, mood, created_at FROM memories WHERE user_id=%s ORDER BY RAND() LIMIT 3",
                            (user,),
                        )
                        rows = cur.fetchall()
                    cur.execute("SELECT COUNT(*) FROM memories WHERE user_id=%s", (user,))
                    total = cur.fetchone()[0]
                picked = rows[:3]
                self._send({
                    "ok": True,
                    "total": total,
                    "memories": [
                        {"id": r[0], "content": r[1], "mood": r[2], "created_at": str(r[3])} for r in picked
                    ],
                })
            elif path == "/chat":
                query = str(body.get("query") or "").strip()
                if not query:
                    return self._send({"error": "empty query"}, 400)
                words = keywords(query)
                rows = []
                with conn() as c, c.cursor() as cur:
                    if words:
                        cond = " OR ".join(["content LIKE %s"] * len(words))
                        cur.execute(
                            "SELECT id, content, mood, created_at FROM memories "
                            "WHERE user_id=%s AND (%s) ORDER BY id DESC LIMIT 5" % ("%s", cond),
                            (user, *[f"%{w}%" for w in words]),
                        )
                        rows = cur.fetchall()
                    if not rows:
                        cur.execute(
                            "SELECT id, content, mood, created_at FROM memories "
                            "WHERE user_id=%s ORDER BY RAND() LIMIT 3",
                            (user,),
                        )
                        rows = cur.fetchall()
                    memories = [
                        {"id": r[0], "content": r[1], "mood": r[2], "created_at": str(r[3])}
                        for r in rows[:3]
                    ]
                    cur.execute("SELECT COUNT(*) FROM memories WHERE user_id=%s", (user,))
                    total = cur.fetchone()[0] + 1
                    cur.execute(
                        "INSERT INTO memories (user_id, content, mood) VALUES (%s, %s, %s)",
                        (user, query[:4000], ""),
                    )
                self._send({
                    "ok": True,
                    "reply": compose_reply(query, memories, total),
                    "total": total,
                    "memories": memories,
                })
            elif path == "/stats":
                with conn() as c, c.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM memories WHERE user_id=%s", (user,))
                    total = cur.fetchone()[0]
                self._send({"ok": True, "total": total})
            else:
                self._send({"error": "not found"}, 404)
        except Exception as exc:
            self._send({"error": str(exc)}, 500)


if __name__ == "__main__":
    init_db()
    ThreadingHTTPServer(("0.0.0.0", 18700), Handler).serve_forever()
