import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from incidentops.config import Settings
from incidentops.evaluation import evaluate
from incidentops.factory import build_service
from incidentops.ingestion import corpus_fingerprint, ingest
from incidentops.models import Principal, Question


def _print(payload):
    print(json.dumps(payload, indent=2))


def run(args) -> int:
    settings = Settings()
    if args.command == "serve":
        from incidentops.api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
        return 0
    if args.command == "demo":
        # Always isolated and offline, even when the user's .env selects a live model.
        with TemporaryDirectory(prefix="incidentops-demo-") as temp:
            settings.provider, settings.tracing = "fake", False
            settings.database_url = f"sqlite:///{temp}/demo.db"
            service = build_service(settings)
            count = ingest(
                settings.corpus_dir, service.store, service.embeddings, settings.chunk_tokens
            )
            print(f"FAKE PROVIDER DEMO — {count} synthetic chunks; no LLM quality evidence")
            for question in (
                "What does PUB-403 mean in release 2.7?",
                "Is rollback safe to execute now?",
            ):
                result = service.ask(
                    Principal("acme", "demo", ("operators",)), Question(question=question)
                )
                _print({"question": question, **result.model_dump()})
        return 0
    service = build_service(settings)
    if args.command == "ingest":
        count = ingest(
            settings.corpus_dir, service.store, service.embeddings, settings.chunk_tokens
        )
        _print(
            {
                "mode": settings.provider,
                "chunks": count,
                "corpus_sha256": corpus_fingerprint(settings.corpus_dir),
            }
        )
        return 0
    if args.command == "ask":
        principal = Principal(args.tenant, "local-cli-operator", ("operators",))
        result = service.ask(principal, Question(question=args.question, region=args.region))
        _print(result.model_dump())
        return 0
    report = evaluate(service, args.dataset)
    report.update(
        {
            "corpus_sha256": corpus_fingerprint(settings.corpus_dir),
            "chat_model": settings.chat_model
            if settings.provider != "fake"
            else "scripted-fixture-v1",
        }
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    _print(report)
    return 0 if report["all_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description="IncidentOps increment 1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Isolated offline demonstration; always fake")
    commands.add_parser("ingest", help="Atomically replace the configured synthetic corpus")
    ask = commands.add_parser("ask", help="Local operator CLI; not a remote auth boundary")
    ask.add_argument("question")
    ask.add_argument("--tenant", choices=["acme", "bravo"], default="acme")
    ask.add_argument("--region", default="west")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--dataset", type=Path, default=Path("data/eval/dev.jsonl"))
    evaluation.add_argument("--output", type=Path)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    try:
        code = run(args)
    except Exception as exc:
        # Do not print errors which might contain model requests, credentials or SQL parameters.
        print(
            f"IncidentOps failed ({type(exc).__name__}). Check configuration and setup guide.",
            file=sys.stderr,
        )
        code = 2
    raise SystemExit(code)
