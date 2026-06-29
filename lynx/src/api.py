"""Service HTTP JSON headless — le moteur LynX hors de Streamlit.

Découple l'analyse de l'UI : utilisable par n'importe quel client (scripts,
autres apps, intégration CI). Stdlib uniquement, multi-thread.

Lancement :  python -m src.api  [--port 8800]

Endpoints :
  GET  /health                          -> {"status":"ok","model":...}
  POST /analyze {corpus, action, semantic?}  -> ImpactReport
  POST /audit   {corpus, deep?}              -> {score, findings, ...}
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import llm
from .audit import audit_matrix
from .models import Action
from .orchestrator import run_impact_analysis, synthesize_verdict


def _analyze(body: dict) -> dict:
    corpus = body.get("corpus", [])
    action = Action(**body["action"])
    semantic = bool(body.get("semantic", True))
    report = run_impact_analysis(corpus, action, semantic=semantic)
    verdict = synthesize_verdict(report, action, use_llm=semantic)
    return {"report": report.model_dump(), "verdict": verdict}


def _audit(body: dict) -> dict:
    rep = audit_matrix(body.get("corpus", []), deep=bool(body.get("deep", True)))
    return {
        "n": rep.n, "score": rep.score, "counts": rep.counts,
        "flagged_ids": rep.flagged_ids, "n_non_audite": rep.n_non_audite,
        "findings": [vars(f) for f in rep.findings],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok", "model": llm.current_model(),
                             "llm_available": llm.llm_available()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": f"corps JSON invalide : {exc}"})
        try:
            route = self.path.rstrip("/")
            if route == "/analyze":
                self._send(200, _analyze(body))
            elif route == "/audit":
                self._send(200, _audit(body))
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def log_message(self, *args):  # silence stdout
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"LynX API sur http://{args.host}:{args.port} (modèle {llm.current_model()})")
    server.serve_forever()


if __name__ == "__main__":
    main()
